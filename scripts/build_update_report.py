"""«Update for Duncan»: el sistema de agentes por subcuenta, los tres perfiles de riesgo y la hoja de ruta de PUDA.

    python scripts/build_update_report.py --datos "C:/PROYECTOS IA/kriptty/configs/grids" --out <carpeta> [--name Duncan]

Lee ``perfiles_curvas.csv`` y ``perfiles.json`` (``scripts/estres_perfiles.py``) y la referencia de BTC de
``estres_curvas_diarias.csv``. No explica cómo funciona cada estrategia: solo su papel en la cartera y sus
resultados. Estilo ejecutivo con los colores de peludacoin.com (azul noche y dorado).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_estres_report import RUTA, pdf  # noqa: E402
from build_investor_docs import cls, nbsp, pct  # noqa: E402

CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; background: #FFFFFF; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font: 9.8pt/1.45 'Inter', Arial, sans-serif; color: #273043; }
.page { width: 210mm; height: 297mm; padding: 14mm 16mm 13mm 19mm; position: relative; overflow: hidden; break-after: page; background: #FFFFFF; }
.page:last-child { break-after: auto; }
.bar { position: absolute; left: 0; top: 0; width: 4.5mm; height: 297mm; background: linear-gradient(180deg, #FBD641, #E0A21B); }
.hero { margin: -14mm -16mm 6mm -19mm; padding: 13mm 16mm 9mm 19mm; background: #0A0F1E; color: #FFFFFF; }
.hero .kicker { color: #FBD641; } .hero h1 { color: #FFFFFF; } .hero .sub { color: #B8C0D2; margin: 0; }
.kicker { font-size: 7.9pt; letter-spacing: .18em; text-transform: uppercase; color: #9A7409; font-weight: 700; margin: 0 0 3mm; }
h1 { font-family: 'Sora', 'Inter', sans-serif; font-weight: 700; font-size: 22pt; line-height: 1.12; color: #0A0F1E; margin: 0 0 2.5mm; letter-spacing: -.01em; }
h2 { font-family: 'Sora', 'Inter', sans-serif; font-weight: 600; font-size: 12.4pt; color: #0A0F1E; margin: 5mm 0 2mm; }
h2::after { content: ""; display: block; width: 12mm; height: .8mm; background: #FBD641; margin-top: 1.2mm; }
h3 { font-size: 9.8pt; font-weight: 700; color: #0A0F1E; margin: 0 0 1mm; }
p { margin: 0 0 2mm; } .sub { color: #4B5468; font-size: 9.8pt; margin-bottom: 4mm; }
.muted { color: #5A6378; } .small { font-size: 7.9pt; color: #6B7488; line-height: 1.4; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 3mm; margin: 2mm 0 1mm; }
.kpi { border: 1px solid #E3E7EF; border-top: 1mm solid #FBD641; border-radius: 2mm; padding: 2.8mm 3.2mm; background: #FAFBFD; }
.kpi .nm { font-size: 7.6pt; letter-spacing: .1em; text-transform: uppercase; color: #6B7488; font-weight: 700; }
.kpi b { display: block; font-family: 'Sora', 'Inter', sans-serif; font-size: 17pt; font-weight: 700; color: #0A0F1E; line-height: 1.15; margin-top: 1mm; }
.kpi span { font-size: 8pt; color: #5A6378; display: block; margin-top: .6mm; }
.kpi.btc { background: #F3F4F7; } .kpi.btc b { color: #6B7488; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 3.5mm 5mm; }
.card { border: 1px solid #E3E7EF; border-radius: 2mm; padding: 3mm 3.6mm; background: #FAFBFD; }
.card p { font-size: 8.9pt; color: #4B5468; margin: 0; }
table { border-collapse: collapse; width: 100%; font-size: 8.7pt; }
th, td { text-align: left; padding: 1.25mm 1.6mm; border-bottom: 1px solid #E6E9F0; vertical-align: top; }
th { font-size: 7.1pt; letter-spacing: .06em; text-transform: uppercase; color: #6B7488; font-weight: 700; white-space: nowrap; }
td.r, th.r { text-align: right; white-space: nowrap; }
.pos { color: #157347; } .neg { color: #B42318; }
tr.tot td { background: #0A0F1E; color: #FFFFFF; font-weight: 700; } tr.tot td.pos { color: #6EE7A0; } tr.tot td.neg { color: #FCA5A5; }
tr.hl td { background: #FFF8DB; font-weight: 600; }
tr.btc td { color: #7C8498; }
table.apretada th, table.apretada td { padding: 1.2mm 1mm; font-size: 8pt; } table.apretada th { font-size: 6.6pt; letter-spacing: .03em; }
.foot { position: absolute; left: 19mm; right: 16mm; bottom: 8mm; display: flex; justify-content: space-between; font-size: 7.4pt; color: #8A93A6; }
ol.steps, ul.lst { margin: 0; padding-left: 5mm; } ol.steps li, ul.lst li { margin-bottom: 1.1mm; }
.pill { display: inline-block; font-size: 7pt; letter-spacing: .06em; text-transform: uppercase; border-radius: 99px; padding: .3mm 2mm; margin-left: 1.5mm; border: 1px solid #C9A227; color: #8A6A08; font-weight: 700; }
.pill.hecho { background: #FBD641; border-color: #FBD641; color: #0A0F1E; }
.fase { display: grid; grid-template-columns: 27mm 1fr; gap: 0 4mm; padding: 2.1mm 0; border-bottom: 1px solid #E6E9F0; }
.fase .cuando { font-weight: 700; color: #0A0F1E; font-size: 8.8pt; }
.fase h3 { margin: 0 0 .5mm; } .fase p { margin: 0; color: #4B5468; font-size: 8.8pt; }
.box { border-left: 1.2mm solid #FBD641; background: #FFFBEA; padding: 2.6mm 3.6mm; border-radius: 0 2mm 2mm 0; margin: 2mm 0; }
.langbar { position: fixed; top: 10px; right: 14px; display: flex; gap: 6px; z-index: 9; font: 600 12px 'Inter', Arial, sans-serif; }
.langbar button { border: 1px solid #D5DAE4; background: #FFFFFF; color: #0A0F1E; border-radius: 999px; padding: 6px 12px; cursor: pointer; }
.langbar button.on { background: #0A0F1E; color: #FBD641; border-color: #0A0F1E; }
@media screen { body { padding: 24px 0; background: #E9ECF2; } .page { margin: 0 auto 24px; box-shadow: 0 2px 18px rgba(10,15,30,.18); } }
@media print { .langbar { display: none; } }
"""
FUENTES = "https://fonts.googleapis.com/css2?family=Sora:wght@400..700&family=Inter:wght@400..700&display=swap"
SUBS = ["mom", "short", "rot", "bull", "scalp", "meme", "dca"]
PERF = ["conservador", "equilibrado", "dinamico"]
CRACKS = {"luna": ("2022-05-05", "2022-05-15"), "celsius": ("2022-06-10", "2022-06-20"), "ftx": ("2022-11-06", "2022-11-12"),
          "aug24": ("2024-08-01", "2024-08-08"), "oct25": ("2025-10-09", "2025-10-12")}

