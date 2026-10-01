"""SUB10 — Pairs trading por cointegración (market-neutral).

Inspirada en el controlador ``stat_arb`` de Hummingbot (v2), adaptada:
Hummingbot usa velas de 1m, apalancamiento 20x y modo hedge; aquí 1H, 2x y
modo one-way (el mismo del resto del sistema).

Para cada par (A, B) con 240 velas de 1H (10 días):
    log(A) = α + β·log(B) + spread       (β por MCO)
    z = (spread − media) / desviación
Filtros de calidad: β > 0, estadístico ADF del spread < −3.34 (Engle-Granger 5%)
y vida media de reversión entre 2 y 72 horas.
Entrada: |z| ≥ 2 → z > 0: SHORT A + LONG B · z < 0: LONG A + SHORT B, con
nocional de B = β × nocional de A (cobertura en log-precios).
Salida: |z| ≤ 0.5 (reversión), |z| ≥ 4 (ruptura de la relación), pérdida del
par > 3% de su nocional, 5 días abierta, o si una pata desaparece (su SL de
emergencia ±10% saltó) se cierra la otra.
Un símbolo no puede estar en dos pares a la vez (modo one-way = posición neta).
"""
from __future__ import annotations

import asyncio

import numpy as np

from .. import clock
from ..exchange.client import perp
from ..indicators import adf_tstat, half_life, hedge_ratio
from ..risk.models import OrderRequest
from .base import Strategy


def pair_stats(a_close: np.ndarray, b_close: np.ndarray, beta: float | None = None) -> dict:
    la, lb = np.log(a_close), np.log(b_close)
    if beta is None:
        beta, alpha = hedge_ratio(la, lb)
    else:
        alpha = float(np.mean(la - beta * lb))
    spread = la - (alpha + beta * lb)
    std = float(np.std(spread))
    z = float((spread[-1] - np.mean(spread)) / std) if std > 0 else 0.0
    return {"beta": float(beta), "z": z, "adf": adf_tstat(spread), "half_life": half_life(spread)}


