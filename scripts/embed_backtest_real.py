"""Inserta el backtest real de 6 años en la presentación para inversores (ES y EN).

    python scripts/embed_backtest_real.py data/real_v2 --data data/history

Añade tres diapositivas después de «Pruebas» (año a año, crisis y agente por agente), reescribe la de
«Rentabilidad» con lo que dice el histórico real, actualiza las referencias al backtest pendiente y
renumera los pies. Es idempotente: si las diapositivas ya existen, las sustituye.
Guarda también las cifras en ``estrategia/datos/backtest_real_resumen.json``.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DECKS = {"es": ROOT / "estrategia" / "Presentacion para inversores.html",
         "en": ROOT / "estrategia" / "investor-pack" / "PUDA Investor presentation (web version).html"}
CAPITAL = 1_000_000  # por subcuenta

CRISES = [  # (clave, inicio, fin)
    ("china", "2021-05-10", "2021-07-20"),
    ("bear", "2021-11-10", "2022-11-21"),
    ("luna", "2022-05-05", "2022-06-18"),
    ("ftx", "2022-11-06", "2022-11-21"),
    ("yen", "2024-07-29", "2024-08-07"),
    ("oct25", "2025-10-06", "2025-10-17"),
    ("ene26", "2026-01-06", "2026-02-05"),
]

T = {
    "es": {
        "footer": "PUDA · Presentación para inversores",
        "kicker": "Backtest real · oct 2020 – ago 2026",
        "h_year": "Seis años de mercado real, año a año",
        "th_year": ["Año", "Qué pasó en el mercado", "BTC", "Cartera PUDA"],
        "events": {
            2020: "Oct–dic. BTC supera los máximos de 2017",
            2021: "Dos máximos y la caída de mayo (China)",
            2022: "Mercado bajista: LUNA, Celsius y FTX",
            2023: "Recuperación tras el bajista",
            2024: "ETF al contado, halving y crash del yen",
            2025: "Liquidaciones récord del 10 de octubre",
            2026: "Ene–ago. Caída del −33% en enero",
        },
        "kpi_dd": "Peor caída de la cartera en 6 años",
        "kpi_dd_sub": "BTC: {btc_dd}",
        "kpi_year": "Peor año de la cartera",
        "kpi_year_sub": "tope anual de pérdidas: ≈ 4%",
        "note_year": ("8 subcuentas de 1 M$ con la configuración del plan (3x, como mucho el 20% en juego, pausa al −10% "
                      "y parada al −20%), sobre velas horarias reales de futuros y spot, con comisiones, slippage y funding."),
        "h_crisis": "En las crisis, el capital aguantó",
        "th_crisis": ["Crisis", "Fechas", "BTC", "Cartera", "Mejor y peor agente"],
        "crisis": {
            "china": ("China y Elon", "may–jul 2021"),
            "bear": ("Mercado bajista", "nov 2021 – nov 2022"),
            "luna": ("LUNA, Celsius y 3AC", "may–jun 2022"),
            "ftx": ("FTX", "nov 2022"),
            "yen": ("Crash del yen", "ago 2024"),
            "oct25": ("Liquidaciones del 10 de octubre", "oct 2025"),
            "ene26": ("Caída de enero", "ene–feb 2026"),
        },
        "best_in": "Mejor {best} {b} · peor {worst} {w}",
        "none_up": "Ninguno en positivo · peor {worst} {w}",
        "note_crisis": ("Variación de la cartera y de BTC entre el inicio y el final de cada periodo. "
                        "Mientras BTC caía, la cuenta en corto (SUB5) y las estrategias neutrales compensaban a los largos."),
        "h_agents": "Agente por agente: qué aporta y qué se revisa",
        "th_agents": ["Agente", "Papel", "6 años", "Por año", "Peor caída", "Mejor año", "Decisión"],
        "avg": "Media anual",
        "roles": {"SUB2": "Arbitraje estadístico", "SUB5": "Cobertura en caídas", "SUB6": "Funding · neutral",
                  "SUB7": "Grid", "SUB8": "DCA de fondo (spot)", "SUB9": "Largo cubierto",
                  "SUB10": "Pares · neutral", "SUB11": "SuperTrend"},
        "verdict": {"keep": "Se mantiene", "review": "Se revisa", "drop": "Candidato a retirar"},
        "note_agents": ("SUB1 (noticias), SUB3 (copy trading) y SUB4 (velas de 1 minuto) no tienen histórico con el que "
                        "probarse: se validan en demo. Tras la parada del −20% el agente no vuelve a operar hasta corregir su estrategia: "
                        "reactivarlo sin cambios lo empeora (SUB2 pasaría a {sub2_restart})."),
        "ret_kicker": "Rentabilidad",
        "h_ret": "El riesgo está probado; el rendimiento, todavía no",
        "ret_table_title": "Lo que habría dado el sistema: 8 subcuentas, 1 M$ cada una",
        "th_ret": ["Año", "Resultado", "Sobre 8 M$"],
        "ret_total": "Total 6 años",
        "ret_note": ("Resultado sobre el capital total de cada subcuenta, del que como mucho el 20% está en juego. "
                     "Cada año se mide sobre el saldo con el que empieza."),
        "ret_card_h": "Cómo se fija el objetivo",
        "ret_card": ["El histórico real confirma la protección del capital, pero no respalda todavía un objetivo de rentabilidad.",
                     "Antes de usar dinero real: se ajustan o retiran los agentes más débiles, se repite el backtest y "
                     "el sistema pasa 6 meses en demo.",
                     "El objetivo se fija con esos datos, nunca antes."],
        "pruebas_old": "Siguiente paso: backtest con histórico real de Bitget y 6 meses en demo.",
        "pruebas_new": "Contrastado después con 6 años de histórico real (páginas siguientes).",
        "resumen_old": "Peor caída del conjunto en el test de estrés: −6,7%.",
        "resumen_new": "En 6 años de mercado real, peor caída del conjunto: {dd}.",
        "ruta_old": "Backtest con histórico real y 6 meses del sistema en demo",
        "ruta_new": "Backtest con datos reales hecho; ajuste de agentes y 6 meses del sistema en demo",
        "pasos_old": ("Backtest real", "El sistema sobre el histórico de Bitget."),
        "pasos_new": ("Ajuste de agentes", "Revisar los agentes más débiles y repetir el backtest real."),
        "aviso_old": "Las pruebas del sistema usan mercados simulados: miden el riesgo, no predicen la rentabilidad.",
        "aviso_new": ("Las pruebas del sistema usan mercados simulados y 6 años de histórico real: miden el riesgo, "
                      "no predicen la rentabilidad."),
        "dec": ",", "thou": ".",
    },
    "en": {
        "footer": "PUDA · Investor presentation",
        "kicker": "Backtest on real data · Oct 2020 – Aug 2026",
        "h_year": "Six years of real markets, year by year",
        "th_year": ["Year", "What happened in the market", "BTC", "PUDA portfolio"],
        "events": {
            2020: "Oct–Dec. BTC breaks its 2017 high",
            2021: "Two peaks and the May crash (China)",
            2022: "Bear market: LUNA, Celsius and FTX",
            2023: "Recovery after the bear market",
            2024: "Spot ETFs, halving and the yen crash",
            2025: "Record liquidations on 10 October",
            2026: "Jan–Aug. A −33% fall in January",
        },
        "kpi_dd": "Worst portfolio drawdown in 6 years",
        "kpi_dd_sub": "BTC: {btc_dd}",
        "kpi_year": "Worst portfolio year",
        "kpi_year_sub": "annual loss cap: ≈ 4%",
        "note_year": ("8 subaccounts of $1M with the plan's settings (3x, at most 20% at stake, pause at −10% and stop "
                      "at −20%), on real hourly futures and spot candles, with fees, slippage and funding."),
        "h_crisis": "Through every crisis, the capital held",
        "th_crisis": ["Crisis", "Dates", "BTC", "Portfolio", "Best and worst agent"],
        "crisis": {
            "china": ("China and Elon", "May–Jul 2021"),
            "bear": ("Bear market", "Nov 2021 – Nov 2022"),
            "luna": ("LUNA, Celsius and 3AC", "May–Jun 2022"),
            "ftx": ("FTX", "Nov 2022"),
            "yen": ("Yen crash", "Aug 2024"),
            "oct25": ("10 October liquidations", "Oct 2025"),
            "ene26": ("January fall", "Jan–Feb 2026"),
        },
        "best_in": "Best {best} {b} · worst {worst} {w}",
        "none_up": "None positive · worst {worst} {w}",
        "note_crisis": ("Change in the portfolio and in BTC from the start to the end of each period. While BTC fell, "
                        "the short account (SUB5) and the neutral strategies offset the long ones."),
        "h_agents": "Agent by agent: what each one adds and what is under review",
        "th_agents": ["Agent", "Role", "6 years", "Per year", "Worst drawdown", "Best year", "Decision"],
        "avg": "Average per year",
        "roles": {"SUB2": "Statistical arbitrage", "SUB5": "Downside hedge", "SUB6": "Funding · neutral",
                  "SUB7": "Grid", "SUB8": "Long-term DCA (spot)", "SUB9": "Hedged long",
                  "SUB10": "Pairs · neutral", "SUB11": "SuperTrend"},
        "verdict": {"keep": "Keep", "review": "Under review", "drop": "Candidate for removal"},
        "note_agents": ("SUB1 (news), SUB3 (copy trading) and SUB4 (1-minute candles) have no history to be tested on: "
                        "they are validated on demo. After the −20% stop the agent does not trade again until its strategy is fixed: "
                        "restarting it unchanged makes it worse (SUB2 would fall to {sub2_restart})."),
        "ret_kicker": "Returns",
        "h_ret": "The risk is proven; the returns are not yet",
        "ret_table_title": "What the system would have made: 8 subaccounts of $1M each",
        "th_ret": ["Year", "Result", "On $8M"],
        "ret_total": "Total, 6 years",
        "ret_note": ("Result on each subaccount's total capital, of which at most 20% is at stake. "
                     "Each year is measured on its opening balance."),
        "ret_card_h": "How the target is set",
        "ret_card": ["Real history confirms that the capital is protected, but it does not yet support a return target.",
                     "Before any real money is used, the weakest agents are adjusted or retired, the backtest is "
                     "run again and the system spends 6 months on demo.",
                     "The target is set with that data, never before."],
        "pruebas_old": "Next step: backtest on real Bitget history and 6 months on demo.",
        "pruebas_new": "Then checked against 6 years of real history (next pages).",
        "resumen_old": "Worst combined drawdown in the stress test: −6.7%.",
        "resumen_new": "Over 6 years of real markets, worst combined drawdown: {dd}.",
        "ruta_old": "Backtest on real history and 6 months of the system on demo",
        "ruta_new": "Backtest on real data completed; agent adjustments and 6 months of the system on demo",
        "pasos_old": ("Real backtest", "The system on Bitget's history."),
        "pasos_new": ("Agent adjustments", "Review the weakest agents and rerun the historical backtest."),
        "aviso_old": "The system tests use simulated markets: they measure risk, they do not predict returns.",
        "aviso_new": ("The system tests use simulated markets and 6 years of real history: they measure risk, "
                      "they do not predict returns."),
        "dec": ".", "thou": ",",
    },
}

SECTION = ("<section id=\"{id}\" data-transition=\"fade\" style=\"background:#F7F5EF;color:#1B2433;"
           "font-family:'Public Sans', Arial, sans-serif;padding:128px 128px 160px;display:flex;flex-direction:column;gap:{gap}px\">\n")
KICKER = '<p style="font-size:24px;letter-spacing:3px;text-transform:uppercase;color:#7A5C0E;font-weight:600">{}</p>\n'
H2 = ("<h2 style=\"font-family:'Source Serif 4', Georgia, serif;font-size:{size}px;font-weight:600;line-height:1.1;"
      "color:#10172A\">{text}</h2>\n")
CARD = "background:#FFFDF8;border:1px solid #E2DCCB;border-radius:16px;padding:32px;display:flex;flex-direction:column;gap:12px"
BIG = "font-family:'Source Serif 4', Georgia, serif;font-size:72px;font-weight:600;line-height:1.05;color:#10172A"
NOTE = '<p style="font-size:22px;line-height:1.4;color:#4A5568">{}</p>\n'
FOOT = ('<div style="position:absolute;left:128px;bottom:64px;width:1664px;display:flex;justify-content:space-between">'
        '<p style="font-size:24px;color:#6B7486">{}</p><p style="font-size:24px;color:#6B7486">00</p></div>\n\n</section>')
GREEN, RED, INK = "#1F6F43", "#9B2C2C", "#1B2433"


# ── Cálculo ────────────────────────────────────────────────────────────
def restart_totals(folder: Path | None) -> dict:
    """Resultado de cada agente si se reactivara tras la parada dura (``backtest_real.py --review-days``)."""
    if not folder or not folder.exists():
        return {}
    return {json.loads(f.read_text())["account"]: round(float(json.loads(f.read_text())["metrics"]["total_return_pct"]), 2)
            for f in folder.glob("SUB*.json")}


def compute(folder: Path, data: Path) -> dict:
    runs = {json.loads(f.read_text())["account"]: json.loads(f.read_text()) for f in folder.glob("SUB*.json")}
    agents = sorted(runs, key=lambda x: int(x[3:]))
    eq = pd.DataFrame({a: pd.Series(runs[a]["equity_daily"], index=pd.to_datetime(runs[a]["dates"])) for a in agents}).dropna()
    eq = eq / eq.iloc[0]
    eq["PORT"] = eq[agents].mean(axis=1)
    btc = pd.read_csv(data / "BTC-USDT_USDT__1h.csv", index_col=0, parse_dates=True)["close"]
    if btc.index.tz is not None:
        btc.index = btc.index.tz_localize(None)
    eq["BTC"] = btc.resample("1D").last().reindex(eq.index).ffill()
    ye = pd.concat([eq.iloc[:1], eq.resample("YE").last()])
    years = ye.pct_change().dropna() * 100
    years.index = years.index.year
    level = ye["PORT"]
    usd = (level.diff().dropna() * len(agents) * CAPITAL)
    usd.index = usd.index.year
    dd = ((eq / eq.cummax()) - 1).min() * 100
    span = (eq.index[-1] - eq.index[0]).days / 365.25  # años del periodo (2020 y 2026 incompletos)
    crises = {}
    for key, a, b in CRISES:
        s = eq.loc[a:b]
        crises[key] = ((s.iloc[-1] / s.iloc[0] - 1) * 100).to_dict()
    me = pd.concat([eq["PORT"].iloc[:1], eq["PORT"].resample("ME").last()]).pct_change().dropna() * 100
    return {
        "agents": agents,
        "start": eq.index[0].strftime("%Y-%m-%d"), "end": eq.index[-1].strftime("%Y-%m-%d"),
        "years": {int(y): {k: round(float(v), 2) for k, v in row.items()} for y, row in years.iterrows()},
        "years_usd": {int(y): round(float(v)) for y, v in usd.items()},
        "total": {k: round(float((eq[k].iloc[-1] / eq[k].iloc[0] - 1) * 100), 2) for k in eq.columns},
        "years_span": round(span, 3),
        "cagr": {k: round(float(((eq[k].iloc[-1] / eq[k].iloc[0]) ** (1 / span) - 1) * 100), 3) for k in eq.columns},
        "usd_per_year": round(float(usd.sum() / span)),
        "max_dd": {k: round(float(v), 2) for k, v in dd.items()},
        "crises": {k: {a: round(float(v), 2) for a, v in d.items()} for k, d in crises.items()},
        "trades": {a: int(runs[a]["metrics"].get("closed_trades") or 0) for a in agents},
        "months_pos_pct": round(float((me > 0).mean() * 100), 1), "worst_month": round(float(me.min()), 2),
    }


def verdict(r: dict, a: str) -> str:
    tot, dd = r["total"][a], r["max_dd"][a]
    if tot < -5:
        return "drop"
    if tot < 0 or dd < -20:
        return "review"
    return "keep"


# ── Formato ────────────────────────────────────────────────────────────
def pct(v: float, lang: str, digits: int = 1, sign: bool = True) -> str:
    if 0 < abs(v) < 0.1:
        digits = 2
    if round(abs(v), digits) == 0:
        return "0" + T[lang]["dec"] + "0%"
    s = f"{abs(v):.{digits}f}".replace(".", T[lang]["dec"])
    return ("+" if v > 0 and sign else "−" if v < 0 else "") + s + "%"


def money(v: float, lang: str) -> str:
    s = f"{abs(v):,.0f}".replace(",", T[lang]["thou"])
    sign = "+" if v > 0 else "−" if v < 0 else ""
    return f"{sign}${s}" if lang == "en" else f"{sign}{s} $"


def color(v: float) -> str:
    return GREEN if v > 0.05 else RED if v < -0.05 else INK


def cell(v: float, lang: str, bold: bool = False) -> str:
    txt = pct(v, lang)
    return f'<td style="text-align:right;color:{color(v)}">{"<b>" + txt + "</b>" if bold else txt}</td>'


def table(headers: list[str], widths: list[int], rows: list[str], size: int = 24, right: tuple = (2, 3)) -> str:
    th = "".join(f'<th style="width:{w}%{";text-align:right" if i in right else ""}">{h}</th>'
                 for i, (h, w) in enumerate(zip(headers, widths, strict=True)))
    return f'<table style="font-size:{size}px;color:#1B2433"><tr>{th}</tr>{"".join(rows)}</table>'


# ── Diapositivas ───────────────────────────────────────────────────────
def slide_years(r: dict, lang: str) -> str:
    t = T[lang]
    rows = [f'<tr><td><b>{y}</b></td><td>{t["events"][y]}</td>{cell(r["years"][y]["BTC"], lang)}'
            f'{cell(r["years"][y]["PORT"], lang, True)}</tr>' for y in sorted(r["years"])]
    rows.append(f'<tr style="background:#EFEADC"><td colspan="2"><b>{t["avg"]}</b></td>{cell(r["cagr"]["BTC"], lang, True)}'
                f'{cell(r["cagr"]["PORT"], lang, True)}</tr>')
    worst_year = min(r["years"].values(), key=lambda d: d["PORT"])["PORT"]
    body = ('<div style="display:flex;gap:40px;align-items:start"><div style="flex:2">'
            + table(t["th_year"], [9, 51, 18, 22], rows, 24)
            + f'</div><div style="flex:1;display:flex;flex-direction:column;gap:24px">'
            f'<div style="{CARD}"><p style="{BIG}">{pct(r["max_dd"]["PORT"], lang)}</p>'
            f'<h3 style="font-size:28px;font-weight:700;line-height:1.2;color:#10172A">{t["kpi_dd"]}</h3>'
            f'<p style="font-size:24px;color:#4A5568">{t["kpi_dd_sub"].format(btc_dd=pct(r["max_dd"]["BTC"], lang))}</p></div>'
            f'<div style="{CARD}"><p style="{BIG}">{pct(worst_year, lang)}</p>'
            f'<h3 style="font-size:28px;font-weight:700;line-height:1.2;color:#10172A">{t["kpi_year"]}</h3>'
            f'<p style="font-size:24px;color:#4A5568">{t["kpi_year_sub"]}</p></div></div></div>\n')
    return (SECTION.format(id="real-anual", gap=28) + KICKER.format(t["kicker"]) + H2.format(size=64, text=t["h_year"])
            + body + NOTE.format(t["note_year"]) + FOOT.format(t["footer"]))


def slide_crises(r: dict, lang: str) -> str:
    t = T[lang]
    rows = []
    for key, _, _ in CRISES:
        c = r["crises"][key]
        best = max(r["agents"], key=lambda a: c[a])
        worst = min(r["agents"], key=lambda a: c[a])
        if c[best] > 0.05:
            did = t["best_in"].format(best=best, b=pct(c[best], lang), worst=worst, w=pct(c[worst], lang))
        else:
            did = t["none_up"].format(worst=worst, w=pct(c[worst], lang))
        name, dates = t["crisis"][key]
        rows.append(f'<tr><td><b>{name}</b></td><td>{dates}</td>{cell(c["BTC"], lang)}{cell(c["PORT"], lang, True)}'
                    f'<td style="color:#4A5568">{did}</td></tr>')
    body = table(t["th_crisis"], [25, 18, 11, 12, 34], rows, 24) + "\n"
    return (SECTION.format(id="real-crisis", gap=32) + KICKER.format(t["kicker"]) + H2.format(size=64, text=t["h_crisis"])
            + body + NOTE.format(t["note_crisis"]) + FOOT.format(t["footer"]))


def slide_agents(r: dict, lang: str) -> str:
    t = T[lang]
    order = sorted(r["agents"], key=lambda a: -r["total"][a])
    rows = []
    for a in order:
        best = max(r["years"], key=lambda y: r["years"][y][a])
        v = verdict(r, a)
        vc = {"keep": GREEN, "review": "#7A5C0E", "drop": RED}[v]
        rows.append(f'<tr><td><b>{a}</b></td><td>{t["roles"][a]}</td>{cell(r["total"][a], lang, True)}{cell(r["cagr"][a], lang)}'
                    f'<td style="text-align:right">{pct(r["max_dd"][a], lang)}</td>'
                    f'<td style="text-align:right">{(str(best) + " · " + pct(r["years"][best][a], lang)) if r["years"][best][a] > 0.05 else "—"}</td>'
                    f'<td style="color:{vc};font-weight:600">{t["verdict"][v]}</td></tr>')
    rows.append(f'<tr style="background:#EFEADC"><td><b>{"Cartera" if lang == "es" else "Portfolio"}</b></td>'
                f'<td>{"8 subcuentas" if lang == "es" else "8 subaccounts"}</td>{cell(r["total"]["PORT"], lang, True)}{cell(r["cagr"]["PORT"], lang, True)}'
                f'<td style="text-align:right">{pct(r["max_dd"]["PORT"], lang)}</td><td></td><td></td></tr>')
    body = table(t["th_agents"], [10, 23, 11, 11, 13, 15, 17], rows, 23, right=(2, 3, 4, 5)) + "\n"
    return (SECTION.format(id="real-agentes", gap=28) + KICKER.format(t["kicker"]) + H2.format(size=64, text=t["h_agents"])
            + body + NOTE.format(t["note_agents"].format(sub2_restart=pct(r.get("restart", {}).get("SUB2", float("nan")), lang)))
            + FOOT.format(t["footer"]))


def slide_returns(r: dict, lang: str) -> str:
    t = T[lang]
    rows = [f'<tr><td>{y}</td>{cell(r["years"][y]["PORT"], lang)}'
            f'<td style="text-align:right;color:{color(r["years"][y]["PORT"])}">{money(r["years_usd"][y], lang)}</td></tr>'
            for y in sorted(r["years"])]
    tot = r["total"]["PORT"]
    rows.append(f'<tr style="background:#EFEADC"><td><b>{t["ret_total"]}</b></td>{cell(tot, lang, True)}'
                f'<td style="text-align:right;color:{color(tot)}"><b>{money(sum(r["years_usd"].values()), lang)}</b></td></tr>')
    rows.append(f'<tr style="background:#EFEADC"><td><b>{t["avg"]}</b></td>{cell(r["cagr"]["PORT"], lang, True)}'
                f'<td style="text-align:right;color:{color(r["cagr"]["PORT"])}"><b>{money(r["usd_per_year"], lang)}</b></td></tr>')
    card = "".join(f'<p style="font-size:25px;line-height:1.4;color:#4A5568">{p}</p>' for p in t["ret_card"])
    body = (f'<div style="display:flex;gap:48px;align-items:start"><div style="flex:1.3;display:flex;flex-direction:column;gap:16px">'
            f'<p style="font-size:26px;font-weight:700;color:#10172A">{t["ret_table_title"]}</p>'
            + table(t["th_ret"], [30, 30, 40], rows, 24, right=(1, 2))
            + f'</div><div style="flex:1;{CARD}"><h3 style="font-size:32px;font-weight:700;color:#10172A">{t["ret_card_h"]}</h3>'
            + card + '</div></div>\n')
    return (SECTION.format(id="rentabilidad", gap=36) + KICKER.format(t["ret_kicker"]) + H2.format(size=64, text=t["h_ret"])
            + body + NOTE.format(t["ret_note"]) + FOOT.format(t["footer"]))


# ── Inserción ──────────────────────────────────────────────────────────
def replace_section(html: str, sid: str, new: str) -> str:
    out, n = re.subn(rf'<section id="{sid}".*?</section>', lambda _: new, html, flags=re.S)
    if n != 1:
        raise SystemExit(f"no se encontró la diapositiva {sid}")
    return out


def must_replace(html: str, old: str, new: str, what: str) -> str:
    if old in html:
        return html.replace(old, new)
    if new in html:
        return html
    raise SystemExit(f"no se encontró el texto de {what}: {old!r}")


def renumber(html: str) -> str:
    n = 0

    def foot(m: re.Match) -> str:
        nonlocal n
        n += 1
        if n == 1:
            return m.group(0)  # portada, sin pie
        return re.sub(r'(<p style="font-size:24px;color:#[0-9A-Fa-f]{6}">)(\d{2})(</p></div>\s*</section>)',
                      lambda f: f"{f.group(1)}{n:02d}{f.group(3)}", m.group(0))
    return re.sub(r'<section id=.*?</section>', foot, html, flags=re.S)


def build(html: str, r: dict, lang: str) -> str:
    t = T[lang]
    html = re.sub(r'<section id="real-(anual|crisis|agentes)".*?</section>\n*', "", html, flags=re.S)
    new = "\n".join([slide_years(r, lang), slide_crises(r, lang), slide_agents(r, lang)])
    html = re.sub(r'(<section id="pruebas".*?</section>\n*)', lambda m: m.group(1) + new + "\n", html, count=1, flags=re.S)
    html = replace_section(html, "rentabilidad", slide_returns(r, lang))
    html = must_replace(html, t["pruebas_old"], t["pruebas_new"], "Pruebas")
    html = re.sub(re.escape(t["resumen_old"]) + "|" + re.escape(t["resumen_new"]).replace(r"\{dd\}", "[^<]*?"),
                  t["resumen_new"].format(dd=pct(r["max_dd"]["PORT"], lang)), html, count=1)
    html = must_replace(html, t["ruta_old"], t["ruta_new"], "Calendario")
    for old, new_ in zip(t["pasos_old"], t["pasos_new"], strict=True):
        html = must_replace(html, f">{old}<", f">{new_}<", "Próximos pasos")
    html = must_replace(html, t["aviso_old"], t["aviso_new"], "Aviso")
    return renumber(html)


PLAN = {"es": ROOT / "estrategia" / "estrategia.html", "en": ROOT / "estrategia" / "estrategia.en.html"}
PT = {
    "es": {"kicker": "Backtest con histórico real · oct 2020 – ago 2026",
           "h2": "Seis años reales: el riesgo aguanta; el rendimiento, todavía no",
           "th": ["Año", "Cartera", "BTC"],
           "kpis": [("Peor caída de la cartera", "dd"), ("Peor año", "wy"), ("LUNA y Celsius", "luna"), ("FTX", "ftx")],
           "btc": "BTC",
           "callout": ("<b>El 3–5% mensual no se sostiene todavía.</b> En seis años la cartera de 8 subcuentas suma {total} "
                       "sobre el capital total (como mucho el 20% en juego). Antes del dinero real: ajustar o retirar {review}, "
                       "repetir el backtest y 6 meses en demo."),
           "how": ("Mismo código de los agentes sobre 104 criptomonedas, velas horarias de futuros y spot, comisiones, "
                   "slippage, funding y macro día a día. Tras la parada del −20% el agente no vuelve a operar hasta corregir su estrategia."),
           "summary": ["3–5% al mes: el histórico real no lo respalda todavía",
                       "Backtest real de 6 años: peor caída {dd} (BTC {btc_dd}) y {total} en total. Se ajustan los agentes "
                       "y se valida en demo antes de fijar un objetivo."]},
    "en": {"kicker": "Backtest on real data · Oct 2020 – Aug 2026",
           "h2": "Six years of real data: risk control holds; returns are not there yet",
           "th": ["Year", "Portfolio", "BTC"],
           "kpis": [("Worst portfolio drawdown", "dd"), ("Worst year", "wy"), ("LUNA and Celsius", "luna"), ("FTX", "ftx")],
           "btc": "BTC",
           "callout": ("<b>3–5% a month is not supported yet.</b> Over six years the 8-subaccount portfolio made {total} on "
                       "total capital (at most 20% at stake). Before real money: adjust or retire {review}, rerun the "
                       "backtest and 6 months on demo."),
           "how": ("The agents' own code on 104 cryptocurrencies, hourly futures and spot candles, fees, slippage, funding "
                   "and day-by-day macro data. After the −20% stop the agent does not trade again until its strategy is fixed."),
           "summary": ["3–5% a month: real history does not support it yet",
                       "Six-year real backtest: worst drawdown {dd} (BTC {btc_dd}) and {total} in total. The agents are "
                       "adjusted and validated on demo before any target is set."]},
}


def review_list(r: dict, lang: str) -> str:
    rev = [a for a in sorted(r["agents"], key=lambda a: r["total"][a]) if verdict(r, a) != "keep"]
    if len(rev) < 2:
        return "".join(rev)
    return ", ".join(rev[:-1]) + (" y " if lang == "es" else " and ") + rev[-1]


def plan_slide(r: dict, lang: str, footer: str) -> str:
    t = PT[lang]
    worst_year = min(r["years"].values(), key=lambda d: d["PORT"])["PORT"]
    vals = {"dd": r["max_dd"]["PORT"], "wy": worst_year, "luna": r["crises"]["luna"]["PORT"], "ftx": r["crises"]["ftx"]["PORT"]}
    sub = {"dd": f'{t["btc"]} {pct(r["max_dd"]["BTC"], lang)}', "wy": "",
           "luna": f'{t["btc"]} {pct(r["crises"]["luna"]["BTC"], lang)}', "ftx": f'{t["btc"]} {pct(r["crises"]["ftx"]["BTC"], lang)}'}
    kpis = "".join(f'<div style="flex:1;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px"><b style="font-size:26px;font-family:var(--display);color:var(--gold)">{pct(vals[k], lang)}</b>'
                   f'<br><span class="note">{label}{" · " + sub[k] if sub[k] else ""}</span></div>' for label, k in t["kpis"])
    rows = "".join(f'<tr><td>{y}</td><td style="text-align:right">{pct(r["years"][y]["PORT"], lang)}</td>'
                   f'<td style="text-align:right">{pct(r["years"][y]["BTC"], lang)}</td></tr>' for y in sorted(r["years"]))
    th = t["th"]
    return (f'<section class="slide" aria-label="Backtest" data-tab="bot">\n'
            f'  <span class="kicker"><span class="tag-c">C · Bot</span> {t["kicker"]}</span>\n'
            f'  <h2>{t["h2"]}</h2>\n'
            f'  <div class="row">{kpis}</div>\n'
            f'  <div class="row grow">\n'
            f'    <div class="col tbl"><table><tr><th>{th[0]}</th><th style="text-align:right">{th[1]}</th>'
            f'<th style="text-align:right">{th[2]}</th></tr>{rows}</table></div>\n'
            f'    <div class="col"><div class="callout">{t["callout"].format(total=pct(r["total"]["PORT"], lang), review=review_list(r, lang))}</div>'
            f'<p class="note">{t["how"]}</p></div>\n'
            f'  </div>\n'
            f'  {footer}\n'
            f'</section>')


def build_plan(html: str, r: dict, lang: str) -> str:
    m = re.search(r'<section class="slide" aria-label="Backtest" data-tab="bot">.*?</section>', html, re.S)
    if not m:
        raise SystemExit("Plan PUDA: no se encontró la diapositiva Backtest")
    footer = re.search(r"<footer>.*?</footer>", m.group(0)).group(0)
    html = html[:m.start()] + plan_slide(r, lang, footer) + html[m.end():]
    pm = re.search(r'(<script id="plan-data">window.PLAN = )(.*?)(;</script>)', html, re.S)
    plan = json.loads(pm.group(2))
    fmt = {"dd": pct(r["max_dd"]["PORT"], lang), "btc_dd": pct(r["max_dd"]["BTC"], lang), "total": pct(r["total"]["PORT"], lang)}
    plan["rec"]["summary"] = [s.format(**fmt) for s in PT[lang]["summary"]]
    data = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
    return html[:pm.start()] + pm.group(1) + data + pm.group(3) + html[pm.end():]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    p.add_argument("--data", type=Path, default=Path("data/history"))
    p.add_argument("--restart", type=Path, default=None, help="carpeta del backtest con --review-days (opcional)")
    a = p.parse_args()
    r = compute(a.folder, a.data)
    r["restart"] = restart_totals(a.restart)
    out = ROOT / "estrategia" / "datos" / "backtest_real_resumen.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    for lang, path in DECKS.items():
        html = build(path.read_text(encoding="utf-8"), r, lang)
        path.write_text(html, encoding="utf-8")
        print(f"{path.name}: {html.count('<section id=')} diapositivas")
    for lang, path in PLAN.items():
        path.write_text(build_plan(path.read_text(encoding="utf-8"), r, lang), encoding="utf-8")
        print(f"{path.name}: diapositiva Backtest y resumen de rentabilidad actualizados")


if __name__ == "__main__":
    main()
