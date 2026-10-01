"""Base común de las 9 estrategias."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .. import clock
from ..config import Settings
from ..data.macro import MacroProvider
from ..exchange.client import ExchangeClient
from ..exchange.router import AccountRouter
from ..risk.guard import OrderRejected
from ..risk.models import OrderRequest, Position, Side
from ..risk.sizing import risk_based_amount
from ..state import StateStore


@dataclass
class Context:
    settings: Settings
    router: AccountRouter
    state: StateStore
    macro: MacroProvider


class Strategy(ABC):
    account_id: str = ""
    name: str = ""
    # Planificación para APScheduler: {"trigger": "interval", "minutes": 30} o cron.
    schedule: dict[str, Any] = {}
    # Estrategias event-driven (SUB4) implementan start() en lugar de schedule.
    event_driven: bool = False
    leverage: int = 3

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.log = logging.getLogger(f"{self.account_id}.{self.__class__.__name__}")
        self.last_run: float | None = None
        self.last_error: str | None = None

    # ── Accesos rápidos ─────────────────────────────────────────────────
    @property
    def client(self) -> ExchangeClient:
        return self.ctx.router.client(self.account_id)

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.ctx.state.get(self.account_id, key, default)

    def set_state(self, key: str, value: Any) -> None:
        self.ctx.state.set(self.account_id, key, value)

    # ── Ciclo ───────────────────────────────────────────────────────────
    @abstractmethod
    async def run_cycle(self) -> None: ...

    async def safe_run(self) -> None:
        """Envoltorio para el scheduler: un fallo no tumba el motor."""
        try:
            await self.enforce_max_hold()
            await self.run_cycle()
            self.last_error = None
        except Exception as e:  # noqa: BLE001
            self.last_error = repr(e)
            self.log.exception("❌ Error en ciclo: %s", e)
        finally:
            self.last_run = clock.now()

    async def enforce_max_hold(self) -> None:
        """Cierra las posiciones de futuros abiertas hace más de MAX_HOLD_HOURS (compras y ventas
        en como mucho 48H). La hora de apertura la anota el router al enviar la orden."""
        hours = self.ctx.settings.max_hold_hours
        exempt = {a.strip().upper() for a in self.ctx.settings.max_hold_exempt.split(",") if a.strip()}
        if hours <= 0 or self.account_id in exempt:
            return
        now = clock.now()
        current = {p.symbol: p for p in await self.positions() if ":" in p.symbol}
        opened = {sym: t for sym, t in (self.get_state("opened_at") or {}).items() if sym in current}
        for sym, pos in current.items():
            if now - opened.setdefault(sym, now) >= hours * 3600:
                await self.ctx.router.close_position(self.account_id, pos, f"más de {hours:g}H abierta",
                                                     self.account_id)
                opened.pop(sym)
        self.set_state("opened_at", opened)

    async def start(self) -> None:  # solo event-driven
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        return {"account": self.account_id, "name": self.name, "last_run": self.last_run,
                "last_error": self.last_error}

    # ── Apertura con sizing por riesgo ──────────────────────────────────
    @property
    def capital_limit(self) -> float:
        """Fracción máxima de la subcuenta comprometida (``MAX_MARGIN_PCT`` × escalón de la rampa):
        margen en futuros y capital en spot. Con 1 M$ por subcuenta y 0.2, como mucho 200.000 $."""
        return self.ctx.router.capital_limit(self.account_id)

    async def open_position(self, symbol: str, side: Side, entry: float, stop_loss: float,
                            take_profit: float | None, risk_pct: float, *,
                            max_notional_pct: float = 1.0, order_type: str = "market",
                            reason: str = "") -> dict | None:
        client = self.client
        await client.load_markets()
        equity = await client.equity("swap")
        # El tope de nocional cuenta las posiciones ya abiertas: con varias posiciones a la vez la
        # exposición total no puede superar equity × apalancamiento (antes el tope era por
        # posición y SUB2 llegaba a 9× el equity; detectado en el test de estrés de 3 años).
        # Además, cada posición tiene como mucho su parte del total (estrategias con varias
        # posiciones): con SL muy ajustados el sizing por riesgo pedía hasta 3× el equity en una
        # sola posición y las comisiones se comían la cuenta (SUB2 en el test de estrés).
        open_notional = sum(abs(p.amount * p.mark_price) for p in await self.positions() if ":" in p.symbol)
        total_cap = equity * self.leverage * max_notional_pct * self.capital_limit
        slots = getattr(self, "MAX_POSITIONS", None) or getattr(self, "MAX_OPEN", None) or 1
        max_notional = max(0.0, min(total_cap / slots, total_cap - open_notional))
        raw = risk_based_amount(equity, risk_pct, entry, stop_loss, max_notional=max_notional)
        amount = client.amount_to_precision(symbol, raw, entry)
        if amount <= 0:
            self.log.info("⏭  %s: tamaño %.8g bajo el mínimo del mercado (equity=%.2f). No se opera.",
                          symbol, raw, equity)
            return None
        order = OrderRequest(
            symbol=symbol, side=side, amount=amount, order_type=order_type,  # type: ignore[arg-type]
            price=entry if order_type == "limit" else None,
            stop_loss=client.price_to_precision(symbol, stop_loss),
            take_profit=client.price_to_precision(symbol, take_profit) if take_profit else None,
            tag=self.account_id, client_id=f"{self.account_id.lower()}-{int(clock.now() * 1000)}",
        )
        self.log.info("🚀 %s %s | entry≈%.6g SL=%.6g TP=%s | riesgo %.2f%% | %s", side.upper(), symbol,
                      entry, stop_loss, f"{take_profit:.6g}" if take_profit else "-", risk_pct * 100, reason)
        try:
            return await self.ctx.router.execute(self.account_id, order, leverage=self.leverage)
        except OrderRejected as e:
            self.log.warning("Orden rechazada: %s", e)
            return None

    async def positions(self, symbols: list[str] | None = None) -> list[Position]:
        return await self.client.positions(symbols)