T = {
    "en": {
        "title": "Update for {name} · Kriptty and PUDA · October 2026",
        "kicker": "Kriptty × PeludaCoin (PUDA) · Update for {name} · October 2026 · Confidential",
        "h1": "AI agents for every subaccount: six years of real markets",
        "sub": ("The trading capital is split into subaccounts. Each one runs a different strategy with its own AI agent, "
                "which follows its own news and market signals every day. The goal is balance: when some lose, others win, "
                "and there is always a short subaccount ready for falling markets."),
        "kpi_nm": {"conservador": "Conservative", "equilibrado": "Balanced", "dinamico": "Dynamic", "btc": "Bitcoin (ref.)"},
        "kpi_lbl": "a year on average · max. fall {dd}",
        "h_how": "How it works",
        "how": [("Seven subaccounts, seven agents", "Each subaccount has a single strategy and a dedicated AI agent. The "
                 "strategies have different formats and look at different signals, so they do not all win or lose at the same time."),
                ("Every agent reads the news, every day", "Market trend, fear & greed, specialised press, Google News and the "
                 "CoinGecko and CoinMarketCap trends. An AI reviewer can only brake: it raises the risk level or vetoes a coin."),
                ("Every coin is checked before trading", "Project age and size, token unlocks, exchanges, backers, security "
                 "scan, number of wallets and whether a single wallet could crash the price. Risky coins are blocked."),
                ("A short subaccount, always ready", "When the market turns down, the short subaccount takes over. It is what "
                 "kept the portfolio positive in 2022, when Bitcoin lost 64%.")],
        "h_risk": "Risk rules, checked every hour",
        "risk": ["A gradual exit first and a hard stop if the price keeps going against a position.",
                 "Each subaccount pauses for two weeks after a 10% fall and stops after 25% until a person reviews it.",
                 "No agent adds risk without human approval; memecoin trades are always closed within 24 hours."],
        "h_subs": "The subaccounts and their role",
        "th_subs": ["Subaccount", "Role in the portfolio", "Active when", "Cons.", "Bal.", "Dyn."],
        "subs": {"mom": ("Trend", "Main engine in rising markets", "Bull and uncertain markets"),
                 "short": ("Short", "Protection: earns when the market falls", "Bear markets · always on standby"),
                 "rot": ("Rotation", "Diversification in rising markets", "Bull and uncertain markets"),
                 "bull": ("Bull-run booster", "Extra return in strong bull markets", "Strong bull runs only"),
                 "scalp": ("Bull-run scalper", "Under test, small weight", "Strong bull runs only"),
                 "meme": ("Memecoins", "Small and experimental, 24-hour trades", "When there is hype"),
                 "dca": ("DCA BTC/ETH", "Long-term savings, no leverage", "Always, weekly")},
        "h_prof": "Three risk profiles",
        "prof_txt": ("The same subaccounts and rules, with different weights and position sizes. The Dynamic profile puts more "
                     "weight on the subaccounts that performed best and less on DCA, which falls hardest in bear markets."),
        "size": {"conservador": "Base size", "equilibrado": "About ×1.5", "dinamico": "About ×2"},
        "size_row": "Position size",
        "h_res": "Results year by year",
        "th_res": ["Profile", "Avg. 21–25", "Per year", "Total", "Max. fall"],
        "res_note": ("2020 starts in October and 2026 ends in September. Each profile's yearly figure is the weighted average of "
                     "its subaccounts, with the weights restored every 1 January. Fees included."),
        "h_w": "Weighted average by subaccount · Dynamic profile",
        "w_note": "Each cell is the subaccount's return that year multiplied by its weight; the bottom row is their sum.",
        "w_tot": "Weighted average",
        "h_usd": "Annual profit on a $1,000,000 portfolio",
        "h_crash": "In each crash",
        "th_crash": ["Crash", "Dates", "Bitcoin", "Conservative", "Balanced", "Dynamic"],
        "crash": {"luna": ("LUNA collapse", "May 2022"), "celsius": ("Celsius and 3AC", "Jun 2022"), "ftx": ("FTX bankruptcy", "Nov 2022"),
                  "aug24": ("Yen carry-trade crash", "Aug 2024"), "oct25": ("10 October liquidations", "Oct 2025")},
        "h_read": "What the results say",
        "read": [("{d_cagr} a year with a {d_dd} maximum fall", "The Dynamic profile turns $1M into {d_usd} over six years. "
                  "Bitcoin did more, but fell {btc_dd} on the way; the portfolio never fell more than {d_dd_abs}."),
                 ("Balance works", "In 2022, when Bitcoin lost 64%, every profile stayed positive thanks to the short subaccount. "
                  "In the crashes the portfolio moved less than 3.5%."),
                 ("Returns come in bull markets", "2021 and 2024 carry most of the result; 2025 and 2026 are flatter. That is why "
                  "the shorts and the bull-run booster sit side by side."),
                 ("Choose the profile, not the promise", "These are historical results with their maximum fall, not a promise of "
                  "return. Live results will be published every month.")],
        "h_next": "Next steps",
        "next": ["Four to eight weeks of live simulation: the agents decide every day without touching money and their daily "
                 "reports are compared with the market.",
                 "Then capital is added gradually, one subaccount at a time, with every increase in exposure approved by a person.",
                 "In parallel, the PUDA roadmap on the last page: company, contract audit, CNAD registration and registered pre-sale."],
        "legal": ("Backtest on historical data from October 2020 to September 2026: past results do not guarantee future returns. "
                  "Hourly prices, exchange fees included; funding costs not included in the memecoin subaccount. The profiles were "
                  "defined with the full history in view, so the results may be somewhat optimistic. For information only: this is "
                  "not an offer of tokens or investment."),
        "foot": "Kriptty × PUDA · Update for {name} · October 2026",
    },
    "es": {
        "title": "Actualización para {name} · Kriptty y PUDA · Octubre 2026",
        "kicker": "Kriptty × PeludaCoin (PUDA) · Actualización para {name} · Octubre 2026 · Confidencial",
        "h1": "Agentes de IA para cada subcuenta: seis años de mercado real",
        "sub": ("El capital de trading se reparte en subcuentas. Cada una tiene una estrategia distinta con su propio agente de IA, "
                "que sigue sus propias noticias y señales de mercado cada día. El objetivo es el equilibrio: cuando unas pierden, "
                "otras ganan, y siempre hay una subcuenta de cortos lista para los mercados que caen."),
        "kpi_nm": {"conservador": "Conservador", "equilibrado": "Equilibrado", "dinamico": "Dinámico", "btc": "Bitcoin (ref.)"},
        "kpi_lbl": "al año de media · caída máx. {dd}",
        "h_how": "Cómo funciona",
        "how": [("Siete subcuentas, siete agentes", "Cada subcuenta tiene una sola estrategia y un agente de IA dedicado. Las "
                 "estrategias tienen formatos distintos y miran señales distintas, así que no ganan ni pierden todas a la vez."),
                ("Cada agente lee las noticias, cada día", "Tendencia del mercado, miedo y codicia, prensa especializada, Google News "
                 "y las tendencias de CoinGecko y CoinMarketCap. Un revisor de IA solo puede frenar: sube el nivel de riesgo o veta una moneda."),
                ("Cada moneda se revisa antes de operar", "Antigüedad y tamaño del proyecto, desbloqueos de tokens, exchanges, fondos "
                 "detrás, escaneo de seguridad, número de carteras y si una sola cartera podría tumbar el precio. Las peligrosas se bloquean."),
                ("Una subcuenta de cortos, siempre lista", "Cuando el mercado se gira a la baja, la subcuenta de cortos toma el relevo. "
                 "Es la que mantuvo la cartera en positivo en 2022, cuando Bitcoin perdió un 64%.")],
        "h_risk": "Reglas de riesgo, revisadas cada hora",
        "risk": ["Primero una salida gradual y un stop duro si el precio sigue en contra de una posición.",
                 "Cada subcuenta se pausa dos semanas si cae un 10% y se para si cae un 25%, hasta que la revise una persona.",
                 "Ningún agente añade riesgo sin aprobación de una persona; las operaciones con memecoins se cierran siempre en 24 horas."],
        "h_subs": "Las subcuentas y su papel",
        "th_subs": ["Subcuenta", "Papel en la cartera", "Activa cuando", "Cons.", "Equil.", "Dinám."],
        "subs": {"mom": ("Tendencia", "Motor principal cuando el mercado sube", "Mercado alcista e incierto"),
                 "short": ("Cortos", "Protección: gana cuando el mercado cae", "Mercado bajista · siempre preparada"),
                 "rot": ("Rotación", "Diversificación cuando el mercado sube", "Mercado alcista e incierto"),
                 "bull": ("Impulso de bull run", "Rentabilidad extra en bull run fuerte", "Solo en bull run fuerte"),
                 "scalp": ("Scalper de bull run", "En prueba, con poco peso", "Solo en bull run fuerte"),
                 "meme": ("Memecoins", "Pequeña y experimental, operaciones de 24 horas", "Cuando hay hype"),
                 "dca": ("DCA BTC/ETH", "Ahorro a largo plazo, sin apalancamiento", "Siempre, cada semana")},
        "h_prof": "Tres perfiles de riesgo",
        "prof_txt": ("Las mismas subcuentas y reglas, con distintos pesos y tamaños de posición. El perfil Dinámico da más peso a "
                     "las subcuentas que mejor funcionaron y menos al DCA, que es el que más cae en los mercados bajistas."),
        "size": {"conservador": "Tamaño base", "equilibrado": "Aprox. ×1,5", "dinamico": "Aprox. ×2"},
        "size_row": "Tamaño de posición",
        "h_res": "Resultados año a año",
        "th_res": ["Perfil", "Media 21–25", "Al año", "Total", "Caída máx."],
        "res_note": ("2020 empieza en octubre y 2026 acaba en septiembre. La cifra de cada año es la media ponderada de las subcuentas "
                     "del perfil, con los pesos reajustados cada 1 de enero. Comisiones incluidas."),
        "h_w": "Media ponderada por subcuenta · perfil Dinámico",
        "w_note": "Cada celda es la rentabilidad de la subcuenta ese año multiplicada por su peso; la última fila es la suma.",
        "w_tot": "Media ponderada",
        "h_usd": "Beneficio anual de una cartera de 1.000.000 $",
        "h_crash": "En cada crack",
        "th_crash": ["Crack", "Fechas", "Bitcoin", "Conservador", "Equilibrado", "Dinámico"],
        "crash": {"luna": ("Hundimiento de LUNA", "may 2022"), "celsius": ("Celsius y 3AC", "jun 2022"), "ftx": ("Quiebra de FTX", "nov 2022"),
                  "aug24": ("Crack del yen", "ago 2024"), "oct25": ("Liquidaciones del 10 de octubre", "oct 2025")},
        "h_read": "Qué dicen los resultados",
        "read": [("{d_cagr} al año con una caída máxima del {d_dd_abs}", "El perfil Dinámico convierte 1 M$ en {d_usd} en seis años. "
                  "Bitcoin hizo más, pero cayó un {btc_dd} por el camino; la cartera nunca cayó más de un {d_dd_abs}."),
                 ("El equilibrio funciona", "En 2022, cuando Bitcoin perdió un 64%, todos los perfiles quedaron en positivo gracias a "
                  "la subcuenta de cortos. En los cracks la cartera se movió menos de un 3,5%."),
                 ("La rentabilidad llega en los mercados alcistas", "2021 y 2024 aportan casi todo; 2025 y 2026 son más planos. Por eso "
                  "los cortos y el impulso de bull run van juntos."),
                 ("Se elige el perfil, no una promesa", "Son resultados históricos con su caída máxima, no una promesa de rentabilidad. "
                  "Los resultados en vivo se publicarán cada mes.")],
        "h_next": "Siguientes pasos",
        "next": ["De cuatro a ocho semanas de simulación en vivo: los agentes deciden cada día sin tocar dinero y sus informes diarios "
                 "se comparan con el mercado.",
                 "Después, capital poco a poco, subcuenta a subcuenta, con cada subida de exposición aprobada por una persona.",
                 "En paralelo, la hoja de ruta de PUDA de la última página: sociedad, auditoría del contrato, registro en la CNAD y preventa registrada."],
        "legal": ("Backtest con datos históricos de octubre de 2020 a septiembre de 2026: los resultados pasados no garantizan "
                  "rentabilidades futuras. Precios por hora, comisiones del exchange incluidas; el coste de financiación no está "
                  "incluido en la subcuenta de memecoins. Los perfiles se definieron viendo todo el histórico, así que los resultados "
                  "pueden ser algo optimistas. Documento informativo: no es una oferta de tokens ni de inversión."),
        "foot": "Kriptty × PUDA · Actualización para {name} · Octubre 2026",
    },
}


