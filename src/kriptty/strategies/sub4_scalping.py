"""SUB4 — Scalping intradía dirigido por WebSocket (ccxt.pro).

Disparo: cierre de cada vela de 1m (watch_ohlcv; reconexión gestionada por ccxt,
en lugar del cliente WS artesanal, que usaba websockets.InvalidStatusCode —
eliminado en websockets ≥ 14 — y fallaba al reconectar).

Entrada (todas): patrón de vela en 1m (hammer, shooting star, engulfing, 3
soldados, 3 cuervos) + tendencia EMA9/EMA21 en 5m + volumen > 1.5× media(20) +
RSI(1m) no extremo (<75 long, >25 short) + spread < 0.05%.
NUEVO filtro de costes: el TP (2·ATR) debe cubrir ≥ 3× la comisión de ida y
vuelta (0.12% taker). En 1m de BTC el ATR suele ser 0.05-0.1%: sin este filtro
la mayoría de operaciones tenían esperanza negativa solo por comisiones.
SL 1.2·ATR(1m), TP 2·ATR. Breakeven en +1 ATR, trailing a 0.8 ATR desde +2 ATR.
Límites: 2 posiciones, 10 trades/día/símbolo, pausa si DD diario > 3%, sin
posiciones de 23:00 a 00:00 UTC, riesgo 1%.
"""
from __future__ import annotations

import asyncio

import pandas as pd

from .. import clock
from ..exchange.client import perp
from ..indicators import atr, ema, last, rsi
from .base import Strategy

ROUND_TRIP_FEE = 0.0012


def detect_pattern(df: pd.DataFrame) -> str | None:
    """Devuelve "bull", "bear" o None según el patrón de la última vela cerrada."""
    if len(df) < 3:
        return None
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    body = abs(c[-1] - o[-1])
    rng = max(h[-1] - lo[-1], 1e-12)
    upper, lower = h[-1] - max(c[-1], o[-1]), min(c[-1], o[-1]) - lo[-1]
    if body / rng < 0.35 and lower >= 2 * body and upper <= body:
        return "bull"  # hammer
    if body / rng < 0.35 and upper >= 2 * body and lower <= body:
        return "bear"  # shooting star
    if c[-2] < o[-2] and c[-1] > o[-1] and c[-1] >= o[-2] and o[-1] <= c[-2]:
        return "bull"  # engulfing alcista
    if c[-2] > o[-2] and c[-1] < o[-1] and c[-1] <= o[-2] and o[-1] >= c[-2]:
        return "bear"  # engulfing bajista
    if all(c[i] > o[i] for i in (-3, -2, -1)) and c[-1] > c[-2] > c[-3]:
        return "bull"  # tres soldados blancos
    if all(c[i] < o[i] for i in (-3, -2, -1)) and c[-1] < c[-2] < c[-3]:
        return "bear"  # tres cuervos negros
    return None


