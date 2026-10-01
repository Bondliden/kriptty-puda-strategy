"""SUB8 — DCA inteligente en spot (BTC 60% / ETH 40%).

Cada 48H por activo:
    filtro estructural: precio > EMA200 SEMANAL (si no hay 200 semanas de
    historial, no se compra — fail-closed)
    RSI diario: > 60 no compra · 40-60 ×1.0 · 30-40 ×1.5 · < 30 ×2.0
SL: orden plan de venta spot sobre TODO el saldo en max(medio × 0.80,
precio × 0.76). La segunda parte (detectada con el backtester) evita que, tras
una subida fuerte, el stop quede a > 25% del precio (el guardián rechazaba las
compras) y protege beneficios: sube con el precio, nunca baja salvo por una
nueva compra que baje el medio. Si el stop no se puede colocar, se vende el
saldo (fail-safe): la posición nunca queda sin protección.
TP: RSI > 70 y PnL > 8% → vende 25% · RSI > 80 y PnL > 15% → vende 50% más.
Máx. 10 entradas por activo; si salta el SL se reinicia el ciclo.
"""
from __future__ import annotations

from .. import clock
from ..exchange.client import spot
from ..indicators import ema, last, rsi
from ..risk.models import OrderRequest
from .base import Strategy


def rsi_multiplier(r: float) -> float:
    if r > 60:
        return 0.0
    if r >= 40:
        return 1.0
    if r >= 30:
        return 1.5
    return 2.0


