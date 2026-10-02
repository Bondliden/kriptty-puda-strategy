"""Puntúa las configuraciones de grid de Kriptty (Passivbot v5: neat y recursive) con el histórico real y
las reglas de los agentes.

    python scripts/puntuar_grids.py --dir "C:/PROYECTOS IA/kriptty/configs/grids/json" \
        --out "C:/PROYECTOS IA/kriptty/configs/grids" --workers 16

Las configuraciones se bajan de Kriptty con la API de administración (``Kriptty.grids()`` / ``grid(id)``)
y **no** se guardan en este repositorio: son de los usuarios de Kriptty.

Cada lado distinto (largo o corto) se prueba en la cuenta de agente que le corresponde, con las mismas
reglas que en vivo:

- **largos**: en las tres cuentas largas (vasos comunicantes, momentum y scalper lateral);
- **cortos**: en la cuenta de cortos;
- en todas, 3 monedas a la vez elegidas cada día y la exposición de la tabla de los agentes. Kriptty
  pasa la exposición a Passivbot con ``-lw``/``-sw``: la del archivo no se usa;
- en todas, apalancamiento 7x, gracefully stop y luego stop si sigue en contra, stop de catástrofe del
  15%, y pausa y parada de la subcuenta;
- memecoins fuera del universo, como en vivo.

No se simulan ``auto_unstuck`` (cierre parcial con pérdida cuando se atasca) ni la entrada respecto a la
EMA (``initial_eprice_ema_dist``): se entra a mercado.

Ajuste 2020-10 → 2023-12 y prueba 2024-01 → 2026-08, igual que el resto del backtest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "scripts"))
import sistema_grids as sg  # noqa: E402

from kriptty.agentes.mercado import MEMECOINS  # noqa: E402
from kriptty.agentes.orquestador import base_coin  # noqa: E402
from kriptty.backtest.gridsim import GridCfg  # noqa: E402

EXPO_LARGOS = {"lateral": 0.08, "alcista": 0.07, "incertidumbre": 0.04, "bajista": 0.04}
EXPO_CORTOS = {"bajista": 0.08}
CUENTAS = {
    # nombre: (lado, criterio, regímenes, incertidumbre, graceful_sl)
    "recursive_vasos": ("long", "lag_long", ("alcista",), True, 0.08),
    "recursive_momentum": ("long", "momentum", ("alcista",), True, 0.08),
    "scalper_lateral": ("long", "scalper", ("lateral",), False, 0.05),
    "cortos_vasos": ("short", "lag_short", ("bajista",), False, 0.08),
}


def a_gridcfg(s: dict, lado: str) -> GridCfg:
    """Un lado de una configuración de Passivbot v5 → parámetros del simulador."""
    comun = dict(side=lado, sl=0.15, leverage=7.0, min_markup=float(s["min_markup"]),
                 markup_range=float(s["markup_range"]), n_close=max(1, int(s["n_close_orders"])))
    if "ddown_factor" in s:
        return GridCfg(mode="recursive", initial_qty_pct=float(s["initial_qty_pct"]),
                       ddown_factor=float(s["ddown_factor"]), rentry_dist=float(s["rentry_pprice_dist"]),
                       rentry_weight=float(s.get("rentry_pprice_dist_wallet_exposure_weighting", 0)), **comun)
    return GridCfg(mode="neat", grid_span=float(s["grid_span"]), n_entries=max(1, int(s["max_n_entry_orders"])),
                   eqty_exp_base=float(s["eqty_exp_base"]), eprice_exp_base=float(s.get("eprice_exp_base", 1)), **comun)


def huella(s: dict) -> str:
    return hashlib.md5(json.dumps({k: v for k, v in s.items() if k != "enabled"}, sort_keys=True).encode()).hexdigest()[:8]


def cargar_configs(carpeta: Path) -> list[dict]:
    """Lados distintos, con la lista de grids y bots de Kriptty que los usan."""
    unicos: dict[tuple[str, str], dict] = {}
    for f in sorted(carpeta.glob("*.json")):
        g = json.loads(f.read_text(encoding="utf-8"))
        for lado in ("long", "short"):
            s = g["config"][lado]
            k = (lado, huella(s))
            u = unicos.setdefault(k, {"lado": lado, "huella": k[1], "params": s, "grids": [], "nombres": set(),
                                      "usuarios": set(), "bots": 0, "activado": 0})
            u["grids"].append(g["id"])
            u["nombres"].add(g["name"])
            u["usuarios"].add(str(g["user"]))
            u["bots"] += int(g["bots"])
            u["activado"] += int(bool(s.get("enabled")))
    out = []
    for u in unicos.values():
        u["nombres"], u["usuarios"] = sorted(u["nombres"]), sorted(u["usuarios"])
        out.append(u)
    return out


def referencias() -> list[dict]:
    """Las configuraciones con las que se hizo el backtest del sistema (para comparar)."""
    ref = {
        "REF recursive 2% · dd 1.0": ("long", GridCfg(mode="recursive", rentry_dist=0.02, ddown_factor=1.0, sl=0.15)),
        "REF neat scalper 2,5% · 6": ("long", GridCfg(mode="neat", grid_span=0.025, sl=0.15)),
        "REF cortos neat 3% · 6": ("short", GridCfg(mode="neat", side="short", grid_span=0.03, rentry_dist=0.015, sl=0.15)),
    }
    return [{"lado": lado, "huella": nombre, "cfg": asdict(cfg), "grids": [], "nombres": [nombre], "usuarios": [],
             "bots": 0, "activado": 0} for nombre, (lado, cfg) in ref.items()]


def specs(configs: list[dict]) -> list[dict]:
    out = []
    for c in configs:
        cfg = c.get("cfg") or asdict(a_gridcfg(c["params"], c["lado"]))
        for cuenta, (lado, criterio, regs, unc, gsl) in CUENTAS.items():
            if lado != c["lado"]:
                continue
            out.append({"v2": True, "kind": "short" if lado == "short" else "recursive", "select": criterio,
                        "regs": regs, "unc": unc, "slots": 3, "rank": 0, "hard_dd": 0.25, "g_sl": gsl,
                        "wel_reg": EXPO_CORTOS if lado == "short" else EXPO_LARGOS, "cfg": cfg,
                        "cuenta": cuenta, "huella": c["huella"]})
    return out


_G: dict = {}


def _init():
    idx, bars, fund = sg.load_all()
    feat = sg.daily_features(bars)
    reg = sg.regimes(feat["BTC"])
    fuera = ("BTC", "ETH") + tuple(c for c in feat if base_coin(c) in MEMECOINS)
    _G.update(idx=idx, bars=bars, fund=fund, reg=reg, ranks=sg.rankings(feat, exclude=fuera))


def _work(spec: dict) -> dict:
    r = sg.run_account_v2(spec, _G["idx"], _G["bars"], _G["fund"], _G["ranks"], _G["reg"])
    eq = pd.Series(r["equity"], index=_G["idx"])
    anual = eq.resample("YE").last()
    anual = (pd.concat([eq.iloc[:1], anual]).pct_change().dropna() * 100).round(1)
    return {"cuenta": spec["cuenta"], "huella": spec["huella"], **{k: v for k, v in r.items() if k != "equity"},
            **sg.split_metrics(r["equity"], _G["idx"]), "anual": {str(k.year): float(v) for k, v in anual.items()}}


def nota(r: dict) -> float:
    """Calmar de la prueba (CAGR / caída máxima), penalizado si el ajuste fue negativo."""
    t, a = r["test"], r["train"]
    calmar = t["cagr"] / max(abs(t["max_dd"]), 1.0)
    return round(calmar - (0.5 if a["cagr"] < 0 else 0.0) - (0.5 if t["cagr"] < 0 else 0.0), 3)


def informe(configs: list[dict], res: list[dict]) -> tuple[str, pd.DataFrame]:
    por = {c["huella"]: c for c in configs}
    filas = []
    for r in res:
        c = por[r["huella"]]
        p = c.get("params") or {}
        modo = (c.get("cfg") or {}).get("mode") or ("recursive" if "ddown_factor" in p else "neat")
        if modo == "neat" and p:
            resumen = f"neat {p['grid_span']:.3g} · {p['max_n_entry_orders']} entradas · cierre {p['min_markup']:.3g}+{p['markup_range']:.3g} ×{p['n_close_orders']}"
        elif p:
            resumen = (f"recursive ini {p['initial_qty_pct']:.3g} · dd {p['ddown_factor']:.3g} · reentrada {p['rentry_pprice_dist']:.3g}"
                       f" · peso {p.get('rentry_pprice_dist_wallet_exposure_weighting', 0):.3g} · cierre {p['min_markup']:.3g}+{p['markup_range']:.3g} ×{p['n_close_orders']}")
        else:
            resumen = c["nombres"][0]
        filas.append({
            "cuenta": r["cuenta"], "lado": c["lado"], "huella": r["huella"], "nombre": " / ".join(c["nombres"])[:60],
            "configuracion": resumen, "grids": " ".join(map(str, c["grids"][:8])) + (" …" if len(c["grids"]) > 8 else ""),
            "bots_kriptty": c["bots"], "usuarios": ", ".join(c["usuarios"]),
            "auto_unstuck": bool(p and float(p.get("auto_unstuck_wallet_exposure_threshold") or 0) > 0),
            "ajuste_mes": r["train"]["monthly_avg"], "ajuste_cagr": r["train"]["cagr"], "ajuste_dd": r["train"]["max_dd"],
            "prueba_mes": r["test"]["monthly_avg"], "prueba_cagr": r["test"]["cagr"], "prueba_dd": r["test"]["max_dd"],
            "total_cagr": r["full"]["cagr"], "total_dd": r["full"]["max_dd"], "ciclos": r["cycles"], "stops": r["stops"],
            "liquidaciones": r["liq"], "parada": r.get("halted", False), "nota": nota(r),
            **{f"a{y}": v for y, v in r["anual"].items()},
        })
    df = pd.DataFrame(filas).sort_values(["cuenta", "nota"], ascending=[True, False])
    lin = ["# Configuraciones de grid de Kriptty · puntuación con el histórico real", "",
           f"{len({c['huella'] for c in configs if c['grids']})} configuraciones distintas (de "
           f"{len({g for c in configs for g in c['grids']})} grids de la base de datos), probadas en la cuenta de agente que les "
           "corresponde con las reglas de los agentes: 3 monedas elegidas cada día, exposición por régimen "
           "(0,08 lateral · 0,07 alcista · 0,04 incertidumbre; cortos 0,08 en bajista), 7x, gracefully stop "
           "+ stop si sigue en contra, stop de catástrofe 15%, pausa −10% y parada −25% de la subcuenta, sin memecoins.",
           "", "**Nota** = rentabilidad anual de la prueba / caída máxima de la prueba (Calmar), con −0,5 por cada "
           "periodo en pérdidas. Ajuste 2020-10 → 2023-12 · prueba 2024-01 → 2026-08. «REF» = configuraciones "
           "del backtest del sistema.", "",
           "Límites: velas de 1 hora (los scalpers reales cierran más ciclos), sin `auto_unstuck` ni entrada "
           "por EMA; la exposición la pone Kriptty (`-lw`), no el archivo.", ""]
    nombres = {"recursive_vasos": "Recursive · vasos comunicantes (largos, alcista e incertidumbre)",
               "recursive_momentum": "Recursive · momentum (largos, alcista e incertidumbre)",
               "scalper_lateral": "Scalper · mercado lateral (largos)",
               "cortos_vasos": "Cortos · vasos comunicantes (bajista)"}
    for cuenta, g in df.groupby("cuenta", sort=False):
        lin += [f"## {nombres.get(cuenta, cuenta)}", "",
                "| # | Configuración | Grids (bots) | Ajuste %/mes · caída | Prueba %/mes · caída | Anual total | Nota |",
                "|---|---|---|---|---|---|---|"]
        for i, (_, f) in enumerate(g.head(12).iterrows(), 1):
            grids = f"{f['grids']} ({f['bots_kriptty']})" if f["grids"] else "—"
            extra = " · unstuck" if f["auto_unstuck"] else ""
            lin.append(f"| {i} | {f['nombre']}: {f['configuracion']}{extra} | {grids} | {f['ajuste_mes']:+.2f} · {f['ajuste_dd']:.1f}% | "
                       f"{f['prueba_mes']:+.2f} · {f['prueba_dd']:.1f}% | {f['total_cagr']:+.1f}% | {f['nota']:+.2f} |")
        lin.append("")
    return "\n".join(lin) + "\n", df


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True, help="carpeta con los JSON de grids bajados de Kriptty")
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=16)
    a = p.parse_args()
    configs = cargar_configs(Path(a.dir)) + referencias()
    todo = specs(configs)
    print(f"{len(configs)} configuraciones · {len(todo)} simulaciones", flush=True)
    res = []
    with ProcessPoolExecutor(a.workers, initializer=_init) as pool:
        futs = [pool.submit(_work, s) for s in todo]
        for k, f in enumerate(as_completed(futs), 1):
            try:
                res.append(f.result())
            except Exception as e:  # noqa: BLE001
                print("✗", e, flush=True)
            if k % 10 == 0:
                print(f"{k}/{len(todo)}", flush=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    texto, df = informe(configs, res)
    (out / "RANKING.md").write_text(texto, encoding="utf-8")
    df.to_csv(out / "ranking.csv", index=False, encoding="utf-8")
    (out / "resultados.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
