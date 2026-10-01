"""Cliente de exchange para backtesting: la misma interfaz que usan las
estrategias en vivo, alimentada con datos históricos y reloj simulado.

Modelo de ejecución (conservador):
  * Órdenes de mercado: al cierre de la última vela ± slippage.
  * Límites: se ejecutan a su precio si el mínimo/máximo de la vela los toca.
  * SL/TP de posición: con el mínimo/máximo de la vela; si en la misma vela se
    tocan ambos se asume el SL (pesimista); si la vela abre más allá del SL,
    se ejecuta a la apertura (gap).
  * Funding: se cobra/paga en cada marca de tiempo de funding histórica.
  * Comisión taker (0.06%) en mercado, SL/TP y stops; maker (0.02%) en límites.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import ccxt

from .. import clock
from ..config import Settings
from ..exchange.paper import PaperExchangeClient
from ..risk.models import OrderRequest
from .market import HistoricalMarket

_ids = itertools.count(1)


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    amount: float
    entry: float
    exit: float
    pnl: float
    closed_at: float


class BacktestClient(PaperExchangeClient):
    def __init__(self, account_id: str, settings: Settings, market: HistoricalMarket,
                 fee_rate: float = 0.0006, slippage_bps: float = 2.0, maker_fee: float = 0.0002):
        super().__init__(account_id, settings)
        self.market = market
        self.fee_rate = fee_rate
        self.taker_fee = fee_rate
        self.maker_fee = maker_fee
        self.slippage = slippage_bps / 10_000
        self.trades: list[ClosedTrade] = []
        self.funding_paid = 0.0
        self.leverage: dict[str, int] = {}
        self.rejected_margin = 0
        self.spot_cost: dict[str, tuple[float, float]] = {}  # coste y cantidad en spot, para el PnL de las ventas

    # ── Datos de mercado ────────────────────────────────────────────────
    def _resolve(self, symbol: str) -> str:
        if self.market.has(symbol):
            return symbol
        alt = f"{symbol}:USDT" if ":" not in symbol else symbol.split(":")[0]
        if self.market.has(alt):
            return alt
        raise KeyError(f"El backtest no tiene datos de {symbol}")

    async def load_markets(self) -> None:
        return None

    def market(self, symbol: str) -> dict:
        return {"limits": {"amount": {"min": 1e-6}, "cost": {"min": 5.0}}}

    def amount_to_precision(self, symbol: str, amount: float, price: float | None = None) -> float:
        rounded = int(amount * 1e6) / 1e6
        if rounded <= 0 or (price and rounded * price < 5.0):
            return 0.0
        return rounded

    def price_to_precision(self, symbol: str, price: float) -> float:
        return float(f"{price:.6g}")

    async def ohlcv(self, symbol, timeframe, limit=200, closed_only=True):
        return self.market.ohlcv(self._resolve(symbol), timeframe, clock.now(), limit)

    async def last_price(self, symbol):
        return self.market.price(self._resolve(symbol), clock.now())

    async def ticker(self, symbol):
        sym = self._resolve(symbol)
        price = self.market.price(sym, clock.now())
        change, volume = self.market.stats_24h(sym, clock.now())
        return {"symbol": symbol, "last": price, "bid": price * (1 - self.slippage),
                "ask": price * (1 + self.slippage), "percentage": change, "quoteVolume": volume}

    async def tickers(self, market_type="swap"):
        out = {}
        for s in self.market.symbols:
            if (":" in s) == (market_type == "swap"):
                try:
                    out[s] = await self.ticker(s)
                except KeyError:
                    continue
        return out

    async def funding_rate(self, symbol):
        sym = self._resolve(symbol)
        rate = self.market.funding_at(sym, clock.now())
        return {"rate": rate if rate is not None else 0.0, "interval_hours": self.market.funding_interval_h.get(sym, 8.0),
                "next_ts": None, "mark": None, "index": None}

    # ── Ejecución ───────────────────────────────────────────────────────
    async def _mark(self, symbol: str) -> float:
        """Solo valora: las ejecuciones ocurren en process_bar()."""
        price = await self.last_price(symbol)
        pos = self.book.get(symbol)
        if pos:
            pos.mark_price = price
            pos.unrealized_pnl = (1 if pos.side == "long" else -1) * (price - pos.entry_price) * pos.amount
        return price

    def _fill(self, symbol, side, amount, price, sl=None, tp=None):
        pos = self.book.get(symbol)
        if ":" not in symbol:  # spot (SUB8): las ventas cuentan como operaciones cerradas al precio medio
            base = symbol.split("/")[0]
            cost, qty = self.spot_cost.get(base, (0.0, 0.0))
            if side == "buy":
                self.spot_cost[base] = (cost + amount * price, qty + amount)
            elif qty > 0:
                sold = min(amount, self.coins.get(base, 0.0))
                avg = cost / qty
                if sold > 0:
                    self.trades.append(ClosedTrade(symbol, "long", sold, avg, price, (price - avg) * sold, clock.now()))
                self.spot_cost[base] = (cost * (1 - sold / qty), qty - sold) if qty - sold > 1e-12 else (0.0, 0.0)
        elif pos is not None and pos.side != ("long" if side == "buy" else "short"):
            closed = min(pos.amount, amount)
            sign = 1 if pos.side == "long" else -1
            self.trades.append(ClosedTrade(symbol, pos.side, closed, pos.entry_price, price,
                                           sign * (price - pos.entry_price) * closed, clock.now()))
        super()._fill(symbol, side, amount, price, sl, tp)

    async def ensure_setup(self, symbol: str, leverage: int) -> None:
        self.leverage[symbol] = leverage
        await super().ensure_setup(symbol, leverage)

    async def _used_margin(self) -> float:
        margin = 0.0
        for sym, p in self.book.items():
            if ":" in sym:
                margin += p.amount * await self.last_price(sym) / self.leverage.get(sym, self.settings.default_leverage)
        return margin

    async def free(self, coin: str = "USDT", account: str = "swap") -> float:
        """Disponible como en el exchange: en futuros, equity menos el margen ya usado."""
        if coin == "USDT" and account == "swap":
            return max(0.0, await self.equity("swap") - await self._used_margin())
        return await super().free(coin, account)

    async def _margin_ok(self, order: OrderRequest, price: float) -> bool:
        """Margen inicial como en el exchange: Σ nocional / apalancamiento ≤ equity de futuros.
        Sin esta comprobación una estrategia con varias posiciones podía apalancarse muy por
        encima de lo configurado (detectado con SUB2 en el test de estrés)."""
        if order.is_spot or order.reduce_only:
            return True
        pos = self.book.get(order.symbol)
        if pos is not None and pos.side != ("long" if order.side == "buy" else "short"):
            return True  # reduce o invierte: no añade margen neto en este modelo
        margin = await self._used_margin()
        new = order.amount * price / self.leverage.get(order.symbol, self.settings.default_leverage)
        return margin + new <= await self.equity("swap") * 1.0001

    async def place(self, order: OrderRequest) -> dict:
        if not await self._margin_ok(order, order.price or await self.last_price(order.symbol)):
            self.rejected_margin += 1
            raise ccxt.InsufficientFunds(f"[backtest] margen insuficiente para {order.symbol}")
        if order.order_type == "limit":
            return await super().place(order)
        oid = f"bt-{next(_ids)}"
        price = await self.last_price(order.symbol)
        price *= 1 + self.slippage if order.side == "buy" else 1 - self.slippage
        amount = order.amount
        if order.reduce_only:
            pos = self.book.get(order.symbol)
            if not pos:
                return {"id": oid, "status": "canceled", "filled": 0.0}
            amount = min(amount, pos.amount)
        self._fill(order.symbol, order.side, amount, price, order.stop_loss, order.take_profit)
        return {"id": oid, "status": "closed", "average": price, "filled": amount, "amount": amount}

    async def close_position(self, position, tag: str = "") -> dict:
        p = self.book.get(position.symbol)
        if p is None:
            return {"status": "closed"}
        price = await self.last_price(position.symbol)
        price *= 1 - self.slippage if p.side == "long" else 1 + self.slippage
        self._fill(p.symbol, "sell" if p.side == "long" else "buy", p.amount, price)
        return {"status": "closed", "average": price}

    def process_bar(self, t: float) -> None:
        """Aplica la vela que cierra en t a órdenes límite, SL/TP, stops spot y funding."""
        for symbol in self.market.symbols:
            bar = self.market.bar_closing_at(symbol, t)
            if bar is None:
                continue
            o, h, lo = float(bar["open"]), float(bar["high"]), float(bar["low"])
            for order in [x for x in self.limits if x["symbol"] == symbol]:
                if (order["side"] == "buy" and lo <= order["price"]) or (order["side"] == "sell" and h >= order["price"]):
                    self.limits.remove(order)
                    pos = self.book.get(symbol)
                    if order["reduce_only"] and not pos:
                        self.status[order["id"]] = "canceled"
                        continue
                    self.status[order["id"]] = "closed"
                    amount = min(order["amount"], pos.amount) if order["reduce_only"] and pos else order["amount"]
                    fill = min(order["price"], o) if order["side"] == "buy" else max(order["price"], o)
                    self.fee_rate = self.maker_fee  # las límites que reposan en el libro pagan maker
                    try:
                        self._fill(symbol, order["side"], amount, fill, order["sl"], order["tp"])
                    finally:
                        self.fee_rate = self.taker_fee
            pos = self.book.get(symbol)
            if pos is not None:
                long = pos.side == "long"
                sl, tp = pos.stop_loss, pos.take_profit
                exit_price = None
                if sl and ((long and lo <= sl) or (not long and h >= sl)):
                    exit_price = min(sl, o) if long else max(sl, o)
                elif tp and ((long and h >= tp) or (not long and lo <= tp)):
                    exit_price = max(tp, o) if long else min(tp, o)
                if exit_price is not None:
                    self._fill(symbol, "sell" if long else "buy", pos.amount, exit_price)
            for stop in [x for x in self.spot_stops if x["symbol"] == symbol]:
                if lo <= stop["trigger"]:
                    self.spot_stops.remove(stop)
                    self.status[stop["id"]] = "closed"
                    self._fill(symbol, "sell", stop["amount"], min(stop["trigger"], o))
            pos = self.book.get(symbol)
            if pos is not None:
                for rate in self.market.funding_between(symbol, t - self.market.base_s, t):
                    payment = rate * pos.amount * float(bar["close"]) * (1 if pos.side == "long" else -1)
                    self.usdt["swap"] -= payment
                    self.funding_paid += payment

    def total_equity(self, t: float) -> float:
        value = self.usdt["swap"] + self.usdt["spot"]
        for p in self.book.values():
            value += (1 if p.side == "long" else -1) * (self.market.price(p.symbol, t) - p.entry_price) * p.amount
        for coin, amt in self.coins.items():
            if amt > 1e-12:
                value += amt * self.market.price(self._resolve(f"{coin}/USDT"), t)
        return value
