"""SUB11 — Seguimiento de tendencia con SuperTrend.

Inspirada en el controlador ``supertrend_v1`` de Hummingbot (v2), que entra
cuando hay tendencia y el precio está a < 1% de la línea SuperTrend, en velas
de 3m. Adaptada a 4H (en 3m el ruido y las comisiones dominan). Al validarla
con el backtester se vio que, con 4×ATR, la línea se mantiene a ~3.4-4.6 ATR
del precio durante la tendencia: el filtro de proximidad casi nunca se cumplía.
Se usa el SuperTrend clásico "stop and reverse" con un filtro anti-persecución:

Señal (vela 4H cerrada, SuperTrend(20, 4.0)), sin posición abierta:
    dirección +1 y precio a ≤ 5 ATR de la línea → LONG
    dirección −1 y precio a ≤ 5 ATR de la línea → SHORT
SL = línea SuperTrend ∓ 0.2%, y se desplaza con ella en cada vela (solo a
favor). Sin TP: al cambiar la dirección se cierra y se abre en sentido contrario.
Riesgo 1% por operación, máx. 3 posiciones, apalancamiento 3x.
"""
from __future__ import annotations

from ..exchange.client import perp
from ..indicators import atr, supertrend
from .base import Strategy


def supertrend_signal(df, period: int, multiplier: float, proximity_atr: float) -> tuple[int, float, float, int]:
    """(señal, línea, distancia en ATR, dirección). señal: +1 long, −1 short, 0 nada."""
    st = supertrend(df, period, multiplier)
    direction, line = int(st["direction"].iloc[-1]), float(st["line"].iloc[-1])
    price = float(df["close"].iloc[-1])
    atr_value = float(atr(df, period).iloc[-1])
    distance = abs(price - line) / atr_value if atr_value > 0 else float("inf")
    return (direction if direction != 0 and distance <= proximity_atr else 0), line, distance, direction


class SuperTrendStrategy(Strategy):
    account_id = "SUB11"
    name = "SuperTrend 4H"
    schedule = {"trigger": "cron", "hour": "0,4,8,12,16,20", "minute": 2}
    leverage = 3

    ASSETS = ["BTC", "ETH", "SOL"]
    TIMEFRAME = "4h"
    PERIOD, MULTIPLIER = 20, 4.0
    PROXIMITY_ATR = 5.0  # Hummingbot usa ~1%; ver docstring
    SL_BUFFER = 0.002
    RISK_PCT = 0.01
    MAX_POSITIONS = 3

    async def run_cycle(self) -> None:
        client = self.client
        positions = {p.symbol: p for p in await self.positions()}
        for asset in self.ASSETS:
            symbol = perp(asset)
            df = await client.ohlcv(symbol, self.TIMEFRAME, 200)
            signal, line, distance, direction = supertrend_signal(df, self.PERIOD, self.MULTIPLIER,
                                                                  self.PROXIMITY_ATR)
            pos = positions.get(symbol)

            if pos is not None:
                pos_dir = 1 if pos.side == "long" else -1
                if direction != pos_dir:
                    await self.ctx.router.close_position(self.account_id, pos, "cambio de tendencia", self.account_id)
                    positions.pop(symbol)
                    pos = None
            if pos is not None:
                new_sl = line * (1 - self.SL_BUFFER * pos_dir)
                current = self.get_state(f"sl:{symbol}")
                if current is None or pos_dir * (new_sl - current) > 0:
                    await self.ctx.router.update_stop_loss(self.account_id, pos, new_sl, self.account_id)
                    self.set_state(f"sl:{symbol}", new_sl)
                continue

            self.ctx.state.delete(self.account_id, f"sl:{symbol}")
            if signal == 0 or len(positions) >= self.MAX_POSITIONS:
                continue
            price = await client.last_price(symbol)
            side = "buy" if signal > 0 else "sell"
            sl = line * (1 - self.SL_BUFFER * signal)
            if (signal > 0 and sl >= price) or (signal < 0 and sl <= price):
                continue  # el precio ya cruzó la línea: la vela siguiente dará la nueva dirección
            if await self.open_position(symbol, side, price, sl, None, self.RISK_PCT,
                                        reason=f"SuperTrend {'+' if signal > 0 else '-'}1, a {distance:.1f} ATR de la línea"):
                self.set_state(f"sl:{symbol}", sl)
                positions[symbol] = None  # cuenta para MAX_POSITIONS
