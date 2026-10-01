"""Exporta a CSV las métricas de cada ejecución del test de estrés (una fila por estrategia,
escenario, trayectoria y configuración), para poder revisarlas en Excel.

    python scripts/export_runs.py antes=data/stress_antes despues=data/stress_despues \\
        x7m20=data/stress_x7m20 x7m20r=data/stress_x7m20r sub5=data/stress_sub5 \\
        --out estrategia/datos/ejecuciones.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

COLUMNS = ["conjunto", "escenario", "semilla", "cuenta", "apalancamiento", "margen_max", "riesgo_escalado",
           "coste_x", "inicio", "fin", "btc_pct", "rentabilidad_pct", "cagr_pct", "max_drawdown_pct", "sharpe",
           "operaciones", "acierto_pct", "comisiones_usdt", "funding_usdt", "equity_final", "archivo"]


def rows(label: str, folder: Path):
    for f in sorted(folder.glob("*.json")):
        r = json.loads(f.read_text())
        m = r["metrics"]
        yield [label, r["scenario"], r["seed"], r["account"], r.get("leverage") or "", r.get("max_margin") or "",
               "sí" if r.get("scale_risk") else "", r["cost_mult"], r["start"], r["end"], r["btc_return_pct"],
               m.get("total_return_pct"), m.get("cagr_pct"), m.get("max_drawdown_pct"), m.get("sharpe"),
               m.get("closed_trades"), m.get("win_rate_pct"), m.get("fees_usdt"), m.get("funding_paid_usdt"),
               m.get("final_equity"), f.name]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("sets", nargs="+", metavar="ETIQUETA=CARPETA")
    p.add_argument("--out", default="estrategia/datos/ejecuciones.csv")
    a = p.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", newline="", encoding="utf-8-sig") as fh:  # BOM: Excel abre bien las tildes
        w = csv.writer(fh, delimiter=";")
        w.writerow(COLUMNS)
        for item in a.sets:
            label, folder = item.split("=", 1)
            for row in rows(label, Path(folder)):
                w.writerow(row)
                n += 1
    print(f"{out}: {n} ejecuciones")


if __name__ == "__main__":
    main()
