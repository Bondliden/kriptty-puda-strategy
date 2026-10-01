"""Análisis del backtest real (``scripts/backtest_real.py``): cartera combinada con 1 M$ por
subcuenta, rentabilidad mes a mes y por año natural, y qué hizo cada agente en cada crisis.

    python scripts/analizar_real.py data/real --data data/history --out estrategia/datos/backtest_real.json

Crisis: las conocidas (China mayo 2021, el bear de 2022, LUNA, FTX, agosto 2024, 10 de octubre de
2025) y, además, las 5 peores caídas de 30 días de BTC del periodo que no se solapen con ellas.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

CAPITAL = 1_000_000  # por subcuenta
KNOWN = [("China y Elon (may–jul 2021)", "2021-05-10", "2021-07-20"),
         ("Bear de 2022 (nov 2021 – nov 2022)", "2021-11-10", "2022-11-21"),
         ("LUNA y Celsius/3AC (may–jun 2022)", "2022-05-05", "2022-06-18"),
         ("FTX (nov 2022)", "2022-11-06", "2022-11-21"),
         ("Crash del yen (ago 2024)", "2024-07-29", "2024-08-07"),
         ("Liquidaciones del 10 de octubre de 2025", "2025-10-06", "2025-10-17")]


def monthly(s: pd.Series) -> pd.Series:
    m = pd.concat([s.iloc[:1], s.resample("ME").last()])
    return m.pct_change().dropna() * 100


def worst_drops(btc: pd.Series, n: int = 5, days: int = 30, taken: list[tuple] | None = None) -> list[tuple]:
    r = btc / btc.shift(days) - 1
    out, used = [], [(pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")) for _, a, b in (taken or [])]
    for end, v in r.dropna().sort_values().items():
        start = end - pd.Timedelta(days=days)
        if any(start <= b and end >= a for a, b in used):
            continue
        used.append((start, end))
        out.append((f"Caída de BTC del {v * 100:.0f}% ({start:%d/%m/%Y} – {end:%d/%m/%Y})",
                    f"{start:%Y-%m-%d}", f"{end:%Y-%m-%d}"))
        if len(out) == n:
            break
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    p.add_argument("--data", type=Path, default=Path("data/history"))
    p.add_argument("--out", default="estrategia/datos/backtest_real.json")
    a = p.parse_args()
    runs = {json.loads(f.read_text())["account"]: json.loads(f.read_text()) for f in sorted(a.folder.glob("SUB*.json"))}
    agents = sorted(runs, key=lambda x: int(x[3:]))
    eq = pd.DataFrame({x: pd.Series(runs[x]["equity_daily"], index=pd.to_datetime(runs[x]["dates"]).tz_localize("UTC"))
                       for x in agents}).dropna()
    eq = eq / eq.iloc[0]  # 1 = capital inicial de cada subcuenta
    btc = pd.read_csv(a.data / "BTC-USDT_USDT__1h.csv", index_col=0, parse_dates=True)["close"]
    btc = btc.resample("1D").last().reindex(eq.index).ffill()
    tot = eq.sum(axis=1) / len(agents)
    crises = KNOWN + worst_drops(btc, taken=KNOWN)

    def window(s: pd.Series, a0: str, b0: str) -> float:
        w = s[a0:b0]
        return round(float(w.iloc[-1] / w.iloc[0] * 100 - 100), 2) if len(w) > 1 else float("nan")

    def dd(s: pd.Series) -> float:
        return round(float((s / s.cummax() - 1).min() * 100), 2)

    years = sorted({d.year for d in eq.index})
    yearly = {}
    for y in years:
        w = tot[str(y)]
        prev = tot[:f"{y - 1}-12-31"]
        base = prev.iloc[-1] if len(prev) else w.iloc[0]
        yearly[str(y)] = {"total": round(float(w.iloc[-1] / base * 100 - 100), 2),
                          "btc": round(float(btc[str(y)].iloc[-1] / (btc[:f"{y - 1}-12-31"].iloc[-1]
                                                                     if len(prev) else btc[str(y)].iloc[0]) * 100 - 100), 1),
                          "agents": {x: round(float(eq[x][str(y)].iloc[-1] / (eq[x][:f"{y - 1}-12-31"].iloc[-1]
                                                                               if len(prev) else eq[x][str(y)].iloc[0]) * 100 - 100), 2)
                                     for x in agents}}
    tm = monthly(tot)
    out = {
        "period": [str(eq.index[0].date()), str(eq.index[-1].date())],
        "agents": {x: {"ret": round(float(eq[x].iloc[-1] * 100 - 100), 2), "dd": dd(eq[x]),
                       "dd_usd": round(-dd(eq[x]) / 100 * CAPITAL),
                       "months_pos": round(float((monthly(eq[x]) > 0).mean() * 100), 1),
                       "worst_month": round(float(monthly(eq[x]).min()), 2),
                       "trades": runs[x]["metrics"].get("closed_trades"),
                       "symbols": runs[x]["symbols"]} for x in agents},
        "portfolio": {"accounts": agents, "capital": CAPITAL * len(agents),
                      "ret": round(float(tot.iloc[-1] * 100 - 100), 2), "dd": dd(tot),
                      "dd_usd": round(-dd(tot) / 100 * CAPITAL * len(agents)),
                      "worst_month": round(float(tm.min()), 2), "best_month": round(float(tm.max()), 2),
                      "median_month": round(float(tm.median()), 2), "mean_month": round(float(tm.mean()), 2),
                      "months_pos": round(float((tm > 0).mean() * 100), 1), "months": len(tm),
                      "cagr": round(float(tot.iloc[-1] ** (365.25 / (eq.index[-1] - eq.index[0]).days) * 100 - 100), 2)},
        "btc": {"ret": round(float(btc.iloc[-1] / btc.iloc[0] * 100 - 100), 1), "dd": dd(btc)},
        "yearly": yearly,
        "crises": [{"name": n, "from": f, "to": t, "btc": window(btc, f, t), "total": window(tot, f, t),
                    "agents": {x: window(eq[x], f, t) for x in agents}} for n, f, t in crises],
        "monthly": {"dates": [d.strftime("%Y-%m") for d in tm.index], "total": [round(float(v), 2) for v in tm],
                    "btc": [round(float(v), 1) for v in monthly(btc).reindex(tm.index)]},
        "curve": {"dates": [d.strftime("%Y-%m-%d") for d in tot.index[::7]],
                  "total": [round(float(v) * 100, 2) for v in tot.iloc[::7]],
                  "btc": [round(float(v / btc.iloc[0]) * 100, 1) for v in btc.iloc[::7]]},
        "corr": {"labels": agents, "matrix": pd.DataFrame({x: monthly(eq[x]) for x in agents}).corr().round(2)
                 .fillna(0).values.tolist()},
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(json.dumps({k: out[k] for k in ("period", "agents", "portfolio", "btc")}, indent=1, ensure_ascii=False))
    print("Años:", json.dumps({y: v["total"] for y, v in yearly.items()}))
    for c in out["crises"]:
        print(f"  {c['name']:55s} BTC {c['btc']:+7.1f}%  cartera {c['total']:+6.2f}%")


if __name__ == "__main__":
    main()
