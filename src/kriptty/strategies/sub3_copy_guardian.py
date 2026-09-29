"""SUB3 — Copy trading nativo de Bitget + guardián de riesgo propio.

REDISEÑO: el original leía posiciones de traders ajenos con
/api/v2/copy/mix-trader/trader-list, trader-detail y trader-open-positions.
Esos endpoints NO existen en la API v2 de Bitget (verificado contra el mapa de
endpoints que mantiene ccxt 4.5): la API de copy trading solo expone la propia
cuenta (como trader o como seguidor). No hay forma programática de "leer" a un
trader élite y replicarlo a mano.

Lo que sí es posible y conserva la idea ("nunca confiar solo en el SL del trader"):
  1. Sigues a los traders desde la web/app de Bitget con la cuenta SUB3
     (la selección por win rate > 55%, PF > 1.5, DD < 25%, > 50 trades se hace
     ahí con los filtros del ranking; anota sus IDs en SUB3_TRADER_IDS).
  2. Este guardián revisa cada 60 s las órdenes copiadas abiertas
     (mix-follower/query-current-orders) e impone SL propio:
         LONG:  SL = max(SL_trader, entry − 1.5·ATR14(1h))   (el más conservador)
         SHORT: SL = min(SL_trader, entry + 1.5·ATR14(1h))
     y TP = el del trader o entry ± 3.75·ATR (mix-follower/setting-tpsl).
  3. Si la pérdida diaria de SUB3 supera el límite, cierra todo
     (mix-follower/close-positions).

⚠️ Los nombres de campos de la respuesta se basan en la doc v2; verificar en
Demo Trading antes de activar (la estrategia viene desactivada por defecto).
"""
from __future__ import annotations

from ..indicators import atr, last
from .base import Strategy


class CopyGuardianStrategy(Strategy):
    account_id = "SUB3"
    name = "Copy trading nativo + guardián SL"
    schedule = {"trigger": "interval", "seconds": 60}

    SL_ATR = 1.5
    TP_ATR = 3.75
    PRODUCT_TYPE = "USDT-FUTURES"

    @staticmethod
    def conservative_sl(side: str, entry: float, trader_sl: float | None, atr_value: float,
                        k: float = 1.5) -> float:
        own = entry - k * atr_value if side == "long" else entry + k * atr_value
        if not trader_sl:
            return own
        return max(own, trader_sl) if side == "long" else min(own, trader_sl)

    async def _orders(self) -> list[dict]:
        resp = await self.client.ex.private_copy_get_v2_copy_mix_follower_query_current_orders(
            {"productType": self.PRODUCT_TYPE, "limit": "50"})
        data = resp.get("data") or {}
        return data.get("trackingList") or data.get("trackingInfoList") or []

    async def run_cycle(self) -> None:
        if self.ctx.settings.trading_mode == "dry_run":
            self.log.info("SUB3 necesita copy trading real (demo/live); en dry_run no hace nada.")
            return
        if not await self.ctx.router.check_daily_loss(self.account_id):
            self.log.warning("Pérdida diaria superada: cerrando posiciones copiadas.")
            await self.client.ex.private_copy_post_v2_copy_mix_follower_close_positions(
                {"productType": self.PRODUCT_TYPE})
            return

        client = self.client
        await client.load_markets()
        for o in await self._orders():
            market_id = o.get("symbol")
            symbol = client.ex.safe_symbol(market_id, None, None, "swap")
            side = "long" if (o.get("posSide") or o.get("holdSide")) == "long" else "short"
            entry = float(o.get("openPriceAvg") or o.get("openPrice") or 0)
            if not entry:
                continue
            trader_sl = float(o.get("presetStopLossPrice") or o.get("stopLossPrice") or 0) or None
            trader_tp = float(o.get("presetStopSurplusPrice") or o.get("stopSurplusPrice") or 0) or None
            atr_value = last(atr(await client.ohlcv(symbol, "1h", 50), 14))
            sl = self.conservative_sl(side, entry, trader_sl, atr_value, self.SL_ATR)
            tp = trader_tp or (entry + self.TP_ATR * atr_value if side == "long" else entry - self.TP_ATR * atr_value)
            if trader_sl and abs(trader_sl - sl) / entry < 1e-4:
                continue  # ya protegido con un SL igual o más conservador
            await client.ex.private_copy_post_v2_copy_mix_follower_setting_tpsl({
                "trackingNo": o.get("trackingNo"), "productType": self.PRODUCT_TYPE, "symbol": market_id,
                "stopLossPrice": client.ex.price_to_precision(symbol, sl),
                "stopSurplusPrice": client.ex.price_to_precision(symbol, tp),
            })
            self.log.info("🛡️  %s %s copiada (trader %s): SL %s → %.6g, TP %.6g", side, symbol,
                          o.get("traderId"), trader_sl, sl, tp)
