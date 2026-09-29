"""SUB5 — Macro-shorting (solo cortos, BTC/ETH).

Activación: dashboard macro válido con score ≤ −3 (≤ −5 = convicción alta,
riesgo 2% en lugar de 1.5%).
Confirmación técnica diaria (TODAS; con datos insuficientes NO se opera — el
original permitía la entrada "por defecto"):
    precio ≤ EMA200·1.02 · 30 ≤ RSI14 ≤ 55 (RSI de Wilder sobre la serie completa;
    el original usaba solo las 14 velas más antiguas) · ≥ 2 de 3 velas bajistas.
SL: máximo de 7 días + 0.3%. TP escalonado con órdenes límite reduce-only:
−4% (33%), −8% (33%), −15% (34%).
Circuit breaker: drawdown de SUB5 > 5% desde máximos → pausa.
Nuevo: si el régimen macro pasa a STRONG_BULL con la posición abierta, se cierra.
"""
from __future__ import annotations

import pandas as pd

from ..exchange.client import perp
from ..indicators import ema, last, rsi
from ..risk.models import OrderRequest
from .base import Strategy


class MacroShortStrategy(Strategy):
    account_id = "SUB5"
    name = "Macro-shorting"
    schedule = {"trigger": "cron", "hour": "0,6,12,18", "minute": 15}
    leverage = 2

    ASSETS = ["BTC", "ETH"]
    SHORT_THRESHOLD = -3.0
    STRONG_THRESHOLD = -5.0
    RISK_PCT, RISK_PCT_STRONG = 0.015, 0.02
    SL_LOOKBACK_DAYS = 7
    SL_BUFFER = 0.003
    TP_LADDER = [(0.04, 0.33), (0.08, 0.33), (0.15, 0.34)]
    MAX_DRAWDOWN = 0.05

    @staticmethod
    def technical_confirmation(daily: pd.DataFrame, price: float) -> tuple[bool, str]:
        if len(daily) < 210:
            return False, f"datos insuficientes ({len(daily)} velas diarias)"
        ema200 = last(ema(daily["close"], 200))
        r = last(rsi(daily["close"], 14))
        bearish = int((daily["close"].iloc[-3:] < daily["open"].iloc[-3:]).sum())
        if price > ema200 * 1.02:
            return False, f"precio {price:.0f} sobre EMA200 {ema200:.0f}"
        if not 30 <= r <= 55:
            return False, f"RSI {r:.1f} fuera de [30, 55]"
        if bearish < 2:
            return False, f"solo {bearish}/3 velas bajistas"
        return True, f"EMA200={ema200:.0f} RSI={r:.1f} bajistas={bearish}/3"

    async def _circuit_breaker(self) -> bool:
        equity = await self.client.equity()
        peak = max(self.get_state("peak_equity", equity), equity)
        self.set_state("peak_equity", peak)
        if peak and (peak - equity) / peak > self.MAX_DRAWDOWN:
            self.log.warning("⏸  Drawdown %.1f%% > %.0f%%: SUB5 en pausa", (peak - equity) / peak * 100,
                             self.MAX_DRAWDOWN * 100)
            return False
        return True

    async def run_cycle(self) -> None:
        dash = await self.ctx.macro.get()
        positions = {p.symbol: p for p in await self.positions()}

        if dash.regime == "STRONG_BULL":
            for p in positions.values():
                await self.ctx.router.close_position(self.account_id, p, "régimen macro alcista", self.account_id)
            return
        if not dash.valid:
            self.log.info("Dashboard macro inválido (cobertura %.0f%%): no se opera", dash.coverage * 100)
            return
        if dash.score > self.SHORT_THRESHOLD:
            self.log.info("Macro score %.2f > %.1f: sin shorts", dash.score, self.SHORT_THRESHOLD)
            return
        if not await self._circuit_breaker():
            return

        client = self.client
        for asset in self.ASSETS:
            symbol = perp(asset)
            if symbol in positions:
                continue
            daily = await client.ohlcv(symbol, "1d", 260)
            price = await client.last_price(symbol)
            ok, why = self.technical_confirmation(daily, price)
            if not ok:
                self.log.info("%s: sin confirmación técnica (%s)", symbol, why)
                continue
            sl = float(daily["high"].iloc[-self.SL_LOOKBACK_DAYS:].max()) * (1 + self.SL_BUFFER)
            sl = max(sl, price * 1.005)
            risk = self.RISK_PCT_STRONG if dash.score <= self.STRONG_THRESHOLD else self.RISK_PCT
            result = await self.open_position(symbol, "sell", price, sl, None, risk,
                                              reason=f"macro={dash.score:+.2f} {why}")
            if not result:
                continue
            # ccxt no devuelve "filled" al crear órdenes en Bitget: se lee la posición real.
            pos = await client.position(symbol)
            if pos:
                await self._place_tp_ladder(symbol, pos.entry_price or price, pos.amount)

    async def _place_tp_ladder(self, symbol: str, entry: float, amount: float) -> None:
        client = self.client
        remaining = amount
        for i, (drop, frac) in enumerate(self.TP_LADDER):
            qty = remaining if i == len(self.TP_LADDER) - 1 else client.amount_to_precision(symbol, amount * frac)
            if qty <= 0:
                continue
            remaining -= qty
            order = OrderRequest(symbol, "buy", qty, "limit", client.price_to_precision(symbol, entry * (1 - drop)),
                                 reduce_only=True, tag=f"{self.account_id}:TP{i + 1}")
            await self.ctx.router.execute(self.account_id, order)
