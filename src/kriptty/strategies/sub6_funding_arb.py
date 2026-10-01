"""SUB6 — Funding rate arbitrage delta-neutral (SHORT perpetuo + LONG spot).

Cambios de la revisión:
  * El intervalo de funding ya no es 8H para todos los pares en Bitget: el APY
    se calcula con el intervalo real de cada par (rate × 24/intervalo × 365).
  * Solo funding POSITIVO. La pata inversa (LONG perp + SHORT spot) exige pedir
    prestado en margin spot; el original la listaba "si disponible" sin
    implementarla de forma segura.
  * El SL de emergencia ±3% en la pata de futuros ROMPÍA la neutralidad: un
    movimiento normal del 3% cerraba el short y dejaba el spot desnudo. Ahora:
    SL de emergencia a +10% con apalancamiento 2x y, si la pata perp desaparece
    (SL/liquidación), se vende el spot en el siguiente chequeo (cada 5 min, no 8H).
  * Filtro de rentabilidad neta: el funding esperado en 7 días debe superar
    1.5× las comisiones de abrir y cerrar las dos patas (~0.32%). Con APY 20%
    (el umbral original) una operación de 3 días no pagaba ni las comisiones.
Salidas (ambas patas): basis > 1.5%, funding < 0 o APY < 8%.
"""
from __future__ import annotations

import asyncio

from .. import clock
from ..risk.models import OrderRequest
from .base import Strategy

ROUND_TRIP_FEES = 0.0032  # spot 0.1%×2 + perp 0.06%×2


def annualized(rate: float, interval_hours: float) -> float:
    return rate * (24 / interval_hours) * 365