def datos(carpeta: Path) -> dict:
    cur = pd.read_csv(carpeta / "perfiles_curvas.csv", index_col=0, parse_dates=True).ffill()
    btc = pd.read_csv(carpeta / "estres_curvas_diarias.csv", index_col=0, parse_dates=True)["BTC (referencia)"]
    cur["btc"] = btc.reindex(cur.index).ffill()
    perfiles = json.loads((carpeta / "perfiles.json").read_text(encoding="utf-8"))
    years = sorted({d.year for d in cur.index})
    dias = (cur.index[-1] - cur.index[0]).days
    M = {"years": years, "perfiles": perfiles, "anual": {}, "cagr": {}, "dd": {}, "total": {}, "crash": {}}
    for col in cur.columns:
        e = cur[col].dropna()
        a = pd.concat([e.iloc[:1], e.resample("YE").last()]).pct_change().dropna() * 100
        M["anual"][col] = {d.year: float(v) for d, v in a.items()}
        M["total"][col] = float((e.iloc[-1] / e.iloc[0] - 1) * 100)
        M["cagr"][col] = float(((e.iloc[-1] / e.iloc[0]) ** (365.25 / dias) - 1) * 100)
        M["dd"][col] = float((e / e.cummax() - 1).min() * 100)
        M["crash"][col] = {}
        for k, (i, f) in CRACKS.items():
            tr = e[(e.index >= pd.Timestamp(i, tz="UTC")) & (e.index <= pd.Timestamp(f, tz="UTC"))]
            M["crash"][col][k] = float((tr.min() / tr.iloc[0] - 1) * 100) if len(tr) > 1 else 0.0
    return M


