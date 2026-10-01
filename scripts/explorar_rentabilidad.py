"""Exploración de rentabilidad: más capital en juego y más apalancamiento, con validación fuera de muestra.

    python scripts/explorar_rentabilidad.py --macro historico/macro --out data/explorar --workers 16
    python scripts/explorar_rentabilidad.py --analizar data/explorar --max-dd 25

1. Ejecuta cada agente sobre todo el histórico real (oct 2020 – ago 2026) con cada combinación de
   capital en juego (MAX_MARGIN_PCT), apalancamiento y parada dura.
2. ``--analizar``: elige la configuración de cada agente y el reparto del capital entre agentes
   **solo con 2020–2023** (entrenamiento), con la caída máxima de la cartera como límite, y lo
   comprueba en 2024–2026 (prueba), que no se ha usado para elegir nada.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_real import WARMUP_DAYS, HistMacro, load, symbols_for  # noqa: E402

from kriptty.backtest.runner import run_backtest  # noqa: E402
from kriptty.strategies import leverage_overrides  # noqa: E402

EQUITY = 10_000.0
START, END, SPLIT = "2020-10-01", "2026-09-01", "2024-01-01"
AGENTS = ["SUB5", "SUB7", "SUB11", "SUB9", "SUB8", "SUB6"]
MARGINS = [0.2, 0.3, 0.4]
LEVERAGES = [3, 7]
STOPS = [0.20, 0.35]


def tag(a: str, m: float, lev: int, stop: float) -> str:
    return f"{a}_m{int(m * 100)}_x{lev}_s{int(stop * 100)}"


def run_one(a: str, m: float, lev: int, stop: float, data: str, macro_path: str, out: str) -> str:
    logging.disable(logging.CRITICAL)
    t0 = time.time()
    s, e = pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC")
    syms = symbols_for(a, Path(data))
    market = load(syms, Path(data), s - pd.Timedelta(days=WARMUP_DAYS.get(a, 30)), e)
    macro = None
    if a in ("SUB5", "SUB9"):
        from kriptty.backtest.macro_hist import score_series
        macro = HistMacro(score_series(macro_path, start="2017-01-01", end=END))
    settings = {"capital_ramp": "0.25,0.5,0.75,1", "max_drawdown_pct": 0.10, "max_total_drawdown_pct": stop,
                "max_hold_hours": 48.0, "max_margin_pct": m}
    res = asyncio.run(run_backtest(a, market, s.to_pydatetime(), e.to_pydatetime(), equity=EQUITY, macro=macro,
                                   overrides=leverage_overrides(a, lev, scale=True), settings_overrides=settings))
    daily = res.equity.resample("1D").last().dropna()
    name = tag(a, m, lev, stop)
    Path(out, name + ".json").write_text(json.dumps({
        "agent": a, "margin": m, "leverage": lev, "stop": stop, "seconds": round(time.time() - t0, 1),
        "trades": int(res.metrics.get("closed_trades") or 0),
        "dates": [d.strftime("%Y-%m-%d") for d in daily.index], "equity": [round(float(x), 2) for x in daily]}))
    return name


# ── Análisis ───────────────────────────────────────────────────────────
def stats(eq: pd.Series) -> dict:
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0.0
    dd = (eq / eq.cummax() - 1).min()
    return {"cagr": cagr * 100, "dd": dd * 100}


def load_runs(folder: Path) -> dict:
    runs = {}
    for f in folder.glob("SUB*.json"):
        d = json.loads(f.read_text())
        runs[f.stem] = (d, pd.Series(d["equity"], index=pd.to_datetime(d["dates"])))
    return runs


def portfolio(curves: dict, weights: dict) -> pd.Series:
    """Cartera con reparto fijo del capital entre subcuentas (cada curva normalizada a 1 al empezar)."""
    df = pd.DataFrame({k: v / v.iloc[0] for k, v in curves.items()}).ffill().dropna()
    return sum(df[k] * w for k, w in weights.items() if w > 0)


def analizar(folder: Path, max_dd: float) -> dict:
    runs = load_runs(folder)
    split = pd.Timestamp(SPLIT)
    rows = []
    for name, (d, eq) in runs.items():
        tr, te, full = eq[:split], eq[split - pd.Timedelta(days=1):], eq
        rows.append({"run": name, "agent": d["agent"], "margin": d["margin"], "lev": d["leverage"], "stop": d["stop"],
                     **{f"train_{k}": v for k, v in stats(tr).items()}, **{f"test_{k}": v for k, v in stats(te).items()},
                     **{f"full_{k}": v for k, v in stats(full).items()}, "trades": d["trades"]})
    tab = pd.DataFrame(rows).sort_values(["agent", "train_cagr"], ascending=[True, False])

    # 1) por agente: la configuración con más rentabilidad en entrenamiento cuya caída no pase de max_dd
    best = {}
    for a, g in tab.groupby("agent"):
        ok = g[g["train_dd"] >= -max_dd]
        pick = (ok if len(ok) else g).sort_values("train_cagr", ascending=False).iloc[0]
        best[a] = pick["run"]
    # 2) reparto entre agentes (pasos del 10%) que maximiza la rentabilidad de entrenamiento con la caída limitada
    curves_tr = {a: runs[r][1][:split] for a, r in best.items()}
    agents = sorted(best)
    steps = range(0, 11)
    top = None
    for combo in itertools.product(steps, repeat=len(agents)):
        if sum(combo) != 10:
            continue
        w = {a: c / 10 for a, c in zip(agents, combo, strict=True)}
        st = stats(portfolio(curves_tr, w))
        if st["dd"] >= -max_dd and (top is None or st["cagr"] > top[1]["cagr"]):
            top = (w, st)
    weights = top[0] if top else {a: 1 / len(agents) for a in agents}
    curves_all = {a: runs[r][1] for a, r in best.items()}
    port = portfolio(curves_all, weights)
    res = {"max_dd": max_dd, "best_config": best, "weights": weights,
           "train": stats(port[:split]), "test": stats(port[split - pd.Timedelta(days=1):]), "full": stats(port),
           "years": {}}
    ye = pd.concat([port.iloc[:1], port.resample("YE").last()])
    for y, v in (ye.pct_change().dropna() * 100).items():
        res["years"][int(y.year)] = round(float(v), 2)
    tab.to_csv(folder / "tabla_configuraciones.csv", index=False, sep=";", decimal=",")
    return res


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/history")
    p.add_argument("--macro", default="historico/macro")
    p.add_argument("--out", default="data/explorar")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--agents", default=",".join(AGENTS))
    p.add_argument("--analizar", default=None)
    p.add_argument("--max-dd", type=float, default=25.0)
    a = p.parse_args()
    if a.analizar:
        res = analizar(Path(a.analizar), a.max_dd)
        Path(a.analizar, f"resultado_dd{int(a.max_dd)}.json").write_text(json.dumps(res, indent=1, default=float))
        print(json.dumps(res, indent=1, default=float))
        return
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(ag, m, lev, st) for ag in a.agents.split(",") for m in MARGINS for lev in LEVERAGES for st in STOPS
            if not (out / (tag(ag, m, lev, st) + ".json")).exists() and not (ag == "SUB8" and lev != 3)]
    jobs.sort(key=lambda j: j[0] in ("SUB6",), reverse=True)  # los lentos primero
    print(f"{len(jobs)} ejecuciones", flush=True)
    with ProcessPoolExecutor(a.workers) as pool:
        futs = {pool.submit(run_one, *j, a.data, a.macro, str(out)): j for j in jobs}
        for f in as_completed(futs):
            try:
                print("✓", f.result(), flush=True)
            except Exception as e:  # noqa: BLE001
                print("✗", futs[f], e, flush=True)


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
