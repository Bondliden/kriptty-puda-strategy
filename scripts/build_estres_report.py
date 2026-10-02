"""Informe para el socio con el estrés test de las subcuentas de los agentes: solo resultados, sin parámetros.

    python scripts/build_estres_report.py --datos "C:/PROYECTOS IA/kriptty/configs/grids" --out <carpeta> [--name Duncan]

Lee ``estres_curvas_diarias.csv`` (``scripts/estres_subcuentas.py``) y escribe una versión en inglés, otra en
español y una página con menú EN/ES, con el estilo del paquete para inversores. Los PDF se imprimen con Edge.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_investor_docs import CSS, cls, head, nbsp, pct  # noqa: E402

EXTRA = """
.wide th, .wide td { padding: 1.2mm 1.1mm; font-size: 8.1pt; }
.wide th { font-size: 6.9pt; letter-spacing: .02em; white-space: nowrap; }
.wide { table-layout: fixed; }
tr.btc td { color: #6B7486; }
.langbar { position: fixed; top: 10px; right: 14px; display: flex; gap: 6px; z-index: 9; font: 600 12px 'Public Sans', Arial, sans-serif; }
.langbar button { border: 1px solid #E2DCCB; background: #FFFDF8; color: #10172A; border-radius: 999px; padding: 6px 12px; cursor: pointer; }
.langbar button.on { background: #10172A; color: #F7F5EF; border-color: #10172A; }
@media screen { body { padding: 24px 0; } .page { margin: 0 auto 24px; box-shadow: 0 2px 18px rgba(16,23,42,.12); } }
@media print { .langbar { display: none; } }
"""
CUENTAS = [  # columna del CSV, clave de texto, peso en la cartera
    ("Momentum · ALL IN 3.0 ×4", "mom", 0.25), ("Cortos · RECURSIVE KRIPTTY ×3", "short", 0.20),
    ("Vasos comunicantes ×1 (observación)", "rot", 0.10), ("Bull run · ALL IN 3.0 ×6", "bull", 0.15),
    ("Bull run · KRIPTTY SCALPER (mutación) ×1", "scalp", 0.05), ("Memecoins · corto tras el pico", "meme", 0.05),
    ("DCA BTC/ETH", "dca", 0.20),
]
PORT, BTC = "CARTERA (reparto propuesto)", "BTC (referencia)"
CRACKS = {"luna": ("2022-05-05", "2022-05-15"), "celsius": ("2022-06-10", "2022-06-20"), "ftx": ("2022-11-06", "2022-11-12"),
          "aug24": ("2024-08-01", "2024-08-08"), "oct25": ("2025-10-09", "2025-10-12")}

T = {
    "en": {
        "title": "Kriptty · AI agents by subaccount · Stress test (October 2026)",
        "kicker": "Kriptty · Stress test of the agents' subaccounts · October 2026 · Confidential · For {name}",
        "h1": "Six years of real markets: each subaccount, year by year",
        "sub": ("Backtest from October 2020 to September 2026 on real hourly prices of more than 100 coins, LUNA and FTT "
                "included · each subaccount runs its own strategy, chosen and adjusted every day by its agent · results "
                "on the whole subaccount, fees included"),
        "kpi": ["portfolio, average per year ({tot} over the period)", "worst portfolio drawdown (BTC: {btc_dd_s})",
                "portfolio during FTX (BTC: {btc_ftx})", "portfolio on 10 October 2025 (BTC: {btc_oct})"],
        "h_table": "Each subaccount, year by year",
        "th": ["Subaccount", "Weight", "Period", "Per year", "Max. fall"],
        "names": {"mom": ("Momentum", "Long the strongest altcoins in uptrends"),
                  "short": ("Short", "Shorts the altcoins that have not fallen yet, in bear markets"),
                  "rot": ("Rotation (watch)", "Long the altcoins lagging behind BTC"),
                  "bull": ("Bull-run booster", "Active only in strong bull runs (BTC +20% in 30 days)"),
                  "scalp": ("Bull-run scalper (test)", "Same days, fast in-and-out trading"),
                  "meme": ("Memecoins", "Short after a memecoin's peak, closed within 24 h · data from Dec 2023"),
                  "dca": ("DCA BTC/ETH", "Buys every week, no leverage")},
        "port": "Portfolio", "btc": "BTC (reference)",
        "note_table": ("2020 starts in October and 2026 ends in September. Each subaccount's result is on its whole capital; "
                       "the portfolio combines them with the weights shown. The agents stay out (no new trades) when the "
                       "market is not in their regime, which is why some years show 0%."),
        "usd_th": "Portfolio of $1,000,000", "usd_row": "Result", "total": "Total",
        "h_crisis": "In each crash",
        "th_crisis": ["Crash", "Dates", "BTC", "Portfolio", "Worst subaccount"],
        "crisis": {"luna": ("LUNA collapse", "May 2022"), "celsius": ("Celsius and 3AC", "Jun 2022"),
                   "ftx": ("FTX bankruptcy", "Nov 2022"), "aug24": ("Yen carry-trade crash", "Aug 2024"),
                   "oct25": ("10 October liquidations", "Oct 2025")},
        "h_read": "What the results say",
        "c1h": "About {cagr} a year with a {dd} maximum fall",
        "c1": ("Over six years the portfolio returns {tot} ({cagr} a year) and never falls more than {dd}, while BTC "
               "fell {btc_dd}. Risk is spread across seven subaccounts that rarely lose at the same time."),
        "c2h": "Returns come in bull markets",
        "c2": ("2021 ({y21}) and 2024 ({y24}) carry most of the result; 2022, 2025 and 2026 are close to flat. In bear "
               "and uncertain markets the agents step aside, which protects capital but also limits returns."),
        "c3h": "Crashes barely touch it",
        "c3": ("During LUNA the portfolio moved {luna}, during FTX {ftx} and on 10 October 2025 {oct}. The largest "
               "losses in those days come from the DCA subaccount, which never sells."),
        "c4h": "The strongest piece is the bull-run booster",
        "c4": ("Active only about one day in six, it returns {bull} a year with a {bull_dd} maximum fall. The memecoin "
               "subaccount is small and experimental: {meme} since December 2023."),
        "h_next": "Next steps",
        "next": ["Four to eight weeks of live simulation: the agents read the market and decide every day, without "
                 "touching money, and their daily reports are compared with what the market does.",
                 "Then capital is added gradually, one subaccount at a time, with exposure increases approved by a person.",
                 "Every subaccount keeps its own stop rules: a gradual exit first, a hard stop if the price keeps "
                 "going against it, and a pause of the whole subaccount after a 10% fall."],
        "legal": ("Backtest on historical data: past results do not guarantee future returns. Hourly prices, exchange fees "
                  "included; funding costs not included in the memecoin subaccount. Some settings were chosen with the "
                  "full history in view, so the result may be somewhat optimistic. For information only: this is not an "
                  "offer of investment."),
        "foot": "Kriptty · Stress test of the agents' subaccounts · October 2026",
    },
    "es": {
        "title": "Kriptty · Agentes por subcuenta · Estrés test (octubre 2026)",
        "kicker": "Kriptty · Estrés test de las subcuentas de los agentes · Octubre 2026 · Confidencial · Para {name}",
        "h1": "Seis años de mercado real: cada subcuenta, año a año",
        "sub": ("Backtest de octubre de 2020 a septiembre de 2026 con precios reales por hora de más de 100 monedas, LUNA "
                "y FTT incluidas · cada subcuenta tiene su estrategia, que su agente elige y ajusta cada día · resultados "
                "sobre la subcuenta entera, con comisiones"),
        "kpi": ["cartera, media al año ({tot} en el periodo)", "peor caída de la cartera (BTC: {btc_dd_s})",
                "cartera durante FTX (BTC: {btc_ftx})", "cartera el 10 de octubre de 2025 (BTC: {btc_oct})"],
        "h_table": "Cada subcuenta, año a año",
        "th": ["Subcuenta", "Peso", "Periodo", "Al año", "Caída máx."],
        "names": {"mom": ("Momentum", "Largos en las altcoins más fuertes cuando el mercado sube"),
                  "short": ("Cortos", "Cortos en las altcoins que aún no han caído, en mercado bajista"),
                  "rot": ("Rotación (observación)", "Largos en las altcoins que se quedan atrás de BTC"),
                  "bull": ("Impulso de bull run", "Solo activa en bull run fuerte (BTC +20% en 30 días)"),
                  "scalp": ("Scalper de bull run (prueba)", "Los mismos días, entradas y salidas rápidas"),
                  "meme": ("Memecoins", "Corto tras el pico de una memecoin, cerrado en 24 h · datos desde dic-2023"),
                  "dca": ("DCA BTC/ETH", "Compra cada semana, sin apalancamiento")},
        "port": "Cartera", "btc": "BTC (referencia)",
        "note_table": ("2020 empieza en octubre y 2026 acaba en septiembre. El resultado de cada subcuenta es sobre todo su "
                       "capital; la cartera las combina con los pesos indicados. Los agentes se quedan fuera (sin operaciones "
                       "nuevas) cuando el mercado no está en su régimen; por eso hay años al 0%."),
        "usd_th": "Cartera de 1.000.000 $", "usd_row": "Resultado", "total": "Total",
        "h_crisis": "En cada crack",
        "th_crisis": ["Crack", "Fechas", "BTC", "Cartera", "Peor subcuenta"],
        "crisis": {"luna": ("Hundimiento de LUNA", "may 2022"), "celsius": ("Celsius y 3AC", "jun 2022"),
                   "ftx": ("Quiebra de FTX", "nov 2022"), "aug24": ("Crack del yen", "ago 2024"),
                   "oct25": ("Liquidaciones del 10 de octubre", "oct 2025")},
        "h_read": "Qué dicen los resultados",
        "c1h": "En torno al {cagr} al año con una caída máxima del {dd}",
        "c1": ("En seis años la cartera da {tot} ({cagr} al año) y nunca cae más de un {dd}, mientras BTC llegó a caer "
               "un {btc_dd}. El riesgo está repartido en siete subcuentas que rara vez pierden a la vez."),
        "c2h": "La rentabilidad llega en los mercados alcistas",
        "c2": ("2021 ({y21}) y 2024 ({y24}) aportan casi todo; 2022, 2025 y 2026 quedan casi planos. En mercado bajista o "
               "incierto los agentes se apartan: protege el capital, pero también limita la rentabilidad."),
        "c3h": "Los cracks apenas la tocan",
        "c3": ("Durante LUNA la cartera se movió {luna}, durante FTX {ftx} y el 10 de octubre de 2025 {oct}. Las mayores "
               "pérdidas de esos días vienen de la subcuenta de DCA, que nunca vende."),
        "c4h": "La pieza más fuerte es el impulso de bull run",
        "c4": ("Activa solo uno de cada seis días, da {bull} al año con una caída máxima del {bull_dd}. La subcuenta de "
               "memecoins es pequeña y experimental: {meme} desde diciembre de 2023."),
        "h_next": "Siguientes pasos",
        "next": ["De cuatro a ocho semanas de simulación en vivo: los agentes leen el mercado y deciden cada día sin tocar "
                 "dinero, y sus informes diarios se comparan con lo que hace el mercado.",
                 "Después, capital poco a poco, subcuenta a subcuenta, con las subidas de exposición aprobadas por una persona.",
                 "Cada subcuenta mantiene sus stops: primero una salida gradual, un stop duro si el precio sigue en contra "
                 "y una pausa de toda la subcuenta si cae un 10%."],
        "legal": ("Backtest con datos históricos: los resultados pasados no garantizan rentabilidades futuras. Precios por "
                  "hora, comisiones del exchange incluidas; el coste de financiación no está incluido en la subcuenta de "
                  "memecoins. Algunos ajustes se eligieron viendo todo el histórico, así que el resultado puede ser algo "
                  "optimista. Documento informativo: no es una oferta de inversión."),
        "foot": "Kriptty · Estrés test de las subcuentas de los agentes · Octubre 2026",
    },
}


def metricas(curvas: pd.DataFrame) -> dict:
    curvas = curvas.ffill()
    years = sorted({d.year for d in curvas.index})
    out = {"years": years, "anual": {}, "total": {}, "cagr": {}, "dd": {}, "crack": {}}
    dias = (curvas.index[-1] - curvas.index[0]).days
    for col in curvas.columns:
        e = curvas[col].dropna()
        a = e.resample("YE").last()
        a = pd.concat([e.iloc[:1], a]).pct_change().dropna() * 100
        out["anual"][col] = {d.year: float(v) for d, v in a.items()}
        out["total"][col] = float((e.iloc[-1] / e.iloc[0] - 1) * 100)
        out["cagr"][col] = float(((e.iloc[-1] / e.iloc[0]) ** (365.25 / dias) - 1) * 100)
        out["dd"][col] = float((e / e.cummax() - 1).min() * 100)
        out["crack"][col] = {}
        for k, (i, f) in CRACKS.items():
            tramo = e[(e.index >= pd.Timestamp(i, tz="UTC")) & (e.index <= pd.Timestamp(f, tz="UTC"))]
            out["crack"][col][k] = float((tramo.min() / tramo.iloc[0] - 1) * 100) if len(tramo) > 1 else 0.0
    return out


def pages(lang: str, name: str, M: dict) -> str:
    t = T[lang]
    p = lambda v: pct(v, lang)  # noqa: E731
    absp = lambda v: pct(abs(v), lang).lstrip("+")  # noqa: E731
    years = M["years"]
    ycols = "".join(f'<th class="r">{y}</th>' for y in years)
    rows = []
    for col, key, peso in CUENTAS:
        nm, rol = t["names"][key]
        rows.append(f'<tr><td><b>{nm}</b><br><span class="muted" style="font-size:7.4pt">{rol}</span></td>'
                    f'<td class="r">{peso:.0%}</td>'
                    + "".join(f'<td class="r {cls(M["anual"][col].get(y, 0.0))}">{p(M["anual"][col].get(y, 0.0))}</td>' for y in years)
                    + f'<td class="r {cls(M["total"][col])}"><b>{p(M["total"][col])}</b></td>'
                    f'<td class="r {cls(M["cagr"][col])}">{p(M["cagr"][col])}</td><td class="r">{p(M["dd"][col])}</td></tr>')
    for col, etiqueta, clase in ((PORT, t["port"], "tot"), (BTC, t["btc"], "btc")):
        rows.append(f'<tr class="{clase}"><td>{etiqueta}</td><td class="r">{"100%" if col == PORT else ""}</td>'
                    + "".join(f'<td class="r {cls(M["anual"][col].get(y, 0.0)) if col == PORT else ""}">{p(M["anual"][col].get(y, 0.0))}</td>' for y in years)
                    + f'<td class="r">{p(M["total"][col])}</td><td class="r">{p(M["cagr"][col])}</td><td class="r">{p(M["dd"][col])}</td></tr>')
    th = t["th"]
    table = ('<table class="wide"><colgroup><col style="width:18.5%"><col style="width:6.5%">' + '<col style="width:7%">' * len(years)
             + '<col style="width:8.5%"><col style="width:8%"><col style="width:9.5%"></colgroup>'
             f'<tr><th>{th[0]}</th><th class="r">{th[1]}</th>{ycols}<th class="r">{th[2]}</th><th class="r">{th[3]}</th>'
             f'<th class="r">{th[4]}</th></tr>{"".join(rows)}</table>')

    # resultado en dólares de una cartera de 1 M$
    e = M["anual"][PORT]
    capital, usd = 1_000_000.0, []
    for y in years:
        r = e.get(y, 0.0) / 100
        usd.append(capital * r)
        capital *= 1 + r
    money = lambda v: (("+" if v > 0 else "−") + (f"${abs(v):,.0f}" if lang == "en" else f"{abs(v):,.0f}".replace(",", ".") + " $"))  # noqa: E731
    usd_tab = (f'<h2>{t["usd_th"]}</h2><table class="wide"><colgroup><col style="width:16%">' + '<col style="width:10.5%">' * len(years)
               + '<col style="width:10%"></colgroup>'
               f'<tr><th></th>{ycols}<th class="r">{t["total"]}</th></tr><tr><td>{t["usd_row"]}</td>'
               + "".join(f'<td class="r {cls(v)}">{money(v)}</td>' for v in usd)
               + f'<td class="r {cls(sum(usd))}"><b>{money(sum(usd))}</b></td></tr></table>')

    crow = []
    for k in CRACKS:
        nm, dt = t["crisis"][k]
        peor = min((c for c, _, _ in CUENTAS), key=lambda c: M["crack"][c][k])
        pk = next(key for c, key, _ in CUENTAS if c == peor)
        crow.append(f'<tr><td><b>{nm}</b></td><td>{dt}</td><td class="r {cls(M["crack"][BTC][k])}">{p(M["crack"][BTC][k])}</td>'
                    f'<td class="r {cls(M["crack"][PORT][k])}"><b>{p(M["crack"][PORT][k])}</b></td>'
                    f'<td class="muted">{t["names"][pk][0]} {p(M["crack"][peor][k])}</td></tr>')
    tc = t["th_crisis"]
    crisis = (f'<table><tr><th>{tc[0]}</th><th>{tc[1]}</th><th class="r">{tc[2]}</th><th class="r">{tc[3]}</th>'
              f'<th>{tc[4]}</th></tr>{"".join(crow)}</table>')

    f = {"tot": p(M["total"][PORT]), "cagr": p(M["cagr"][PORT]), "dd": absp(M["dd"][PORT]), "btc_dd": absp(M["dd"][BTC]),
         "btc_dd_s": p(M["dd"][BTC]),
         "y21": p(e.get(2021, 0)), "y24": p(e.get(2024, 0)), "luna": p(M["crack"][PORT]["luna"]),
         "ftx": p(M["crack"][PORT]["ftx"]), "oct": p(M["crack"][PORT]["oct25"]),
         "bull": p(M["cagr"]["Bull run · ALL IN 3.0 ×6"]), "bull_dd": absp(M["dd"]["Bull run · ALL IN 3.0 ×6"]),
         "meme": p(M["total"]["Memecoins · corto tras el pico"]), "btc_ftx": p(M["crack"][BTC]["ftx"]),
         "btc_oct": p(M["crack"][BTC]["oct25"])}
    kv = [p(M["cagr"][PORT]), p(M["dd"][PORT]), p(M["crack"][PORT]["ftx"]), p(M["crack"][PORT]["oct25"])]
    kpis = '<div class="kpis">' + "".join(f'<div class="kpi"><b>{v}</b><span>{lab.format(**f)}</span></div>'
                                          for v, lab in zip(kv, t["kpi"], strict=True)) + "</div>"
    cards = "".join(f'<div class="card"><h3>{t[h].format(**f)}</h3><p class="muted" style="margin:0">{t[c].format(**f)}</p></div>'
                    for h, c in (("c1h", "c1"), ("c2h", "c2"), ("c3h", "c3"), ("c4h", "c4")))
    p1 = (f'<div class="page"><div class="bar"></div><p class="kicker">{t["kicker"].format(name=name)}</p>'
          f'<h1>{t["h1"]}</h1><p class="sub">{t["sub"]}</p>{kpis}<h2>{t["h_table"]}</h2>{table}'
          f'<p class="small" style="margin-top:2mm">{t["note_table"]}</p>'
          f'<div class="foot"><span>{t["foot"]}</span><span>1 / 2</span></div></div>')
    usd_arriba = usd_tab.replace("<h2>", "<h2 style=\"margin-top:0\">", 1)
    p2 = (f'<div class="page"><div class="bar"></div>{usd_arriba}'
          f'<h2>{t["h_crisis"]}</h2>{crisis}'
          f'<h2>{t["h_read"]}</h2><div class="grid2">{cards}</div>'
          f'<h2>{t["h_next"]}</h2><ol class="steps">' + "".join(f"<li>{x}</li>" for x in t["next"]) + "</ol>"
          f'<p class="small" style="margin-top:6mm">{t["legal"]}</p>'
          f'<div class="foot"><span>{t["foot"]}</span><span>2 / 2</span></div></div>')
    return p1 + p2


def doc(lang: str, name: str, M: dict) -> str:
    html = head(T[lang]["title"], lang).replace(CSS, CSS + EXTRA)
    return nbsp(html + pages(lang, name, M) + "</body></html>")


def bilingual(name: str, M: dict) -> str:
    html = head(T["en"]["title"], "en").replace(CSS, CSS + EXTRA)
    menu = ('<nav class="langbar" aria-label="Language"><button type="button" data-l="en" class="on">English</button>'
            '<button type="button" data-l="es">Español</button></nav>')
    script = ("<script>document.querySelectorAll('.langbar button').forEach(b=>b.onclick=()=>{"
              "document.querySelectorAll('.langbar button').forEach(x=>x.classList.toggle('on',x===b));"
              "document.querySelectorAll('[data-lang]').forEach(d=>d.hidden=d.dataset.lang!==b.dataset.l);"
              "document.documentElement.lang=b.dataset.l;document.title=b.dataset.l==='en'?"
              f"{T['en']['title']!r}:{T['es']['title']!r};}});</script>")
    body = (menu + f'<div data-lang="en">{pages("en", name, M)}</div>'
            f'<div data-lang="es" hidden>{pages("es", name, M)}</div>' + script)
    return nbsp(html + body + "</body></html>")


def pdf(html: Path) -> Path | None:
    edge = next((p for p in (Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                             Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")) if p.exists()), None)
    if edge is None:
        return None
    out = html.with_suffix(".pdf")
    subprocess.run([str(edge), "--headless", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=6000",
                    f"--print-to-pdf={out}", html.resolve().as_uri()], check=False, capture_output=True, timeout=120)
    return out if out.exists() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datos", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="Duncan")
    a = ap.parse_args()
    curvas = pd.read_csv(Path(a.datos) / "estres_curvas_diarias.csv", index_col=0, parse_dates=True)
    M = metricas(curvas)
    slug = re.sub(r"\W+", " ", a.name).strip()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    archivos = {f"Kriptty agents - Stress test - for {slug}.html": doc("en", a.name, M),
                f"Kriptty agentes - Estres test - para {slug}.html": doc("es", a.name, M),
                f"Kriptty agents - Stress test - for {slug} (EN-ES).html": bilingual(a.name, M)}
    for nombre, html in archivos.items():
        ruta = out / nombre
        ruta.write_text(html, encoding="utf-8")
        hecho = pdf(ruta) if "(EN-ES)" not in nombre else None
        print(nombre, "· PDF" if hecho else "")


if __name__ == "__main__":
    main()
