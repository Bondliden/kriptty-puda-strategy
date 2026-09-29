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
from datetime import UTC, datetime

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

    # ── Kill-switch diario ──────────────────────────────────────────────
    async def check_daily_loss(self, account_id: str, account: str = "swap") -> bool:
        """True si la cuenta puede abrir posiciones hoy."""
        today = datetime.now(UTC).date().isoformat()
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
        except OrderRejected as e:
            self.state.journal(**journal, status="rejected", detail=str(e))
            log.warning("%s", e)
            raise

        if not order.is_spot and not order.reduce_only:
            await client.ensure_setup(order.symbol, leverage or self.settings.default_leverage)

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
