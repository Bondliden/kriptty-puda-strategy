from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Side = Literal["buy", "sell"]


@dataclass
class OrderRequest:
    """Orden normalizada que toda estrategia envía al AccountRouter.

    symbol usa la notación unificada de ccxt:
        perpetuo USDT-M -> "BTC/USDT:USDT"      spot -> "BTC/USDT"
    amount siempre en unidades del activo base.
    """

    symbol: str
    side: Side
    amount: float
    order_type: Literal["market", "limit"] = "market"
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reduce_only: bool = False
    client_id: str | None = None
    tag: str = ""
    # Solo para la pata de cobertura de una estructura delta-neutral (SUB6):
    # su protección es el cierre coordinado del par, no un SL propio.
    sl_exempt_reason: str | None = None

    @property
    def is_spot(self) -> bool:
        return ":" not in self.symbol

    @property
    def opens_long(self) -> bool:
        return self.side == "buy" and not self.reduce_only

    @property
    def opens_short(self) -> bool:
        return self.side == "sell" and not self.reduce_only and not self.is_spot


@dataclass
class Position:
    symbol: str
    side: Literal["long", "short"]
    amount: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def notional(self) -> float:
        return self.amount * self.mark_price
