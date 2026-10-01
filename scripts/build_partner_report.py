"""Informe para el socio: resultado de cada estrategia en el backtest real, sin explicar cómo funcionan.

    python scripts/build_partner_report.py [--name Duncan]

Escribe en estrategia/investor-pack/src/ una versión en inglés, otra en español (para los PDF) y una
página con menú EN/ES para abrir en el navegador. Las cifras salen de
estrategia/datos/backtest_real_resumen.json (scripts/embed_backtest_real.py).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_investor_docs import CSS, SRC, YEARS, R, cls, head, nbsp, pct  # noqa: E402

CRISES = ["china", "bear", "luna", "ftx", "yen", "oct25", "ene26"]
EXTRA = """
.wide th, .wide td { padding: 1.2mm 1.4mm; font-size: 8.4pt; }
.dec { font-weight: 600; line-height: 1.25; }
.wide th { letter-spacing: .04em; }
.wide { table-layout: fixed; }
.keep { color: #1F6F43; } .review { color: #7A5C0E; } .drop { color: #9B2C2C; }
tr.btc td { color: #6B7486; }
.langbar { position: fixed; top: 10px; right: 14px; display: flex; gap: 6px; z-index: 9; font: 600 12px 'Public Sans', Arial, sans-serif; }
.langbar button { border: 1px solid #E2DCCB; background: #FFFDF8; color: #10172A; border-radius: 999px; padding: 6px 12px; cursor: pointer; }
.langbar button.on { background: #10172A; color: #F7F5EF; border-color: #10172A; }
@media screen { body { padding: 24px 0; } .page { margin: 0 auto 24px; box-shadow: 0 2px 18px rgba(16,23,42,.12); } }
@media print { .langbar { display: none; } }
"""

T = {
    "en": {
        "title": "PUDA · Results by strategy (October 2026)",
        "kicker": "PeludaCoin (PUDA) · Results by strategy · October 2026 · Confidential · For {name}",
        "h1": "Six years of real markets: what each strategy delivered",
        "sub": ("Backtest from October 2020 to August 2026 · 8 subaccounts of $1M with the plan's settings · "
                "the same code that will trade live"),
        "kpi": ["portfolio over 6 years ({usd} on $8M); {cagr} a year on average", "worst portfolio drawdown (BTC: {btc})",
                "worst portfolio year (annual loss cap ≈ 4%)", "during LUNA and Celsius (BTC: {btc_luna})"],
        "h_table": "Each strategy, year by year",
        "th": ["Strategy", "6 years", "Per year", "Max. fall", "Decision"],
        "roles": {"SUB2": "Statistical arbitrage", "SUB5": "Downside hedge", "SUB6": "Funding · neutral",
                  "SUB7": "Grid", "SUB8": "Periodic buying (spot)", "SUB9": "Hedged long",
                  "SUB10": "Pairs · neutral", "SUB11": "Trend following"},
        "dec": {"keep": "Keep", "review": "Under review", "drop": "Candidate for removal"},
        "port": "Portfolio (8 × $1M)", "btc": "BTC (reference)",
        "note_table": ("2020 starts in October and 2026 ends in August. Result on each subaccount's total capital, of which "
                       "at most 20% is at stake. «Under review»: negative over the period or a drawdown beyond −20%."),
        "h_crisis": "In each crisis",
        "th_crisis": ["Crisis", "Dates", "BTC", "Portfolio", "Best and worst strategy"],
        "crisis": {"china": ("China and Elon", "May–Jul 2021"), "bear": ("Bear market", "Nov 2021 – Nov 2022"),
                   "luna": ("LUNA, Celsius and 3AC", "May–Jun 2022"), "ftx": ("FTX", "Nov 2022"),
                   "yen": ("Yen crash", "Aug 2024"), "oct25": ("10 October liquidations", "Oct 2025"),
                   "ene26": ("January fall", "Jan–Feb 2026")},
        "none_up": "none positive · {w}",
        "foot": "PUDA · Results by strategy · October 2026",
        "h_read": "What the results say",
        "c1h": "The capital is protected",
        "c1": ("Over six years the portfolio never fell more than {dd}, while BTC fell as much as {btc_dd}. Its worst year "
               "was {wy}, within the annual cap. It made money during LUNA and was essentially flat during FTX."),
        "c2h": "The returns are not there yet",
        "c2": ("In total it made {tot} ({usd}), {cagr} a year on average ({usd_y} a year). That is why we are not giving a return target yet: it will be set with the "
               "adjusted agents and 6 months on demo."),
        "c3h": "What works",
        "c3": "{keep}. SUB5 contributes the most: {sub5}, with its best year in 2022, when the market fell hardest.",
        "c4h": "What is under review",
        "c4": "{review}. SUB10 is a candidate for removal ({sub10}).",
        "usd_th": "Result in dollars ($8M)", "usd_row": "Portfolio", "total": "Total",
        "h_stop": "The −20% stop protects",
        "stop": ("When a strategy falls 20% from its peak, it stops trading until it is fixed. In the backtest it triggered "
                 "for SUB2, SUB11 and SUB9. We tested restarting them automatically after 90 days and the result was much "
                 "worse (SUB2 would go from {a} to {b}). The stop stays as it is."),
        "h_next": "Next steps",
        "next": ["Adjust or remove {review} and rerun the backtest.",
                 "Test on demo the three strategies that have no history to be simulated on (news, copy trading and the "
                 "1-minute one).",
                 "Six months of the whole system on Bitget's demo environment before any real money is used.",
                 "Set the return target with that data."],
        "legal": ("Confidential document for the partners' internal use. Simulation on real historical data with fees, "
                  "slippage and funding; no past or simulated result guarantees future results."),
        "and": "and",
    },
    "es": {
        "title": "PUDA · Resultados por estrategia (octubre 2026)",
        "kicker": "PeludaCoin (PUDA) · Resultados por estrategia · Octubre 2026 · Confidencial · Para {name}",
        "h1": "Seis años de mercado real: qué ha dado cada estrategia",
        "sub": ("Backtest de octubre 2020 a agosto 2026 · 8 subcuentas de 1 M$ con la configuración del plan · "
                "el mismo código que operará en real"),
        "kpi": ["cartera en 6 años ({usd} sobre 8 M$); de media, {cagr} al año", "peor caída de la cartera (BTC: {btc})",
                "peor año de la cartera (tope anual de pérdidas ≈ 4%)", "durante LUNA y Celsius (BTC: {btc_luna})"],
        "h_table": "Resultado de cada estrategia, año a año",
        "th": ["Estrategia", "6 años", "Por año", "Peor caída", "Decisión"],
        "roles": {"SUB2": "Arbitraje estadístico", "SUB5": "Cobertura en caídas", "SUB6": "Funding · neutral",
                  "SUB7": "Grid", "SUB8": "Compra periódica (spot)", "SUB9": "Largo cubierto",
                  "SUB10": "Pares · neutral", "SUB11": "Seguimiento de tendencia"},
        "dec": {"keep": "Se mantiene", "review": "Se revisa", "drop": "Candidata a retirar"},
        "port": "Cartera (8 × 1 M$)", "btc": "BTC (referencia)",
        "note_table": ("2020 empieza en octubre y 2026 acaba en agosto. Resultado sobre el capital total de cada subcuenta, "
                       "del que como mucho el 20% está en juego. «Se revisa»: pierde en el total o su caída pasó del −20%."),
        "h_crisis": "En cada crisis",
        "th_crisis": ["Crisis", "Fechas", "BTC", "Cartera", "Mejor y peor estrategia"],
        "crisis": {"china": ("China y Elon", "may–jul 2021"), "bear": ("Mercado bajista", "nov 2021 – nov 2022"),
                   "luna": ("LUNA, Celsius y 3AC", "may–jun 2022"), "ftx": ("FTX", "nov 2022"),
                   "yen": ("Crash del yen", "ago 2024"), "oct25": ("Liquidaciones del 10 de octubre", "oct 2025"),
                   "ene26": ("Caída de enero", "ene–feb 2026")},
        "none_up": "ninguna en positivo · {w}",
        "foot": "PUDA · Resultados por estrategia · Octubre 2026",
        "h_read": "Lo que dicen los resultados",
        "c1h": "El capital está protegido",
        "c1": ("En seis años la cartera nunca cayó más de un {dd}, mientras BTC llegó a caer un {btc_dd}. Su peor año fue "
               "un {wy}, dentro del tope anual. En LUNA ganó y en FTX se quedó prácticamente plana."),
        "c2h": "El rendimiento, todavía no",
        "c2": ("En total suma un {tot} ({usd}): de media, un {cagr} al año ({usd_y} al año). Por eso no damos aún un objetivo de rentabilidad: se fijará con los "
               "agentes ajustados y 6 meses en demo."),
        "c3h": "Lo que funciona",
        "c3": "{keep}. SUB5 es la que más aporta: {sub5}, y su mejor año fue 2022, cuando más cayó el mercado.",
        "c4h": "Lo que se revisa",
        "c4": "{review}. SUB10 es candidata a retirar ({sub10}).",
        "usd_th": "Resultado en dólares (8 M$)", "usd_row": "Cartera", "total": "Total",
        "h_stop": "La parada del −20% protege",
        "stop": ("Cuando una estrategia cae un 20% desde su máximo, deja de operar hasta que se corrige. En el backtest saltó "
                 "en SUB2, SUB11 y SUB9. Probamos a reactivarlas automáticamente a los 90 días y el resultado empeoró mucho "
                 "(SUB2 pasaría de {a} a {b}). La parada se queda como está."),
        "h_next": "Próximos pasos",
        "next": ["Ajustar o retirar {review} y repetir el backtest.",
                 "Probar en demo las tres estrategias que no tienen histórico con el que simularse (noticias, copy "
                 "trading y la de velas de 1 minuto).",
                 "Seis meses de todo el sistema en el entorno demo de Bitget antes de usar dinero real.",
                 "Fijar el objetivo de rentabilidad con esos datos."],
        "legal": ("Documento confidencial para uso interno de los socios. Simulación sobre datos históricos reales con "
                  "comisiones, slippage y funding; ningún resultado pasado o simulado garantiza resultados futuros."),
        "and": "y",
    },
}


def decision(a: str) -> str:
    tot, dd = R["total"][a], R["max_dd"][a]
    if tot < -5:
        return "drop"
    if tot < 0 or dd < -20:
        return "review"
    return "keep"


def money(v: float, lang: str) -> str:
    s = f"{abs(v):,.0f}"
    sign = "+" if v > 0 else "−" if v < 0 else ""
    return f"{sign}${s}" if lang == "en" else sign + s.replace(",", ".") + " $"


def join(items: list[str], lang: str) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {T[lang]['and']} " + items[-1]


def pages(lang: str, name: str) -> str:
    t = T[lang]
    p = lambda v: pct(v, lang)  # noqa: E731
    absp = lambda v: pct(abs(v), lang).lstrip("+")  # noqa: E731
    yr = lambda y, k: R["years"][str(y)][k]  # noqa: E731
    agents = sorted(R["agents"], key=lambda a: -R["total"][a])
    worst_year = min(yr(y, "PORT") for y in YEARS)
    total_usd = sum(R["years_usd"].values())
    keep = [a for a in agents if decision(a) == "keep"]
    review = [a for a in agents if decision(a) != "keep"]

    ycols = "".join(f'<th class="r">{y}</th>' for y in YEARS)
    rows = []
    for a in agents:
        d = decision(a)
        rows.append(f'<tr><td><b>{a}</b><br><span class="muted" style="font-size:7.6pt">{t["roles"][a]}</span></td>'
                    + "".join(f'<td class="r {cls(yr(y, a))}">{p(yr(y, a))}</td>' for y in YEARS)
                    + f'<td class="r {cls(R["total"][a])}"><b>{p(R["total"][a])}</b></td><td class="r {cls(R["cagr"][a])}">{p(R["cagr"][a])}</td>'
                    f'<td class="r">{p(R["max_dd"][a])}</td><td class="dec {d}">{t["dec"][d]}</td></tr>')
    rows.append(f'<tr class="tot"><td>{t["port"]}</td>'
                + "".join(f'<td class="r {cls(yr(y, "PORT"))}">{p(yr(y, "PORT"))}</td>' for y in YEARS)
                + f'<td class="r {cls(R["total"]["PORT"])}">{p(R["total"]["PORT"])}</td><td class="r {cls(R["cagr"]["PORT"])}">{p(R["cagr"]["PORT"])}</td><td class="r">{p(R["max_dd"]["PORT"])}</td><td></td></tr>')
    rows.append(f'<tr class="btc"><td>{t["btc"]}</td>' + "".join(f'<td class="r">{p(yr(y, "BTC"))}</td>' for y in YEARS)
                + f'<td class="r">{p(R["total"]["BTC"])}</td><td class="r">{p(R["cagr"]["BTC"])}</td><td class="r">{p(R["max_dd"]["BTC"])}</td><td></td></tr>')
    th = t["th"]
    table = ('<table class="wide"><colgroup><col style="width:14%">' + '<col style="width:6.7%">' * len(YEARS)
             + '<col style="width:8%"><col style="width:8%"><col style="width:8.5%"><col style="width:14.6%"></colgroup>'
             f'<tr><th>{th[0]}</th>{ycols}<th class="r">{th[1]}</th><th class="r">{th[2]}</th><th class="r">{th[3]}</th>'
             f'<th>{th[4]}</th></tr>{"".join(rows)}</table>')

    crow = []
    for key in CRISES:
        c = R["crises"][key]
        best = max(R["agents"], key=lambda a: c[a])
        worst = min(R["agents"], key=lambda a: c[a])
        w = f"{worst} {p(c[worst])}"
        bw = f"{best} {p(c[best])} · {w}" if c[best] > 0.05 else t["none_up"].format(w=w)
        nm, dt = t["crisis"][key]
        crow.append(f'<tr><td><b>{nm}</b></td><td>{dt}</td><td class="r {cls(c["BTC"])}">{p(c["BTC"])}</td>'
                    f'<td class="r {cls(c["PORT"])}"><b>{p(c["PORT"])}</b></td><td class="muted">{bw}</td></tr>')
    tc = t["th_crisis"]
    crisis = (f'<table><tr><th>{tc[0]}</th><th>{tc[1]}</th><th class="r">{tc[2]}</th><th class="r">{tc[3]}</th>'
              f'<th>{tc[4]}</th></tr>{"".join(crow)}</table>')

    usd = "".join(f'<td class="r {cls(R["years_usd"][str(y)])}">{money(R["years_usd"][str(y)], lang)}</td>' for y in YEARS)
    usd_tab = (f'<h2>{t["usd_th"]}</h2><table class="wide"><colgroup><col style="width:13%">'
               + '<col style="width:11%">' * len(YEARS) + '<col style="width:10%"></colgroup>'
               f'<tr><th></th>{ycols}<th class="r">{t["total"]}</th></tr>'
               f'<tr><td>{t["usd_row"]}</td>{usd}<td class="r {cls(total_usd)}"><b>{money(total_usd, lang)}</b></td></tr></table>')

    kv = [p(R["total"]["PORT"]), p(R["max_dd"]["PORT"]), p(worst_year), p(R["crises"]["luna"]["PORT"])]
    kf = {"usd": money(total_usd, lang), "btc": p(R["max_dd"]["BTC"]), "btc_luna": p(R["crises"]["luna"]["BTC"]),
          "cagr": p(R["cagr"]["PORT"])}
    kpis = '<div class="kpis">' + "".join(f'<div class="kpi"><b>{v}</b><span>{lab.format(**kf)}</span></div>'
                                          for v, lab in zip(kv, t["kpi"], strict=True)) + "</div>"
    f = {"dd": absp(R["max_dd"]["PORT"]), "btc_dd": absp(R["max_dd"]["BTC"]), "wy": p(worst_year),
         "tot": p(R["total"]["PORT"]), "usd": money(total_usd, lang), "keep": join(keep, lang),
         "sub5": p(R["total"]["SUB5"]), "review": join(review, lang), "sub10": p(R["total"]["SUB10"]),
         "a": p(R["total"]["SUB2"]), "b": p(R["restart"]["SUB2"]), "cagr": p(R["cagr"]["PORT"]),
         "usd_y": money(R["usd_per_year"], lang)}
    cards = "".join(f'<div class="card"><h3>{t[h]}</h3><p class="muted" style="margin:0">{t[c].format(**f)}</p></div>'
                    for h, c in (("c1h", "c1"), ("c2h", "c2"), ("c3h", "c3"), ("c4h", "c4")))
    p1 = (f'<div class="page"><div class="bar"></div><p class="kicker">{t["kicker"].format(name=name)}</p>'
          f'<h1>{t["h1"]}</h1><p class="sub">{t["sub"]}</p>{kpis}<h2>{t["h_table"]}</h2>{table}'
          f'<p class="small" style="margin-top:2mm">{t["note_table"]}</p>{usd_tab}'
          f'<div class="foot"><span>{t["foot"]}</span><span>1 / 2</span></div></div>')
    p2 = (f'<div class="page"><div class="bar"></div><h2 style="margin-top:0">{t["h_crisis"]}</h2>{crisis}'
          f'<h2>{t["h_read"]}</h2>'
          f'<div class="grid2">{cards}</div><h2>{t["h_stop"]}</h2><p>{t["stop"].format(**f)}</p>'
          f'<h2>{t["h_next"]}</h2><ol class="steps">' + "".join(f"<li>{x.format(**f)}</li>" for x in t["next"]) + "</ol>"
          f'<p class="small" style="margin-top:6mm">{t["legal"]}</p>'
          f'<div class="foot"><span>{t["foot"]}</span><span>2 / 2</span></div></div>')
    return p1 + p2


def doc(lang: str, name: str) -> str:
    html = head(T[lang]["title"], lang).replace(CSS, CSS + EXTRA)
    return nbsp(html + pages(lang, name) + "</body></html>")


def bilingual(name: str) -> str:
    html = head(T["en"]["title"], "en").replace(CSS, CSS + EXTRA)
    menu = ('<nav class="langbar" aria-label="Language"><button type="button" data-l="en" class="on">English</button>'
            '<button type="button" data-l="es">Español</button></nav>')
    script = ("<script>document.querySelectorAll('.langbar button').forEach(b=>b.onclick=()=>{"
              "document.querySelectorAll('.langbar button').forEach(x=>x.classList.toggle('on',x===b));"
              "document.querySelectorAll('[data-lang]').forEach(d=>d.hidden=d.dataset.lang!==b.dataset.l);"
              "document.documentElement.lang=b.dataset.l;document.title=b.dataset.l==='en'?"
              f"{T['en']['title']!r}:{T['es']['title']!r};}});</script>")
    body = (menu + f'<div data-lang="en">{pages("en", name)}</div>'
            f'<div data-lang="es" hidden>{pages("es", name)}</div>' + script)
    return nbsp(html + body + "</body></html>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Duncan")
    a = ap.parse_args()
    slug = re.sub(r"\W+", " ", a.name).strip()
    out = {f"4 - PUDA Results by strategy - for {slug}.html": doc("en", a.name),
           f"4 - PUDA Resultados por estrategia - para {slug}.html": doc("es", a.name),
           f"PUDA Results by strategy - for {slug} (EN-ES).html": bilingual(a.name)}
    for fname, html in out.items():
        (SRC / fname).write_text(html, encoding="utf-8")
        print(fname)


if __name__ == "__main__":
    main()
