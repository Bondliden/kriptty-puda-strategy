"""AccountRouter — punto único por el que pasa TODA orden del sistema.

Responsabilidades:
  1. Un ExchangeClient por cuenta (MAIN + SUB1..SUB9), con sus propias API keys.
  2. Validar el Stop Loss (guard.validate_order) antes de enviar nada.
  3. Kill-switch diario por cuenta (pérdida > MAX_DAILY_LOSS_PCT bloquea aperturas).
  4. Tras una compra spot con SL: colocar el stop; si falla, deshacer la compra.
  5. Registrar cada orden (enviada o rechazada) en el diario SQLite.
"""
from __future__ import annotations

import logging

from .. import clock
from ..config import ACCOUNT_IDS, Settings, load_credentials
from ..risk.guard import OrderRejected, validate_order
from ..risk.models import OrderRequest, Position
from ..state import StateStore
from .client import ExchangeClient
from .paper import PaperExchangeClient

log = logging.getLogger(__name__)


class AccountRouter:
    def __init__(self, settings: Settings, state: StateStore):
        settings.check_live_guard()
        self.settings = settings
        self.state = state
        self._clients: dict[str, ExchangeClient] = {}

    def client(self, account_id: str) -> ExchangeClient:
        account_id = account_id.upper()
        if account_id not in ACCOUNT_IDS:
            raise ValueError(f"Cuenta desconocida: {account_id}")
        if account_id not in self._clients:
            if self.settings.trading_mode == "dry_run":
                self._clients[account_id] = PaperExchangeClient(account_id, self.settings)
            else:
                creds = load_credentials(account_id)
                if creds is None:
                    raise RuntimeError(
                        f"Faltan BITGET_{account_id}_API_KEY/SECRET/PASSPHRASE para modo "
                        f"{self.settings.trading_mode}"
                    )
                self._clients[account_id] = ExchangeClient(account_id, creds, self.settings)
        return self._clients[account_id]

    # ── Corte por drawdown desde máximos ────────────────────────────────
    async def check_drawdown(self, account_id: str) -> bool:
        """True si la cuenta puede abrir según su drawdown (equity spot + futuros).

        * Pausa: caída > MAX_DRAWDOWN_PCT desde el máximo reciente → sin aperturas durante
          DRAWDOWN_COOLDOWN_DAYS; después el máximo de referencia se reinicia.
        * Parada dura: caída > MAX_TOTAL_DRAWDOWN_PCT desde el máximo histórico (no se reinicia)
          → sin aperturas hasta revisión manual.
        """
        exempt = {a.strip().upper() for a in self.settings.drawdown_exempt.split(",") if a.strip()}
        if account_id in exempt or self.settings.max_drawdown_pct <= 0:
            return True
        client = self.client(account_id)
        equity = await client.equity("swap") + await client.equity("spot")
        now = clock.now()
        record = self.state.get("drawdown", account_id) or {"peak": equity, "hwm": equity, "halted_until": 0.0}
        record.setdefault("hwm", record["peak"])
        if record.get("hard_stop"):
            return False
        if record.get("halted_until", 0.0) > now:
            return False
        if record.get("halted_until", 0.0):
            record.update(peak=equity, halted_until=0.0)  # fin de la pausa: nuevo máximo de referencia
        record["peak"] = max(record["peak"], equity)
        record["hwm"] = max(record["hwm"], equity)
        hard = self.settings.max_total_drawdown_pct
        if hard > 0 and record["hwm"] > 0 and (record["hwm"] - equity) / record["hwm"] > hard:
            record["hard_stop"] = True
            log.critical("⛔ [%s] Drawdown %.1f%% desde el máximo histórico > %.0f%%: parada dura. "
                         "Revisa la estrategia y borra el estado 'drawdown' para reanudar.",
                         account_id, (record["hwm"] - equity) / record["hwm"] * 100, hard * 100)
            self.state.set("drawdown", account_id, record)
            return False
        if record["peak"] > 0 and (record["peak"] - equity) / record["peak"] > self.settings.max_drawdown_pct:
            record["halted_until"] = now + self.settings.drawdown_cooldown_days * 86_400
            log.warning("🛑 [%s] Drawdown %.1f%% desde máximos > %.0f%%: sin aperturas durante %d días.",
                        account_id, (record["peak"] - equity) / record["peak"] * 100,
                        self.settings.max_drawdown_pct * 100, self.settings.drawdown_cooldown_days)
            self.state.set("drawdown", account_id, record)
            return False
        self.state.set("drawdown", account_id, record)
        return True

    # ── Rampa de capital ────────────────────────────────────────────────
    def _ramp_steps(self) -> list[float]:
        return [float(x) for x in self.settings.capital_ramp.split(",") if x.strip()]

    def capital_limit(self, account_id: str) -> float:
        """Fracción de la subcuenta que se puede comprometer ahora (margen en futuros, capital en
        spot): MAX_MARGIN_PCT por el escalón actual de la rampa."""
        base = min(1.0, self.settings.max_margin_pct)
        steps = self._ramp_steps()
        if not steps:
            return base
        record = self.state.get("ramp", account_id.upper()) or {}
        return base * steps[min(record.get("stage", 0), len(steps) - 1)]

    async def update_ramp(self, account_id: str) -> None:
        """Sube o baja un escalón de la rampa según el resultado desde el inicio del escalón."""
        steps = self._ramp_steps()
        if not steps:
            return
        client = self.client(account_id)
        equity = await client.equity("swap") + await client.equity("spot")
        now = clock.now()
        record = self.state.get("ramp", account_id)
        if not record:
            self.state.set("ramp", account_id, {"stage": 0, "since": now, "start": equity})
            return
        stage, start = record["stage"], record["start"]
        if stage > 0 and start > 0 and equity < start * (1 - self.settings.ramp_step_back_pct):
            stage -= 1
            log.warning("📉 [%s] Rampa: −%.1f%% desde el inicio del escalón → baja al %.0f%% del límite",
                        account_id, (1 - equity / start) * 100, steps[stage] * 100)
        elif now - record["since"] >= self.settings.ramp_step_days * 86_400:
            if equity > start and stage < len(steps) - 1:  # sin operar (equity plano) no sube
                stage += 1
                log.info("📈 [%s] Rampa: escalón en beneficio → sube al %.0f%% del límite", account_id,
                         steps[stage] * 100)
        else:
            return
        self.state.set("ramp", account_id, {"stage": stage, "since": now, "start": equity})

    async def check_margin(self, account_id: str, order: OrderRequest, price: float, leverage: int) -> None:
        """Rechaza la orden si el margen comprometido en futuros superaría MAX_MARGIN_PCT del
        equity de la subcuenta. Una orden que reduce o invierte una posición no suma margen."""
        limit = self.capital_limit(account_id)
        if limit >= 1.0:
            return
        client = self.client(account_id)
        positions = [p for p in await client.positions() if ":" in p.symbol]
        side = "long" if order.side == "buy" else "short"
        if any(p.symbol == order.symbol and p.side != side for p in positions):
            return
        notional = sum(abs(p.amount * p.mark_price) for p in positions) + order.amount * price
        equity = await client.equity("swap") + await client.equity("spot")
        margin = notional / max(leverage, 1)
        if equity > 0 and margin > limit * equity * 1.0001:
            raise OrderRejected(f"[{account_id}] margen {margin / equity:.0%} del equity > máximo {limit:.0%}")

    async def can_open(self, account_id: str, account: str = "swap") -> bool:
        """Kill-switch diario de la cartera indicada + corte por drawdown de la subcuenta."""
        return await self.check_daily_loss(account_id, account) and await self.check_drawdown(account_id)

    # ── Kill-switch diario ──────────────────────────────────────────────
    async def check_daily_loss(self, account_id: str, account: str = "swap") -> bool:
        """True si la cuenta puede abrir posiciones hoy."""
        today = clock.utcnow().date().isoformat()
        key = f"{account_id}:{account}"
        record = self.state.get("daily_equity", key)
        equity = await self.client(account_id).equity(account)
        if not record or record.get("date") != today:
            self.state.set("daily_equity", key, {"date": today, "start": equity, "halted": False})
            return True
        if record.get("halted"):
            return False
        start = record["start"] or 0
        if start > 0 and (start - equity) / start > self.settings.max_daily_loss_pct:
            record["halted"] = True
            self.state.set("daily_equity", key, record)
            log.warning("🛑 [%s] Kill-switch: pérdida diaria %.2f%% > %.2f%%. Sin aperturas hasta mañana.",
                        account_id, (start - equity) / start * 100, self.settings.max_daily_loss_pct * 100)
            return False
        return True

    # ── Ejecución ───────────────────────────────────────────────────────
    async def execute(self, account_id: str, order: OrderRequest, *, leverage: int | None = None) -> dict:
        account_id = account_id.upper()
        client = self.client(account_id)
        ref_price = order.price if order.order_type == "limit" else await client.last_price(order.symbol)
        journal = dict(account=account_id, tag=order.tag, symbol=order.symbol, side=order.side,
                       amount=order.amount, price=ref_price, stop_loss=order.stop_loss,
                       take_profit=order.take_profit, mode=self.settings.trading_mode)
        try:
            validate_order(order, ref_price, self.settings.max_sl_distance_pct)
            opening = not order.reduce_only and not (order.is_spot and order.side == "sell")
            if opening and not await self.check_daily_loss(account_id, "spot" if order.is_spot else "swap"):
                raise OrderRejected(f"[{account_id}] kill-switch diario activo")
            if opening:
                await self.update_ramp(account_id)
            if opening and not await self.check_drawdown(account_id):
                raise OrderRejected(f"[{account_id}] pausa por drawdown desde máximos")
            if opening and not order.is_spot:
                await self.check_margin(account_id, order, ref_price, leverage or self.settings.default_leverage)
        except OrderRejected as e:
            self.state.journal(**journal, status="rejected", detail=str(e))
            log.warning("%s", e)
            raise

        if not order.is_spot and not order.reduce_only:
            await client.ensure_setup(order.symbol, leverage or self.settings.default_leverage)
            opened = self.state.get(account_id, "opened_at") or {}
            opened.setdefault(order.symbol, clock.now())  # para MAX_HOLD_HOURS
            self.state.set(account_id, "opened_at", opened)

        log.info("📤 [%s|%s] %s %s %s %.8g @ %s SL=%s TP=%s%s", account_id, order.tag,
                 order.order_type.upper(), order.side.upper(), order.symbol, order.amount,
                 order.price or "MKT", order.stop_loss, order.take_profit,
                 " (reduce-only)" if order.reduce_only else "")
        try:
            result = await client.place(order)
        except Exception as e:
            self.state.journal(**journal, status="error", detail=repr(e))
            raise

        if order.is_spot and order.side == "buy" and order.stop_loss is not None:
            filled = float(result.get("filled") or order.amount)
            try:
                stop = await client.place_spot_stop(order.symbol, filled, order.stop_loss)
                result["stop_order"] = stop
            except Exception as e:
                log.error("❌ [%s] No se pudo colocar el SL spot (%s). Deshaciendo compra.", account_id, e)
                await client.place(OrderRequest(order.symbol, "sell", filled, tag=f"{order.tag}:rollback"))
                self.state.journal(**journal, status="rolled_back", detail=repr(e))
                raise OrderRejected(f"SL spot no colocado; compra deshecha: {e}") from e

        self.state.journal(**journal, status="sent", detail=str(result.get("id")))
        return result

    async def update_stop_loss(self, account_id: str, position: Position, new_sl: float, tag: str = "") -> None:
        client = self.client(account_id)
        new_sl = client.price_to_precision(position.symbol, new_sl) if client.ex.markets else new_sl
        await client.replace_stop_loss(position, new_sl)
        self.state.journal(account=account_id, tag=tag, symbol=position.symbol, side="sl_update",
                           amount=position.amount, price=position.mark_price, stop_loss=new_sl,
                           take_profit=None, mode=self.settings.trading_mode, status="sent", detail="")
        log.info("🔄 [%s|%s] SL %s → %.6g", account_id, tag, position.symbol, new_sl)

    async def close_position(self, account_id: str, position: Position, reason: str, tag: str = "") -> dict:
        client = self.client(account_id)
        await client.cancel_all(position.symbol)
        result = await client.close_position(position, tag)
        self.state.journal(account=account_id, tag=tag, symbol=position.symbol, side="close",
                           amount=position.amount, price=position.mark_price, stop_loss=None,
                           take_profit=None, mode=self.settings.trading_mode, status="sent", detail=reason)
        log.info("🔒 [%s|%s] Cerrada %s %s: %s", account_id, tag, position.side, position.symbol, reason)
        return result

    async def close(self) -> None:
        for c in self._clients.values():
            await c.close()
