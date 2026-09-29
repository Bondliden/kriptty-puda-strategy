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


def supertrend(df: pd.DataFrame, period: int = 20, multiplier: float = 4.0) -> pd.DataFrame:
    """SuperTrend clásico (bandas finales iterativas, ATR de Wilder).

    Devuelve columnas ``line`` (nivel de stop dinámico) y ``direction``
    (+1 tendencia alcista, -1 bajista, 0 sin datos suficientes).
    """
    a = atr(df, period).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    upper, lower = hl2 + multiplier * a, hl2 - multiplier * a
    f_up, f_low = upper.copy(), lower.copy()
    direction = np.zeros(n, dtype=int)
    line = np.full(n, np.nan)
    start = int(np.argmax(~np.isnan(a))) if (~np.isnan(a)).any() else n
    for i in range(start, n):
        if i == start:
            direction[i] = 1 if close[i] >= hl2[i] else -1
        else:
            if not (upper[i] < f_up[i - 1] or close[i - 1] > f_up[i - 1]):
                f_up[i] = f_up[i - 1]
            if not (lower[i] > f_low[i - 1] or close[i - 1] < f_low[i - 1]):
                f_low[i] = f_low[i - 1]
            if close[i] > f_up[i - 1]:
                direction[i] = 1
            elif close[i] < f_low[i - 1]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]
        line[i] = f_low[i] if direction[i] == 1 else f_up[i]
    return pd.DataFrame({"line": line, "direction": direction}, index=df.index)


def hedge_ratio(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    """MCO y = alpha + beta·x. Devuelve (beta, alpha)."""
    beta, alpha = np.polyfit(x, y, 1)
    return float(beta), float(alpha)


def adf_tstat(series: np.ndarray) -> float:
    """Estadístico t de Dickey-Fuller (sin retardos) sobre Δs = c + ρ·s(t-1).

    Muy negativo ⇒ el spread revierte a la media. Valor crítico de
    Engle-Granger para 2 series al 5% ≈ -3.34.
    """
    s = np.asarray(series, dtype=float)
    ds, lag = np.diff(s), s[:-1]
    X = np.column_stack([np.ones_like(lag), lag])
    coef, *_ = np.linalg.lstsq(X, ds, rcond=None)
    resid = ds - X @ coef
    dof = max(len(ds) - 2, 1)
    sigma2 = resid @ resid / dof
    cov = sigma2 * np.linalg.inv(X.T @ X)
    return float(coef[1] / np.sqrt(cov[1, 1]))


def half_life(series: np.ndarray) -> float:
    """Vida media de reversión (en barras) de un proceso AR(1); inf si no revierte."""
    s = np.asarray(series, dtype=float)
    rho = np.polyfit(s[:-1], np.diff(s), 1)[0]
    return float(-np.log(2) / rho) if rho < 0 else float("inf")
