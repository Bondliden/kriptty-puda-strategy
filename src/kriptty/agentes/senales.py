"""Señales diarias comunes al backtest (``scripts/sistema_grids.py``) y a los agentes en vivo.

Todo se calcula con velas **diarias** cerradas: el régimen y los rankings de un día se usan al día
siguiente, igual en el backtest que en vivo, para que lo probado sea lo que se opera.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TOP_LIQ = 40                      # universo diario: las 40 monedas con más volumen de los últimos 30 días
REGIMENES = ("alcista", "lateral", "bajista", "incertidumbre")
CRITERIOS = ("scalper", "momentum", "weak", "lag_long", "lag_short", "hype", "pico")   # hype y pico: detector de memes


def features(daily: pd.DataFrame, btc_daily_close: pd.Series) -> pd.DataFrame:
    """Indicadores de una moneda a partir de sus velas diarias (open, high, low, close, volume)."""
    d = daily
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    chop = tr.rolling(14).sum() / (d.high.rolling(14).max() - d.low.rolling(14).min())
    btc_r = btc_daily_close.pct_change()
    r = d.close.pct_change()
    beta = (r.rolling(60).cov(btc_r) / btc_r.rolling(60).var()).clip(0.3, 3)
    return pd.DataFrame({
        "close": d.close, "liq": (d.volume * d.close).rolling(30).mean(),
        "atr_pct": atr / d.close, "chop": chop,
        "ret7": d.close.pct_change(7), "ret30": d.close.pct_change(30),
        "ema50": d.close.ewm(span=50).mean(), "ema200": d.close.ewm(span=200).mean(),
        # «vasos comunicantes»: lo que la moneda va por detrás (−) o por delante (+) de lo que le tocaría
        # moverse con BTC en los últimos 7 días, según su beta de 60 días
        "lag7": d.close.pct_change(7) - beta * btc_daily_close.pct_change(7),
    })


def features_all(daily: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    btc = daily["BTC"]["close"]
    return {coin: features(d, btc.reindex(d.index)) for coin, d in daily.items()}


def regimes(btc: pd.DataFrame, desplazar: bool = True) -> pd.Series:
    """Régimen de cada día (``btc`` = features de BTC). Con ``desplazar`` (backtest), el valor de un día se
    decide con los datos hasta el cierre del día anterior; sin desplazar (en vivo), el último valor es el
    régimen calculado con la última vela cerrada, que es el que se aplica hoy."""
    r = btc.close.pct_change()
    vol = r.rolling(14).std() * np.sqrt(365)
    vol_rank = vol.rolling(365, min_periods=120).rank(pct=True)
    drop3 = btc.close / btc.close.rolling(3).max() - 1
    reg = pd.Series("lateral", index=btc.index)
    bull = (btc.close > btc.ema200) & (btc.ema50 > btc.ema200) & (btc.ret30 > 0.05)
    bear = (btc.close < btc.ema200) & ((btc.ema50 < btc.ema200) | (btc.ret30 < -0.10))
    unc = (vol_rank > 0.9) | (drop3 < -0.08)
    reg[bull] = "alcista"
    reg[bear] = "bajista"
    reg[unc] = "incertidumbre"
    return reg.shift(1).fillna("lateral") if desplazar else reg          # se usa al día siguiente


def bull_extremo(btc: pd.DataFrame, desplazar: bool = True) -> pd.Series:
    """Bull run fuerte: BTC sube más de un 20% en 30 días y está por encima de su media de 50 días. Es el filtro
    con el que KRIPTTY ALL IN 3.0 a ×6 dio +20,5% anual en el backtest (``bull_extremo.csv``)."""
    s = (btc.ret30 > 0.20) & (btc.close > btc.ema50)
    return s.shift(1).fillna(False).astype(bool) if desplazar else s


def rank_day(g: pd.DataFrame) -> dict[str, list[str]]:
    """Ordena las monedas de un día por cada criterio (``g``: una fila por moneda con liq, chop, ret30,
    atr_pct y lag7). El universo son las ``TOP_LIQ`` más líquidas."""
    g = g.dropna().nlargest(TOP_LIQ, "liq")
    return {
        "scalper": g[(g.atr_pct > 0.02) & (g.atr_pct < 0.10)].sort_values("chop", ascending=False).index.tolist(),
        "momentum": g[g.atr_pct < 0.12].sort_values("ret30", ascending=False).index.tolist(),
        "weak": g.sort_values("ret30").index.tolist(),
        "lag_long": g[g.atr_pct < 0.12].sort_values("lag7").index.tolist(),          # las más rezagadas
        "lag_short": g.sort_values("lag7", ascending=False).index.tolist(),          # las que aún no han caído
    }


def rankings(feat: dict[str, pd.DataFrame], exclude=("BTC", "ETH")) -> dict[str, dict[pd.Timestamp, list[str]]]:
    """Para cada día y criterio, las monedas ordenadas de mejor a peor."""
    cols = ["liq", "chop", "ret30", "atr_pct", "lag7"]
    panel = pd.concat({c: f[cols] for c, f in feat.items() if c not in exclude}, names=["coin", "day"])
    out: dict[str, dict] = {k: {} for k in CRITERIOS if k not in ("hype", "pico")}
    for day, g in panel.groupby(level="day"):
        ranks = rank_day(g.droplevel("day"))
        for k, v in ranks.items():
            out[k][day] = v
    return out