class ScalpingStrategy(Strategy):
    account_id = "SUB4"
    name = "Scalping WebSocket 1m"
    event_driven = True
    leverage = 5

    ASSETS = ["BTC", "ETH", "SOL"]
    SL_ATR, TP_ATR = 1.2, 2.0
    BE_ATR, TRAIL_START_ATR, TRAIL_ATR = 1.0, 2.0, 0.8
    VOLUME_SPIKE = 1.5
    MAX_SPREAD = 0.0005
    RISK_PCT = 0.01
    MAX_POSITIONS = 2
    MAX_TRADES_DAY = 10
    MAX_DAILY_DD = 0.03
    CUTOFF_HOUR_UTC = 23

    def evaluate(self, m1: pd.DataFrame, m5: pd.DataFrame) -> tuple[str | None, float, str]:
        """Devuelve (lado, atr, motivo). Pura: testeable sin exchange."""
        atr_value = last(atr(m1, 14))
        price = float(m1["close"].iloc[-1])
        if self.TP_ATR * atr_value / price < 3 * ROUND_TRIP_FEE:
            return None, atr_value, "ATR demasiado bajo para cubrir comisiones"
        pattern = detect_pattern(m1)
        if pattern is None:
            return None, atr_value, "sin patrón"
        vol_ok = m1["volume"].iloc[-1] > self.VOLUME_SPIKE * m1["volume"].iloc[-21:-1].mean()
        if not vol_ok:
            return None, atr_value, "sin pico de volumen"
        trend_up = last(ema(m5["close"], 9)) > last(ema(m5["close"], 21))
        r = last(rsi(m1["close"], 14))
        if pattern == "bull" and trend_up and r < 75:
            return "buy", atr_value, f"patrón alcista, RSI={r:.0f}"
        if pattern == "bear" and not trend_up and r > 25:
            return "sell", atr_value, f"patrón bajista, RSI={r:.0f}"
        return None, atr_value, f"patrón {pattern} contra tendencia/RSI"

    # ── Límites diarios ─────────────────────────────────────────────────
    def _day(self) -> dict:
        today = clock.utcnow().date().isoformat()
        d = self.get_state("day")
        if not d or d.get("date") != today:
            d = {"date": today, "trades": {}, "start_equity": None, "paused": False}
        return d

    async def _can_trade(self, symbol: str) -> bool:
        d = self._day()
        equity = await self.client.equity()
        if d["start_equity"] is None:
            d["start_equity"] = equity
        if not d["paused"] and d["start_equity"] and (d["start_equity"] - equity) / d["start_equity"] > self.MAX_DAILY_DD:
            d["paused"] = True
            self.log.warning("⏸  Drawdown diario > %.0f%%: SUB4 en pausa hasta mañana", self.MAX_DAILY_DD * 100)
        self.set_state("day", d)
        return not d["paused"] and d["trades"].get(symbol, 0) < self.MAX_TRADES_DAY

    def _count_trade(self, symbol: str) -> None:
        d = self._day()
        d["trades"][symbol] = d["trades"].get(symbol, 0) + 1
        self.set_state("day", d)

    # ── Gestión de posiciones ───────────────────────────────────────────
    async def _manage(self, pos, atr_value: float) -> None:
        if clock.utcnow().hour >= self.CUTOFF_HOUR_UTC:
            await self.ctx.router.close_position(self.account_id, pos, "cierre intradía 23:00 UTC", self.account_id)
            return
        sign = 1 if pos.side == "long" else -1
        gain_atr = sign * (pos.mark_price - pos.entry_price) / atr_value
        current = self.get_state(f"sl:{pos.symbol}")
        new_sl = None
        if gain_atr >= self.TRAIL_START_ATR:
            new_sl = pos.mark_price - sign * self.TRAIL_ATR * atr_value
        elif gain_atr >= self.BE_ATR:
            new_sl = pos.entry_price
        if new_sl is not None and (current is None or sign * (new_sl - current) > 0):
            await self.ctx.router.update_stop_loss(self.account_id, pos, new_sl, self.account_id)
            self.set_state(f"sl:{pos.symbol}", new_sl)

    async def on_closed_candle(self, symbol: str) -> None:
        client = self.client
        m1 = await client.ohlcv(symbol, "1m", 100)
        positions = await self.positions()
        mine = next((p for p in positions if p.symbol == symbol), None)
        atr_value = last(atr(m1, 14))
        if mine:
            await self._manage(mine, atr_value)
            return
        self.ctx.state.delete(self.account_id, f"sl:{symbol}")
        if (clock.utcnow().hour >= self.CUTOFF_HOUR_UTC or len(positions) >= self.MAX_POSITIONS
                or not await self._can_trade(symbol)):
            return
        m5 = await client.ohlcv(symbol, "5m", 60)
        side, atr_value, reason = self.evaluate(m1, m5)
        if side is None:
            self.log.debug("%s: %s", symbol, reason)
            return
        t = await client.ticker(symbol)
        bid, ask = float(t.get("bid") or 0), float(t.get("ask") or 0)
        if not bid or (ask - bid) / bid > self.MAX_SPREAD:
            return
        entry = ask if side == "buy" else bid
        sign = 1 if side == "buy" else -1
        sl, tp = entry - sign * self.SL_ATR * atr_value, entry + sign * self.TP_ATR * atr_value
        if await self.open_position(symbol, side, entry, sl, tp, self.RISK_PCT, reason=reason):
            self.set_state(f"sl:{symbol}", sl)
            self._count_trade(symbol)

    async def _watch(self, symbol: str) -> None:
        import ccxt.pro as ccxtpro

        ws = ccxtpro.bitget({"options": {"defaultType": "swap"}})
        if self.ctx.settings.mode_for(self.account_id) == "demo":
            ws.set_sandbox_mode(True)
        last_open = None
        try:
            while True:
                try:
                    candles = await ws.watch_ohlcv(symbol, "1m")
                    current_open = candles[-1][0]
                    if last_open is not None and current_open != last_open:
                        await self.on_closed_candle(symbol)
                    last_open = current_open
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — ccxt reconecta en la siguiente llamada
                    self.last_error = repr(e)
                    self.log.warning("WS %s: %s — reintentando en 5s", symbol, e)
                    await asyncio.sleep(5)
        finally:
            await ws.close()

    async def start(self) -> None:
        self.log.info("🚀 SUB4 escuchando velas 1m de %s", self.ASSETS)
        await asyncio.gather(*(self._watch(perp(a)) for a in self.ASSETS))

    async def run_cycle(self) -> None:  # permite un ciclo manual (MCP / tests)
        for a in self.ASSETS:
            await self.on_closed_candle(perp(a))