class SmartDCAStrategy(Strategy):
    account_id = "SUB8"
    name = "DCA inteligente"
    schedule = {"trigger": "cron", "hour": 1, "minute": 30}

    ALLOCATION = {"BTC": 0.6, "ETH": 0.4}
    BASE_BUY_PCT = 0.04
    INTERVAL_H = 48
    MAX_ENTRIES = 10
    SL_FROM_AVG = 0.20
    MAX_STOP_DISTANCE = 0.24  # < 25% del guardián de riesgo
    RESTOP_STEP = 0.02
    TP1 = (70, 0.08, 0.25)
    TP2 = (80, 0.15, 0.50)

    async def run_cycle(self) -> None:
        base = self.get_state("budget")  # equity spot inicial: las compras no cambian con el saldo
        if base is None:
            base = await self.client.equity("spot")
            self.set_state("budget", base)
        budget = base * self.capital_limit  # spot: el límite (y la rampa) se aplica al capital
        for asset, weight in self.ALLOCATION.items():
            await self._cycle_asset(asset, budget * weight)

    async def _cycle_asset(self, asset: str, allocation: float) -> None:
        client = self.client
        symbol = spot(asset)
        st = self.get_state(asset, {"cost": 0.0, "qty": 0.0, "entries": 0, "last_buy": 0,
                                     "tp1": False, "tp2": False, "stop_id": None})
        held = await client.holding(asset)
        if st["qty"] > 0 and held < st["qty"] * 0.05:
            self.log.warning("🛑 %s: SL ejecutado (saldo %.6g). Reiniciando el ciclo DCA.", asset, held)
            st = {"cost": 0.0, "qty": 0.0, "entries": 0, "last_buy": 0, "tp1": False, "tp2": False, "stop_id": None}
        elif st["qty"] > 0:
            st["cost"] *= held / st["qty"]  # mantener el precio medio tras ventas parciales
            st["qty"] = held

        daily = await client.ohlcv(symbol, "1d", 120)
        r = last(rsi(daily["close"], 14))
        price = await client.last_price(symbol)

        if st["qty"] > 0 and self.stop_price(st["cost"] / st["qty"], price) > (st.get("stop_price") or 0) * (1 + self.RESTOP_STEP):
            st["stop_id"] = await self._reprotect(symbol, st, price)  # el stop sigue al precio

        if st["qty"] > 0:
            avg = st["cost"] / st["qty"]
            pnl = price / avg - 1
            for flag, (rsi_min, pnl_min, frac) in (("tp1", self.TP1), ("tp2", self.TP2)):
                if not st[flag] and r > rsi_min and pnl > pnl_min:
                    qty = client.amount_to_precision(symbol, st["qty"] * frac, price)
                    if qty > 0:
                        await client.cancel_all(symbol)  # liberar saldo bloqueado por el stop
                        await self.ctx.router.execute(self.account_id,
                                                      OrderRequest(symbol, "sell", qty, tag=f"SUB8:{flag}"))
                        st["cost"] *= 1 - qty / st["qty"]
                        st["qty"] -= qty
                        st[flag] = True
                        st["stop_id"] = await self._reprotect(symbol, st, price)
                        self.log.info("💰 %s %s: vendido %.6g (RSI %.0f, PnL %.1f%%)", asset, flag, qty, r, pnl * 100)

        if clock.now() - st["last_buy"] < self.INTERVAL_H * 3600 or st["entries"] >= self.MAX_ENTRIES:
            self.set_state(asset, st)
            return
        weekly = await client.ohlcv(symbol, "1w", 260)
        if len(weekly) < 200:
            self.log.info("%s: solo %d velas semanales, EMA200 no fiable. No se compra.", asset, len(weekly))
            self.set_state(asset, st)
            return
        ema200w = last(ema(weekly["close"], 200))
        mult = rsi_multiplier(r)
        if price <= ema200w or mult == 0:
            self.log.info("⏸  %s: sin compra (precio %.2f vs EMA200w %.2f, RSI %.1f)", asset, price, ema200w, r)
            self.set_state(asset, st)
            return

        usdt = min(allocation * self.BASE_BUY_PCT * mult, await client.free("USDT", "spot") * 0.98)
        qty = client.amount_to_precision(symbol, usdt / price, price)
        if qty <= 0:
            self.log.info("%s: importe de compra por debajo del mínimo", asset)
            self.set_state(asset, st)
            return
        new_avg = (st["cost"] + qty * price) / (st["qty"] + qty)
        order = OrderRequest(symbol, "buy", qty, stop_loss=client.price_to_precision(symbol, self.stop_price(new_avg, price)),
                             tag="SUB8:dca")
        result = await self.ctx.router.execute(self.account_id, order)
        fill = float(result.get("average") or price)
        st.update(cost=st["cost"] + qty * fill, qty=st["qty"] + qty, entries=st["entries"] + 1,
                  last_buy=clock.now(), tp1=False, tp2=False)
        # El router dejó un stop para esta compra; lo sustituimos por uno único para todo el saldo.
        partial_stop = (result.get("stop_order") or {}).get("id")
        st["stop_id"] = await self._reprotect(symbol, st, price, extra_old=[partial_stop])
        self.log.info("🛒 %s DCA #%d: %.6g @ %.2f (×%.1f, RSI %.1f) medio=%.2f SL=%.2f", asset, st["entries"],
                      qty, fill, mult, r, st["cost"] / st["qty"], st.get("stop_price") or 0)
        self.set_state(asset, st)

    def stop_price(self, avg: float, price: float) -> float:
        return max(avg * (1 - self.SL_FROM_AVG), price * (1 - self.MAX_STOP_DISTANCE))

    async def _reprotect(self, symbol: str, st: dict, price: float, extra_old: list | None = None) -> str | None:
        client = self.client
        old_ids = [i for i in [st.get("stop_id"), *(extra_old or [])] if i]
        for oid in old_ids:  # cancelar primero libera el saldo que el nuevo stop necesita
            try:
                await client.cancel(oid, symbol, trigger=True)
            except Exception as e:  # noqa: BLE001
                self.log.debug("cancel stop %s: %s", oid, e)
        if st["qty"] <= 0:
            return None
        sl = client.price_to_precision(symbol, self.stop_price(st["cost"] / st["qty"], price))
        qty = client.amount_to_precision(symbol, st["qty"])
        try:
            stop = await client.place_spot_stop(symbol, qty, sl)
        except Exception:
            self.log.exception("❌ No se pudo recolocar el SL de %s: vendiendo para no quedar desprotegido", symbol)
            await self.ctx.router.execute(self.account_id, OrderRequest(symbol, "sell", qty, tag="SUB8:failsafe"))
            st.update(cost=0.0, qty=0.0, entries=0, stop_price=None)
            return None
        st["stop_price"] = sl
        return stop.get("id")
