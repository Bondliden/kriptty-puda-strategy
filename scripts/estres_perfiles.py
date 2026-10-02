"""Tres perfiles de riesgo con las mismas subcuentas: conservador (el del informe), equilibrado y dinámico.

    python scripts/estres_perfiles.py --grids "C:/PROYECTOS IA/kriptty/configs/grids" --out "C:/PROYECTOS IA/kriptty/configs/grids"

Cambian la exposición de cada subcuenta (multiplicador sobre la tabla de los agentes) y los pesos. Las reglas de
riesgo son las mismas: stops, pausa de la subcuenta al −10% y parada al −25%.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import estres_subcuentas as es  # noqa: E402
import meme_hype as mh  # noqa: E402

# subcuenta: (huella, cuenta del agente, solo bull run)
BASE = {"mom": ("6658c442", "recursive_momentum", False), "short": ("f34bf687", "cortos_vasos", False),
        "rot": ("REF recursive 2% · dd 1.0", "recursive_vasos", False), "bull": ("6658c442", "recursive_momentum", True),
        "scalp": ("c3113918", "recursive_momentum", True)}
PERFILES = {
    "conservador": {"mult": {"mom": 4, "short": 3, "rot": 1, "bull": 6, "scalp": 1}, "meme": 0.08,
                    "pesos": {"mom": .25, "short": .20, "rot": .10, "bull": .15, "scalp": .05, "meme": .05, "dca": .20}},
    "equilibrado": {"mult": {"mom": 6, "short": 4, "rot": 2, "bull": 6, "scalp": 1}, "meme": 0.20,
                    "pesos": {"mom": .30, "short": .15, "rot": .05, "bull": .25, "scalp": .05, "meme": .05, "dca": .15}},
    "dinamico": {"mult": {"mom": 8, "short": 5, "rot": 2, "bull": 8, "scalp": 1}, "meme": 0.40,
                 "pesos": {"mom": .30, "short": .15, "rot": .05, "bull": .30, "scalp": .05, "meme": .05, "dca": .10}},
}


def _tarea(args):
    clave, mult = args
    huella, cuenta, solo_bull = BASE[clave]
    nombre, eq = es._grid((f"{clave}×{mult}", huella, cuenta, mult, solo_bull))
    return nombre, eq


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--grids", required=True)
    p.add_argument("--memes", default="data/memes")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    trabajos = sorted({(k, m) for perf in PERFILES.values() for k, m in perf["mult"].items()})
    with ProcessPoolExecutor(8, initializer=es._init_grid, initargs=(str(Path(a.grids) / "json"),)) as pool:
        curvas = dict(pool.map(_tarea, trabajos))
        curvas["dca"] = pool.submit(es._dca).result()[1]
        curvas["btc"] = pool.submit(es._btc).result()[1]
    datos = {c: mh.indicadores(df) for c, df in mh.cargar(Path(a.memes)).items()}
    for n in {perf["meme"] for perf in PERFILES.values()}:
        curvas[f"meme×{n}"] = es.memes(datos, n, 24, 0.20)
    idx = curvas["dca"].index
    al = {k: v.reindex(idx).ffill().fillna(1.0) for k, v in curvas.items()}

    filas, salida = [], {}
    for nombre, perf in PERFILES.items():
        partes = {k: al[f"{k}×{perf['mult'][k]}"] for k in BASE} | {"meme": al[f"meme×{perf['meme']}"], "dca": al["dca"]}
        # pesos reajustados cada 1 de enero
        valor, trozos = 1.0, []
        for _, tramo in pd.DataFrame(partes).groupby(idx.year):
            ant = pd.DataFrame(partes)[idx < tramo.index[0]]
            base = ant.iloc[-1] if len(ant) else tramo.iloc[0]
            seg = sum(perf["pesos"][k] * tramo[k] / base[k] for k in partes) * valor
            trozos.append(seg)
            valor = float(seg.iloc[-1])
        eq = pd.concat(trozos)
        an = es.anual(eq)
        dias = (eq.index[-1] - eq.index[0]).days
        filas.append({"perfil": nombre, **an, "media 2021-2025": round(sum(an[str(y)] for y in range(2021, 2026)) / 5, 1),
                      "anual medio": round(float((eq.iloc[-1] ** (365.25 / dias) - 1) * 100), 1),
                      "total": round(float((eq.iloc[-1] - 1) * 100), 1), "caída máx": es.caida(eq),
                      **{c: es.en_crack(eq, *v) for c, v in es.CRACKS.items()}})
        salida[nombre] = eq
        for k in partes:
            salida[f"{nombre}:{k}"] = partes[k]
    tabla = pd.DataFrame(filas)
    out = Path(a.out)
    tabla.to_csv(out / "perfiles.csv", index=False, encoding="utf-8")
    pd.DataFrame(salida).to_csv(out / "perfiles_curvas.csv", encoding="utf-8")
    (out / "perfiles.json").write_text(json.dumps(PERFILES, ensure_ascii=False), encoding="utf-8")
    pd.set_option("display.width", 250)
    print(tabla.to_string(index=False))
    # subcuentas por separado con los multiplicadores nuevos
    sub = pd.DataFrame([{"curva": k, **es.anual(v), "caída máx": es.caida(v)} for k, v in al.items()])
    print(sub.to_string(index=False))


if __name__ == "__main__":
    main()
