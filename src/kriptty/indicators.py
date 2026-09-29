"""Indicadores técnicos (pandas puro, sin dependencias de TA externas).

Todas las funciones reciben un DataFrame OHLCV con columnas
open/high/low/close/volume ordenado de más antiguo a más reciente.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR con suavizado de Wilder (alpha = 1/period)."""
    return true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder sobre TODA la serie (el valor relevante es el último)."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def bollinger(series: pd.Series, period: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    return mid - k * std, mid, mid + k * std


def last(series: pd.Series) -> float:
    value = series.iloc[-1]
    if pd.isna(value):
        raise ValueError("Indicador sin datos suficientes")
    return float(value)


def ohlcv_to_df(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp").astype(float)
