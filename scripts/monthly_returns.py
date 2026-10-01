"""Rentabilidad mensual de las carteras en el test de estrés, por nivel de apalancamiento.

    python scripts/monthly_returns.py nativo=data/stress_despues x5=data/stress_x5 x7=data/stress_x7 \\
        --out data/monthly_report.json

Para cada carpeta de resultados (``kriptty-stress``) y cada trayectoria suma el equity diario de
las estrategias de cada cartera (mismo capital en cada una) y calcula: rentabilidad mensual
(mediana, media, % de meses ≥ 5%, % de meses ≤ −10%, peor mes), drawdown máximo, CAGR y si
alguna subcuenta tocó la parada dura del 40%.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

PORTFOLIOS = {
    "nucleo": ["SUB5", "SUB6", "SUB9", "SUB10"],
    "completa": ["SUB2", "SUB5", "SUB6", "SUB9", "SUB10", "SUB11"],
}
HARD_STOP = -40.0


def monthly(dates: list[str], equity: np.ndarray) -> list[float]:
    last: dict[str, float] = {}
    for d, e in zip(dates, equity, strict=True):
        last[d[:7]] = float(e)
    vals = list(last.values())
    first = float(equity[0])
    rets, prev = [], first
    for v in vals:
        rets.append((v / prev - 1) * 100)
        prev = v
    return rets


def analyse(label: str, folder: Path) -> list[dict]:
    runs = defaultdict(dict)
    for f in folder.glob("*_c1*.json"):
        r = json.loads(f.read_text())
        if r["cost_mult"] != 1 or r["account"] in ("SUB4", "SUB7", "SUB8"):
            continue
        runs[(r["scenario"], r["seed"])][r["account"]] = r
    out = []
    for (sc, seed), accs in sorted(runs.items()):
        for name, members in PORTFOLIOS.items():
            if not all(a in accs for a in members):
                continue
            rs = [accs[a] for a in members]
            n = min(len(r["equity_daily"]) for r in rs)
            dates = rs[0]["dates"][:n]
            total = np.sum([np.asarray(r["equity_daily"][:n]) for r in rs], axis=0)
            mrets = monthly(dates, total)
            dd = float((total / np.maximum.accumulate(total) - 1).min() * 100)
            years = n / 365.25
            out.append({
                "set": label, "portfolio": name, "scenario": sc, "seed": seed,
                "monthly": [round(x, 2) for x in mrets],
                "median_month": round(float(np.median(mrets)), 2),
                "mean_month": round(float(np.mean(mrets)), 2),
                "share_ge_5": round(float(np.mean(np.asarray(mrets) >= 5)) * 100, 1),
                "share_le_m10": round(float(np.mean(np.asarray(mrets) <= -10)) * 100, 1),
                "worst_month": round(float(min(mrets)), 2),
                "best_month": round(float(max(mrets)), 2),
                "max_dd": round(dd, 2),
                "cagr": round(((total[-1] / total[0]) ** (1 / years) - 1) * 100, 2),
                "total_return": round((total[-1] / total[0] - 1) * 100, 2),
                "hard_stops": sum(1 for r in rs if r["metrics"]["max_drawdown_pct"] <= HARD_STOP),
                "accounts": {r["account"]: {"ret": round(r["metrics"]["total_return_pct"], 1),
                                            "dd": round(r["metrics"]["max_drawdown_pct"], 1)} for r in rs},
                "curve": [round(float(x) / float(total[0]) * 100, 2) for x in total[::7]] + [
                    round(float(total[-1]) / float(total[0]) * 100, 2)],
            })
    return out


def summary(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["set"], r["portfolio"])].append(r)
    out = []
    for (s, p), rs in groups.items():
        months = np.concatenate([r["monthly"] for r in rs])
        out.append({"set": s, "portfolio": p, "paths": len(rs),
                    "median_month": round(float(np.median(months)), 2),
                    "mean_month": round(float(np.mean(months)), 2),
                    "share_ge_3": round(float(np.mean(months >= 3)) * 100, 1),
                    "share_ge_5": round(float(np.mean(months >= 5)) * 100, 1),
                    "share_le_m10": round(float(np.mean(months <= -10)) * 100, 1),
                    "worst_month": round(float(months.min()), 2),
                    "median_cagr": round(float(np.median([r["cagr"] for r in rs])), 2),
                    "worst_dd": round(min(r["max_dd"] for r in rs), 2),
                    "median_dd": round(float(np.median([r["max_dd"] for r in rs])), 2),
                    "paths_with_hard_stop": sum(1 for r in rs if r["hard_stops"] > 0),
                    "paths_losing": sum(1 for r in rs if r["total_return"] < 0)})
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("sets", nargs="+", metavar="ETIQUETA=CARPETA")
    p.add_argument("--out", default="data/monthly_report.json")
    a = p.parse_args()
    rows = []
    for item in a.sets:
        label, folder = item.split("=", 1)
        rows += analyse(label, Path(folder))
    report = {"paths": rows, "summary": summary(rows), "portfolios": PORTFOLIOS}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, separators=(",", ":")))
    for s in sorted(report["summary"], key=lambda x: (x["portfolio"], x["set"])):
        print(s)


if __name__ == "__main__":
    main()