def paginas(lang: str, name: str, M: dict) -> str:
    t = T[lang]
    p = lambda v: pct(v, lang)  # noqa: E731
    absp = lambda v: pct(abs(v), lang).lstrip("+")  # noqa: E731
    money = lambda v: (("+" if v > 0 else "−" if v < 0 else "") + (f"${abs(v):,.0f}" if lang == "en" else f"{abs(v):,.0f}".replace(",", ".") + " $"))  # noqa: E731
    years = M["years"]
    foot = t["foot"].format(name=name)
    ycols = "".join(f'<th class="r">{y}</th>' for y in years)
    pie = lambda n: f'<div class="foot"><span>{foot}</span><span>{n} / 5</span></div></div>'  # noqa: E731

    # ── página 1: portada y cómo funciona ──
    kpis = "".join(
        f'<div class="kpi{" btc" if c == "btc" else ""}"><div class="nm">{t["kpi_nm"][c]}</div><b>{p(M["cagr"][c])}</b>'
        f'<span>{t["kpi_lbl"].format(dd=p(M["dd"][c]))}</span></div>' for c in PERF + ["btc"])
    how = "".join(f'<div class="card"><h3>{a}</h3><p>{b}</p></div>' for a, b in t["how"])
    p1 = (f'<div class="page"><div class="bar"></div><div class="hero"><p class="kicker">{t["kicker"].format(name=name)}</p>'
          f'<h1>{t["h1"]}</h1><p class="sub">{t["sub"]}</p></div><div class="kpis">{kpis}</div>'
          f'<h2>{t["h_how"]}</h2><div class="grid2">{how}</div>'
          f'<h2>{t["h_risk"]}</h2><ul class="lst">' + "".join(f"<li>{x}</li>" for x in t["risk"]) + "</ul>" + pie(1))

    # ── página 2: subcuentas y perfiles ──
    filas = "".join(
        f'<tr><td><b>{t["subs"][k][0]}</b></td><td>{t["subs"][k][1]}</td><td class="muted">{t["subs"][k][2]}</td>'
        + "".join(f'<td class="r">{M["perfiles"][pf]["pesos"][k]:.0%}</td>' for pf in PERF) + "</tr>" for k in SUBS)
    filas += (f'<tr class="hl"><td colspan="3">{t["size_row"]}</td>'
              + "".join(f'<td class="r">{t["size"][pf]}</td>' for pf in PERF) + "</tr>")
    th = t["th_subs"]
    p2 = (f'<div class="page"><div class="bar"></div><h2 style="margin-top:0">{t["h_subs"]}</h2>'
          f'<table><colgroup><col style="width:20%"><col style="width:34%"><col style="width:22%"><col style="width:8%"><col style="width:8%"><col style="width:8%"></colgroup>'
          f'<tr><th>{th[0]}</th><th>{th[1]}</th><th>{th[2]}</th><th class="r">{th[3]}</th><th class="r">{th[4]}</th><th class="r">{th[5]}</th></tr>{filas}</table>'
          f'<h2>{t["h_prof"]}</h2><div class="box">{t["prof_txt"]}</div>')
    # resultados por perfil
    rows = ""
    for c in PERF + ["btc"]:
        an = M["anual"][c]
        media = sum(an.get(y, 0) for y in range(2021, 2026)) / 5
        clase = "btc" if c == "btc" else ("tot" if c == "dinamico" else "")
        rows += (f'<tr class="{clase}"><td><b>{t["kpi_nm"][c]}</b></td>' + "".join(
            f'<td class="r {"" if c == "btc" else cls(an.get(y, 0))}">{p(an.get(y, 0))}</td>' for y in years)
            + f'<td class="r">{p(media)}</td><td class="r">{p(M["cagr"][c])}</td><td class="r">{p(M["total"][c])}</td>'
            f'<td class="r">{p(M["dd"][c])}</td></tr>')
    tr = t["th_res"]
    p2 += (f'<h2>{t["h_res"]}</h2><table class="apretada"><colgroup><col style="width:13%">' + '<col style="width:7.3%">' * len(years)
           + '<col style="width:8.4%"><col style="width:7.6%"><col style="width:8.6%"><col style="width:9.3%"></colgroup>'
           f'<tr><th>{tr[0]}</th>{ycols}<th class="r">{tr[1]}</th><th class="r">{tr[2]}</th><th class="r">{tr[3]}</th><th class="r">{tr[4]}</th></tr>{rows}</table>'
           f'<p class="small" style="margin-top:2mm">{t["res_note"]}</p>' + pie(2))

    # ── página 3: media ponderada por subcuenta (dinámico) y beneficio anual ──
    pf = "dinamico"
    pes = M["perfiles"][pf]["pesos"]
    wfilas = "".join(f'<tr><td>{t["subs"][k][0]} <span class="muted">({pes[k]:.0%})</span></td>' + "".join(
        f'<td class="r {cls(pes[k] * M["anual"][f"{pf}:{k}"].get(y, 0))}">{p(pes[k] * M["anual"][f"{pf}:{k}"].get(y, 0))}</td>' for y in years)
        + "</tr>" for k in SUBS)
    wfilas += (f'<tr class="tot"><td>{t["w_tot"]}</td>' + "".join(
        f'<td class="r">{p(sum(pes[k] * M["anual"][f"{pf}:{k}"].get(y, 0) for k in SUBS))}</td>' for y in years) + "</tr>")
    ufilas = ""
    for c in PERF:
        cap, vals = 1_000_000.0, []
        for y in years:
            b = cap * M["anual"][c].get(y, 0) / 100
            vals.append(b)
            cap += b
        clase = "tot" if c == "dinamico" else ""
        ufilas += (f'<tr class="{clase}"><td><b>{t["kpi_nm"][c]}</b></td>' + "".join(f'<td class="r">{money(v)}</td>' for v in vals)
                   + f'<td class="r"><b>{money(sum(vals))}</b></td></tr>')
    p3 = (f'<div class="page"><div class="bar"></div><h2 style="margin-top:0">{t["h_w"]}</h2>'
          f'<table><colgroup><col style="width:23%">' + '<col style="width:11%">' * len(years) + f'</colgroup><tr><th></th>{ycols}</tr>{wfilas}</table>'
          f'<p class="small" style="margin-top:2mm">{t["w_note"]}</p>'
          f'<h2>{t["h_usd"]}</h2><table><colgroup><col style="width:14%">' + '<col style="width:10.8%">' * len(years)
          + f'<col style="width:10.4%"></colgroup><tr><th></th>{ycols}<th class="r">Total</th></tr>{ufilas}</table>')
    cfilas = "".join(f'<tr><td><b>{t["crash"][k][0]}</b></td><td class="muted">{t["crash"][k][1]}</td>'
                     f'<td class="r neg">{p(M["crash"]["btc"][k])}</td>'
                     + "".join(f'<td class="r {cls(M["crash"][c][k])}">{p(M["crash"][c][k])}</td>' for c in PERF) + "</tr>" for k in CRACKS)
    tc = t["th_crash"]
    p3 += (f'<h2>{t["h_crash"]}</h2><table><tr><th>{tc[0]}</th><th>{tc[1]}</th>' + "".join(f'<th class="r">{x}</th>' for x in tc[2:])
           + f'</tr>{cfilas}</table>' + pie(3))

    # ── página 4: lectura y siguientes pasos ──
    cap = 1_000_000 * (1 + M["total"]["dinamico"] / 100)
    f = {"d_cagr": p(M["cagr"]["dinamico"]), "d_dd": p(M["dd"]["dinamico"]), "d_dd_abs": absp(M["dd"]["dinamico"]),
         "d_usd": money(cap).lstrip("+"), "btc_dd": absp(M["dd"]["btc"])}
    lect = "".join(f'<div class="card"><h3>{a.format(**f)}</h3><p>{b.format(**f)}</p></div>' for a, b in t["read"])
    p4 = (f'<div class="page"><div class="bar"></div><h2 style="margin-top:0">{t["h_read"]}</h2><div class="grid2">{lect}</div>'
          f'<h2>{t["h_next"]}</h2><ol class="steps">' + "".join(f"<li>{x}</li>" for x in t["next"]) + "</ol>"
          f'<p class="small" style="margin-top:6mm">{t["legal"]}</p>' + pie(4))

    # ── páginas 5 y 6: hoja de ruta de PUDA (como en peludacoin.com) ──
    r = RUTA[lang]
    fases = "".join(f'<div class="fase"><div class="cuando">{c}</div><div><h3>{titulo}<span class="pill {"hecho" if e == "done" else ""}">'
                    f'{r["estado"][e]}</span></h3><p>{texto}</p></div></div>' for c, e, titulo, texto in r["fases"])
    p5 = (f'<div class="page"><div class="bar"></div><div class="hero"><p class="kicker">{r["r_kicker"].format(name=name)}</p>'
          f'<h1>{r["r_h1"]}</h1><p class="sub">{r["r_sub"]}</p></div>{fases}'
          f'<p class="small" style="margin-top:4mm">{r["r_note"]}</p>' + pie(5))
    return p1 + p2 + p3 + p4 + p5


