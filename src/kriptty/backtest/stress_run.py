"""Test de estrés de varios años: todas las estrategias backtesteables sobre
mercados sintéticos (``stress.py``), varias trayectorias por escenario y costes
estresados. Ejecuta el mismo código que opera en vivo.

    kriptty-stress --scenarios ciclo,bear,lateral --seeds 3 --out data/stress
    kriptty-stress --scenarios ciclo --seeds 1 --cost-mult 2   # comisiones y slippage ×2
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

from . import stress
from .market import HistoricalMarket
from .runner import DEFAULT_SYMBOLS, run_backtest

STRATEGIES = ["SUB2", "SUB4", "SUB5", "SUB6", "SUB7", "SUB8", "SUB9", "SUB10", "SUB11"]
EQUITY = 10_000.0
# SUB4 opera en velas de 1m: se prueba en el mes más extremo de cada trayectoria (el del mayor crash).
SUB4_WINDOW_DAYS = 30


def _market(sm: stress.SyntheticMarket, account: str, rng: np.random.Generator) -> tuple[HistoricalMarket, str]:
    syms = [s for s in DEFAULT_SYMBOLS[account] if s in sm.candles]
    funding = {s: sm.funding[s] for s in syms if s in sm.funding}
    if account == "SUB4":
        return HistoricalMarket({s: stress.to_1m(sm.candles[s], rng) for s in syms}, "1m", funding,
                                {s: 8.0 for s in syms}), "1m"
    return HistoricalMarket({s: sm.candles[s] for s in syms}, "1h", funding, {s: 8.0 for s in syms}), "1h"


def _worst_month(sm: stress.SyntheticMarket, start: pd.Timestamp) -> pd.Timestamp:
    btc = sm.candles["BTC/USDT:USDT"]["close"][start:]
    drop = btc.pct_change(24 * 3).idxmin()  # mayor caída en 72H
    return max(start, (drop - pd.Timedelta(days=10)).floor("D"))


def run_one(scenario: str, seed: int, account: str, cost_mult: float = 1.0) -> dict:
    logging.disable(logging.CRITICAL)
    t0 = time.time()
    sm = stress.generate(scenario, seed=seed)
    rng = np.random.default_rng(seed + 1000)
    start = sm.phases[1][0]  # tras el calentamiento
    end = sm.phases[-1][1]
    if account == "SUB4":
        start = _worst_month(sm, start)
        end = start + pd.Timedelta(days=SUB4_WINDOW_DAYS)
        sm.candles = {s: df[start - pd.Timedelta(days=3):end] for s, df in sm.candles.items()}
    market, tf = _market(sm, account, rng)
    res = asyncio.run(run_backtest(account, market, start.to_pydatetime(), end.to_pydatetime(), equity=EQUITY,
                                   fee_rate=0.0006 * cost_mult, maker_fee=0.0002 * cost_mult,
                                   slippage_bps=2.0 * cost_mult, macro=stress.SeriesMacro(sm.macro)))
    m = res.metrics
    daily = res.equity.resample("1D").last().dropna()
    btc = sm.candles["BTC/USDT:USDT"]["close"]
    return {
        "scenario": scenario, "seed": seed, "account": account, "cost_mult": cost_mult, "timeframe": tf,
        "start": str(start.date()), "end": str(end.date()), "seconds": round(time.time() - t0, 1),
        "metrics": {k: (None if isinstance(v, float) and not np.isfinite(v) else round(float(v), 4))
                    for k, v in m.items()},
        "btc_return_pct": round(float(btc[:end].iloc[-1] / btc[start:].iloc[0] * 100 - 100), 2),
        "equity_daily": [round(float(x), 2) for x in daily.to_numpy()],
        "dates": [d.strftime("%Y-%m-%d") for d in daily.index],
    }


def build_report(dirs: dict[str, Path], step_days: int = 7) -> dict:
    """Agrega los JSON de cada ejecución: métricas por estrategia/escenario y curvas semanales.
    ``dirs`` = {etiqueta (p. ej. "antes", "despues"): carpeta}. La cartera combinada suma el
    equity de las estrategias de 3 años (SUB4 se prueba en una ventana de 30 días y va aparte)."""
    out: dict = {"runs": [], "portfolio": []}
    for label, d in dirs.items():
        by_key: dict[tuple, list[dict]] = {}
        for f in sorted(Path(d).glob("*.json")):
            r = json.loads(f.read_text())
            curve = r["equity_daily"][::step_days] + [r["equity_daily"][-1]]
            out["runs"].append({"set": label, "scenario": r["scenario"], "seed": r["seed"], "account": r["account"],
                                "cost_mult": r["cost_mult"], "start": r["start"], "end": r["end"],
                                "btc": r["btc_return_pct"], "m": r["metrics"],
                                "curve": [round(x / EQUITY * 100, 2) for x in curve]})
            if r["account"] != "SUB4":
                by_key.setdefault((r["scenario"], r["seed"], r["cost_mult"]), []).append(r)
        for (sc, seed, cm), rs in by_key.items():
            n = min(len(r["equity_daily"]) for r in rs)
            total = np.sum([np.asarray(r["equity_daily"][:n]) for r in rs], axis=0)
            capital = EQUITY * len(rs)
            dd = float((total / np.maximum.accumulate(total) - 1).min() * 100)
            curve = list(total[::step_days]) + [total[-1]]
            out["portfolio"].append({"set": label, "scenario": sc, "seed": seed, "cost_mult": cm,
                                     "accounts": sorted(r["account"] for r in rs),
                                     "return_pct": round(float(total[-1] / capital * 100 - 100), 2),
                                     "max_dd_pct": round(dd, 2),
                                     "curve": [round(float(x) / capital * 100, 2) for x in curve]})
    out["scenarios"] = {k: {"title": v.title, "description": v.description,
                            "phases": [[p.name, p.days] for p in v.phases]} for k, v in stress.SCENARIOS.items()}
    return out


def cli() -> None:
    p = argparse.ArgumentParser(description="Test de estrés multi-año de las estrategias de Kriptty")
    p.add_argument("--scenarios", default="ciclo,bear,lateral")
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--seed-offset", type=int, default=0)
    p.add_argument("--strategies", default=",".join(STRATEGIES))
    p.add_argument("--cost-mult", type=float, default=1.0, help="multiplica comisiones y slippage")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out", default="data/stress")
    p.add_argument("--report", nargs="+", metavar="ETIQUETA=CARPETA",
                   help="solo agrega resultados ya calculados en un JSON (p. ej. antes=data/a despues=data/b)")
    a = p.parse_args()
    if a.report:
        dirs = dict(item.split("=", 1) for item in a.report)
        report = build_report({k: Path(v) for k, v in dirs.items()})
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, separators=(",", ":")))
        print(f"Informe: {a.out} ({len(report['runs'])} ejecuciones)")
        return
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(sc, a.seed_offset + s, acc, a.cost_mult) for sc in a.scenarios.split(",") for s in range(a.seeds)
            for acc in a.strategies.split(",")]
    jobs.sort(key=lambda j: j[2] not in ("SUB2", "SUB6", "SUB11", "SUB10"))  # las lentas primero
    with ProcessPoolExecutor(a.workers) as pool:
        futures = {pool.submit(run_one, *j): j for j in jobs}
        for f in as_completed(futures):
            sc, seed, acc, cm = futures[f]
            try:
                r = f.result()
            except Exception as e:  # noqa: BLE001
                print(f"✗ {sc} seed={seed} {acc}: {e!r}", flush=True)
                continue
            name = f"{sc}_s{seed}_{acc}_c{cm:g}.json"
            (out / name).write_text(json.dumps(r))
            m = r["metrics"]
            print(f"✓ {sc:8s} seed={seed} {acc:6s} ret {m['total_return_pct']:+8.1f}%  dd {m['max_drawdown_pct']:6.1f}%  "
                  f"trades {m['closed_trades']:5.0f}  ({r['seconds']}s)", flush=True)
