"""Cartera de cuentas con grids: elige con 2020–2023 y comprueba en 2024–2026.

    python scripts/analizar_grids.py data/grids/resultados.json --explorar data/explorar --max-dd 25

1. Por tipo de cuenta (scalper, recursive, short, dca), las 10 mejores configuraciones de entrenamiento
   (rentabilidad mensual media) cuya caída máxima de entrenamiento no pase de ``max_dd``.
2. Añade la cuenta macro-corto (SUB5) de ``scripts/explorar_rentabilidad.py`` con su mejor configuración
   de entrenamiento.
3. Selección hacia delante: añade cuentas (1 M$ cada una) mientras suba la rentabilidad mensual de
   entrenamiento de la cartera sin que su caída pase de ``max_dd``. Cada configuración se usa una vez.
4. Informe de entrenamiento, prueba y años, y la comparación con una o dos cuentas de cortos.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

START, SPLIT = "2020-10-01", "2024-01-01"
CAP = 1_000_000.0


def metrics(eq: pd.Series) -> dict:
    eq = eq.dropna()
    m = pd.concat([eq.iloc[:1], eq.resample("ME").last()]).pct_change().dropna() * 100
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    ratio = eq.iloc[-1] / eq.iloc[0]
    return {"mes_medio": round(float(m.mean()), 2), "mes_mediana": round(float(m.median()), 2),
            "meses_3pct": round(float((m >= 3).mean() * 100), 1), "peor_mes": round(float(m.min()), 2),
            "anual": round(float((ratio ** (1 / yrs) - 1) * 100), 2) if ratio > 0 else -100.0,
            "caida_max": round(float(((eq / eq.cummax()) - 1).min() * 100), 2)}


def label(spec: dict) -> str:
    c = spec.get("cfg", {})
    if spec["kind"] == "dca":
        return f"DCA BTC/ETH {spec['weekly'] * 52 / 1000:.0f}k$/año"
    if spec["kind"] == "macro_short":
        return f"SUB5 macro-corto {spec['name']}"
    sl = f"{c['sl'] * 100:.0f}%" if c.get("sl") else "sin stop"
    extra = (f"span {c['grid_span'] * 100:.1f}%" if c["mode"] == "neat"
             else f"reentrada {c['rentry_dist'] * 100:.0f}% ×{c['ddown_factor']}")
    regs = "+".join(spec.get("regs") or []) or ("+incert." if spec.get("unc") else "")
    return (f"{spec['kind']} {c['mode']} {c['side']} · {spec.get('select', '')} · {regs} · expo {c['wel']} · "
            f"stop {sl} · {extra} · venta {c['min_markup'] * 100:.1f}–{(c['min_markup'] + c['markup_range']) * 100:.1f}%")


def load(path: Path, explorar: Path | None) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    idx = pd.date_range(START, periods=len(rows[0]["equity_daily"]), freq="1D", tz="UTC")
    out = []
    for r in rows:
        eq = pd.Series(r["equity_daily"], index=idx).ffill()
        out.append({"spec": r["spec"], "eq": eq, "stats": {k: r.get(k) for k in ("cycles", "stops", "pauses", "halted")}})
    if explorar and explorar.exists():
        for f in explorar.glob("SUB5_*.json"):
            d = json.loads(f.read_text())
            s = pd.Series(d["equity"], index=pd.to_datetime(d["dates"]).tz_localize("UTC"))
            s = (s / s.iloc[0] * CAP).reindex(idx).ffill().bfill()
            out.append({"spec": {"kind": "macro_short", "name": f.stem}, "eq": s, "stats": {}})
    return out


def train_ok(eq: pd.Series, max_dd: float) -> bool:
    return metrics(eq[:SPLIT])["caida_max"] >= -max_dd


def daily_ret(eq: pd.Series) -> pd.Series:
    return eq.pct_change().fillna(0.0)


def calmar(r: pd.Series) -> float:
    eq = (1 + r).cumprod()
    yrs = len(r) / 365.25
    ann = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1
    dd = ((eq / eq.cummax()) - 1).min()
    return ann / abs(dd) if dd < 0 else ann * 100


def build(cands: list[dict], max_accounts: int, n_short: int | None) -> list[dict]:
    """Selección hacia delante por Calmar de entrenamiento (rentabilidad anual / caída máxima) de la cartera
    con el mismo capital en cada cuenta: busca cuentas que no caigan a la vez."""
    chosen: list[dict] = []
    best_score = -np.inf
    while len(chosen) < max_accounts:
        best = None
        for c in cands:
            if c in chosen:
                continue
            if n_short is not None and c["spec"]["kind"] == "short" and                     sum(x["spec"]["kind"] == "short" for x in chosen) >= n_short:
                continue
            r = sum(daily_ret(x["eq"][:SPLIT]) for x in chosen + [c]) / (len(chosen) + 1)
            s = calmar(r)
            if best is None or s > best[1]:
                best = (c, s)
        if best is None or best[1] <= best_score:
            break
        chosen.append(best[0])
        best_score = best[1]
    return chosen


def leverage_for(r_train: pd.Series, max_dd: float) -> float:
    """Factor por el que se puede multiplicar la exposición de todas las cuentas para que la caída de
    entrenamiento llegue a ``max_dd`` (aproximación lineal: k × la rentabilidad diaria)."""
    lo, hi = 0.1, 20.0
    for _ in range(40):
        k = (lo + hi) / 2
        eq = (1 + k * r_train).cumprod()
        dd = ((eq / eq.cummax()) - 1).min() * 100
        lo, hi = (k, hi) if dd > -max_dd else (lo, k)
    return round(lo, 2)


def report(chosen: list[dict], max_dd: float) -> dict:
    n = len(chosen)
    r = sum(daily_ret(c["eq"]) for c in chosen) / n
    k = leverage_for(r[:SPLIT], max_dd)
    scaled = (1 + k * r).cumprod() * CAP * n
    port = (1 + r).cumprod() * CAP * n
    years = pd.concat([scaled.iloc[:1], scaled.resample("YE").last()]).pct_change().dropna() * 100
    return {"cuentas": [{"cuenta": label(c["spec"]), "entrenamiento": metrics(c["eq"][:SPLIT]),
                         "prueba": metrics(c["eq"][SPLIT:]), **c["stats"]} for c in chosen],
            "cartera": {"n_cuentas": n, "capital": n * CAP,
                        "sin_escalar": {"entrenamiento": metrics(port[:SPLIT]), "prueba": metrics(port[SPLIT:])},
                        "factor_exposicion": k,
                        "escalada": {"entrenamiento": metrics(scaled[:SPLIT]), "prueba": metrics(scaled[SPLIT:]),
                                     "todo": metrics(scaled),
                                     "por_año": {int(x.year): round(float(v), 2) for x, v in years.items()}}}}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("resultados", type=Path)
    p.add_argument("--explorar", type=Path, default=Path("data/explorar"))
    p.add_argument("--max-dd", type=float, default=25.0)
    p.add_argument("--max-accounts", type=int, default=11)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    rows = load(a.resultados, a.explorar)
    by_kind: dict[str, list] = {}
    for r in rows:
        tr = r["eq"][:SPLIT]
        alive = tr.iloc[-60:].nunique() > 1          # descarta las que ya estaban paradas al final del entrenamiento
        if train_ok(r["eq"], a.max_dd) and alive:
            by_kind.setdefault(r["spec"]["kind"], []).append(r)
    top = {}
    for k, lst in by_kind.items():
        lst.sort(key=lambda r: calmar(daily_ret(r["eq"][:SPLIT])), reverse=True)
        top[k] = lst[:10]
    out = {"max_dd": a.max_dd, "mejores_por_tipo": {
        k: [{"cuenta": label(r["spec"]), "entrenamiento": metrics(r["eq"][:SPLIT]), "prueba": metrics(r["eq"][SPLIT:])}
            for r in v[:5]] for k, v in top.items()}}
    cands = [r for v in top.values() for r in v]
    for n_short in (1, 2):
        chosen = build(cands, a.max_accounts, n_short)
        out[f"cartera_{n_short}_short"] = report(chosen, a.max_dd) if chosen else None
    text = json.dumps(out, ensure_ascii=False, indent=1, default=float)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
