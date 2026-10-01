"""Dashboard macro histórico para el backtest sobre datos reales.

Reconstruye, día a día, el mismo score que calcula ``MacroDataCollector`` en vivo (FRED + Fear &
Greed, ``data.macro.build_dashboard``) con lo que se sabía ese día: las series diarias llegan al
día siguiente, la Fed (media mensual) a los 3 días de acabar el mes y la inflación (CPI) unos 45
días después del mes al que se refiere. Archivos de ``scripts/descargar_macro.ps1``:
``FEDFUNDS.csv``, ``CPIAUCSL.csv``, ``SP500.csv``, ``NASDAQCOM.csv``, ``DTWEXBGS.csv``,
``VIXCLS.csv``, ``T10Y2Y.csv`` (CSV de FRED) y ``fear_greed.csv`` (alternative.me).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.macro import MIN_COVERAGE, build_dashboard

FRED = {"fed_rate": "FEDFUNDS", "cpi": "CPIAUCSL", "sp500": "SP500", "nasdaq": "NASDAQCOM",
        "dxy": "DTWEXBGS", "vix": "VIXCLS", "yield_curve": "T10Y2Y"}
DAILY_LAG = pd.Timedelta(days=1)
FED_LAG = pd.Timedelta(days=34)   # media del mes M (fecha FRED = día 1) disponible ~3 días tras acabar
CPI_LAG = pd.Timedelta(days=46)   # CPI del mes M publicado a mediados de M+1


def _fred(path: Path) -> pd.Series:
    df = pd.read_csv(path)
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    s.index = pd.to_datetime(df.iloc[:, 0])
    return s.dropna().sort_index()


def _fear_greed(path: Path) -> pd.Series:
    """CSV de alternative.me (``format=csv``): líneas con valor y fecha DD-MM-AAAA en cualquier orden."""
    rows = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        d = re.search(r"(\d{2})-(\d{2})-(\d{4})", line)
        v = re.search(r"(?:^|[,\s\"])(\d{1,3})(?=[,\s\"]|$)", line.replace(d.group(0), "") if d else "")
        if d and v and 0 <= int(v.group(1)) <= 100:
            rows[pd.Timestamp(int(d.group(3)), int(d.group(2)), int(d.group(1)))] = float(v.group(1))
    return pd.Series(rows, dtype=float).sort_index()


def _available(metric: pd.Series | pd.DataFrame, lag: pd.Timedelta, days: pd.DatetimeIndex):
    """Valor conocido en cada día: la observación de fecha d se conoce en d + lag."""
    m = metric.copy()
    m.index = m.index + lag
    m = m[~m.index.duplicated(keep="last")]
    return m.reindex(m.index.union(days)).ffill().reindex(days)


def build_series(folder: str | Path, start: str = "2018-01-01", end: str | None = None) -> pd.DataFrame:
    """DataFrame diario (UTC) con ``score`` y ``coverage`` y la señal de cada variable."""
    folder = Path(folder)
    days = pd.date_range(start, end or pd.Timestamp.now().normalize(), freq="D")
    raw: dict[str, pd.DataFrame] = {}
    for name, sid in FRED.items():
        f = folder / f"{sid}.csv"
        if not f.exists():
            continue
        s = _fred(f)
        if name == "fed_rate":
            raw[name] = _available(pd.DataFrame({"current": s, "delta_6m": s - s.shift(6)}), FED_LAG, days)
        elif name == "cpi":
            yoy = s / s.shift(12) * 100 - 100
            raw[name] = _available(pd.DataFrame({"yoy": yoy, "yoy_prev": yoy.shift(1)}), CPI_LAG, days)
        elif name == "yield_curve":
            raw[name] = _available(pd.DataFrame({"current": s}), DAILY_LAG, days)
        else:
            raw[name] = _available(pd.DataFrame({"current": s, "change_1d": s.pct_change() * 100}), DAILY_LAG, days)
    fg = folder / "fear_greed.csv"
    if fg.exists():
        raw["fear_greed"] = _available(pd.DataFrame({"current": _fear_greed(fg)}), DAILY_LAG, days)
    out = []
    for i, day in enumerate(days):
        today = {k: {c: float(v) for c, v in df.iloc[i].items()} for k, df in raw.items()
                 if not df.iloc[i].isna().any()}
        dash = build_dashboard(today)
        out.append({"date": day, "score": dash.score, "coverage": dash.coverage, **dash.signals})
    df = pd.DataFrame(out).set_index("date")
    df.index = df.index.tz_localize("UTC")
    return df


def score_series(folder: str | Path, **kw) -> pd.Series:
    """Score diario para ``stress.SeriesMacro``; días sin cobertura suficiente → NaN (dashboard inválido)."""
    df = build_series(folder, **kw)
    return df["score"].where(df["coverage"] >= MIN_COVERAGE, np.nan)
