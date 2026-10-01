"""Inserta los datos del test de estrés en estrategia/estrategia(.en).html.

    python scripts/embed_plan.py --stress data/stress_report.json --monthly data/monthly_report.json \\
        --rec data/recomendacion.json --hedge data/hedge_report.json --runs estrategia/datos/ejecuciones.csv

- ``--stress``: ``kriptty-stress --report antes=… despues=…`` (curvas de la cartera antes/después).
- ``--monthly``: ``scripts/monthly_returns.py`` (rentabilidad mensual por configuración).
- ``--rec``: recomendación por idioma ({"es": {...}, "en": {...}}).
- ``--hedge``: ``scripts/hedge_short.py`` (apalancamiento de la cuenta short).
- ``--runs``: ``scripts/export_runs.py`` (todas las ejecuciones, para comprobarlas en la presentación).
- ``--agents``: ``scripts/plan_agents.py`` (la propuesta: un agente por estrategia).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

PAGES = Path(__file__).resolve().parent.parent / "estrategia"


def num(v: str) -> float | None:
    return float(v) if v not in ("", "None") else None


def runs_table(path: Path) -> dict:
    cols = ["set", "scenario", "seed", "account", "leverage", "btc", "ret", "dd", "sharpe", "trades"]
    rows = []
    with path.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            label = r["conjunto"] + ("2" if float(r["coste_x"]) > 1 else "")
            rows.append([label, r["escenario"], int(r["semilla"]), r["cuenta"], num(r["apalancamiento"]),
                         round(float(r["btc_pct"]), 1), round(float(r["rentabilidad_pct"]), 2),
                         round(float(r["max_drawdown_pct"]), 2),
                         None if num(r["sharpe"]) is None else round(float(r["sharpe"]), 2), num(r["operaciones"])])
    return {"cols": cols, "rows": rows}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stress", type=Path, required=True)
    p.add_argument("--monthly", type=Path, required=True)
    p.add_argument("--rec", type=Path)
    p.add_argument("--hedge", type=Path)
    p.add_argument("--runs", type=Path)
    p.add_argument("--agents", type=Path)
    a = p.parse_args()
    stress = json.loads(a.stress.read_text())
    monthly = json.loads(a.monthly.read_text())
    rec = json.loads(a.rec.read_text()) if a.rec else {}
    data = {
        "stress": {"scenarios": stress["scenarios"],
                   "portfolio": [x for x in stress["portfolio"] if x["cost_mult"] == 1]},
        "monthly": {"summary": monthly["summary"],
                    "paths": [{k: x[k] for k in ("set", "portfolio", "scenario", "seed", "monthly")}
                              for x in monthly["paths"]]},
    }
    if a.hedge:
        h = json.loads(a.hedge.read_text())
        crash = {}
        for g in h["groups"]:
            v = [x["crash_btc"] for x in h["paths"] if x["group"] == g and x["hedge"] == "sin"]
            crash[g] = {"btc": f"{max(v):.0f}% … {min(v):.0f}%".replace("-", "−")}
        data["hedge"] = {"summary": h["summary"], "beta": h["beta"], "groups": h["groups"], "levs": h["levs"],
                         "crash": crash}
    if a.runs:
        data["runs"] = runs_table(a.runs)
    if a.agents:
        ag = json.loads(a.agents.read_text())
        data["agents"] = {k: ag[k] for k in ("agents", "portfolio", "crash", "corr")}
    for name, lang in (("estrategia.html", "es"), ("estrategia.en.html", "en")):
        page = PAGES / name
        if not page.exists():
            continue
        payload = json.dumps({**data, "rec": rec.get(lang, {})}, ensure_ascii=False, separators=(",", ":"))
        block = f'<script id="plan-data">window.PLAN = {payload};</script>'
        html, n = re.subn(r'<script id="plan-data">.*?</script>', lambda _, b=block: b, page.read_text(), flags=re.S)
        if n != 1:
            sys.exit(f"{name}: no se encontró el bloque plan-data")
        page.write_text(html)
        print(f"{name}: datos insertados ({len(payload) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
