"""Apalancamiento de la cuenta short (SUB5) como cobertura del resto de cuentas.

    kriptty-stress --strategies SUB5 --leverage L --max-margin 0.2 --scale-risk --out data/stress_sub5   (L = 2 3 5 7 10)
    python scripts/hedge_short.py --others data/stress_x7m20 --sub8 data/stress_despues \\
        --sweep data/stress_sub5 --out data/hedge_report.json

Para cada trayectoria suma el equity de las demás cuentas (mismo capital en cada una) y le añade
SUB5 con cada apalancamiento, sin SUB5 (efectivo) y con la SUB5 original. Mide el drawdown de la
cartera, el peor mes, la rentabilidad y el resultado en la peor caída de 30 días de BTC (lo que
pierden las demás frente a lo que gana el short). También calcula la beta a la baja de cada
cuenta (cuánto se mueve en los días en que BTC cae más de un 3%).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from kriptty.backtest import stress

LEVS = [2, 3, 5, 7, 10]
GROUPS = {"completa": ["SUB2", "SUB6", "SUB8", "SUB9", "SUB10", "SUB11"], "nucleo": ["SUB6", "SUB8", "SUB9", "SUB10"]}
SCENARIOS = ["ciclo", "bear", "lateral"]


def load(path: Path) -> pd.Series:
    r = json.loads(path.read_text())
    return pd.Series(r["equity_daily"], index=pd.to_datetime(r["dates"]).tz_localize("UTC"))


def months(tot: pd.Series) -> np.ndarray:
    m = pd.concat([tot.iloc[:1], tot.resample("ME").last()])
    return (m.pct_change().dropna() * 100).to_numpy()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--others", type=Path, required=True, help="carpeta x7m20 de las demás cuentas")
    p.add_argument("--sub8", type=Path, required=True, help="carpeta con SUB8 (spot, sin apalancar)")
    p.add_argument("--sweep", type=Path, required=True, help="carpeta con SUB5 a cada apalancamiento")
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--out", default="data/hedge_report.json")
    a = p.parse_args()

    def other(sc: str, seed: int, acc: str) -> pd.Series:
        if acc == "SUB8":
            return load(a.sub8 / f"{sc}_s{seed}_SUB8_c1.json")
        return load(a.others / f"{sc}_s{seed}_{acc}_c1_x7_m0.2.json")

    rows, betas = [], {acc: [] for acc in sorted({x for g in GROUPS.values() for x in g})}
    for sc in SCENARIOS:
        for seed in range(a.seeds):
            btc = stress.generate(sc, seed=seed).candles["BTC/USDT:USDT"]["close"].resample("1D").last()
            for acc in betas:
                e = other(sc, seed, acc)
                d = pd.DataFrame({"e": e.pct_change(), "b": btc.reindex(e.index).pct_change()}).dropna()
                dn = d[d.b < -0.03]
                betas[acc].append(float(dn.e.mean() / dn.b.mean()))
            hedges = {"sin": None, "antes": load(a.others / f"{sc}_s{seed}_SUB5_c1_x7_m0.2.json")}
            for lev in LEVS:
                hedges[f"x{lev}"] = load(a.sweep / f"{sc}_s{seed}_SUB5_c1_x{lev}_m0.2_r.json")
            for g, accs in GROUPS.items():
                oth = pd.concat([other(sc, seed, x) for x in accs], axis=1).dropna().sum(axis=1)
                b = btc.reindex(oth.index)
                b30 = b / b.shift(30) - 1
                end = b30.idxmin()
                start = end - pd.Timedelta(days=30)
                for k, h in hedges.items():
                    h = pd.Series(10_000.0, index=oth.index) if h is None else h.reindex(oth.index).ffill()
                    tot = oth + h
                    cap = float(tot.iloc[0])
                    mr = months(tot)
                    rows.append({"group": g, "hedge": k, "scenario": sc, "seed": seed,
                                 "max_dd": round(float((tot / tot.cummax() - 1).min() * 100), 2),
                                 "ret": round(float(tot.iloc[-1] / cap * 100 - 100), 2),
                                 "median_month": round(float(np.median(mr)), 2),
                                 "worst_month": round(float(mr.min()), 2),
                                 "crash_btc": round(float(b30.min() * 100), 1),
                                 "crash_others": round(float((oth[end] - oth[start]) / cap * 100), 2),
                                 "crash_short": round(float((h[end] - h[start]) / cap * 100), 2),
                                 "short_dd": round(float((h / h.cummax() - 1).min() * 100), 2),
                                 "short_ret": round(float(h.iloc[-1] / 100 - 100), 2)})
    df = pd.DataFrame(rows)
    agg = df.groupby(["group", "hedge"]).agg(
        max_dd_med=("max_dd", "median"), max_dd_worst=("max_dd", "min"), ret_med=("ret", "median"),
        month_med=("median_month", "median"), worst_month=("worst_month", "min"),
        crash_others=("crash_others", "median"), crash_short=("crash_short", "median"),
        short_dd_worst=("short_dd", "min"), short_ret_med=("short_ret", "median")).round(2).reset_index()
    order = ["sin", "antes"] + [f"x{lev}" for lev in LEVS]
    for g in GROUPS:
        print("==", g)
        print(agg[agg.group == g].set_index("hedge").reindex(order).drop(columns="group").to_string())
    beta = {acc: {"median": round(float(np.median(v)), 3), "p90": round(float(np.percentile(v, 90)), 3)}
            for acc, v in betas.items()}
    print("beta a la baja:", beta)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"paths": rows, "summary": agg.to_dict("records"), "beta": beta,
                                       "levs": LEVS, "groups": GROUPS}, separators=(",", ":")))


if __name__ == "__main__":
    main()
