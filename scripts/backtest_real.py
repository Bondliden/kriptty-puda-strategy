"""Backtest sobre histórico REAL con la configuración definitiva del Plan PUDA.

    python scripts/importar_historico.py historico --out data/history
    python scripts/backtest_real.py --macro historico --out data/real

Cada agente en su subcuenta, con el mismo código que opera en vivo y la configuración del plan:
  * núcleo (SUB5, SUB6, SUB8, SUB9, SUB10): 3x, margen ≤ 20% con el riesgo escalado al apalancamiento;
  * satélites (SUB2, SUB7, SUB11): apalancamiento propio, margen ≤ 10%;
  * todos: rampa 25/50/75/100% por meses, pausa al −10%, parada dura al −20% (hasta revisión manual; con
    ``--review-days N`` se reactiva a los N días desde el primer escalón) y 48H como mucho por
    operación (SUB6, SUB8 y SUB9 exentas por diseño).
SUB2 y SUB6 eligen entre todas las monedas descargadas (como en vivo, donde miran todo el mercado).
El macro de SUB5/SUB9 se reconstruye día a día con lo que se sabía ese día (``macro_hist``).
SUB1 (noticias), SUB3 (copy trading) y SUB4 (velas de 1 minuto) no entran.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from kriptty import clock
from kriptty.backtest.data import _interval_hours
from kriptty.backtest.market import HistoricalMarket
from kriptty.backtest.runner import DEFAULT_SYMBOLS, run_backtest
from kriptty.data.macro import MacroDashboard
from kriptty.strategies import leverage_overrides

EQUITY = 10_000.0
CORE = ["SUB5", "SUB6", "SUB8", "SUB9", "SUB10"]
SATELLITES = ["SUB2", "SUB7", "SUB11"]
COMMON = {"capital_ramp": "0.25,0.5,0.75,1", "max_drawdown_pct": 0.10, "max_total_drawdown_pct": 0.20,
          "max_hold_hours": 48.0, "max_margin_pct": 0.20}
WARMUP_DAYS = {"SUB5": 300, "SUB8": 1500, "SUB9": 300, "SUB2": 40, "SUB7": 40, "SUB10": 15, "SUB11": 40}


class HistMacro:
    """Score macro diario reconstruido; los días sin cobertura suficiente el dashboard es inválido."""

    def __init__(self, score: pd.Series):
        self.score = score.sort_index()
        self._ts = self.score.index.as_unit("s").asi8

    async def get(self) -> MacroDashboard:
        i = int(np.searchsorted(self._ts, clock.now(), side="right")) - 1
        v = float(self.score.iloc[i]) if i >= 0 else float("nan")
        return MacroDashboard(score=0.0 if np.isnan(v) else v, coverage=0.0 if np.isnan(v) else 1.0,
                              timestamp=clock.now())


def _csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, index_col=0, parse_dates=True)


def available(data: Path) -> tuple[list[str], list[str]]:
    perps = sorted(p.name.split("__")[0].replace("-USDT_USDT", "") for p in data.glob("*-USDT_USDT__1h.csv"))
    spots = sorted(p.name.split("__")[0].replace("-USDT", "") for p in data.glob("*-USDT__1h.csv"))
    return perps, spots


def symbols_for(account: str, data: Path) -> list[str]:
    perps, spots = available(data)
    if account == "SUB2":
        return [f"{c}/USDT:USDT" for c in perps]
    if account == "SUB6":
        both = [c for c in perps if c in spots]
        return [s for c in both for s in (f"{c}/USDT:USDT", f"{c}/USDT")]
    have = {f"{c}/USDT:USDT" for c in perps} | {f"{c}/USDT" for c in spots}
    return [s for s in DEFAULT_SYMBOLS[account] if s in have]


def load(symbols: list[str], data: Path, start: pd.Timestamp, end: pd.Timestamp) -> HistoricalMarket:
    candles, funding, intervals = {}, {}, {}
    for s in symbols:
        safe = s.replace("/", "-").replace(":", "_")
        candles[s] = _csv(data / f"{safe}__1h.csv")[start:end]
        f = data / f"{safe}__funding.csv"
        if ":" in s and f.exists():
            funding[s] = _csv(f).iloc[:, 0][start:end]
            intervals[s] = _interval_hours(funding[s])
    return HistoricalMarket(candles, "1h", funding, intervals)


def run_one(account: str, data: str, macro_path: str | None, start: str, end: str, review_days: int = 0) -> dict:
    logging.disable(logging.CRITICAL)
    t0 = time.time()
    data_p = Path(data)
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    syms = symbols_for(account, data_p)
    market = load(syms, data_p, s - pd.Timedelta(days=WARMUP_DAYS.get(account, 30)), e)
    macro = None
    if macro_path:
        from kriptty.backtest.macro_hist import score_series
        macro = HistMacro(score_series(macro_path, start="2017-01-01", end=end))
    core = account in CORE
    settings = dict(COMMON, hard_stop_review_days=review_days)
    if not core:
        settings["max_margin_by_account"] = f"{account}=0.1"
    res = asyncio.run(run_backtest(account, market, s.to_pydatetime(), e.to_pydatetime(), equity=EQUITY,
                                   macro=macro, overrides=leverage_overrides(account, 3 if core else None, scale=True),
                                   settings_overrides=settings))
    daily = res.equity.resample("1D").last().dropna()
    m = res.metrics
    return {"account": account, "start": start, "end": end, "symbols": len(syms), "core": core,
            "macro": bool(macro_path), "seconds": round(time.time() - t0, 1),
            "metrics": {k: (None if isinstance(v, float) and not np.isfinite(v) else round(float(v), 4))
                        for k, v in m.items()},
            "equity_daily": [round(float(x), 2) for x in daily.to_numpy()],
            "dates": [d.strftime("%Y-%m-%d") for d in daily.index]}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/history")
    p.add_argument("--macro", default=None, help="carpeta con los CSV de FRED y fear_greed.csv")
    p.add_argument("--accounts", default=",".join(CORE + SATELLITES))
    p.add_argument("--start", default="2020-10-01")
    p.add_argument("--end", default="2026-09-01")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out", default="data/real")
    p.add_argument("--review-days", type=int, default=0,
                   help="reactivar tras la parada dura a los N días (0 = como en vivo: hasta revisión manual)")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    accounts = a.accounts.split(",")
    if not a.macro:
        skipped = [x for x in accounts if x in ("SUB5", "SUB9")]
        accounts = [x for x in accounts if x not in skipped]
        if skipped:
            print(f"Sin datos macro: {', '.join(skipped)} no se ejecutan")
    with ProcessPoolExecutor(a.workers) as pool:
        futs = {pool.submit(run_one, x, a.data, a.macro, a.start, a.end, a.review_days): x for x in accounts}
        for f in as_completed(futs):
            acc = futs[f]
            try:
                r = f.result()
            except Exception as exc:  # noqa: BLE001
                print(f"✗ {acc}: {exc!r}", flush=True)
                continue
            (out / f"{acc}.json").write_text(json.dumps(r))
            m = r["metrics"]
            print(f"✓ {acc:6s} ret {m['total_return_pct']:+8.1f}%  dd {m['max_drawdown_pct']:6.1f}%  "
                  f"trades {m['closed_trades']:5.0f}  símbolos {r['symbols']:3d}  ({r['seconds']}s)", flush=True)


if __name__ == "__main__":
    main()
