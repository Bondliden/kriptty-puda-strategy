"""Descarga y caché (CSV) de histórico de Bitget vía ccxt (endpoints públicos,
sin API key). Paginación automática de ccxt (``paginate``)."""
from __future__ import annotations

import logging
from pathlib import Path

import ccxt.async_support as ccxt
import pandas as pd

from ..indicators import ohlcv_to_df
from .market import TF_SECONDS, HistoricalMarket

log = logging.getLogger(__name__)


def _path(data_dir: Path, symbol: str, kind: str) -> Path:
    safe = symbol.replace("/", "-").replace(":", "_")
    return data_dir / f"{safe}__{kind}.csv"


def _covers(df: pd.DataFrame, start_ms: int, end_ms: int, step_ms: int) -> bool:
    if df.empty:
        return False
    first = int(df.index[0].timestamp() * 1000)
    last = int(df.index[-1].timestamp() * 1000)
    return first <= start_ms + step_ms and last >= end_ms - 2 * step_ms


async def fetch_ohlcv(ex, symbol: str, timeframe: str, start_ms: int, end_ms: int, data_dir: Path,
                      offline: bool = False) -> pd.DataFrame:
    path = _path(data_dir, symbol, timeframe)
    step_ms = TF_SECONDS[timeframe] * 1000
    if path.exists():
        cached = pd.read_csv(path, index_col=0, parse_dates=True)
        if _covers(cached, start_ms, end_ms, step_ms) or offline:
            return cached
    if offline:
        raise FileNotFoundError(f"Sin caché para {symbol} {timeframe} en {data_dir}")
    rows = await ex.fetch_ohlcv(symbol, timeframe, since=start_ms,
                                params={"paginate": True, "until": end_ms, "paginationCalls": 1000})
    df = ohlcv_to_df(rows)
    df = df[~df.index.duplicated()].sort_index()
    data_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    log.info("⬇️  %s %s: %d velas", symbol, timeframe, len(df))
    return df


async def fetch_funding(ex, symbol: str, start_ms: int, end_ms: int, data_dir: Path,
                        offline: bool = False) -> pd.Series:
    path = _path(data_dir, symbol, "funding")
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
    if offline:
        return pd.Series(dtype=float)
    rows = await ex.fetch_funding_rate_history(symbol, since=start_ms,
                                               params={"paginate": True, "until": end_ms})
    s = pd.Series({pd.Timestamp(r["timestamp"], unit="ms", tz="UTC"): float(r["fundingRate"]) for r in rows},
                  name="rate", dtype=float).sort_index()
    data_dir.mkdir(parents=True, exist_ok=True)
    s.to_csv(path)
    log.info("⬇️  %s funding: %d registros", symbol, len(s))
    return s


def _interval_hours(s: pd.Series) -> float:
    if len(s) < 3:
        return 8.0
    return float(pd.Series(s.index).diff().dt.total_seconds().median() / 3600)


async def load_market(symbols: list[str], timeframe: str, start: pd.Timestamp, end: pd.Timestamp,
                      data_dir: str | Path = "data/history", offline: bool = False,
                      with_funding: bool = False) -> HistoricalMarket:
    data_dir = Path(data_dir)
    ex = ccxt.bitget({"enableRateLimit": True})
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    candles, funding, intervals = {}, {}, {}
    try:
        for s in symbols:
            candles[s] = await fetch_ohlcv(ex, s, timeframe, start_ms, end_ms, data_dir, offline)
            if with_funding and ":" in s:
                funding[s] = await fetch_funding(ex, s, start_ms, end_ms, data_dir, offline)
                intervals[s] = _interval_hours(funding[s])
    finally:
        await ex.close()
    return HistoricalMarket(candles, timeframe, funding, intervals)