def cabecera(titulo: str, lang: str) -> str:
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{titulo}</title>'
            f'<link rel="stylesheet" href="{FUENTES}"><style>{CSS}</style></head><body>')


def doc(lang: str, name: str, M: dict) -> str:
    return nbsp(cabecera(T[lang]["title"].format(name=name), lang) + paginas(lang, name, M) + "</body></html>")


def bilingual(name: str, M: dict) -> str:
    menu = ('<nav class="langbar" aria-label="Language"><button type="button" data-l="en" class="on">English</button>'
            '<button type="button" data-l="es">Español</button></nav>')
    script = ("<script>document.querySelectorAll('.langbar button').forEach(b=>b.onclick=()=>{"
              "document.querySelectorAll('.langbar button').forEach(x=>x.classList.toggle('on',x===b));"
              "document.querySelectorAll('[data-lang]').forEach(d=>d.hidden=d.dataset.lang!==b.dataset.l);"
              "document.documentElement.lang=b.dataset.l;});</script>")
    body = (menu + f'<div data-lang="en">{paginas("en", name, M)}</div><div data-lang="es" hidden>{paginas("es", name, M)}</div>' + script)
    return nbsp(cabecera(T["en"]["title"].format(name=name), "en") + body + "</body></html>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datos", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="Duncan")
    a = ap.parse_args()
    M = datos(Path(a.datos))
    slug = re.sub(r"\W+", " ", a.name).strip()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    archivos = {f"Update for {slug} - Kriptty and PUDA (October 2026).html": doc("en", a.name, M),
                f"Actualizacion para {slug} - Kriptty y PUDA (octubre 2026).html": doc("es", a.name, M),
                f"Update for {slug} - Kriptty and PUDA (October 2026) (EN-ES).html": bilingual(a.name, M)}
    for nombre, html in archivos.items():
        ruta = out / nombre
        ruta.write_text(html, encoding="utf-8")
        print(nombre, "· PDF" if "(EN-ES)" not in nombre and pdf(ruta) else "")


if __name__ == "__main__":
    main()
