"""Agente de memecoins: detecta el «hype» y el pico de las memecoins para la cuenta pequeña de memecoins
(cierre a las 24 horas como máximo). Solo lectura: no envía órdenes.

Universo:

- la categoría «meme-token» de CoinGecko, que se actualiza sola cuando salen memecoins nuevas;
- la lista fija ``mercado.MEMECOINS``;
- cualquier moneda **recién listada** en Bitget (menos de 3 días), que es donde empezaron TRUMP y MELANIA.

Las que aguantan (DOGE, PEPE, SHIB, BONK, WIF…) siguen dentro.

Dos señales, con las mismas definiciones que el backtest ``scripts/meme_hype.py``:

* **hype** (para largos): sube ``R`` en 24 h con un volumen ``V`` veces su media diaria de la semana
  anterior; o es nueva, sube ``R`` desde su primera vela y mueve más de 20 M$ en 24 h.
* **pico** (para cortos): ha llegado a subir ``R_pico`` en 24 h y ya cae ``D`` desde el máximo de 24 h.

Resultado del backtest (2023-12 → 2026-09, 33 memecoins):

- comprar el hype pierde: 115 de 120 variantes acaban en negativo, con un 35% de aciertos y caídas
  medianas del −77%. En TRUMP y MELANIA los stops saltaban en la primera hora;
- vender en corto tras el pico (``R_pico`` = 100%, ``D`` = 10%, stop 20%, objetivo 20%, 24 h) es la única
  regla positiva en los dos periodos: +24,6% y caída máxima del −16%, pero con solo 21 operaciones.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .mercado import BITGET, COINGECKO, MEMECOINS, PRODUCT, Bitget, _get, base_coin

NUEVA_H, NUEVA_VOL = 72, 20e6
REGLAS = {"R": 0.25, "V": 2.0, "nuevas": True, "R_pico": 1.0, "D": 0.10, "D_max": 0.30}


@dataclass
class Senal:
    coin: str
    tipo: str                     # "hype" (largo) | "pico" (corto tras el pico)
    ret24: float                  # subida en 24 h (o desde la primera vela si es nueva)
    pico24: float                 # la mayor subida en 24 h de las últimas 24 h
    caida: float                  # caída desde el máximo de 24 h
    vratio: float | None          # volumen de 24 h / media diaria de la semana anterior
    vol24: float                  # USDT en 24 h
    nueva: bool                   # menos de 3 días cotizando en Bitget
    tendencia: bool               # en «trending» de CoinGecko

    @property
    def fuerza(self) -> float:
        return (self.ret24 if self.tipo == "hype" else self.pico24) * (self.vratio or 5.0)


def memes_coingecko(paginas: int = 1) -> set[str]:
    """Símbolos de la categoría «meme-token» de CoinGecko (las 250 con más capitalización por página)."""
    out: set[str] = set()
    for p in range(1, paginas + 1):
        datos = _get(COINGECKO, {"vs_currency": "usd", "category": "meme-token", "order": "market_cap_desc",
                                 "per_page": 250, "page": p})
        out |= {str(c.get("symbol", "")).upper() for c in datos if c.get("symbol")}
    return out


def tendencia_coingecko() -> set[str]:
    try:
        return {c["item"]["symbol"].upper() for c in _get("https://api.coingecko.com/api/v3/search/trending").get("coins", [])}
    except RuntimeError:
        return set()


def velas_1h(coin: str, horas: int = 200) -> pd.DataFrame:
    datos = _get(f"{BITGET}/api/v2/mix/market/candles",
                 {"symbol": f"{coin}USDT", "productType": PRODUCT, "granularity": "1H", "limit": min(horas, 200)})["data"]
    df = pd.DataFrame([d[:7] for d in datos], columns=["ts", "open", "high", "low", "close", "base_vol", "quote_vol"])
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    return df.set_index("ts").sort_index().astype(float)


def medir(df: pd.DataFrame, ahora: pd.Timestamp | None = None) -> dict:
    """Indicadores con las velas cerradas, igual que el backtest."""
    df = df[df.index < (ahora or pd.Timestamp.now(tz="UTC")).floor("h")]
    c, qv = df["close"], df["quote_vol"]
    nueva = len(df) < NUEVA_H
    vol24 = float(qv.tail(24).sum())
    ret = c / c.shift(24) - 1
    ret24 = float(ret.iloc[-1]) if len(df) > 24 else (float(c.iloc[-1] / df["open"].iloc[0] - 1) if len(df) else 0.0)
    pico24 = float(ret.tail(24).max()) if len(df) > 24 else ret24
    max24 = float(df["high"].tail(24).max()) if len(df) else 0.0
    previo = qv.iloc[:-24].tail(24 * 7)
    vratio = float(vol24 / (previo.mean() * 24)) if len(previo) >= 72 and previo.mean() > 0 else None
    return {"ret24": ret24, "pico24": pico24, "caida": (1 - float(c.iloc[-1]) / max24) if max24 else 0.0,
            "vratio": vratio, "vol24": vol24, "nueva": nueva}


def es_hype(m: dict, R: float, V: float, nuevas: bool = True) -> bool:
    if m["vratio"] is not None and m["ret24"] >= R and m["vratio"] >= V:
        return True
    return nuevas and m["nueva"] and m["ret24"] >= R and m["vol24"] >= NUEVA_VOL


def es_pico(m: dict, R_pico: float, D: float, D_max: float = 1.0) -> bool:
    """``D_max``: en vivo no se entra si ya ha caído demasiado desde el máximo (se llegaría tarde)."""
    return m["pico24"] >= R_pico and D <= m["caida"] <= D_max


def detectar(bitget: Bitget | None = None, reglas: dict | None = None,
             universo_memes: set[str] | None = None) -> dict[str, list[Senal]]:
    """Señales de ahora mismo: {"hype": […], "pico": […]}, de más a menos fuerza."""
    r = {**REGLAS, **(reglas or {})}
    bitget = bitget or Bitget()
    tick = bitget.tickers()
    if universo_memes is None:
        try:
            universo_memes = memes_coingecko()
        except RuntimeError:
            universo_memes = set()
    memes = universo_memes | MEMECOINS
    trending = tendencia_coingecko()
    out: dict[str, list[Senal]] = {"hype": [], "pico": []}
    umbral = min(r["R"], r["R_pico"] / 2)
    for coin, t in tick.items():
        rango = (t.high24h / t.low24h - 1) if t.low24h else 0.0
        if t.change24h < umbral and rango < umbral:      # filtro barato antes de pedir velas
            continue
        b = base_coin(coin)
        try:
            df = velas_1h(coin)
        except RuntimeError:
            continue
        if df.empty:
            continue
        m = medir(df)
        if b not in memes and not m["nueva"]:
            continue
        base = dict(coin=coin, ret24=m["ret24"], pico24=m["pico24"], caida=m["caida"], vratio=m["vratio"],
                    vol24=m["vol24"], nueva=m["nueva"], tendencia=b in trending)
        if es_hype(m, r["R"], r["V"], r["nuevas"]):
            out["hype"].append(Senal(tipo="hype", **base))
        if es_pico(m, r["R_pico"], r["D"], r["D_max"]):
            out["pico"].append(Senal(tipo="pico", **base))
    return {k: sorted(v, key=lambda s: -s.fuerza) for k, v in out.items()}
