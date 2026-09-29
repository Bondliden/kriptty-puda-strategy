"""Dashboard macro compartido por SUB5 (macro-shorting) y SUB9 (collar).

Correcciones respecto al diseño original:
  * CPIAUCSL es un ÍNDICE (~320), no un % interanual: el original lo comparaba
    con 4-5% y puntuaba SIEMPRE "muy bajista" (-4 puntos permanentes). Ahora se
    calcula la inflación interanual real (13 observaciones mensuales).
  * Los umbrales estaban mal ordenados (">= 1.5" antes que ">= 3.0"), así que
    STRONG_BULLISH era inalcanzable en índices y DXY.
  * Si una fuente fallaba se usaban valores de 2023 (Fed 5.33%, CPI 3.5%) o 0.0:
    ahora la variable se excluye y, si falta >40% del peso, el dashboard es
    inválido y SUB5 no opera (fail-closed).
  * La escala real era ±20 (pesos suman 1 × señal ±2 × 10); ahora es ±10 como
    se documentó, para que los umbrales -3/-5 signifiquen lo previsto.
  * Yahoo Finance (no oficial, bloquea bots) se sustituye por FRED: SP500,
    NASDAQCOM, VIXCLS y DTWEXBGS (índice dólar amplio, proxy del DXY).
  * La Fed se puntúa por dirección (Δ 6 meses) en lugar de niveles fijos de 2023.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass, field

import aiohttp
import pandas as pd

log = logging.getLogger(__name__)

WEIGHTS = {"fed_rate": 0.20, "cpi": 0.20, "sp500": 0.15, "nasdaq": 0.15,
           "dxy": 0.10, "vix": 0.10, "fear_greed": 0.05, "yield_curve": 0.05}
MIN_COVERAGE = 0.6


def score_fed(delta_6m: float) -> int:
    if delta_6m >= 0.25:
        return -2
    if delta_6m > 0:
        return -1
    if delta_6m <= -0.75:
        return 2
    if delta_6m <= -0.25:
        return 1
    return 0


def score_cpi(yoy: float, yoy_prev: float) -> int:
    if yoy > 5.0 and yoy >= yoy_prev:
        return -2
    if yoy > 4.0 and yoy >= yoy_prev:
        return -1
    if yoy <= 2.5:
        return 2
    if yoy < yoy_prev:
        return 1
    return 0


def score_equity(change_1d: float) -> int:
    if change_1d <= -2.5:
        return -2
    if change_1d <= -1.0:
        return -1
    if change_1d >= 3.0:
        return 2
    if change_1d >= 1.5:
        return 1
    return 0


def score_dollar(change_1d: float) -> int:
    if change_1d >= 0.8:
        return -2
    if change_1d >= 0.3:
        return -1
    if change_1d <= -0.8:
        return 2
    if change_1d <= -0.3:
        return 1
    return 0


def score_vix(level: float, change_1d_pct: float) -> int:
    if level >= 30 and change_1d_pct > 5:
        return -2
    if level >= 25:
        return -1
    if level <= 15 and change_1d_pct < 0:
        return 2
    if level <= 18:
        return 1
    return 0


def score_fear_greed(value: float) -> int:
    """Contrarian: codicia extrema = riesgo de techo."""
    if value >= 80:
        return -2
    if value >= 65:
        return -1
    if value <= 20:
        return 2
    if value <= 35:
        return 1
    return 0


def score_yield_curve(spread: float) -> int:
    if spread < -0.5:
        return -2
    if spread < 0.0:
        return -1
    if spread > 1.0:
        return 2
    if spread > 0.3:
        return 1
    return 0


@dataclass
class MacroDashboard:
    score: float
    coverage: float
    signals: dict[str, int] = field(default_factory=dict)
    values: dict[str, float] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def valid(self) -> bool:
        return self.coverage >= MIN_COVERAGE

    @property
    def regime(self) -> str:
        if not self.valid:
            return "UNKNOWN"
        if self.score <= -5:
            return "CRISIS"
        if self.score <= -3:
            return "BEAR"
        if self.score >= 3:
            return "STRONG_BULL"
        return "NEUTRAL"

    def summary(self) -> str:
        parts = ", ".join(f"{k}={v:+d}" for k, v in self.signals.items())
        return f"score={self.score:+.2f} cobertura={self.coverage:.0%} régimen={self.regime} [{parts}]"


def build_dashboard(raw: dict[str, dict]) -> MacroDashboard:
    signals: dict[str, int] = {}
    values: dict[str, float] = {}
    if "fed_rate" in raw:
        signals["fed_rate"] = score_fed(raw["fed_rate"]["delta_6m"])
        values["fed_rate"] = raw["fed_rate"]["current"]
    if "cpi" in raw:
        signals["cpi"] = score_cpi(raw["cpi"]["yoy"], raw["cpi"]["yoy_prev"])
        values["cpi_yoy"] = raw["cpi"]["yoy"]
    for name in ("sp500", "nasdaq"):
        if name in raw:
            signals[name] = score_equity(raw[name]["change_1d"])
            values[name] = raw[name]["current"]
    if "dxy" in raw:
        signals["dxy"] = score_dollar(raw["dxy"]["change_1d"])
        values["dxy"] = raw["dxy"]["current"]
    if "vix" in raw:
        signals["vix"] = score_vix(raw["vix"]["current"], raw["vix"]["change_1d"])
        values["vix"] = raw["vix"]["current"]
    if "fear_greed" in raw:
        signals["fear_greed"] = score_fear_greed(raw["fear_greed"]["current"])
        values["fear_greed"] = raw["fear_greed"]["current"]
    if "yield_curve" in raw:
        signals["yield_curve"] = score_yield_curve(raw["yield_curve"]["current"])
        values["yield_curve"] = raw["yield_curve"]["current"]
    coverage = sum(WEIGHTS[k] for k in signals)
    score = sum(s * WEIGHTS[k] for k, s in signals.items()) / coverage * 5 if coverage else 0.0
    return MacroDashboard(score=round(score, 2), coverage=round(coverage, 2), signals=signals, values=values)


class MacroDataCollector:
    FRED_API = "https://api.stlouisfed.org/fred/series/observations"
    FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    FEAR_GREED = "https://api.alternative.me/fng/?limit=2"

    def __init__(self, fred_api_key: str = ""):
        self.fred_api_key = fred_api_key

    async def _fred(self, session: aiohttp.ClientSession, series: str) -> pd.Series:
        timeout = aiohttp.ClientTimeout(total=15)
        if self.fred_api_key:
            params = {"series_id": series, "api_key": self.fred_api_key, "file_type": "json",
                      "sort_order": "desc", "limit": 400}
            async with session.get(self.FRED_API, params=params, timeout=timeout) as r:
                obs = (await r.json()).get("observations", [])
            s = pd.Series({o["date"]: o["value"] for o in obs})
        else:  # sin clave: CSV público
            async with session.get(self.FRED_CSV, params={"id": series}, timeout=timeout) as r:
                df = pd.read_csv(io.StringIO(await r.text()))
            s = pd.Series(df.iloc[:, 1].values, index=df.iloc[:, 0].values)
        s = pd.to_numeric(s, errors="coerce").dropna()
        s.index = pd.to_datetime(s.index)
        return s.sort_index()

    async def _fear_greed(self, session: aiohttp.ClientSession) -> float:
        async with session.get(self.FEAR_GREED, timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json(content_type=None)
        return float(data["data"][0]["value"])

    async def collect(self) -> dict[str, dict]:
        series = {"fed_rate": "FEDFUNDS", "cpi": "CPIAUCSL", "sp500": "SP500", "nasdaq": "NASDAQCOM",
                  "dxy": "DTWEXBGS", "vix": "VIXCLS", "yield_curve": "T10Y2Y"}
        async with aiohttp.ClientSession(headers={"User-Agent": "kriptty/0.2"}) as session:
            results = await asyncio.gather(
                *(self._fred(session, sid) for sid in series.values()),
                self._fear_greed(session), return_exceptions=True)
        raw: dict[str, dict] = {}
        for (name, sid), s in zip(series.items(), results[:-1]):
            if isinstance(s, Exception) or len(s) < 2:
                log.warning("Macro: sin datos de %s (%s)", sid, s if isinstance(s, Exception) else "vacío")
                continue
            try:
                if name == "fed_rate":
                    raw[name] = {"current": float(s.iloc[-1]), "delta_6m": float(s.iloc[-1] - s.iloc[-7])}
                elif name == "cpi":
                    raw[name] = {"yoy": float(s.iloc[-1] / s.iloc[-13] * 100 - 100),
                                 "yoy_prev": float(s.iloc[-2] / s.iloc[-14] * 100 - 100)}
                elif name == "yield_curve":
                    raw[name] = {"current": float(s.iloc[-1])}
                else:
                    raw[name] = {"current": float(s.iloc[-1]),
                                 "change_1d": float(s.iloc[-1] / s.iloc[-2] * 100 - 100)}
            except IndexError:
                log.warning("Macro: historial insuficiente en %s", sid)
        fg = results[-1]
        if not isinstance(fg, Exception):
            raw["fear_greed"] = {"current": fg}
        return raw


class MacroProvider:
    """Caché compartida: SUB5 y SUB9 leen el mismo dashboard sin duplicar llamadas."""

    def __init__(self, fred_api_key: str = "", ttl_seconds: float = 3 * 3600):
        self.collector = MacroDataCollector(fred_api_key)
        self.ttl = ttl_seconds
        self._cached: MacroDashboard | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> MacroDashboard:
        async with self._lock:
            if self._cached is None or time.time() - self._cached.timestamp > self.ttl:
                self._cached = build_dashboard(await self.collector.collect())
                log.info("📊 Macro: %s", self._cached.summary())
            return self._cached
