"""SUB2 — Arbitraje estadístico "vasos comunicantes".

1. Universo: perpetuos USDT-M de Bitget con volumen 24h ≥ 5M USDT (antes: Top 200
   CoinGecko + comprobar si cotiza en Bitget; ahora el universo ya es operable).
2. Líderes: top 5 por % 24h con subida ≥ +4%.
3. Rezagadas: lag = 0.7·(líderes24h − alt24h) + 0.3·(líderes1h − alt1h) ≥ 4%,
   correlación de retornos log diarios (30d) con la cesta de líderes ≥ 0.65.
4. Entrada LONG. SL: mínimo de 48h − 0.5%. TP: máximo de 48h (mínimo +6%).
   Nuevo: R:R mínimo 1.5 y salida por tiempo a las 48h (la tesis es un lag de
   2-24h; si no se cierra en 48h ya no es un lag).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import clock
from .base import Strategy


class StatArbStrategy(Strategy):
    account_id = "SUB2"
    name = "Stat Arb vasos comunicantes"
    schedule = {"trigger": "cron", "hour": "0,4,8,12,16,20", "minute": 5}
    leverage = 3

    MIN_VOLUME_USDT = 5_000_000
    UNIVERSE_SIZE = 200
    LEADERS = 5
    LEADER_MIN_CHANGE = 4.0
    MIN_LAG = 4.0
    MIN_CORR = 0.65
    CORR_DAYS = 30
    CANDIDATES_TO_CHECK = 15
    SL_BUFFER = 0.005
    MIN_TP_PCT = 0.06
    MIN_RR = 1.5
    MAX_SL_PCT = 0.12
    MAX_POSITIONS = 3
    RISK_PCT = 0.02
    TIME_STOP_H = 48
    BLACKLIST = {"USDC/USDT:USDT", "FDUSD/USDT:USDT", "TUSD/USDT:USDT"}

    @staticmethod
    def lag_score(leader_24h: float, alt_24h: float, leader_1h: float, alt_1h: float) -> float:
        return 0.7 * (leader_24h - alt_24h) + 0.3 * (leader_1h - alt_1h)

    @staticmethod
    def correlation(alt_closes: pd.Series, basket: pd.DataFrame) -> float:
        rets = np.log(pd.concat([alt_closes.rename("alt"), basket.mean(axis=1).rename("basket")],
                                axis=1).dropna()).diff().dropna()
        if len(rets) < 10:
            return 0.0
        return float(rets["alt"].corr(rets["basket"]))

    async def _change_1h(self, symbol: str) -> float:
        df = await self.client.ohlcv(symbol, "1h", limit=2, closed_only=False)
        return float(df["close"].iloc[-1] / df["open"].iloc[-1] * 100 - 100) if len(df) else 0.0

    async def _manage_time_stops(self, positions) -> None:
        opened = self.get_state("opened_at", {})
        for p in positions:
            ts = opened.get(p.symbol)
            if ts and clock.now() - ts > self.TIME_STOP_H * 3600:
                await self.ctx.router.close_position(self.account_id, p, "time stop 48h", self.account_id)
                opened.pop(p.symbol, None)
        live = {p.symbol for p in positions}
        self.set_state("opened_at", {s: t for s, t in opened.items() if s in live})

    async def run_cycle(self) -> None:
        client = self.client
        positions = await self.positions()
        await self._manage_time_stops(positions)
        if len(positions) >= self.MAX_POSITIONS:
            return

        tickers = await client.tickers("swap")
        universe = sorted(
            (t for s, t in tickers.items() if s.endswith("/USDT:USDT") and s not in self.BLACKLIST and client.is_crypto(s)
             and float(t.get("quoteVolume") or 0) >= self.MIN_VOLUME_USDT and t.get("percentage") is not None),
            key=lambda t: float(t["quoteVolume"]), reverse=True)[: self.UNIVERSE_SIZE]
        leaders = sorted(universe, key=lambda t: float(t["percentage"]), reverse=True)[: self.LEADERS]
        leaders = [t for t in leaders if float(t["percentage"]) >= self.LEADER_MIN_CHANGE]
        if not leaders:
            self.log.info("Sin líderes (+%.0f%% 24h). Nada que hacer.", self.LEADER_MIN_CHANGE)
            return

        leader_syms = [t["symbol"] for t in leaders]
        leader_24h = float(np.mean([float(t["percentage"]) for t in leaders]))
        leader_1h = float(np.mean([await self._change_1h(s) for s in leader_syms]))
        basket = pd.concat({s: (await client.ohlcv(s, "1d", self.CORR_DAYS + 1))["close"] for s in leader_syms},
                           axis=1)

        held = {p.symbol for p in positions}
        pre = sorted(
            (t for t in universe if t["symbol"] not in leader_syms and t["symbol"] not in held
             and -10 < float(t["percentage"]) < leader_24h - self.MIN_LAG),
            key=lambda t: float(t["percentage"]))[: self.CANDIDATES_TO_CHECK]

        scored = []
        for t in pre:
            sym = t["symbol"]
            lag = self.lag_score(leader_24h, float(t["percentage"]), leader_1h, await self._change_1h(sym))
            if lag < self.MIN_LAG:
                continue
            corr = self.correlation((await client.ohlcv(sym, "1d", self.CORR_DAYS + 1))["close"], basket)
            if corr >= self.MIN_CORR:
                scored.append((lag * corr, sym, lag, corr, float(t["last"])))
        scored.sort(reverse=True)

        opened = self.get_state("opened_at", {})
        for _, sym, lag, corr, price in scored[: self.MAX_POSITIONS - len(positions)]:
            h1 = await client.ohlcv(sym, "1h", limit=48)
            sl = float(h1["low"].min()) * (1 - self.SL_BUFFER)
            tp = max(float(h1["high"].max()), price * (1 + self.MIN_TP_PCT))
            sl_pct, rr = (price - sl) / price, (tp - price) / max(price - sl, 1e-12)
            if sl >= price or sl_pct > self.MAX_SL_PCT or rr < self.MIN_RR:
                self.log.info("%s descartada: SL %.1f%% R:R %.2f", sym, sl_pct * 100, rr)
                continue
            if await self.open_position(sym, "buy", price, sl, tp, self.RISK_PCT,
                                        reason=f"lag={lag:.1f}% corr={corr:.2f} R:R={rr:.2f}"):
                opened[sym] = clock.now()
        self.set_state("opened_at", opened)