class PairsTradingStrategy(Strategy):
    account_id = "SUB10"
    name = "Pairs trading cointegración"
    schedule = {"trigger": "cron", "minute": 3}
    leverage = 2

    PAIRS = [("BTC", "ETH"), ("ETH", "SOL"), ("SOL", "AVAX"), ("LINK", "DOT")]
    TIMEFRAME = "1h"
    LOOKBACK = 240
    ENTRY_Z, EXIT_Z, STOP_Z = 2.0, 0.5, 4.0
    ADF_MAX = -3.34
    HALF_LIFE_RANGE = (2, 72)
    CAPITAL_PER_PAIR = 0.25
    MAX_PAIRS = 2
    PAIR_STOP = 0.03
    LEG_SL = 0.10
    TIME_STOP_H = 120

    async def _closes(self, symbol: str) -> np.ndarray:
        return (await self.client.ohlcv(symbol, self.TIMEFRAME, self.LOOKBACK))["close"].to_numpy()

    async def run_cycle(self) -> None:
        pairs: dict = self.get_state("pairs", {})
        await self._manage(pairs)
        self.set_state("pairs", pairs)
        if len(pairs) < self.MAX_PAIRS:
            await self._scan(pairs)
            self.set_state("pairs", pairs)

    async def _manage(self, pairs: dict) -> None:
        positions = {p.symbol: p for p in await self.positions()}
        for key, pr in list(pairs.items()):
            pa, pb = positions.get(pr["a"]), positions.get(pr["b"])
            reason = None
            if pa is None or pb is None:
                reason = "una pata cerrada por su SL de emergencia"
            else:
                st = pair_stats(await self._closes(pr["a"]), await self._closes(pr["b"]), pr["beta"])
                pnl = pa.unrealized_pnl + pb.unrealized_pnl
                if abs(st["z"]) <= self.EXIT_Z or np.sign(st["z"]) == pr["side"]:
                    reason = f"reversión z={st['z']:+.2f}"
                elif abs(st["z"]) >= self.STOP_Z:
                    reason = f"ruptura z={st['z']:+.2f}"
                elif pnl < -self.PAIR_STOP * pr["gross"]:
                    reason = f"pérdida del par {pnl:.2f}"
                elif clock.now() - pr["opened"] > self.TIME_STOP_H * 3600:
                    reason = "time stop"
            if reason:
                self.log.info("🔒 Cerrando par %s: %s", key, reason)
                for p in (pa, pb):
                    if p is not None:
                        await self.ctx.router.close_position(self.account_id, p, reason, self.account_id)
                pairs.pop(key)

    async def _scan(self, pairs: dict) -> None:
        busy = {s for pr in pairs.values() for s in (pr["a"], pr["b"])}
        candidates = []
        for a_asset, b_asset in self.PAIRS:
            a, b = perp(a_asset), perp(b_asset)
            key = f"{a_asset}|{b_asset}"
            if key in pairs or a in busy or b in busy:
                continue
            ca, cb = await self._closes(a), await self._closes(b)
            if min(len(ca), len(cb)) < self.LOOKBACK * 0.9:
                continue
            n = min(len(ca), len(cb))
            st = pair_stats(ca[-n:], cb[-n:])
            lo, hi = self.HALF_LIFE_RANGE
            if (st["beta"] > 0 and st["adf"] < self.ADF_MAX and lo <= st["half_life"] <= hi
                    and self.ENTRY_Z <= abs(st["z"]) < self.STOP_Z):
                candidates.append((abs(st["z"]), key, a, b, st))
        for _, key, a, b, st in sorted(candidates, reverse=True):
            if len(pairs) >= self.MAX_PAIRS or a in busy or b in busy:
                continue
            if await self._open(key, a, b, st, pairs):
                busy |= {a, b}

    async def _open(self, key: str, a: str, b: str, st: dict, pairs: dict) -> bool:
        client = self.client
        await client.load_markets()
        pa, pb = await client.last_price(a), await client.last_price(b)
        # el tope por par respeta el límite de capital (margen ≤ MAX_MARGIN_PCT × rampa) entre los pares
        gross = await client.equity() * min(self.CAPITAL_PER_PAIR,
                                            self.capital_limit * self.leverage * 0.98 / self.MAX_PAIRS)
        beta = st["beta"]
        qa = client.amount_to_precision(a, gross / (1 + beta) / pa, pa)
        qb = client.amount_to_precision(b, gross * beta / (1 + beta) / pb, pb)
        if qa <= 0 or qb <= 0:
            self.log.info("%s: capital insuficiente para las dos patas", key)
            return False
        side = -1 if st["z"] > 0 else 1  # +1 = LONG A / SHORT B
        leg_a = self._leg(a, "buy" if side > 0 else "sell", qa, pa)
        leg_b = self._leg(b, "sell" if side > 0 else "buy", qb, pb)
        self.log.info("🚀 Par %s z=%+.2f β=%.2f ADF=%.2f vida media=%.0fh → %s A / %s B", key, st["z"], beta,
                      st["adf"], st["half_life"], leg_a.side, leg_b.side)
        results = await asyncio.gather(*(self.ctx.router.execute(self.account_id, o, leverage=self.leverage)
                                         for o in (leg_a, leg_b)), return_exceptions=True)
        if any(isinstance(r, Exception) for r in results):
            self.log.error("❌ Fallo abriendo %s: %s. Deshaciendo.", key, results)
            for sym, r in ((a, results[0]), (b, results[1])):
                if not isinstance(r, Exception) and (p := await client.position(sym)):
                    await self.ctx.router.close_position(self.account_id, p, "rollback par", self.account_id)
            return False
        pairs[key] = {"a": a, "b": b, "side": side, "beta": beta, "gross": qa * pa + qb * pb,
                      "opened": clock.now(), "entry_z": st["z"]}
        return True

    def _leg(self, symbol: str, side: str, amount: float, price: float) -> OrderRequest:
        sl = price * (1 - self.LEG_SL) if side == "buy" else price * (1 + self.LEG_SL)
        return OrderRequest(symbol, side, amount, stop_loss=self.client.price_to_precision(symbol, sl),  # type: ignore[arg-type]
                            tag=f"{self.account_id}:pair")
