"""Estrés test del montaje completo de subcuentas de los agentes, año a año y en los cracks.

    python scripts/estres_subcuentas.py --grids "C:/PROYECTOS IA/kriptty/configs/grids" --out "C:/PROYECTOS IA/kriptty/configs/grids"

Subcuentas (las de ``config/agentes.toml``):

1. Momentum · KRIPTTY ALL IN 3.0 a ×4.
2. Cortos · RECURSIVE KRIPTTY (lado corto) a ×3.
3. Vasos comunicantes en observación (recursive de referencia, ×1).
4. Bull run fuerte · KRIPTTY ALL IN 3.0 a ×6, solo con BTC +20% en 30 días.
5. Bull run fuerte · KRIPTTY SCALPER (la mutación) a ×1, solo con BTC +20% en 30 días.
6. Memecoins · corto tras el pico (desde dic-2023, que es cuando hay datos de memecoins). Se prueban la
   exposición que propone el usuario (0,08 por operación a 7x) y cierres a las 12, 24 y 48 horas.
7. DCA BTC/ETH: 100.000 $ al año.

La cartera combina las subcuentas con el reparto de ``REPARTO``. Cracks: LUNA, 3AC/Celsius, FTX, agosto de 2024
y el 10 de octubre de 2025.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meme_hype as mh  # noqa: E402
import puntuar_grids as pg  # noqa: E402

GRIDS = [
    # nombre, huella, cuenta del agente, multiplicador de exposición, solo bull run fuerte
    ("Momentum · ALL IN 3.0 ×4", "6658c442", "recursive_momentum", 4, False),
    ("Cortos · RECURSIVE KRIPTTY ×3", "f34bf687", "cortos_vasos", 3, False),
    ("Vasos comunicantes ×1 (observación)", "REF recursive 2% · dd 1.0", "recursive_vasos", 1, False),
    ("Bull run · ALL IN 3.0 ×6", "6658c442", "recursive_momentum", 6, True),
    ("Bull run · KRIPTTY SCALPER (mutación) ×1", "c3113918", "recursive_momentum", 1, True),
]
REPARTO = {"Momentum · ALL IN 3.0 ×4": 0.25, "Cortos · RECURSIVE KRIPTTY ×3": 0.20,
           "Vasos comunicantes ×1 (observación)": 0.10, "Bull run · ALL IN 3.0 ×6": 0.15,
           "Bull run · KRIPTTY SCALPER (mutación) ×1": 0.05, "Memecoins · corto tras el pico": 0.05,
           "DCA BTC/ETH": 0.20}
CRACKS = {"LUNA (may-2022)": ("2022-05-05", "2022-05-15"), "3AC/Celsius (jun-2022)": ("2022-06-10", "2022-06-20"),
          "FTX (nov-2022)": ("2022-11-06", "2022-11-12"), "Agosto 2024": ("2024-08-01", "2024-08-08"),
          "10-oct-2025": ("2025-10-09", "2025-10-12")}
MEME_REGLA = {"lado": "corto", "R": 1.0, "V": None, "D": 0.1, "SL": 0.2, "salida": "tp", "nivel": 0.2, "nuevas": False}


def _bull(feat: dict) -> pd.Series:
    btc = feat["BTC"]
    return ((btc.ret30 > 0.20) & (btc.close > btc.ema50)).shift(1).fillna(False).astype(bool)


def _init():
    pg._init()
    pg._G["bull"] = _bull(pg.sg.daily_features(pg._G["bars"]))


def _grid(args) -> tuple[str, pd.Series]:
    nombre, huella, cuenta, mult, solo_bull = args
    cfgs = {c["huella"]: c for c in pg.cargar_configs(Path(pg._G["dir"])) + pg.referencias()}
    s = [x for x in pg.specs([cfgs[huella]]) if x["cuenta"] == cuenta][0]
    s["wel_reg"] = {k: v * mult for k, v in s["wel_reg"].items()}
    reg = pg._G["reg"]
    if solo_bull:
        s.update(regs=("alcista",), unc=False)
        b = pg._G["bull"].reindex(reg.index).fillna(False)
        reg = pd.Series(np.where(b, "alcista", "lateral"), index=reg.index)
    r = pg.sg.run_account_v2(s, pg._G["idx"], pg._G["bars"], pg._G["fund"], pg._G["ranks"], reg)
    return nombre, pd.Series(r["equity"], index=pg._G["idx"]).resample("1D").last() / pg.sg.CAPITAL


def _dca(_=None) -> tuple[str, pd.Series]:
    r = pg.sg.run_dca({"weekly": 100_000 / 52}, pg._G["idx"], pg._G["bars"])
    return "DCA BTC/ETH", pd.Series(r["equity"], index=pg._G["idx"]).resample("1D").last() / pg.sg.CAPITAL


def _btc(_=None) -> tuple[str, pd.Series]:
    c = pg._G["bars"]["BTC"]["close"].ffill().bfill()
    return "BTC (referencia)", (c / c.iloc[0]).resample("1D").last()


def _init_grid(directorio: str):
    _init()
    pg._G["dir"] = directorio


def memes(datos: dict, nocional: float, horas: int, sl: float) -> pd.Series:
    """Cuenta de memecoins con la regla de corto tras el pico: ``nocional`` = exposición por operación y
    ``horas`` = cierre por tiempo (las señales siguen midiéndose en 24 h)."""
    mh.MAX_HORAS, mh.MARGEN, mh.APALANC = horas, nocional / 7.0, 7.0
    ops = mh.candidatas(datos, {**MEME_REGLA, "SL": sl})
    curva, _ = mh.cartera(ops)
    eq = (curva / mh.CAPITAL).resample("1D").last().ffill()
    return eq


def anual(eq: pd.Series) -> dict[str, float]:
    a = eq.resample("YE").last()
    a = pd.concat([eq.iloc[:1], a]).pct_change().dropna() * 100
    return {str(k.year): round(float(v), 1) for k, v in a.items()}


def caida(eq: pd.Series) -> float:
    return round(float((eq / eq.cummax() - 1).min() * 100), 1)


def en_crack(eq: pd.Series, ini: str, fin: str) -> float | None:
    tramo = eq[(eq.index >= pd.Timestamp(ini, tz="UTC")) & (eq.index <= pd.Timestamp(fin, tz="UTC"))]
    if len(tramo) < 2:
        return None
    return round(float((tramo.min() / tramo.iloc[0] - 1) * 100), 1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--grids", required=True, help="carpeta con json/ de las configuraciones de Kriptty")
    p.add_argument("--memes", default="data/memes")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    out = Path(a.out)
    with ProcessPoolExecutor(6, initializer=_init_grid, initargs=(str(Path(a.grids) / "json"),)) as pool:
        futs = [pool.submit(_grid, g) for g in GRIDS] + [pool.submit(_dca), pool.submit(_btc)]
        curvas = dict(f.result() for f in futs)

    # memecoins: la propuesta del usuario (0,08 a 7x) con cierres a 12, 24 y 48 h; y la del backtest (0,40)
    variantes = {}
    datos = {c: mh.indicadores(df) for c, df in mh.cargar(Path(a.memes)).items()}
    for nocional in (0.08, 0.20, 0.40):
        for horas in (12, 24, 48):
            for sl in (0.12, 0.20):
                eq = memes(datos, nocional, horas, sl)
                variantes[(nocional, horas, sl)] = eq
    tabla_memes = pd.DataFrame([{"exposición por operación": n, "cierre (h)": h, "stop": sl, "total %": round(float((e.iloc[-1] - 1) * 100), 1),
                                 "caída máx %": caida(e), **anual(e)} for (n, h, sl), e in variantes.items()])
    elegida = variantes[(0.08, 24, 0.20)]          # la propuesta del usuario con el stop que aguanta la volatilidad
    curvas["Memecoins · corto tras el pico"] = elegida

    idx = curvas["Momentum · ALL IN 3.0 ×4"].index
    alineadas = {k: v.reindex(idx).ffill().fillna(1.0) for k, v in curvas.items()}
    cartera = sum(REPARTO[k] * alineadas[k] for k in REPARTO)
    alineadas["CARTERA (reparto propuesto)"] = cartera
    pd.DataFrame(alineadas).to_csv(out / "estres_curvas_diarias.csv", encoding="utf-8")

    filas = []
    for k, e in alineadas.items():
        filas.append({"subcuenta": k, "peso": REPARTO.get(k, 1.0), **anual(e), "total %": round(float((e.iloc[-1] - 1) * 100), 1),
                      "anual medio %": round(float((e.iloc[-1] ** (365.25 / (e.index[-1] - e.index[0]).days) - 1) * 100), 1),
                      "caída máx %": caida(e), **{c: en_crack(e, *v) for c, v in CRACKS.items()}})
    tabla = pd.DataFrame(filas)
    tabla.to_csv(out / "estres_subcuentas.csv", index=False, encoding="utf-8")
    tabla_memes.to_csv(out / "estres_memes.csv", index=False, encoding="utf-8")
    (out / "estres_subcuentas.json").write_text(json.dumps({"reparto": REPARTO, "cracks": CRACKS}, ensure_ascii=False), encoding="utf-8")
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 30)
    print(tabla.to_string(index=False))
    print()
    print(tabla_memes.to_string(index=False))


if __name__ == "__main__":
    main()
