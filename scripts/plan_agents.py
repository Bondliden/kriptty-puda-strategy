"""Análisis de la propuesta «un agente por estrategia» (1 M$ por subcuenta, 20% en juego, rampa).

    kriptty-stress --strategies SUB5,SUB6,SUB8,SUB9,SUB10 --leverage 3 --max-margin 0.2 --scale-risk \\
        --set capital_ramp=0.25,0.5,0.75,1 --set max_drawdown_pct=0.1 --set max_total_drawdown_pct=0.2 \\
        --tag mi --out data/stress_mi
    python scripts/plan_agents.py data/stress_mi --out estrategia/datos/mi_estrategia.json

Por agente: peor drawdown, rentabilidad, meses en positivo y lo que hizo en los peores meses de BTC.
Para el conjunto: drawdown, peor mes (en % y en dólares con 1 M$ por subcuenta) y la correlación
mensual entre agentes («si pierde uno, gana otro»).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from kriptty.backtest import stress

CAPITAL = 1_000_000  # por subcuenta


def monthly(s: pd.Series) -> pd.Series:
    m = pd.concat([s.iloc[:1], s.resample("ME").last()])
    return m.pct_change().dropna() * 100


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    p.add_argument("--out", default="estrategia/datos/mi_estrategia.json")
    a = p.parse_args()
    runs: dict = defaultdict(dict)
    for f in sorted(a.folder.glob("*.json")):
        r = json.loads(f.read_text())
        if r["cost_mult"] == 1:
            runs[(r["scenario"], r["seed"])][r["account"]] = r
    agents = sorted({acc for v in runs.values() for acc in v}, key=lambda x: int(x[3:]))
    per = defaultdict(lambda: defaultdict(list))
    mrets: dict[str, list] = defaultdict(list)
    port_rows, crash_rows = [], []
    for (sc, seed), accs in sorted(runs.items()):
        if not all(x in accs for x in agents):
            continue
        btc = stress.generate(sc, seed=seed).candles["BTC/USDT:USDT"]["close"].resample("1D").last()
        eq = {x: pd.Series(accs[x]["equity_daily"], index=pd.to_datetime(accs[x]["dates"]).tz_localize("UTC"))
              for x in agents}
        df = pd.DataFrame(eq).dropna()
        bm = monthly(btc.reindex(df.index))
        am = {x: monthly(df[x]) for x in agents}
        worst = bm.nsmallest(3).index  # los 3 peores meses de BTC de la trayectoria
        for x in agents:
            m = accs[x]["metrics"]
            per[x]["dd"].append(m["max_drawdown_pct"])
            per[x]["ret"].append(m["total_return_pct"])
            per[x]["pos"].append(float((am[x] > 0).mean() * 100))
            per[x]["crash"].append(float(am[x].reindex(worst).mean()))
            mrets[x].extend(am[x].tolist())
        tot = df.sum(axis=1)
        tm = monthly(tot)
        port_rows.append({"scenario": sc, "seed": seed,
                          "max_dd": float((tot / tot.cummax() - 1).min() * 100),
                          "ret": float(tot.iloc[-1] / tot.iloc[0] * 100 - 100),
                          "worst_month": float(tm.min()), "median_month": float(tm.median()),
                          "pos": float((tm > 0).mean() * 100), "months": [round(v, 2) for v in tm]})
        crash_rows.append({"scenario": sc, "seed": seed, "btc": [round(float(v), 1) for v in bm.reindex(worst)],
                           "agents": {x: [round(float(v), 2) for v in am[x].reindex(worst)] for x in agents},
                           "total": [round(float(v), 2) for v in tm.reindex(worst)]})
    med = lambda v: round(float(np.median(v)), 2)  # noqa: E731
    agents_out = {x: {"worst_dd": round(min(per[x]["dd"]), 2), "median_dd": med(per[x]["dd"]),
                      "median_ret": med(per[x]["ret"]), "months_pos": med(per[x]["pos"]),
                      "crash_month": med(per[x]["crash"]),
                      "worst_dd_usd": round(-min(per[x]["dd"]) / 100 * CAPITAL)} for x in agents}
    n = min(len(v) for v in mrets.values())
    corr = pd.DataFrame({x: mrets[x][:n] for x in agents}).corr().round(2)
    allm = np.concatenate([r["months"] for r in port_rows])
    port = {"accounts": agents, "capital": CAPITAL * len(agents), "at_play": CAPITAL * len(agents) * 0.2,
            "worst_dd": round(min(r["max_dd"] for r in port_rows), 2), "median_dd": med([r["max_dd"] for r in port_rows]),
            "worst_month": round(float(allm.min()), 2), "median_month": med(allm),
            "mean_month": round(float(allm.mean()), 2), "months_pos": round(float((allm > 0).mean() * 100), 1),
            "median_ret": med([r["ret"] for r in port_rows]), "paths": len(port_rows),
            "paths_losing": sum(r["ret"] < 0 for r in port_rows)}
    port["worst_dd_usd"] = round(-port["worst_dd"] / 100 * port["capital"])
    port["worst_month_usd"] = round(-port["worst_month"] / 100 * port["capital"])
    out = {"agents": agents_out, "portfolio": port, "paths": port_rows, "crash": crash_rows,
           "corr": {"labels": agents, "matrix": corr.values.tolist()}}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(json.dumps(agents_out, indent=1))
    print(json.dumps(port, indent=1))
    print(corr.to_string())


if __name__ == "__main__":
    main()
