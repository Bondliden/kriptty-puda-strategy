"""Cuenta de «hype» de memecoins: entrar cuando una memecoin explota (precio y volumen) y cerrar como mucho a
las 24 horas. Backtest con velas de 1 hora de Bitget (``scripts/descargar_memes.py``).

    python scripts/descargar_memes.py --out data/memes
    python scripts/meme_hype.py --dir data/memes --out data/meme_hype

Dos ideas, cada una con varias variantes:

* **largo** (subirse a la ola): la moneda sube ``R`` en 24 h con un volumen ``V`` veces su media diaria de la
  semana anterior; o es **nueva** (menos de 3 días cotizando), ya sube ``R`` desde su apertura y mueve más de
  20 M$ en 24 h. Se entra a mercado en la hora siguiente.
* **corto tras el pico**: hubo una subida de ``R`` en 24 h y el precio ya cae ``D`` desde el máximo de 24 h.

Salidas: stop ``SL``, objetivo ``TP`` o stop dinámico ``TS`` desde el mejor precio, y **siempre a las 24 h**.
Cuenta de 100.000 $, 3 operaciones a la vez como máximo, 20% de margen por operación a 2x (40% de nocional) y
comisión taker en la entrada y en la salida. Sin funding (en pleno hype puede costar 0,1–0,3% al día).
Sesgo de supervivencia: solo están las memecoins que Bitget lista hoy (GOAT, NEIRO o GIGA ya no).
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

TAKER = 0.0006
CAPITAL = 100_000.0
MARGEN, APALANC, MAX_ABIERTAS = 0.20, 2.0, 3
HORAS = 24                 # ventana de las señales (subida y pico en 24 h)
MAX_HORAS = 24             # cierre por tiempo de cada operación
CORTE = pd.Timestamp("2025-07-01", tz="UTC")          # ajuste antes, prueba después
NUEVA_H, NUEVA_VOL = 72, 20e6


def cargar(carpeta: Path) -> dict[str, pd.DataFrame]:
    out = {}
    for f in sorted(carpeta.glob("*_1h.csv")):
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(df) < 24 * 10:
            continue
        df = df[~df.index.duplicated()].sort_index()
        df = df.reindex(pd.date_range(df.index[0], df.index[-1], freq="1h"))
        df["close"] = df["close"].ffill()
        for k in ("open", "high", "low"):
            df[k] = df[k].fillna(df["close"])
        df["quote_vol"] = df["quote_vol"].fillna(0.0)
        out[f.name.split("_1h")[0]] = df
    return out


def indicadores(df: pd.DataFrame) -> dict[str, np.ndarray]:
    c = df["close"]
    ret24 = c / c.shift(HORAS) - 1
    vol24 = df["quote_vol"].rolling(HORAS, min_periods=1).sum()          # en las nuevas, desde la primera vela
    base = df["quote_vol"].shift(HORAS).rolling(24 * 7, min_periods=72).mean() * 24
    return {"o": df["open"].to_numpy(), "h": df["high"].to_numpy(), "l": df["low"].to_numpy(), "c": c.to_numpy(),
            "ret24": ret24.to_numpy(), "vratio": (vol24 / base).to_numpy(), "vol24": vol24.to_numpy(),
            "max24": df["high"].rolling(HORAS).max().to_numpy(), "pico": ret24.rolling(HORAS).max().to_numpy(),
            "desde_inicio": (c / df["open"].iloc[0] - 1).to_numpy(), "t": df.index}


def salida(x: dict, i0: int, lado: int, sl: float, modo: str, nivel: float | None) -> tuple[int, float]:
    """Simula una operación que entra a la apertura de la vela ``i0``. Devuelve (vela de salida, precio)."""
    o, h, lo, c = x["o"], x["h"], x["l"], x["c"]
    p0 = o[i0]
    stop = p0 * (1 - lado * sl)
    tp = p0 * (1 + lado * nivel) if modo == "tp" else None
    mejor = p0
    fin = min(i0 + MAX_HORAS, len(c)) - 1
    for j in range(i0, fin + 1):
        adverso, favor = (lo[j], h[j]) if lado > 0 else (h[j], lo[j])
        nivel_ts = mejor * (1 - lado * nivel) if modo == "trail" else None
        corte = stop if nivel_ts is None else (max(stop, nivel_ts) if lado > 0 else min(stop, nivel_ts))
        if (adverso - corte) * lado <= 0:                       # primero el lado malo (conservador)
            abre_peor = (o[j] - corte) * lado <= 0
            return j, (o[j] if abre_peor and j > i0 else corte)
        if tp is not None and (favor - tp) * lado >= 0:
            return j, tp
        mejor = max(mejor, favor) if lado > 0 else min(mejor, favor)
    return fin, c[fin]


def candidatas(datos: dict[str, dict], cfg: dict) -> list[tuple]:
    """Operaciones posibles de cada moneda (con 24 h de espera entre una y otra en la misma moneda)."""
    out = []
    for coin, x in datos.items():
        n = len(x["c"])
        i = 6                      # las nuevas se pueden operar desde la 6.ª hora; la regla normal necesita 4 días de volumen
        libre_desde = 0
        while i < n - 1:
            if i < libre_desde:
                i += 1
                continue
            ok = False
            if cfg["lado"] == "largo":
                ok = bool(x["ret24"][i] >= cfg["R"] and x["vratio"][i] >= cfg["V"])          # NaN → False
                if cfg["nuevas"] and not ok and i < NUEVA_H:
                    ok = x["desde_inicio"][i] >= cfg["R"] and x["vol24"][i] >= NUEVA_VOL
            else:
                ok = x["pico"][i] >= cfg["R"] and x["c"][i] <= x["max24"][i] * (1 - cfg["D"])
            if ok:
                lado = 1 if cfg["lado"] == "largo" else -1
                j, px = salida(x, i + 1, lado, cfg["SL"], cfg["salida"], cfg["nivel"])
                ret = lado * (px / x["o"][i + 1] - 1) - 2 * TAKER
                out.append((x["t"][i + 1], x["t"][j], coin, ret))
                libre_desde = j + HORAS
                i = j + 1
            else:
                i += 1
    return sorted(out)


def cartera(ops: list[tuple]) -> tuple[pd.Series, list[tuple]]:
    """Reparte el capital: 3 a la vez, 20% de margen a 2x por operación, interés compuesto."""
    eq = CAPITAL
    abiertas: list[tuple] = []          # (salida, nocional, ret, moneda, entrada)
    hechas, curva = [], [(ops[0][0] if ops else pd.Timestamp("2024-01-01", tz="UTC"), CAPITAL)]
    for ent, sal, coin, ret in ops:
        for a in sorted([a for a in abiertas if a[0] <= ent]):
            eq += a[1] * a[2]
            curva.append((a[0], eq))
            abiertas.remove(a)
        if len(abiertas) >= MAX_ABIERTAS or any(a[3] == coin for a in abiertas):
            continue
        nocional = eq * MARGEN * APALANC
        abiertas.append((sal, nocional, ret, coin, ent))
        hechas.append((ent, sal, coin, ret, nocional * ret))
    for a in sorted(abiertas):
        eq += a[1] * a[2]
        curva.append((a[0], eq))
    s = pd.Series([v for _, v in curva], index=pd.DatetimeIndex([t for t, _ in curva])).sort_index()
    return s[~s.index.duplicated(keep="last")], hechas


def metricas(curva: pd.Series, hechas: list[tuple], desde=None, hasta=None) -> dict:
    ops = [h for h in hechas if (desde is None or h[0] >= desde) and (hasta is None or h[0] < hasta)]
    c = curva[(curva.index >= desde) if desde is not None else slice(None)]
    if hasta is not None:
        c = c[c.index < hasta]
    if len(ops) == 0 or len(c) < 2:
        return {"ops": 0, "acierto": 0.0, "media_op": 0.0, "rent": 0.0, "dd": 0.0}
    r = np.array([h[3] for h in ops])
    dd = float((c / c.cummax() - 1).min() * 100)
    return {"ops": len(ops), "acierto": round(float((r > 0).mean() * 100), 1), "media_op": round(float(r.mean() * 100), 2),
            "rent": round(float((c.iloc[-1] / c.iloc[0] - 1) * 100), 1), "dd": round(dd, 1)}


def configuraciones() -> list[dict]:
    out = []
    salidas = [("tp", 0.2), ("tp", 0.4), ("trail", 0.1), ("trail", 0.2), ("tiempo", None)]
    for R, V, sl, (modo, nivel), nuevas in itertools.product((0.15, 0.25, 0.4), (2, 4), (0.08, 0.15), salidas, (False, True)):
        out.append({"lado": "largo", "R": R, "V": V, "D": None, "SL": sl, "salida": modo, "nivel": nivel, "nuevas": nuevas})
    for R, D, sl, (modo, nivel) in itertools.product((0.3, 0.5, 1.0), (0.1, 0.2), (0.1, 0.2), salidas):
        out.append({"lado": "corto", "R": R, "V": None, "D": D, "SL": sl, "salida": modo, "nivel": nivel, "nuevas": False})
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="data/memes")
    p.add_argument("--out", default="data/meme_hype")
    a = p.parse_args()
    datos = {c: indicadores(df) for c, df in cargar(Path(a.dir)).items()}
    print(f"{len(datos)} memecoins", flush=True)
    filas, detalle = [], {}
    for k, cfg in enumerate(configuraciones()):
        ops = candidatas(datos, cfg)
        curva, hechas = cartera(ops)
        nombre = (f"{cfg['lado']} R{cfg['R']}" + (f" V{cfg['V']}" if cfg["V"] else f" D{cfg['D']}") + f" SL{cfg['SL']} "
                  + f"{cfg['salida']}{'' if cfg['nivel'] is None else cfg['nivel']}" + (" +nuevas" if cfg["nuevas"] else ""))
        anual = {}
        for y in sorted({h[0].year for h in hechas}):
            m = metricas(curva, hechas, pd.Timestamp(f"{y}-01-01", tz="UTC"), pd.Timestamp(f"{y + 1}-01-01", tz="UTC"))
            anual[f"a{y}"] = m["rent"]
        filas.append({"config": nombre, **cfg, **{f"ajuste_{k2}": v for k2, v in metricas(curva, hechas, None, CORTE).items()},
                      **{f"prueba_{k2}": v for k2, v in metricas(curva, hechas, CORTE, None).items()},
                      **{f"total_{k2}": v for k2, v in metricas(curva, hechas).items()}, **anual})
        detalle[nombre] = [(str(h[0]), str(h[1]), h[2], round(h[3] * 100, 1), round(h[4])) for h in hechas]
        if k % 40 == 0:
            print(f"{k} configuraciones", flush=True)
    df = pd.DataFrame(filas)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "resultados.csv", index=False, encoding="utf-8")
    (out / "operaciones.json").write_text(json.dumps(detalle, ensure_ascii=False), encoding="utf-8")
    pd.set_option("display.width", 260)
    cols = ["config", "ajuste_ops", "ajuste_acierto", "ajuste_media_op", "ajuste_rent", "ajuste_dd",
            "prueba_ops", "prueba_acierto", "prueba_media_op", "prueba_rent", "prueba_dd", "total_rent", "total_dd"]
    cols += [c for c in df.columns if c.startswith("a20")]
    df["robusta"] = (df.ajuste_rent > 0) & (df.prueba_rent > 0)
    print(df.sort_values(["robusta", "prueba_rent"], ascending=False)[cols].head(20).to_string(index=False))
    print("\nrobustas (positivas en ajuste y prueba):", int(df.robusta.sum()), "de", len(df))


if __name__ == "__main__":
    main()
