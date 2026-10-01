"""SUB9 — Collar dinámico: exposición larga neta ajustada por régimen macro.

REVISIÓN: el original mantenía un LONG y un SHORT del mismo perpetuo en la
misma cuenta (modo hedge). Económicamente eso equivale a un único LONG de
tamaño L·(1 − ratio): el funding de ambas patas se compensa sobre la parte
cubierta y el PnL también, pero se pagan comisiones y margen dobles, y el
modo hedge entra en conflicto con el modo one-way del resto del sistema.
Aquí se mantiene directamente la exposición neta objetivo:

    capital = 80% del equity · nocional = capital × (1 − ratio)
    STRONG_BULL → ratio 0.30 · NEUTRAL → 0.50 · BEAR → 0.70 · CRISIS → 0.90
    macro no disponible → 0.70 (conservador)

Activo: BTC o ETH, el de mayor momentum a 30 días (al abrir).
SL: EMA200 diaria − 1% (si el precio está por debajo, exposición 0), acotado a
un máximo del 15% bajo el precio: en tendencias alcistas fuertes la EMA200 queda
a >25% y el guardián de riesgo rechazaría la orden (SUB9 no abriría nunca).
Circuit breaker: pérdida neta > 3% del capital asignado → cerrar y 24H de pausa.
Rebalanceo cada 6H si el ratio efectivo se desvía > 0.05 del objetivo.
"""
from __future__ import annotations

from .. import clock
from ..exchange.client import perp
from ..indicators import ema, last
from ..risk.models import OrderRequest
from .base import Strategy

HEDGE_RATIO = {"STRONG_BULL": 0.30, "NEUTRAL": 0.50, "BEAR": 0.70, "CRISIS": 0.90, "UNKNOWN": 0.70}


class CollarStrategy(Strategy):
    account_id = "SUB9"
    name = "Collar dinámico"
    schedule = {"trigger": "interval", "minutes": 15}
    leverage = 2

    ASSETS = ["BTC", "ETH"]
    CAPITAL_FRACTION = 0.80
    MAX_NET_DRAWDOWN = 0.03
    COOLDOWN_H = 24
    REBALANCE_EVERY_H = 6
    RATIO_TOLERANCE = 0.05
    EMA_BUFFER = 0.01
    MAX_SL_PCT = 0.15

    async def _pick_asset(self) -> str:
        best, best_mom = self.ASSETS[0], float("-inf")
        for a in self.ASSETS:
            d = await self.client.ohlcv(perp(a), "1d", 31)
            mom = float(d["close"].iloc[-1] / d["close"].iloc[0] - 1)
            if mom > best_mom:
                best, best_mom = a, mom
        return best

    async def run_cycle(self) -> None:
        client = self.client
        st = self.get_state("state", {"asset": None, "cooldown_until": 0, "last_rebalance": 0, "capital": None})
        if clock.now() < st["cooldown_until"]:
            return
        asset = st["asset"] or await self._pick_asset()
        symbol = perp(asset)
        pos = await client.position(symbol)

        # Circuit breaker sobre el capital asignado (se comprueba cada 15 min).
        if pos and st["capital"] and pos.unrealized_pnl < -self.MAX_NET_DRAWDOWN * st["capital"]:
            await self.ctx.router.close_position(self.account_id, pos,
                                                 f"drawdown neto > {self.MAX_NET_DRAWDOWN:.0%}", "SUB9")
            st.update(asset=None, cooldown_until=clock.now() + self.COOLDOWN_H * 3600, capital=None)
            self.set_state("state", st)
            return
        if pos and clock.now() - st["last_rebalance"] < self.REBALANCE_EVERY_H * 3600:
            return

        dash = await self.ctx.macro.get()
        ratio = HEDGE_RATIO[dash.regime]
        daily = await client.ohlcv(symbol, "1d", 260)
        price = await client.last_price(symbol)
        ema_floor = last(ema(daily["close"], 200)) * (1 - self.EMA_BUFFER) if len(daily) >= 200 else None
        below_trend = ema_floor is None or price <= ema_floor
        sl = None if below_trend else max(ema_floor, price * (1 - self.MAX_SL_PCT))
        equity = await client.equity()
        capital = min(st["capital"] or equity * self.CAPITAL_FRACTION,
                      equity * self.capital_limit * self.leverage * 0.98)  # margen ≤ límite × rampa
        target = 0.0 if below_trend else capital * (1 - ratio)
        current = pos.notional if pos and pos.side == "long" else 0.0
        self.log.info("Régimen %s → cobertura %.0f%% | %s nocional actual %.2f objetivo %.2f",
                      dash.regime, ratio * 100, symbol, current, target)

        if target == 0 and pos:
            await self.ctx.router.close_position(self.account_id, pos, "precio bajo EMA200−1% o sin datos", "SUB9")
            st.update(asset=None, capital=None)
        elif abs(current - target) / capital > self.RATIO_TOLERANCE or (target and not pos):
            delta = client.amount_to_precision(symbol, abs(target - current) / price, price)
            if delta > 0 and target > current:
                order = OrderRequest(symbol, "buy", delta, stop_loss=client.price_to_precision(symbol, sl),
                                     tag="SUB9:rebalance")
                await self.ctx.router.execute(self.account_id, order, leverage=self.leverage)
                pos = await client.position(symbol)
                if pos:  # un único SL para toda la posición
                    await self.ctx.router.update_stop_loss(self.account_id, pos, sl, "SUB9")
                st.update(asset=asset, capital=capital, sl=sl)
            elif delta > 0:
                await self.ctx.router.execute(self.account_id, OrderRequest(symbol, "sell", delta, reduce_only=True,
                                                                            tag="SUB9:rebalance"))
        elif pos and sl and sl > (st.get("sl") or 0):  # el SL sigue a la EMA200 (solo hacia arriba)
            await self.ctx.router.update_stop_loss(self.account_id, pos, sl, "SUB9")
            st["sl"] = sl
        st["last_rebalance"] = clock.now()
        self.set_state("state", st)
