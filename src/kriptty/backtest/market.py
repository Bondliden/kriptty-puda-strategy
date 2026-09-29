"""Datos históricos en memoria con acceso "sin mirar al futuro".

Las velas base se indexan por hora de APERTURA (como ccxt). Una vela está
disponible en el instante t solo si su cierre (apertura + duración) ≤ t.
Los timeframes superiores se agregan una sola vez y se cortan por cierre.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14_400, "1d": 86_400, "1w": 604_800}
def _epoch_s(index: pd.DatetimeIndex) -> np.ndarray:
    """Segundos epoch, sea cual sea la resolución del índice (pandas 3 usa µs a menudo)."""
    return index.as_unit("s").asi8


_RULES = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D", "1w": "W-MON"}


class HistoricalMarket:
    def __init__(self, candles: dict[str, pd.DataFrame], base_tf: str,
                 funding: dict[str, pd.Series] | None = None, funding_interval_h: dict[str, float] | None = None):
        if base_tf not in TF_SECONDS:
            raise ValueError(f"Timeframe base no soportado: {base_tf}")
        self.base_tf = base_tf
        self.base_s = TF_SECONDS[base_tf]
        self.candles = {s: df.sort_index() for s, df in candles.items()}
        self.funding = funding or {}
        self.funding_interval_h = funding_interval_h or {}
        self._frames: dict[tuple[str, str], tuple[pd.DataFrame, np.ndarray]] = {}

    @property
    def symbols(self) -> list[str]:
        return list(self.candles)

    def has(self, symbol: str) -> bool:
        return symbol in self.candles

    def _frame(self, symbol: str, tf: str) -> tuple[pd.DataFrame, np.ndarray]:
        key = (symbol, tf)
        if key not in self._frames:
            base = self.candles[symbol]
            if tf == self.base_tf:
                df = base
            else:
                if TF_SECONDS[tf] < self.base_s:
                    raise ValueError(f"No se puede pedir {tf} con timeframe base {self.base_tf}")
                kwargs = {"origin": "epoch"} if tf in ("5m", "15m", "1h", "4h") else {}
                df = base.resample(_RULES[tf], label="left", closed="left", **kwargs).agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
            opens = _epoch_s(df.index)
            self._frames[key] = (df, opens + TF_SECONDS[tf])  # hora de cierre de cada vela
        return self._frames[key]

    def ohlcv(self, symbol: str, tf: str, t: float, limit: int) -> pd.DataFrame:
        df, closes = self._frame(symbol, tf)
        end = int(np.searchsorted(closes, t, side="right"))
        return df.iloc[max(0, end - limit):end]

    def bar_closing_at(self, symbol: str, t: float) -> pd.Series | None:
        df, closes = self._frame(symbol, self.base_tf)
        i = int(np.searchsorted(closes, t, side="left"))
        if i < len(closes) and closes[i] == t:
            return df.iloc[i]
        return None

    def price(self, symbol: str, t: float) -> float:
        df = self.ohlcv(symbol, self.base_tf, t, 1)
        if df.empty:
            raise KeyError(f"Sin datos de {symbol} en t={pd.Timestamp(t, unit='s', tz='UTC')}")
        return float(df["close"].iloc[-1])

    def stats_24h(self, symbol: str, t: float) -> tuple[float, float]:
        """(cambio % 24h, volumen en USDT 24h)."""
        n = max(1, 86_400 // self.base_s)
        df = self.ohlcv(symbol, self.base_tf, t, n + 1)
        if len(df) < 2:
            return 0.0, 0.0
        change = float(df["close"].iloc[-1] / df["close"].iloc[0] * 100 - 100)
        volume = float((df["close"] * df["volume"]).iloc[1:].sum())
        return change, volume

    def funding_between(self, symbol: str, t0: float, t1: float) -> list[float]:
        s = self.funding.get(symbol)
        if s is None or s.empty:
            return []
        ts = _epoch_s(s.index)
        lo, hi = np.searchsorted(ts, t0, side="right"), np.searchsorted(ts, t1, side="right")
        return [float(v) for v in s.iloc[lo:hi].to_numpy()]

    def funding_at(self, symbol: str, t: float) -> float | None:
        s = self.funding.get(symbol)
        if s is None or s.empty:
            return None
        ts = _epoch_s(s.index)
        i = int(np.searchsorted(ts, t, side="right")) - 1
        return float(s.iloc[i]) if i >= 0 else None

    def time_range(self) -> tuple[float, float]:
        starts = [df.index[0].timestamp() for df in self.candles.values()]
        ends = [df.index[-1].timestamp() + self.base_s for df in self.candles.values()]
        return max(starts), min(ends)
