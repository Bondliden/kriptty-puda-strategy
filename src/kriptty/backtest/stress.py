"""Mercados sintéticos para tests de estrés de varios años.

No sustituye al histórico real (``kriptty-backtest`` con datos de Bitget): sirve
para comprobar cómo se comportan las estrategias —el mismo código que opera en
vivo— ante regímenes y eventos extremos controlados (euforia, bear market de
−70%, crashes de un día del −25%, rangos laterales largos) y con muchas
trayectorias distintas (Monte Carlo).

Modelo (velas de 1H, 4 subpasos de 15 min para máximos/mínimos):
    log P_i = β_i · F  +  OU_i  +  RW_i
  * F: factor de mercado con deriva y volatilidad por fase, volatilidad
    agrupada (GARCH simple) y saltos (crashes) programados y aleatorios.
  * OU_i: componente idiosincrático con reversión a la media (vida media de
    días) → algunos pares están parcialmente cointegrados, como en la realidad.
  * RW_i: paseo aleatorio idiosincrático pequeño → la cointegración no es perfecta.
  * No hay "rezagos" explotables a propósito (SUB2): la hipótesis nula es que
    no existen, para no fabricar beneficios.
Funding (cada 8H): media por fase + componente de momentum de 24H + ruido persistente (AR(1)),
acotado a ±0.3%. Spot = perpetuo con un basis ligado al funding.
Volumen en USDT realista y mayor en velas de mucho movimiento.
El score macro sigue al régimen con 30 días de retraso y ruido (sin mirar al futuro).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import clock
from ..data.macro import MacroDashboard


@dataclass(frozen=True)
class Phase:
    name: str
    days: int
    drift: float          # deriva anual del factor (log), p. ej. +0.8
    vol: float            # volatilidad anual del factor
    funding: float        # funding medio por 8H (0.0001 = 0.01%)
    macro: float          # score macro del régimen (−10…+10)
    crashes: tuple[tuple[int, float], ...] = ()   # (día dentro de la fase, caída log del factor)


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    description: str
    phases: tuple[Phase, ...]
    random_jumps_per_year: float = 3.0

    @property
    def days(self) -> int:
        return sum(p.days for p in self.phases)


# Historia previa (calentamiento de EMA200 semanal de SUB8, EMA200 diaria de SUB5/SUB9…).
WARMUP = Phase("histórico previo", 1500, drift=0.35, vol=0.62, funding=0.0001, macro=0.5)

SCENARIOS: dict[str, Scenario] = {
    "ciclo": Scenario(
        "ciclo", "Ciclo completo",
        "Acumulación, euforia con flash crash, bear market del −70% con dos crashes de un día "
        "(tipo LUNA y FTX), suelo lateral y recuperación.",
        (
            Phase("acumulación", 120, 0.10, 0.45, 0.0001, 0.0),
            Phase("euforia", 240, 1.10, 0.62, 0.0004, 3.5, crashes=((150, -0.20),)),
            Phase("distribución", 60, -0.10, 0.55, 0.0002, 0.5),
            Phase("bear market", 300, -1.35, 0.75, -0.00005, -4.0, crashes=((90, -0.28), (230, -0.25))),
            Phase("suelo lateral", 200, 0.0, 0.40, 0.00005, -1.0),
            Phase("recuperación", 176, 0.70, 0.50, 0.0002, 2.0),
        ),
    ),
    "bear": Scenario(
        "bear", "Bear market prolongado",
        "Tres años de caída con rebotes falsos y crashes: el peor caso para estrategias largas.",
        (
            Phase("techo", 90, 0.0, 0.60, 0.0003, 1.0),
            Phase("primera caída", 250, -1.20, 0.80, -0.00005, -4.5, crashes=((60, -0.25),)),
            Phase("rebote falso", 120, 0.60, 0.60, 0.0002, -1.0),
            Phase("capitulación", 260, -1.00, 0.85, -0.0001, -6.0, crashes=((40, -0.30), (180, -0.22))),
            Phase("lateral bajista", 376, -0.15, 0.50, 0.0, -3.0),
        ),
        random_jumps_per_year=4.0,
    ),
    "lateral": Scenario(
        "lateral", "Lateral con flash crashes",
        "Tres años sin tendencia, volatilidad media y flash crashes que se recuperan.",
        (
            Phase("rango", 365, 0.0, 0.45, 0.0001, 0.0, crashes=((200, -0.15),)),
            Phase("rango volátil", 365, 0.0, 0.65, 0.0001, -1.0, crashes=((120, -0.22),)),
            Phase("rango estrecho", 366, 0.05, 0.35, 0.00008, 0.5),
        ),
    ),
}

# (β al factor, volatilidad idiosincrática anual, vida media OU en días, USDT negociados por hora, precio inicial)
ASSETS: dict[str, tuple[float, float, float, float, float]] = {
    "BTC": (1.00, 0.10, 20, 900e6, 27_000), "ETH": (1.15, 0.25, 10, 450e6, 1_650),
    "SOL": (1.55, 0.45, 8, 180e6, 22), "XRP": (1.20, 0.50, 12, 120e6, 0.52),
    "DOGE": (1.60, 0.60, 10, 90e6, 0.065), "ADA": (1.30, 0.45, 9, 40e6, 0.26),
    "AVAX": (1.55, 0.45, 8, 35e6, 9.5), "LINK": (1.35, 0.40, 6, 45e6, 7.5),
    "DOT": (1.35, 0.40, 6, 25e6, 4.2), "LTC": (1.10, 0.40, 15, 30e6, 65),
    "BCH": (1.20, 0.50, 15, 25e6, 230), "TRX": (0.60, 0.30, 20, 20e6, 0.09),
    "NEAR": (1.55, 0.55, 8, 30e6, 1.1), "APT": (1.60, 0.60, 8, 25e6, 5.5),
    "ARB": (1.60, 0.55, 7, 30e6, 0.85), "OP": (1.60, 0.55, 7, 25e6, 1.3),
    "SUI": (1.70, 0.65, 8, 30e6, 0.45), "INJ": (1.70, 0.65, 8, 20e6, 8.0),
    "AAVE": (1.40, 0.50, 10, 20e6, 60), "UNI": (1.40, 0.50, 10, 20e6, 4.3),
}
HOURS_PER_YEAR = 24 * 365
SUBSTEPS = 4


@dataclass
class SyntheticMarket:
    candles: dict[str, pd.DataFrame]
    funding: dict[str, pd.Series]
    macro: pd.Series                   # score macro diario observable (con retraso)
    phases: list[tuple[pd.Timestamp, pd.Timestamp, str]] = field(default_factory=list)


def _phase_arrays(phases: list[Phase], hours: int, rng: np.random.Generator, jumps_per_year: float):
    drift = np.empty(hours)
    vol = np.empty(hours)
    fund = np.empty(hours)
    macro = np.empty(hours)
    jumps = np.zeros(hours)
    spans = []
    h = 0
    for p in phases:
        n = p.days * 24
        sl = slice(h, min(h + n, hours))
        drift[sl], vol[sl], fund[sl], macro[sl] = p.drift, p.vol, p.funding, p.macro
        for day, size in p.crashes:
            # El crash se reparte en 6 horas y rebota un 30% en las 48H siguientes (como en los reales).
            k = h + day * 24 + int(rng.integers(0, 24))
            if k + 54 < hours:
                jumps[k:k + 6] += size / 6
                jumps[k + 6:k + 54] += -size * 0.3 / 48
                vol[k:k + 24 * 7] *= 1.8
                macro[k:k + 24 * 30] = np.minimum(macro[k:k + 24 * 30], -6.0)
        spans.append((h, min(h + n, hours), p.name))
        h += n
    # Saltos aleatorios (no programados) en ambos sentidos, sesgados a la baja.
    n_rand = rng.poisson(jumps_per_year * hours / HOURS_PER_YEAR)
    for k in rng.integers(0, hours, n_rand):
        jumps[k] += rng.choice([-1, -1, 1]) * rng.uniform(0.04, 0.10)
    return drift, vol, fund, macro, jumps, spans


def generate(scenario: Scenario | str, seed: int = 0, end: str | pd.Timestamp = "2026-10-01",
             assets: list[str] | None = None, warmup: Phase | None = WARMUP,
             spot_assets: tuple[str, ...] = ("BTC", "ETH", "SOL", "XRP", "DOGE", "LINK")) -> SyntheticMarket:
    sc = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    rng = np.random.default_rng(seed)
    assets = assets or list(ASSETS)
    phases = ([warmup] if warmup else []) + list(sc.phases)
    days = sum(p.days for p in phases)
    hours = days * 24
    end_ts = pd.Timestamp(end, tz="UTC").floor("D")
    start_ts = end_ts - pd.Timedelta(days=days)
    drift, vol, fund_mean, macro_h, jumps, spans = _phase_arrays(phases, hours, rng, sc.random_jumps_per_year)

    # Factor de mercado en subpasos de 15 min con volatilidad agrupada.
    steps = hours * SUBSTEPS
    dt = 1 / (HOURS_PER_YEAR * SUBSTEPS)
    cluster = np.exp(np.convolve(rng.normal(0, 0.35, hours), np.ones(72) / np.sqrt(72), mode="same") * 0.6)
    sig = np.repeat(vol * cluster / np.sqrt(np.mean(cluster ** 2)), SUBSTEPS)
    mu = np.repeat(drift, SUBSTEPS)
    shocks = rng.standard_t(5, steps) / np.sqrt(5 / 3)  # colas gruesas
    f_steps = (mu - 0.5 * sig ** 2) * dt + sig * np.sqrt(dt) * shocks
    f_steps += np.repeat(jumps / SUBSTEPS, SUBSTEPS)
    factor = np.cumsum(f_steps)

    idx = pd.date_range(start_ts, periods=hours, freq="1h", tz="UTC")
    candles: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    f8 = idx[::8]
    for name in assets:
        beta, ivol, hl_days, usd_vol, p0 = ASSETS[name]
        theta = np.log(2) / (hl_days * 24 * SUBSTEPS)
        e = rng.normal(0, ivol * np.sqrt(dt), steps)
        # OU discreto x_t = (1−θ)·x_{t−1} + ε_t, calculado como EWM (en C) sobre ε/θ.
        ou = pd.Series(np.r_[0.0, e / theta]).ewm(alpha=theta, adjust=False).mean().to_numpy()[1:]
        rw = np.cumsum(rng.normal(0, ivol * 0.6 * np.sqrt(dt), steps))
        logp = np.log(p0) + beta * factor + ou * 1.5 + rw
        sub = np.exp(logp).reshape(hours, SUBSTEPS)
        close = sub[:, -1]
        open_ = np.r_[p0, close[:-1]]
        high = np.maximum(sub.max(axis=1), open_)
        low = np.minimum(sub.min(axis=1), open_)
        move = np.abs(np.log(close / open_)) / (np.repeat(vol, 1) * beta / np.sqrt(HOURS_PER_YEAR) + 1e-9)
        volume = usd_vol * (0.6 + 0.4 * move) * rng.lognormal(0, 0.3, hours) / close
        perp = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
        candles[f"{name}/USDT:USDT"] = perp

        # Funding por 8H: media del régimen + momentum de 24H + ruido, acotado.
        r24 = pd.Series(np.log(close)).diff(24).fillna(0).to_numpy()[::8]
        # El funding real es persistente: ruido AR(1) con vida media de ~3 días (9 periodos de 8H).
        a_f = 0.5 ** (1 / 9)
        noise = pd.Series(rng.normal(0, 0.00005, len(f8)) / (1 - a_f)).ewm(alpha=1 - a_f, adjust=False).mean()
        noise = noise.to_numpy() * np.sqrt((1 - a_f) / (1 + a_f))
        rate = fund_mean[::8] * (0.7 + 0.3 * beta) + 0.002 * r24 + noise
        rate = np.clip(rate, -0.003, 0.003)
        funding[f"{name}/USDT:USDT"] = pd.Series(rate, index=f8, name="rate")
        if name in spot_assets:
            premium = np.repeat(np.clip(rate * 3, -0.004, 0.006), 8)[:hours] + rng.normal(0, 0.0002, hours)
            k = 1 / (1 + premium)
            candles[f"{name}/USDT"] = pd.DataFrame(
                {"open": open_ * k, "high": high * k, "low": low * k, "close": close * k, "volume": volume * 0.5},
                index=idx)

    # Macro observable: régimen con 30 días de retraso + ruido (los datos macro llegan tarde).
    daily = pd.Series(macro_h[::24], index=idx[::24])
    macro_obs = (daily.shift(30).bfill() + rng.normal(0, 0.8, len(daily))).clip(-10, 10)
    phase_spans = [(idx[a], idx[b - 1] + pd.Timedelta(hours=1), n) for a, b, n in spans]
    return SyntheticMarket(candles, funding, macro_obs, phase_spans)


def to_1m(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Baja una serie de 1H a 1m con un puente browniano por vela (para SUB4)."""
    n = len(df)
    steps = rng.normal(0, 1, (n, 60))
    path = np.cumsum(steps, axis=1)
    path -= np.linspace(0, 1, 60)[None, :] * path[:, -1:]
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    rng_hl = np.log(df["high"].to_numpy() / df["low"].to_numpy())
    scale = rng_hl / (path.max(axis=1) - path.min(axis=1) + 1e-12)
    logp = np.log(o)[:, None] + np.linspace(0, 1, 61)[None, 1:] * np.log(c / o)[:, None] + path * scale[:, None] * 0.6
    closes = np.exp(logp).ravel()
    opens = np.r_[o[0], closes[:-1]]
    noise = np.exp(np.abs(rng.normal(0, 0.0004, len(closes))))
    idx = pd.date_range(df.index[0], periods=len(closes), freq="1min", tz="UTC")
    vol = np.repeat(df["volume"].to_numpy() / 60, 60) * rng.lognormal(0, 0.6, len(closes))
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * noise,
                         "low": np.minimum(opens, closes) / noise, "close": closes, "volume": vol}, index=idx)


class SeriesMacro:
    """Proveedor macro para el backtest: lee el score diario observable en el instante simulado."""

    def __init__(self, series: pd.Series):
        self.series = series.sort_index()
        self._ts = self.series.index.as_unit("s").asi8

    async def get(self) -> MacroDashboard:
        i = int(np.searchsorted(self._ts, clock.now(), side="right")) - 1
        score = float(self.series.iloc[max(i, 0)])
        return MacroDashboard(score=score, coverage=1.0, timestamp=clock.now())
