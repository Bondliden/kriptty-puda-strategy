"""¿Cuánta exposición hace falta para llegar al 20% anual con caída máxima del 25%?

Toma las mejores configuraciones de ``puntuar_grids.py`` (``ranking.csv``) y las repite con la exposición
de la tabla de los agentes multiplicada (×1 … ×6). Después combina las cuentas en una cartera con el mismo
capital en cada una.

    python scripts/escalar_grids.py --dir "C:/PROYECTOS IA/kriptty/configs/grids/json" \
        --out "C:/PROYECTOS IA/kriptty/configs/grids" --top 2
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puntuar_grids as pg  # noqa: E402

MULT = (1, 2, 3, 4, 5, 6)
CUENTAS_CARTERA = ("recursive_momentum", "recursive_vasos", "cortos_vasos")


def _work(spec: dict) -> dict:
    r = pg.sg.run_account_v2(spec, pg._G["idx"], pg._G["bars"], pg._G["fund"], pg._G["ranks"], pg._G["reg"])
    eq = pd.Series(r["equity"], index=pg._G["idx"]).resample("1D").last()
    return {"cuenta": spec["cuenta"], "huella": spec["huella"], "mult": spec["mult"], "liq": r["liq"],
            "stops": r["stops"], "parada": r.get("halted", False), **pg.sg.split_metrics(r["equity"], pg._G["idx"]),
            "equity": eq.round(0).tolist(), "dias": [d.strftime("%Y-%m-%d") for d in eq.index]}


def metricas(eq: pd.Series) -> dict:
    eq = eq.copy()
    eq.index = pd.to_datetime(eq.index, utc=True)
    sp = pd.Timestamp(pg.sg.SPLIT, tz="UTC")
    return {"total": pg.sg.metrics(eq), "ajuste": pg.sg.metrics(eq[:sp]), "prueba": pg.sg.metrics(eq[sp:])}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--top", type=int, default=2, help="mejores configuraciones por cuenta")
    p.add_argument("--workers", type=int, default=16)
    a = p.parse_args()
    out = Path(a.out)
    rk = pd.read_csv(out / "ranking.csv")
    elegidas = (rk[rk.cuenta.isin(CUENTAS_CARTERA)].sort_values("nota", ascending=False)
                .groupby("cuenta").head(a.top)[["cuenta", "huella", "nombre"]])
    configs = {c["huella"]: c for c in pg.cargar_configs(Path(a.dir)) + pg.referencias()}
    todo = []
    for (_, f), m in itertools.product(elegidas.iterrows(), MULT):
        base = [s for s in pg.specs([configs[f.huella]]) if s["cuenta"] == f.cuenta][0]
        base["wel_reg"] = {k: v * m for k, v in base["wel_reg"].items()}
        base["mult"] = m
        todo.append(base)
    print(f"{len(todo)} simulaciones", flush=True)
    res = []
    with ProcessPoolExecutor(a.workers, initializer=pg._init) as pool:
        for k, fut in enumerate(as_completed([pool.submit(_work, s) for s in todo]), 1):
            res.append(fut.result())
            if k % 10 == 0:
                print(f"{k}/{len(todo)}", flush=True)
    nombres = dict(zip(elegidas.huella, elegidas.nombre, strict=True))
    filas = [{"cuenta": r["cuenta"], "config": nombres[r["huella"]][:40], "huella": r["huella"], "mult": r["mult"],
              "ajuste_cagr": r["train"]["cagr"], "ajuste_dd": r["train"]["max_dd"], "prueba_cagr": r["test"]["cagr"],
              "prueba_dd": r["test"]["max_dd"], "total_cagr": r["full"]["cagr"], "total_dd": r["full"]["max_dd"],
              "liq": r["liq"], "parada": r["parada"]} for r in res]
    tabla = pd.DataFrame(filas).sort_values(["cuenta", "huella", "mult"])

    # cartera: la mejor configuración de cada cuenta, mismo capital en cada una, todas las combinaciones de multiplicador
    curvas = {(r["cuenta"], r["huella"], r["mult"]): pd.Series(r["equity"], index=r["dias"]) for r in res}
    mejor = elegidas.groupby("cuenta").head(1).set_index("cuenta").huella.to_dict()
    cartera = []
    for ms in itertools.product(MULT, repeat=len(mejor)):
        partes = [curvas[(c, h, m)] for (c, h), m in zip(mejor.items(), ms, strict=True)]
        eq = sum(partes) / len(partes)
        mt = metricas(eq)
        cartera.append({**{f"x_{c}": m for c, m in zip(mejor, ms, strict=True)},
                        "total_cagr": mt["total"]["cagr"], "total_dd": mt["total"]["max_dd"],
                        "ajuste_cagr": mt["ajuste"]["cagr"], "ajuste_dd": mt["ajuste"]["max_dd"],
                        "prueba_cagr": mt["prueba"]["cagr"], "prueba_dd": mt["prueba"]["max_dd"],
                        "prueba_mes": mt["prueba"]["monthly_avg"]})
    cartera = pd.DataFrame(cartera)
    ok = cartera[(cartera.ajuste_dd >= -25) & (cartera.prueba_dd >= -25)].sort_values("prueba_cagr", ascending=False)
    tabla.to_csv(out / "escalado.csv", index=False, encoding="utf-8")
    cartera.to_csv(out / "cartera_escalada.csv", index=False, encoding="utf-8")
    (out / "escalado.json").write_text(json.dumps({"mejor": mejor}, ensure_ascii=False), encoding="utf-8")
    pd.set_option("display.width", 220)
    print(tabla.to_string(index=False))
    print("\nCartera (caída ≤ 25% en ajuste y prueba), mejores por rentabilidad de la prueba:")
    print(ok.head(15).to_string(index=False))


if __name__ == "__main__":
    main()
