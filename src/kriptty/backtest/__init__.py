"""Backtesting de las estrategias con el mismo código que opera en vivo.

    kriptty-backtest --strategy SUB11 --start 2025-01-01 --end 2026-09-01
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

import pandas as pd

from .client import BacktestClient
from .market import HistoricalMarket
from .runner import (
    DEFAULT_SYMBOLS,
    UNSUPPORTED,
    BacktestResult,
    buy_and_hold,
    run_backtest,
    summarize,
)

__all__ = ["BacktestClient", "BacktestResult", "HistoricalMarket", "run_backtest", "summarize", "cli"]

# Histórico previo necesario para calentar indicadores (EMA200 diaria, etc.).
DEFAULT_WARMUP_DAYS = {"SUB5": 300, "SUB8": 1500, "SUB9": 300, "SUB2": 40, "SUB7": 40, "SUB10": 15, "SUB11": 40}


def cli() -> None:
    p = argparse.ArgumentParser(description="Backtest de una estrategia de Kriptty sobre histórico de Bitget")
    p.add_argument("--strategy", required=True, help="SUB2, SUB4…SUB11")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (inicio de la operativa)")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (por defecto: hoy)")
    p.add_argument("--timeframe", default=None, help="vela base (por defecto 1h; SUB4 requiere 1m)")
    p.add_argument("--symbols", default=None, help="lista separada por comas en notación ccxt")
    p.add_argument("--equity", type=float, default=10_000)
    p.add_argument("--macro-score", type=float, default=0.0,
                   help="score macro fijo para SUB5/SUB9 (el histórico macro no se reproduce)")
    p.add_argument("--fee", type=float, default=0.0006, help="comisión por ejecución (taker)")
    p.add_argument("--maker-fee", type=float, default=0.0002, help="comisión de órdenes límite (maker)")
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--warmup-days", type=int, default=None)
    p.add_argument("--data-dir", default="data/history")
    p.add_argument("--offline", action="store_true", help="usar solo datos en caché")
    p.add_argument("--out", default="data/backtests")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    account = a.strategy.upper()
    if account in UNSUPPORTED:
        p.error(f"{account} no es backtesteable: {UNSUPPORTED[account]}")
    timeframe = a.timeframe or ("1m" if account == "SUB4" else "1h")
    start = pd.Timestamp(a.start, tz="UTC")
    end = pd.Timestamp(a.end, tz="UTC") if a.end else pd.Timestamp.now(tz="UTC").floor("h")
    warmup = pd.Timedelta(days=a.warmup_days if a.warmup_days is not None else DEFAULT_WARMUP_DAYS.get(account, 30))
    symbols = [s.strip() for s in a.symbols.split(",")] if a.symbols else DEFAULT_SYMBOLS[account]

    from .data import load_market

    market = asyncio.run(load_market(symbols, timeframe, start - warmup, end, a.data_dir, a.offline,
                                     with_funding=account in ("SUB6", "SUB10", "SUB11", "SUB5", "SUB9", "SUB7", "SUB2")))
    result = asyncio.run(run_backtest(account, market, start.to_pydatetime(), end.to_pydatetime(), equity=a.equity,
                                      macro_score=a.macro_score, fee_rate=a.fee, slippage_bps=a.slippage_bps,
                                      maker_fee=a.maker_fee))
    print(summarize(result))
    ref = symbols[0]
    print(f"  Referencia: comprar y mantener {ref} → {buy_and_hold(market, ref, start.to_pydatetime(), end.to_pydatetime()):+.2f}%")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"{account}_{start:%Y%m%d}_{end:%Y%m%d}"
    result.equity.to_csv(f"{stem}_equity.csv", header=["equity"])
    pd.DataFrame([t.__dict__ for t in result.trades]).to_csv(f"{stem}_trades.csv", index=False)
    Path(f"{stem}_metrics.json").write_text(json.dumps(result.metrics, indent=2, default=str))
    print(f"  Resultados en {stem}_*.csv / _metrics.json")