class FundingArbStrategy(Strategy):
    account_id = "SUB6"
    name = "Funding rate arbitrage"
    schedule = {"trigger": "interval", "minutes": 5}
    leverage = 2

    TOP_N = 30
    MIN_APY = 0.20
    EXIT_APY = 0.08
    MIN_VOLUME_USDT = 10_000_000
    MAX_ENTRY_BASIS = 0.0015
    EXIT_BASIS = 0.015
    HOLD_DAYS_EXPECTED = 7
    EDGE_MULTIPLE = 1.5
    MAX_PAIRS = 3
    CAPITAL_PER_PAIR = 0.20
    EMERGENCY_SL = 0.10
    DELTA_TOLERANCE = 0.005
    SCAN_EVERY_S = 30 * 60

    def has_edge(self, rate: float, interval_hours: float) -> bool:
        expected = rate * (24 / interval_hours) * self.HOLD_DAYS_EXPECTED
        return expected >= self.EDGE_MULTIPLE * ROUND_TRIP_FEES

    async def run_cycle(self) -> None:
        await self.manage_pairs()
        if clock.now() - self.get_state("last_scan", 0) >= self.SCAN_EVERY_S:
            self.set_state("last_scan", clock.now())
            await self.scan_and_open()

    # ── Gestión ─────────────────────────────────────────────────────────
    async def manage_pairs(self) -> None:
        pairs: dict = self.get_state("pairs", {})
        if not pairs:
            return
        client = self.client
        positions = {p.symbol: p for p in await self.positions()}
        for perp_sym, pair in list(pairs.items()):
            spot_sym, base = pair["spot"], pair["spot"].split("/")[0]
            pos = positions.get(perp_sym)
            if pos is None or pos.side != "short":
                self.log.warning("⚠️  Pata perp de %s desaparecida (SL/liquidación): vendiendo spot", perp_sym)
                await self._sell_spot(spot_sym, base)
                pairs.pop(perp_sym)
                continue
            fr = await client.funding_rate(perp_sym)
            spot_px = await client.last_price(spot_sym)
            basis = abs(pos.mark_price - spot_px) / spot_px
            apy = annualized(fr["rate"], fr["interval_hours"])
            reason = None
            if basis > self.EXIT_BASIS:
                reason = f"basis {basis:.2%} > {self.EXIT_BASIS:.1%}"
            elif fr["rate"] < 0:
                reason = "funding negativo"
            elif apy < self.EXIT_APY:
                reason = f"APY {apy:.1%} < {self.EXIT_APY:.0%}"
            if reason:
                await self._close_pair(pos, spot_sym, base, reason)
                pairs.pop(perp_sym)
                continue
            spot_amt = await client.holding(base)
            if pos.amount and abs(spot_amt - pos.amount) / pos.amount > self.DELTA_TOLERANCE:
                await self._rebalance(pos, spot_amt)
        self.set_state("pairs", pairs)

    async def _rebalance(self, pos, spot_amt: float) -> None:
        diff = self.client.amount_to_precision(pos.symbol, abs(pos.amount - spot_amt))
        if diff <= 0:
            return
        if pos.amount > spot_amt:  # demasiado short → recomprar parte
            order = OrderRequest(pos.symbol, "buy", diff, reduce_only=True, tag="SUB6:rebalance")
        else:
            order = OrderRequest(pos.symbol, "sell", diff, stop_loss=pos.mark_price * (1 + self.EMERGENCY_SL),
                                 tag="SUB6:rebalance")
        self.log.info("⚖️  Rebalanceo delta %s: perp=%.6g spot=%.6g", pos.symbol, pos.amount, spot_amt)
        await self.ctx.router.execute(self.account_id, order, leverage=self.leverage)

    async def _sell_spot(self, spot_sym: str, base: str) -> None:
        client = self.client
        await client.cancel_all(spot_sym)
        amount = client.amount_to_precision(spot_sym, await client.holding(base))
        if amount > 0:
            await self.ctx.router.execute(self.account_id, OrderRequest(spot_sym, "sell", amount, tag="SUB6:spot_exit"))

    async def _close_pair(self, pos, spot_sym: str, base: str, reason: str) -> None:
        self.log.info("🔒 Cerrando par %s: %s", pos.symbol, reason)
        await asyncio.gather(self.ctx.router.close_position(self.account_id, pos, reason, "SUB6"),
                             self._sell_spot(spot_sym, base))

    # ── Apertura ────────────────────────────────────────────────────────
    async def scan_and_open(self) -> None:
        pairs: dict = self.get_state("pairs", {})
        if len(pairs) >= self.MAX_PAIRS:
            return
        # Las dos patas tienen que poder abrirse: si una cartera está bloqueada (kill-switch o
        # drawdown) se espera. Antes se abría la pata permitida, se rechazaba la otra y se
        # deshacía, pagando comisiones en cada escaneo (detectado en el test de estrés).
        router = self.ctx.router
        if not (await router.can_open(self.account_id, "swap") and await router.can_open(self.account_id, "spot")):
            self.log.info("⏸  Kill-switch o pausa por drawdown activos: no se abren pares")
            return
        client = self.client
        await client.load_markets()
        perps = await client.tickers("swap")
        spots = await client.tickers("spot")
        ranked = sorted((t for s, t in perps.items() if s.endswith("/USDT:USDT") and client.is_crypto(s)),
                        key=lambda t: float(t.get("quoteVolume") or 0), reverse=True)[: self.TOP_N]
        candidates = []
        for t in ranked:
            perp_sym = t["symbol"]
            spot_sym = perp_sym.split(":")[0]
            st = spots.get(spot_sym)
            if perp_sym in pairs or st is None or float(t.get("quoteVolume") or 0) < self.MIN_VOLUME_USDT:
                continue
            fr = await client.funding_rate(perp_sym)
            apy = annualized(fr["rate"], fr["interval_hours"])
            basis = abs(float(t["last"]) - float(st["last"])) / float(st["last"])
            if (fr["rate"] > 0 and apy >= self.MIN_APY and basis <= self.MAX_ENTRY_BASIS
                    and self.has_edge(fr["rate"], fr["interval_hours"])):
                candidates.append((apy, perp_sym, spot_sym, float(st["last"])))
        candidates.sort(reverse=True)

        for apy, perp_sym, spot_sym, price in candidates[: self.MAX_PAIRS - len(pairs)]:
            if await self._open_pair(perp_sym, spot_sym, price, apy):
                pairs[perp_sym] = {"spot": spot_sym, "opened": clock.now(), "apy_at_open": apy}
                self.set_state("pairs", pairs)

    async def _open_pair(self, perp_sym: str, spot_sym: str, price: float, apy: float) -> bool:
        client = self.client
        total = await client.equity("swap") + await client.equity("spot")
        notional = total * self.CAPITAL_PER_PAIR
        spot_free = await client.free("USDT", "spot")
        swap_free = await client.free("USDT", "swap")
        notional = min(notional, spot_free * 0.98, swap_free * self.leverage * 0.9)
        amount = min(client.amount_to_precision(perp_sym, notional / price, price),
                     client.amount_to_precision(spot_sym, notional / price, price))
        if amount <= 0:
            self.log.info("%s: capital insuficiente en spot/futuros para abrir el par", perp_sym)
            return False
        self.log.info("🚀 Abriendo par %s APY=%.1f%% nocional≈%.2f", perp_sym, apy * 100, amount * price)
        short = OrderRequest(perp_sym, "sell", amount, stop_loss=price * (1 + self.EMERGENCY_SL), tag="SUB6:perp")
        long = OrderRequest(spot_sym, "buy", amount, tag="SUB6:spot",
                            sl_exempt_reason="pata de cobertura delta-neutral (SUB6)")
        results = await asyncio.gather(self.ctx.router.execute(self.account_id, short, leverage=self.leverage),
                                       self.ctx.router.execute(self.account_id, long),
                                       return_exceptions=True)
        failed = [r for r in results if isinstance(r, Exception)]
        if not failed:
            return True
        self.log.error("❌ Fallo al abrir el par %s: %s. Deshaciendo la pata ejecutada.", perp_sym, failed)
        if not isinstance(results[0], Exception):
            pos = await client.position(perp_sym)
            if pos:
                await self.ctx.router.close_position(self.account_id, pos, "rollback par", "SUB6")
        if not isinstance(results[1], Exception):
            await self._sell_spot(spot_sym, spot_sym.split("/")[0])
        return False
