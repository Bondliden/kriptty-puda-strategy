"""Sistema de cuentas con grids y agente diario (reglas), sobre el histórico real.

    python scripts/sistema_grids.py --out data/grids --workers 16        # barrido y validación

Cada día, con lo que se sabía al cerrar el día anterior:

1. **Régimen** del mercado (BTC): alcista, lateral, bajista o incertidumbre.
2. Cada **cuenta** tiene una estrategia y elige su moneda del día entre las líquidas:
   * ``scalper``: grid neat en largo, solo en régimen lateral o alcista, en las monedas más
     «picadas» (mucho recorrido sin tendencia: índice de choppiness alto).
   * ``recursive``: grid recursive en largo en régimen alcista (monedas con más fuerza) y,
     opcionalmente, recursive prudente en incertidumbre.
   * ``short``: grid en corto en régimen bajista, en las monedas más débiles.
   * ``dca``: compra semanal fija de BTC/ETH, siempre, sin apalancamiento.
3. Si cambia la moneda o el régimen deja de ser el suyo, la posición se gestiona hasta cerrarse
   («graceful stop») o se cierra al momento si el régimen pasa a bajista/incertidumbre
   (``hard_exit``).

Los parámetros se eligen con 2020-10 → 2023-12 y se comprueban en 2024-01 → 2026-08.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kriptty.backtest.gridsim import CoinRunner, GridCfg  # noqa: E402

DATA = Path("data/history")
START, END, SPLIT = "2020-10-01", "2026-09-01", "2024-01-01"
CAPITAL = 1_000_000.0
TOP_LIQ = 40          # universo diario: las 40 monedas con más volumen de los últimos 30 días


# ── Datos ──────────────────────────────────────────────────────────────
def load_all() -> tuple[pd.DatetimeIndex, dict[str, pd.DataFrame], dict[str, np.ndarray]]:
    idx = pd.date_range(START, END, freq="1h", tz="UTC", inclusive="left")
    bars, fund = {}, {}
    for f in sorted(DATA.glob("*-USDT_USDT__1h.csv")):
        coin = f.name.split("-")[0]
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        df = df[~df.index.duplicated()].reindex(idx)
        if df["close"].notna().sum() < 24 * 120:
            continue
        bars[coin] = df
        ff = DATA / f"{coin}-USDT_USDT__funding.csv"
        if ff.exists():
            fr = pd.read_csv(ff, index_col=0, parse_dates=True).iloc[:, 0]
            fr = fr[~fr.index.duplicated()]
            fund[coin] = fr.reindex(idx).fillna(0.0).to_numpy()
    return idx, bars, fund


def daily_features(bars: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = {}
    btc_d = bars["BTC"]["close"].resample("1D").last()
    btc_r = btc_d.pct_change()
    for coin, df in bars.items():
        d = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        chop = tr.rolling(14).sum() / (d.high.rolling(14).max() - d.low.rolling(14).min())
        out[coin] = pd.DataFrame({
            "close": d.close, "liq": (d.volume * d.close).rolling(30).mean(),
            "atr_pct": atr / d.close, "chop": chop,
            "ret7": d.close.pct_change(7), "ret30": d.close.pct_change(30),
            "ema50": d.close.ewm(span=50).mean(), "ema200": d.close.ewm(span=200).mean(),
        })
        r = d.close.pct_change()
        beta = (r.rolling(60).cov(btc_r) / btc_r.rolling(60).var()).clip(0.3, 3)
        # «vasos comunicantes»: lo que la moneda va por detrás (−) o por delante (+) de lo que le tocaría
        # moverse con BTC en los últimos 7 días
        out[coin]["lag7"] = d.close.pct_change(7) - beta * btc_d.pct_change(7)
    return out


def regimes(btc: pd.DataFrame) -> pd.Series:
    """Régimen de cada día, decidido con los datos hasta el cierre del día anterior."""
    r = btc.close.pct_change()
    vol = r.rolling(14).std() * np.sqrt(365)
    vol_rank = vol.rolling(365, min_periods=120).rank(pct=True)
    drop3 = btc.close / btc.close.rolling(3).max() - 1
    reg = pd.Series("lateral", index=btc.index)
    bull = (btc.close > btc.ema200) & (btc.ema50 > btc.ema200) & (btc.ret30 > 0.05)
    bear = (btc.close < btc.ema200) & ((btc.ema50 < btc.ema200) | (btc.ret30 < -0.10))
    unc = (vol_rank > 0.9) | (drop3 < -0.08)
    reg[bull] = "alcista"
    reg[bear] = "bajista"
    reg[unc] = "incertidumbre"
    return reg.shift(1).fillna("lateral")          # se usa al día siguiente


# ── Selección diaria ───────────────────────────────────────────────────
def rankings(feat: dict[str, pd.DataFrame]) -> dict[str, dict[pd.Timestamp, list[str]]]:
    """Para cada día y criterio, las monedas ordenadas de mejor a peor (universo: las más líquidas)."""
    cols = ["liq", "chop", "ret30", "atr_pct", "lag7"]
    panel = pd.concat({c: f[cols] for c, f in feat.items() if c not in ("BTC", "ETH")}, names=["coin", "day"])
    out = {"scalper": {}, "momentum": {}, "weak": {}, "lag_long": {}, "lag_short": {}}
    for day, g in panel.groupby(level="day"):
        g = g.droplevel("day").dropna()
        if g.empty:
            continue
        g = g.nlargest(TOP_LIQ, "liq")
        out["scalper"][day] = g[(g.atr_pct > 0.02) & (g.atr_pct < 0.10)].sort_values("chop", ascending=False).index.tolist()
        out["momentum"][day] = g[g.atr_pct < 0.12].sort_values("ret30", ascending=False).index.tolist()
        out["weak"][day] = g.sort_values("ret30").index.tolist()
        out["lag_long"][day] = g[g.atr_pct < 0.12].sort_values("lag7").index.tolist()          # las más rezagadas
        out["lag_short"][day] = g.sort_values("lag7", ascending=False).index.tolist()          # las que aún no han caído
    return out


# ── Cuentas ────────────────────────────────────────────────────────────
ACTIVE = {"scalper": {"lateral", "alcista"}, "recursive": {"alcista"}, "short": {"bajista"}}


SELECT = {"scalper": "scalper", "recursive": "momentum", "short": "weak"}


def account_schedule(kind: str, rank: int, ranks, reg: pd.Series, slots: int, uncertainty_mode: bool,
                     select: str | None = None, regs: tuple | None = None) -> dict[str, pd.Series]:
    """Para cada moneda: serie diaria 1 = la cuenta la opera ese día (puede abrir ciclos), 2 = cierre forzado."""
    sched: dict[str, dict] = {}
    days = reg.index
    prev: set[str] = set()
    for day in days:
        r = reg.loc[day]
        active = r in (set(regs) if regs else ACTIVE[kind]) or (uncertainty_mode and kind == "recursive" and r == "incertidumbre")
        coins = set()
        if active:
            for s in range(slots):
                pool = ("BTC", "ETH") if (uncertainty_mode and r == "incertidumbre") else None
                lst = ranks[select or SELECT[kind]].get(day - pd.Timedelta(days=1), [])
                c = pool[s % 2] if pool else (lst[rank + s] if len(lst) > rank + s else None)
                if c:
                    coins.add(c)
        hard = (kind != "short" and r in ("bajista", "incertidumbre") and not (uncertainty_mode and kind == "recursive")) or \
               (kind == "short" and r in ("alcista",))
        for c in coins:
            sched.setdefault(c, {})[day] = 1
        if hard:
            for c in prev - coins:
                sched.setdefault(c, {})[day] = 2
        prev = coins
    return {c: pd.Series(v).reindex(days).fillna(0) for c, v in sched.items()}


PAUSE_DD, PAUSE_DAYS = 0.10, 14     # reglas de cada subcuenta (como en vivo)


def run_account(spec: dict, idx, bars, fund, ranks, reg) -> dict:
    """Todas las monedas de la cuenta avanzan juntas, hora a hora, con las reglas de la subcuenta:
    pausa de 14 días si cae un 10% desde su máximo y parada definitiva si cae ``hard_dd`` desde el máximo
    histórico (en vivo: hasta revisión manual)."""
    kind, cfg = spec["kind"], GridCfg(**spec["cfg"])
    if kind == "dca":
        return run_dca(spec, idx, bars)
    hard_dd = spec.get("hard_dd", 0.20)
    sched = account_schedule(kind, spec.get("rank", 0), ranks, reg, spec.get("slots", 2), spec.get("unc", False),
                             spec.get("select"), tuple(spec["regs"]) if spec.get("regs") else None)
    runners, plans = {}, {}
    for coin, s in sched.items():
        if coin not in bars:
            continue
        df = bars[coin]
        hourly = s.reindex(idx, method="ffill").fillna(0).to_numpy()
        ok = df["close"].notna().to_numpy()
        o, h, lo, c = (df[k].ffill().bfill().to_numpy() for k in ("open", "high", "low", "close"))
        runners[coin] = CoinRunner(cfg, o, h, lo, c, CAPITAL, fund.get(coin))
        plans[coin] = ((hourly == 1) & ok, (hourly == 2) | ~ok)   # sin datos (retirada) = cierre forzado
    n = len(idx)
    eq = np.full(n, CAPITAL)
    last = {c: 0.0 for c in runners}
    peak = hwm = CAPITAL
    paused_until, halted = -1, False
    pauses = 0
    active: set[str] = set()
    for i in range(n):
        trading = not halted and i >= paused_until
        for coin, r in runners.items():
            allow, force = plans[coin]
            if force[i] and r.open:
                r.close_now(i)
            if r.open or (trading and allow[i]):
                last[coin] = r.step(i, trading and bool(allow[i]))
                active.add(coin)
        e = CAPITAL + sum(last.values())
        eq[i] = e
        if halted:
            continue
        hwm = max(hwm, e)
        peak = max(peak, e) if i >= paused_until else peak
        if hwm > 0 and (hwm - e) / hwm > hard_dd:
            for r in runners.values():
                r.close_now(i, "halt")
            halted = True
        elif i >= paused_until and peak > 0 and (peak - e) / peak > PAUSE_DD:
            for r in runners.values():
                r.close_now(i, "pause")
            paused_until = i + PAUSE_DAYS * 24
            peak = e
            pauses += 1
        if halted or paused_until > i:
            e = CAPITAL + sum(r.b.realized for r in runners.values())
            for coin, r in runners.items():
                last[coin] = r.b.realized
            eq[i] = e
    stats = {"cycles": sum(r.cycles for r in runners.values()), "stops": sum(r.stops for r in runners.values()),
             "liq": sum(r.liqs for r in runners.values()), "fees": float(sum(r.b.fees for r in runners.values())),
             "coins": len(runners), "pauses": pauses, "halted": halted}
    return {"equity": eq, **stats}


def run_account_v2(spec: dict, idx, bars, fund, ranks, reg) -> dict:
    """Versión «operativa»: exposición por régimen del día (``wel_reg``), varias monedas a la vez y salida
    en dos fases: la moneda que deja de estar elegida (o el régimen que deja de ser el suyo) pasa a
    graceful stop; si desde ahí el precio sigue en contra ``g_sl``, se cierra. ``cfg.sl`` queda como stop
    de catástrofe (puede ser None)."""
    cfg = GridCfg(**spec["cfg"])
    hard_dd = spec.get("hard_dd", 0.25)
    wel_reg = spec["wel_reg"]
    base = max(wel_reg.values())
    cfg = replace(cfg, wel=base)
    sched = account_schedule(spec["kind"], spec.get("rank", 0), ranks, reg, spec.get("slots", 3), spec.get("unc", False),
                             spec.get("select"), tuple(spec["regs"]) if spec.get("regs") else None)
    reg_h = reg.reindex(idx, method="ffill").fillna("lateral").to_numpy()
    scale_h = np.array([wel_reg.get(r, 0.0) / base for r in reg_h])
    runners, allow_of = {}, {}
    for coin, s in sched.items():
        if coin not in bars:
            continue
        df = bars[coin]
        ok = df["close"].notna().to_numpy()
        allow_of[coin] = (s.reindex(idx, method="ffill").fillna(0).to_numpy() == 1) & ok
        o, h, lo, c = (df[k].ffill().bfill().to_numpy() for k in ("open", "high", "low", "close"))
        runners[coin] = (CoinRunner(cfg, o, h, lo, c, CAPITAL, fund.get(coin)), ok)
    n = len(idx)
    eq = np.full(n, CAPITAL)
    last = {c: 0.0 for c in runners}
    peak = hwm = CAPITAL
    paused_until, halted, pauses = -1, False, 0
    g_sl = spec.get("g_sl")
    for i in range(n):
        trading = not halted and i >= paused_until
        for coin, (r, ok) in runners.items():
            allow = allow_of[coin][i] and trading
            if not ok[i] and r.open:
                r.close_now(i, "delisted")
            elif r.open and not allow_of[coin][i]:
                r.set_graceful(i, g_sl)
            if r.open or allow:
                last[coin] = r.step(i, allow, scale_h[i])
        e = CAPITAL + sum(last.values())
        eq[i] = e
        if halted:
            continue
        hwm = max(hwm, e)
        if i >= paused_until:
            peak = max(peak, e)
        if (hwm - e) / hwm > hard_dd:
            for r, _ in runners.values():
                r.close_now(i, "halt")
            halted = True
        elif i >= paused_until and (peak - e) / peak > PAUSE_DD:
            for r, _ in runners.values():
                r.close_now(i, "pause")
            paused_until, peak, pauses = i + PAUSE_DAYS * 24, e, pauses + 1
        if halted or paused_until > i:
            for coin, (r, _) in runners.items():
                last[coin] = r.b.realized
            eq[i] = CAPITAL + sum(last.values())
    rs = [r for r, _ in runners.values()]
    return {"equity": eq, "cycles": sum(r.cycles for r in rs), "stops": sum(r.stops for r in rs),
            "liq": sum(r.liqs for r in rs), "fees": float(sum(r.b.fees for r in rs)), "coins": len(rs),
            "pauses": pauses, "halted": halted}


def run_dca(spec: dict, idx, bars) -> dict:
    """Compra semanal fija (``weekly`` $) de BTC 60% / ETH 40%, sin apalancamiento ni stop."""
    weekly = spec.get("weekly", 200_000 / 52)
    btc, eth = bars["BTC"]["close"].ffill().bfill(), bars["ETH"]["close"].ffill().bfill()
    buy = (idx.dayofweek == 0) & (idx.hour == 0)
    cash, qb, qe = CAPITAL, 0.0, 0.0
    eq = np.zeros(len(idx))
    for i in range(len(idx)):
        if buy[i] and cash > weekly:
            cash -= weekly
            qb += weekly * 0.6 / btc.iloc[i] * (1 - 0.001)
            qe += weekly * 0.4 / eth.iloc[i] * (1 - 0.001)
        eq[i] = cash + qb * btc.iloc[i] + qe * eth.iloc[i]
    return {"equity": eq, "cycles": 0, "stops": 0, "liq": 0, "fees": 0.0, "coins": 2}


# ── Métricas ───────────────────────────────────────────────────────────
def metrics(eq: pd.Series) -> dict:
    eq = eq.dropna()
    m = eq.resample("ME").last()
    m = pd.concat([eq.iloc[:1], m]).pct_change().dropna() * 100
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return {"monthly_avg": round(float(m.mean()), 2), "monthly_median": round(float(m.median()), 2),
            "months_3pct": round(float((m >= 3).mean() * 100), 1), "worst_month": round(float(m.min()), 2),
            "cagr": round(float(((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100), 2),
            "max_dd": round(float(((eq / eq.cummax()) - 1).min() * 100), 2)}


def split_metrics(eq: np.ndarray, idx) -> dict:
    s = pd.Series(eq, index=idx)
    sp = pd.Timestamp(SPLIT, tz="UTC")
    return {"train": metrics(s[:sp]), "test": metrics(s[sp:]), "full": metrics(s)}


# ── Barrido ────────────────────────────────────────────────────────────
MARKUPS = [(0.005, 0.01), (0.01, 0.014), (0.02, 0.02)]


def grid_specs(hard_dd: float = 0.25) -> list[dict]:
    specs = []
    base = {"slots": 2, "rank": 0, "hard_dd": hard_dd}
    for sel, regs, wel, sl, span, (mm, mr) in itertools.product(
            ["scalper", "lag_long"], [("lateral",), ("lateral", "alcista")], [0.1, 0.2, 0.28, 0.4],
            [0.03, 0.05, 0.08, 0.12, None], [0.015, 0.025, 0.05], MARKUPS):
        specs.append({**base, "kind": "scalper", "select": sel, "regs": regs,
                      "cfg": asdict(GridCfg(mode="neat", wel=wel, sl=sl, grid_span=span, min_markup=mm, markup_range=mr))})
    for sel, unc, wel, sl, dist, dd, (mm, mr) in itertools.product(
            ["momentum", "lag_long"], [False, True], [0.1, 0.2, 0.28, 0.4], [0.05, 0.08, 0.12, None],
            [0.01, 0.02, 0.03], [0.6, 1.0], MARKUPS[:2]):
        specs.append({**base, "kind": "recursive", "select": sel, "unc": unc,
                      "cfg": asdict(GridCfg(mode="recursive", wel=wel, sl=sl, rentry_dist=dist, ddown_factor=dd,
                                            min_markup=mm, markup_range=mr))})
    for mode, sel, regs, wel, sl, step, (mm, mr) in itertools.product(
            ["neat", "recursive"], ["weak", "lag_short"], [("bajista",), ("bajista", "incertidumbre")],
            [0.1, 0.2, 0.28, 0.4], [0.05, 0.08, 0.12], [0.015, 0.025, 0.05], MARKUPS[:2]):
        cfg = GridCfg(mode=mode, side="short", wel=wel, sl=sl, grid_span=step * 2, rentry_dist=step,
                      min_markup=mm, markup_range=mr)
        specs.append({**base, "kind": "short", "select": sel, "regs": regs, "cfg": asdict(cfg)})
    return specs


WEL_REG = {"suave": {"lateral": 0.08, "alcista": 0.07, "incertidumbre": 0.04, "bajista": 0.04},
           "lateral": {"lateral": 0.08, "alcista": 0.04, "incertidumbre": 0.04, "bajista": 0.0},
           "plano": {"lateral": 0.07, "alcista": 0.07, "incertidumbre": 0.07, "bajista": 0.07}}


def grid_specs_v2(hard_dd: float = 0.25) -> list[dict]:
    """Exposición 0,04–0,08 por moneda según el régimen, 3–8 monedas, salida graceful + stop escalonado."""
    specs = []
    base = {"v2": True, "rank": 0, "hard_dd": hard_dd}
    for wr, slots, gsl, sl, span, (mm, mr), sel in itertools.product(
            ["suave", "lateral", "plano"], [3, 5, 8], [0.03, 0.05, 0.08, None], [0.15, None],
            [0.025, 0.05], MARKUPS[:2], ["scalper", "lag_long"]):
        specs.append({**base, "kind": "scalper", "select": sel, "slots": slots, "regs": ("lateral", "alcista", "incertidumbre"),
                      "wel_reg": WEL_REG[wr], "wr": wr, "g_sl": gsl,
                      "cfg": asdict(GridCfg(mode="neat", sl=sl, grid_span=span, min_markup=mm, markup_range=mr))})
    for wr, slots, gsl, sl, dist, sel, unc in itertools.product(
            ["suave", "plano"], [3, 5, 8], [0.03, 0.05, 0.08, None], [0.15, None], [0.01, 0.02, 0.03],
            ["lag_long", "momentum"], [False, True]):
        specs.append({**base, "kind": "recursive", "select": sel, "slots": slots, "unc": unc,
                      "wel_reg": WEL_REG[wr], "wr": wr, "g_sl": gsl,
                      "cfg": asdict(GridCfg(mode="recursive", sl=sl, rentry_dist=dist, ddown_factor=1.0))})
    for slots, gsl, sl, step, mode, sel, regs in itertools.product(
            [3, 5, 8], [0.03, 0.05, 0.08], [0.15, None], [0.015, 0.025], ["neat", "recursive"],
            ["weak", "lag_short"], [("bajista",), ("bajista", "incertidumbre")]):
        specs.append({**base, "kind": "short", "select": sel, "slots": slots, "regs": regs, "g_sl": gsl,
                      "wel_reg": {"bajista": 0.08, "incertidumbre": 0.04},
                      "cfg": asdict(GridCfg(mode=mode, side="short", sl=sl, grid_span=step * 2, rentry_dist=step))})
    return specs


_G = {}


def _init():
    idx, bars, fund = load_all()
    feat = daily_features(bars)
    reg = regimes(feat["BTC"])
    _G.update(idx=idx, bars=bars, fund=fund, feat=feat, reg=reg, ranks=rankings(feat))


def _work(spec: dict) -> dict:
    fn = run_account_v2 if spec.get("v2") else run_account
    r = fn(spec, _G["idx"], _G["bars"], _G["fund"], _G["ranks"], _G["reg"])
    return {"spec": spec, **{k: v for k, v in r.items() if k != "equity"},
            **split_metrics(r["equity"], _G["idx"]), "equity_daily": pd.Series(r["equity"], index=_G["idx"]).resample("1D").last().round(0).tolist()}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="data/grids")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--v2", action="store_true", help="exposición por régimen, varias monedas y salida escalonada")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    specs = (grid_specs_v2() if a.v2 else grid_specs()) + [{"kind": "dca", "weekly": w, "cfg": {}}
                                                           for w in (100_000 / 52, 200_000 / 52, 400_000 / 52)]
    if a.limit:
        specs = specs[: a.limit]
    done = 0
    results = []
    with ProcessPoolExecutor(a.workers, initializer=_init) as pool:
        futs = [pool.submit(_work, s) for s in specs]
        for f in as_completed(futs):
            try:
                results.append(f.result())
            except Exception as e:  # noqa: BLE001
                print("✗", e, flush=True)
            done += 1
            if done % 25 == 0:
                print(f"{done}/{len(specs)}", flush=True)
    (out / "resultados.json").write_text(json.dumps(results), encoding="utf-8")
    print(f"listo: {len(results)} cuentas simuladas")


if __name__ == "__main__":
    main()
