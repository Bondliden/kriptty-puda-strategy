"""SUB7 — Grid adaptativo (Bollinger 4H + ATR diario), solo largo.

Configuración (cada 24H o tras reset):
    rango = [BB_lower, BB_upper] de BB(20, 2σ) en 4H
    celda = 0.5 × ATR(14) diario → niveles = rango/celda, acotado a [4, 20]
    compras límite en cada nivel por debajo del precio, cada una con SL
    adjunto en BB_lower − 0.5·celda (la regla "toda orden con SL" se cumple
    en el propio exchange, no solo en el bot).
Operación: compra ejecutada → venta límite reduce-only un nivel arriba;
venta ejecutada → se repone la compra.
SL/reset: precio fuera de [lower − ½ celda, upper + ½ celda] → cancelar todo,
cerrar y reconstruir. 3 resets en 24H → pausa hasta el día siguiente.
Nuevo: tamaño por nivel limitado para que, con TODAS las compras llenas y el
precio en el SL, la pérdida no supere el 4% del equity (el original no lo acotaba).
"""
from __future__ import annotations

from .. import clock
from ..exchange.client import perp
from ..indicators import atr, bollinger, last
from ..risk.guard import OrderRejected
from ..risk.models import OrderRequest
from .base import Strategy


def build_levels(lower: float, upper: float, cell: float, min_levels: int = 4,
                 max_levels: int = 20) -> tuple[list[float], float]:
    n = int((upper - lower) / cell) if cell > 0 else 0
    n = max(min_levels, min(max_levels, n))
    step = (upper - lower) / n
    return [lower + i * step for i in range(n + 1)], step


class GridStrategy(Strategy):
    account_id = "SUB7"
    name = "Grid adaptativo BB+ATR"
    schedule = {"trigger": "interval", "minutes": 5}
    leverage = 3

    ASSET = "BTC"
    REBUILD_H = 24
    MAX_RESETS_DAY = 3
    CAPITAL_FRACTION = 0.6
    MAX_GRID_LOSS = 0.04

    @property
    def symbol(self) -> str:
        return perp(self.ASSET)

    async def run_cycle(self) -> None:
        grid = self.get_state("grid")
        price = await self.client.last_price(self.symbol)
        today = clock.utcnow().date().isoformat()
        resets = self.get_state("resets", {"date": today, "count": 0})
        if resets["date"] != today:
            resets = {"date": today, "count": 0}
            self.set_state("resets", resets)

        if grid and (price < grid["sl_low"] or price > grid["sl_high"]):
            self.log.warning("💥 Ruptura del rango (%.2f fuera de [%.2f, %.2f]): reset",
                             price, grid["sl_low"], grid["sl_high"])
            await self._teardown("ruptura de rango")
            resets["count"] += 1
            self.set_state("resets", resets)
            grid = None
        if resets["count"] >= self.MAX_RESETS_DAY:
            self.log.info("⏸  %d resets hoy: grid en pausa hasta mañana", resets["count"])
            return
        if grid and clock.now() - grid["built_at"] > self.REBUILD_H * 3600:
            await self._teardown("reconfiguración 24H")
            grid = None
        if grid is None:
            await self._build(price)
        else:
            await self._maintain(grid)

    async def _teardown(self, reason: str) -> None:
        await self.client.cancel_all(self.symbol)
        pos = await self.client.position(self.symbol)
        if pos:
            await self.ctx.router.close_position(self.account_id, pos, reason, self.account_id)
        self.ctx.state.delete(self.account_id, "grid")

    async def _build(self, price: float) -> None:
        client = self.client
        await client.load_markets()
        h4 = await client.ohlcv(self.symbol, "4h", 60)
        d1 = await client.ohlcv(self.symbol, "1d", 30)
        lower_s, _, upper_s = bollinger(h4["close"], 20, 2.0)
        lower, upper = last(lower_s), last(upper_s)
        cell_target = 0.5 * last(atr(d1, 14))
        if not lower < price < upper:
            self.log.info("Precio %.2f fuera de las bandas [%.2f, %.2f]: esperando", price, lower, upper)
            return
        levels, step = build_levels(lower, upper, cell_target)
        buy_levels = [lv for lv in levels if lv < price * 0.999]
        if not buy_levels:
            return
        sl = lower - 0.5 * step
        equity = await client.equity()
        per_level_capital = equity * self.CAPITAL_FRACTION * self.leverage / len(buy_levels)
        worst_loss_per_unit = sum(lv - sl for lv in buy_levels)
        amount_raw = min(per_level_capital / price, equity * self.MAX_GRID_LOSS / worst_loss_per_unit)
        amount = client.amount_to_precision(self.symbol, amount_raw, price)
        if amount <= 0:
            self.log.info("Capital insuficiente para el grid (%.8g < mínimo)", amount_raw)
            return
        grid = {"built_at": clock.now(), "step": step, "amount": amount, "sl": sl,
                "sl_low": sl, "sl_high": upper + 0.5 * step, "levels": {}}
        for lv in buy_levels:
            res = await self._place_buy(lv, amount, sl, step)
            grid["levels"][f"{lv:.8f}"] = {"buy_id": res.get("id") if res else None, "sell_id": None}
        self.set_state("grid", grid)
        self.log.info("🧱 Grid %s: %d compras de %.6g entre %.2f y %.2f (celda %.2f, SL %.2f)",
                      self.symbol, len(buy_levels), amount, lower, upper, step, sl)

    async def _place_buy(self, level: float, amount: float, sl: float, step: float) -> dict | None:
        c = self.client
        order = OrderRequest(self.symbol, "buy", amount, "limit", c.price_to_precision(self.symbol, level),
                             stop_loss=c.price_to_precision(self.symbol, sl), tag="SUB7:buy")
        try:
            return await self.ctx.router.execute(self.account_id, order, leverage=self.leverage)
        except OrderRejected as e:  # p. ej. kill-switch diario: el nivel queda sin orden
            self.log.warning("Compra del grid en %.2f no enviada: %s", level, e)
            return None

    async def _maintain(self, grid: dict) -> None:
        client = self.client
        open_ids = {o["id"] for o in await client.open_orders(self.symbol)}
        for key, lv in grid["levels"].items():
            level = float(key)
            if lv["buy_id"] and lv["buy_id"] not in open_ids:
                if await client.order_status(lv["buy_id"], self.symbol) != "closed":
                    lv["buy_id"] = None  # cancelada fuera del bot: se repone en el próximo rebuild
                    continue
                sell = OrderRequest(self.symbol, "sell", grid["amount"], "limit",
                                    client.price_to_precision(self.symbol, level + grid["step"]),
                                    reduce_only=True, tag="SUB7:sell")
                res = await self.ctx.router.execute(self.account_id, sell)
                lv.update(buy_id=None, sell_id=res.get("id"))
                self.log.info("✅ Compra %.2f ejecutada → venta en %.2f", level, level + grid["step"])
            elif lv["sell_id"] and lv["sell_id"] not in open_ids:
                if await client.order_status(lv["sell_id"], self.symbol) != "closed":
                    lv["sell_id"] = None
                    continue
                res = await self._place_buy(level, grid["amount"], grid["sl"], grid["step"])
                lv.update(buy_id=res.get("id") if res else None, sell_id=None)
                self.log.info("💰 Venta %.2f ejecutada → compra repuesta", level + grid["step"])
        self.set_state("grid", grid)
