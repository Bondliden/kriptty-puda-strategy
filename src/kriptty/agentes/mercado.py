"""Datos de mercado en vivo: API pública de Bitget (sin claves) y top por capitalización de CoinGecko.

Solo lectura. Nada de aquí envía órdenes.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

import pandas as pd

BITGET = "https://api.bitget.com"
PRODUCT = "USDT-FUTURES"
COINGECKO = "https://api.coingecko.com/api/v3/coins/markets"
# Monedas estables y envoltorios que nunca deben operarse como altcoins
NO_ALTS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USDS", "USD1", "BUSD", "WBTC", "WETH", "STETH",
           "WSTETH", "WEETH", "CBBTC", "XAUT", "PAXG", "BTCB", "SUSDE", "USDD", "LEO"}


def _get(url: str, params: dict | None = None, timeout: float = 20.0, retries: int = 3):
    q = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(q, headers={"User-Agent": "kriptty-agentes/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — red: reintento con espera
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET {url} falló: {last}")


@dataclass
class Ticker:
    symbol: str          # p. ej. ALGOUSDT
    coin: str            # ALGO
    last: float
    usdt_volume_24h: float
    funding: float
    change24h: float = 0.0   # variación en 24 h (0,25 = +25%)
    high24h: float = 0.0
    low24h: float = 0.0


def base_coin(coin: str) -> str:
    """1000PEPE → PEPE, 10000SATS → SATS (para cruzar con CoinGecko)."""
    return re.sub(r"^1(0{3,6})", "", coin)


class Bitget:
    """Lector de mercado de futuros USDT de Bitget."""

    def tickers(self) -> dict[str, Ticker]:
        data = _get(f"{BITGET}/api/v2/mix/market/tickers", {"productType": PRODUCT})["data"]
        out = {}
        for t in data:
            sym = t["symbol"]
            if not sym.endswith("USDT"):
                continue
            coin = sym[:-4]
            out[coin] = Ticker(sym, coin, float(t["lastPr"] or 0), float(t.get("usdtVolume") or 0),
                               float(t.get("fundingRate") or 0), float(t.get("change24h") or 0),
                               float(t.get("high24h") or 0), float(t.get("low24h") or 0))
        return out

    def daily(self, coin: str, days: int = 90) -> pd.DataFrame:
        """Velas diarias UTC (``1Dutc``) de los últimos ``days`` días, de la más antigua a la más reciente."""
        sym = f"{coin}USDT"
        rows: list[list] = []
        end = None
        while len(rows) < days:
            params = {"symbol": sym, "productType": PRODUCT, "granularity": "1Dutc", "limit": 90}
            if end is None:
                batch = _get(f"{BITGET}/api/v2/mix/market/candles", params)["data"]
            else:
                params["endTime"] = end
                batch = _get(f"{BITGET}/api/v2/mix/market/history-candles", params)["data"]
            if not batch:
                break
            rows = batch + rows
            first = int(batch[0][0])
            if end is not None and first >= end:
                break
            end = first - 1
            if len(batch) < 90:
                break
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "base_vol", "quote_vol"][: len(rows[0])])
        df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        df = df.drop_duplicates("ts").set_index("ts").sort_index()
        for c in ("open", "high", "low", "close", "base_vol"):
            df[c] = df[c].astype(float)
        df["volume"] = df["base_vol"]
        today = pd.Timestamp.now(tz="UTC").normalize()
        df = df[df.index < today]                     # solo velas cerradas
        return df[["open", "high", "low", "close", "volume"]].tail(days)

    def price(self, coin: str) -> float:
        data = _get(f"{BITGET}/api/v2/mix/market/ticker", {"symbol": f"{coin}USDT", "productType": PRODUCT})["data"]
        return float(data[0]["lastPr"])


def top_marketcap(n: int = 200) -> dict[str, int]:
    """Símbolos de las ``n`` criptomonedas con más capitalización (CoinGecko) → puesto en el ranking."""
    out: dict[str, int] = {}
    per_page = 250
    page = 1
    while len(out) < n:
        data = _get(COINGECKO, {"vs_currency": "usd", "order": "market_cap_desc", "per_page": per_page, "page": page})
        if not data:
            break
        for c in data:
            sym = str(c.get("symbol", "")).upper()
            rank = c.get("market_cap_rank") or 10_000
            if sym and sym not in out and rank <= n:
                out[sym] = int(rank)
        page += 1
        if page > 2:
            break
    return out


# Memecoins: sin proyecto detrás; fuera del universo salvo que la configuración diga lo contrario
MEMECOINS = {"DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "TRUMP", "MELANIA", "FARTCOIN", "PENGU", "PUMP", "POPCAT",
             "MEW", "BRETT", "MOG", "NEIRO", "TURBO", "MEME", "BOME", "SPX", "GIGA", "PNUT", "ACT", "GOAT", "MOODENG",
             "CHILLGUY", "BABYDOGE", "DOGS", "CAT", "USELESS", "BANANAS31", "SATS", "RATS", "ORDI", "MYRO", "SLERF",
             "PONKE", "TOSHI", "SUNDOG", "HIPPO", "BROCCOLI", "GORK", "WHY", "LADYS", "ELON", "AIDOGE", "CHEEMS"}


def kraken_assets() -> set[str]:
    """Activos listados en Kraken (API pública). XBT → BTC, XDG → DOGE; sin sufijos de staking («.S»)."""
    r = _get("https://api.kraken.com/0/public/Assets")["result"]
    alias = {"XBT": "BTC", "XDG": "DOGE"}
    return {alias.get(v["altname"].split(".")[0], v["altname"].split(".")[0]).upper() for v in r.values()}


def bitget_name(coin: str) -> list[str]:
    """Nombres alternativos en Bitget para una moneda (SHIB → 1000SHIB, PEPE → 1000PEPE…)."""
    return [coin, f"1000{coin}", f"10000{coin}", f"1000000{coin}"]
