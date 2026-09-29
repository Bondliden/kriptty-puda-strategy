"""Broker simulado para TRADING_MODE=dry_run.

Usa precios reales (endpoints públicos de Bitget vía ccxt) pero ejecuta en
memoria: posiciones one-way con SL/TP, órdenes límite, stops spot y comisiones.
Es aproximado (fills al último precio, sin slippage ni funding): sirve para ver
el comportamiento de las estrategias, no para medir rentabilidad. Para eso,
usar TRADING_MODE=demo (Bitget Demo Trading).
"""
from __future__ import annotations

import itertools
import logging

from ..config import Settings
from ..risk.models import OrderRequest, Position
from .client import ExchangeClient

log = logging.getLogger(__name__)
TAKER_FEE = 0.0006
_ids = itertools.count(1)


class PaperExchangeClient(ExchangeClient):
    fee_rate = TAKER_FEE

    def __init__(self, account_id: str, settings: Settings):
        super().__init__(account_id, None, settings)
        self.usdt = {"swap": settings.dry_run_equity, "spot": settings.dry_run_equity}
        self.coins: dict[str, float] = {}
        self.book: dict[str, Position] = {}
        self.limits: list[dict] = []
        self.spot_stops: list[dict] = []
        self.status: dict[str, str] = {}
        self.fees_paid = 0.0

    # ── Simulación ──────────────────────────────────────────────────────
    def _fill(self, symbol: str, side: str, amount: float, price: float,
              sl: float | None = None, tp: float | None = None) -> None:
        if ":" not in symbol and side == "sell":
            amount = min(amount, self.coins.get(symbol.split("/")[0], 0.0))
        self.fees_paid += amount * price * self.fee_rate
        if ":" not in symbol:
            base = symbol.split("/")[0]
            if side == "buy":
                self.usdt["spot"] -= amount * price * (1 + self.fee_rate)
                self.coins[base] = self.coins.get(base, 0.0) + amount
            else:
                self.coins[base] = self.coins.get(base, 0.0) - amount
                self.usdt["spot"] += amount * price * (1 - self.fee_rate)
            return

        self.usdt["swap"] -= amount * price * self.fee_rate
        direction = "long" if side == "buy" else "short"
        pos = self.book.get(symbol)
        if pos is None:
            self.book[symbol] = Position(symbol, direction, amount, price, price, stop_loss=sl, take_profit=tp)
            return
        if pos.side == direction:
            total = pos.amount + amount
            pos.entry_price = (pos.entry_price * pos.amount + price * amount) / total
            pos.amount = total
            pos.stop_loss = sl if sl is not None else pos.stop_loss
            pos.take_profit = tp if tp is not None else pos.take_profit
            return
        closed = min(pos.amount, amount)
        sign = 1 if pos.side == "long" else -1
        self.usdt["swap"] += sign * (price - pos.entry_price) * closed
        pos.amount -= closed
        remaining = amount - closed
        if pos.amount <= 1e-12:
            del self.book[symbol]
            if remaining > 1e-12:
                self.book[symbol] = Position(symbol, direction, remaining, price, price, stop_loss=sl, take_profit=tp)

    async def _mark(self, symbol: str) -> float:
        price = await self.last_price(symbol)
        for o in [o for o in self.limits if o["symbol"] == symbol]:
            if (o["side"] == "buy" and price <= o["price"]) or (o["side"] == "sell" and price >= o["price"]):
                self.limits.remove(o)
                pos = self.book.get(symbol)
                if o["reduce_only"] and not pos:
                    self.status[o["id"]] = "canceled"  # como el exchange: nada que reducir
                    continue
                self.status[o["id"]] = "closed"
                amount = min(o["amount"], pos.amount) if o["reduce_only"] and pos else o["amount"]
                self._fill(symbol, o["side"], amount, o["price"], o["sl"], o["tp"])
                log.info("[PAPER %s] limit %s %s %.6f @ %.4f ejecutada", self.account_id,
                         o["side"], symbol, amount, o["price"])
        pos = self.book.get(symbol)
        if pos:
            pos.mark_price = price
            sign = 1 if pos.side == "long" else -1
            pos.unrealized_pnl = sign * (price - pos.entry_price) * pos.amount
            hit_sl = pos.stop_loss and ((pos.side == "long" and price <= pos.stop_loss)
                                        or (pos.side == "short" and price >= pos.stop_loss))
            hit_tp = pos.take_profit and ((pos.side == "long" and price >= pos.take_profit)
                                          or (pos.side == "short" and price <= pos.take_profit))
            if hit_sl or hit_tp:
                exit_price = pos.stop_loss if hit_sl else pos.take_profit
                log.info("[PAPER %s] %s %s ejecutado @ %.4f", self.account_id,
                         "SL" if hit_sl else "TP", symbol, exit_price)
                self._fill(symbol, "sell" if pos.side == "long" else "buy", pos.amount, exit_price)
        for s in [s for s in self.spot_stops if s["symbol"] == symbol]:
            if price <= s["trigger"]:
                self.spot_stops.remove(s)
                self.status[s["id"]] = "closed"
                self._fill(symbol, "sell", s["amount"], s["trigger"])
                log.info("[PAPER %s] stop spot %s ejecutado @ %.4f", self.account_id, symbol, s["trigger"])
        return price

    # ── Interfaz de ExchangeClient ──────────────────────────────────────
    async def ensure_setup(self, symbol: str, leverage: int) -> None:
        return None

    async def equity(self, account: str = "swap") -> float:
        if account == "spot":
            value = self.usdt["spot"]
            for coin, amt in self.coins.items():
                if amt > 0:
                    value += amt * await self._mark(f"{coin}/USDT")
            return value
        upnl = 0.0
        for symbol in list(self.book):
            await self._mark(symbol)
            if symbol in self.book:
                upnl += self.book[symbol].unrealized_pnl
        return self.usdt["swap"] + upnl

    async def free(self, coin: str = "USDT", account: str = "swap") -> float:
        if coin == "USDT":
            return self.usdt[account]
        return self.coins.get(coin, 0.0)

    async def holding(self, coin: str) -> float:
        await self._mark(f"{coin}/USDT")
        return self.coins.get(coin, 0.0)

    async def positions(self, symbols: list[str] | None = None) -> list[Position]:
        watched = set(symbols or []) | set(self.book) | {o["symbol"] for o in self.limits}
        for symbol in watched:
            if ":" in symbol:
                await self._mark(symbol)
        return [p for s, p in self.book.items() if symbols is None or s in symbols]

    async def open_orders(self, symbol: str, trigger: bool = False) -> list[dict]:
        await self._mark(symbol)
        if trigger:
            return [{"id": s["id"], "symbol": symbol, "info": {"planType": "spot_stop"}, **s}
                    for s in self.spot_stops if s["symbol"] == symbol]
        return [{"id": o["id"], "symbol": symbol, "side": o["side"], "price": o["price"],
                 "amount": o["amount"], "reduceOnly": o["reduce_only"], "info": {}}
                for o in self.limits if o["symbol"] == symbol]

    async def order_status(self, order_id: str, symbol: str) -> str:
        await self._mark(symbol)
        return self.status.get(order_id, "canceled")

    async def place(self, order: OrderRequest) -> dict:
        oid = f"paper-{next(_ids)}"
        if order.order_type == "limit":
            self.status[oid] = "open"
            self.limits.append({"id": oid, "symbol": order.symbol, "side": order.side,
                                "amount": order.amount, "price": order.price,
                                "reduce_only": order.reduce_only,
                                "sl": order.stop_loss, "tp": order.take_profit})
            return {"id": oid, "status": "open", "price": order.price, "amount": order.amount}
        price = await self.last_price(order.symbol)
        pos = self.book.get(order.symbol)
        amount = order.amount
        if order.reduce_only:
            if not pos:
                return {"id": oid, "status": "canceled", "filled": 0.0}
            amount = min(amount, pos.amount)
        self._fill(order.symbol, order.side, amount, price, order.stop_loss, order.take_profit)
        return {"id": oid, "status": "closed", "average": price, "filled": amount, "amount": amount}

    async def place_spot_stop(self, symbol: str, amount: float, trigger_price: float) -> dict:
        oid = f"paper-{next(_ids)}"
        self.status[oid] = "open"
        self.spot_stops.append({"id": oid, "symbol": symbol, "amount": amount, "trigger": trigger_price})
        return {"id": oid, "status": "open"}

    async def cancel(self, order_id: str, symbol: str, trigger: bool = False,
                     plan_type: str | None = None) -> None:
        if self.status.get(order_id) == "open":
            self.status[order_id] = "canceled"
        self.limits = [o for o in self.limits if o["id"] != order_id]
        self.spot_stops = [s for s in self.spot_stops if s["id"] != order_id]

    async def cancel_all(self, symbol: str) -> None:
        for o in self.limits + self.spot_stops:
            if o["symbol"] == symbol and self.status.get(o["id"]) == "open":
                self.status[o["id"]] = "canceled"
        self.limits = [o for o in self.limits if o["symbol"] != symbol]
        self.spot_stops = [s for s in self.spot_stops if s["symbol"] != symbol]

    async def replace_stop_loss(self, position: Position, new_sl: float) -> dict:
        if position.symbol in self.book:
            self.book[position.symbol].stop_loss = new_sl
        return {"status": "ok", "stop_loss": new_sl}

    async def close_position(self, position: Position, tag: str = "") -> dict:
        price = await self.last_price(position.symbol)
        if position.symbol in self.book:
            p = self.book[position.symbol]
            self._fill(p.symbol, "sell" if p.side == "long" else "buy", p.amount, price)
        return {"status": "closed", "average": price}

    async def transfer(self, coin: str, amount: float, from_account: str, to_account: str) -> dict:
        self.usdt[from_account] -= amount
        self.usdt[to_account] += amount
        return {"status": "ok"}
