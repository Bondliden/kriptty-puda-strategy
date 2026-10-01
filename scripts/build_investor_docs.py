"""Genera la carta de actualización y el resumen de una página (ES y EN) con el backtest real.

    python scripts/embed_backtest_real.py data/real_v2      # crea estrategia/datos/backtest_real_resumen.json
    python scripts/build_investor_docs.py                   # escribe los HTML en estrategia/investor-pack/src

Los PDF se imprimen con Edge (ver estrategia/LEEME.md). Las cifras salen del JSON: no se copian a mano.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "estrategia" / "investor-pack" / "src"
R = json.loads((ROOT / "estrategia" / "datos" / "backtest_real_resumen.json").read_text(encoding="utf-8"))

CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; background: #F7F5EF; }
body { font: 10.4pt/1.45 'Public Sans', Arial, sans-serif; color: #1B2433; }
.page { width: 210mm; height: 297mm; padding: 15mm 17mm 13mm; position: relative; overflow: hidden; break-after: page; }
.page:last-child { break-after: auto; }
.bar { position: absolute; left: 0; top: 0; width: 5mm; height: 297mm; background: #D4AF37; }
.kicker { font-size: 8.4pt; letter-spacing: .18em; text-transform: uppercase; color: #7A5C0E; font-weight: 600; margin: 0 0 3mm; }
h1 { font-family: 'Source Serif 4', Georgia, serif; font-weight: 600; font-size: 23pt; line-height: 1.12; color: #10172A; margin: 0 0 2mm; }
h2 { font-family: 'Source Serif 4', Georgia, serif; font-weight: 600; font-size: 13pt; color: #10172A; margin: 5mm 0 1.5mm; }
h3 { font-size: 10.4pt; font-weight: 700; color: #10172A; margin: 0 0 1mm; }
p { margin: 0 0 2mm; }
.sub { color: #4A5568; font-size: 10pt; margin-bottom: 5mm; }
.muted { color: #4A5568; }
.small { font-size: 8.2pt; color: #6B7486; line-height: 1.4; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 3mm; margin: 3mm 0 2mm; }
.kpi { background: #FFFDF8; border: 1px solid #E2DCCB; border-radius: 3mm; padding: 3mm 3.5mm; }
.kpi b { display: block; font-family: 'Source Serif 4', Georgia, serif; font-size: 18pt; font-weight: 600; color: #10172A; line-height: 1.1; }
.kpi span { font-size: 8.3pt; color: #4A5568; line-height: 1.3; display: block; margin-top: 1mm; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm 6mm; }
.card { background: #FFFDF8; border: 1px solid #E2DCCB; border-radius: 3mm; padding: 3.5mm 4mm; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; }
th, td { text-align: left; padding: 1.3mm 2mm; border-bottom: 1px solid rgba(16,23,42,.14); vertical-align: top; }
th { font-size: 7.6pt; letter-spacing: .08em; text-transform: uppercase; color: #6B7486; font-weight: 600; }
td.r, th.r { text-align: right; white-space: nowrap; }
.pos { color: #1F6F43; } .neg { color: #9B2C2C; }
tr.tot td { background: #EFEADC; font-weight: 700; }
.foot { position: absolute; left: 17mm; right: 17mm; bottom: 8mm; display: flex; justify-content: space-between; font-size: 7.6pt; color: #8C97AD; }
ol.steps { margin: 0; padding-left: 5mm; } ol.steps li { margin-bottom: 1mm; }
.one { font-size: 9.5pt; line-height: 1.4; padding-top: 13mm; }
.one h1 { font-size: 21pt; }
.one .sub { margin-bottom: 4mm; }
.one .card { padding: 3mm 3.5mm; }
.one h2 { margin-top: 4mm; }
.one td, .one th { padding: 1.1mm 2mm; }
"""

YEARS = sorted(int(y) for y in R["years"])


def pct(v: float, lang: str) -> str:
    d = 2 if 0 < abs(v) < 0.1 else 1
    if round(abs(v), d) == 0:
        return "0,0%" if lang == "es" else "0.0%"
    s = f"{abs(v):.{d}f}"
    if lang == "es":
        s = s.replace(".", ",")
    return ("+" if v > 0 else "−") + s + "%"


def cls(v: float) -> str:
    return "pos" if v > 0.05 else "neg" if v < -0.05 else ""


def y(year: int, key: str) -> float:
    return R["years"][str(year)][key]


def c(crisis: str, key: str) -> float:
    return R["crises"][crisis][key]


DD, BTC_DD = R["max_dd"]["PORT"], R["max_dd"]["BTC"]
WORST_Y = min(y(a, "PORT") for a in YEARS)
TOTAL = R["total"]["PORT"]

L = {
    "es": {
        "lang": "es", "dec": ",",
        "letter_title": "PUDA · Carta de actualización (octubre 2026)",
        "letter_kicker": "PeludaCoin (PUDA) · Carta de actualización",
        "letter_h1": "Dónde está PUDA y qué viene ahora",
        "letter_sub": "Octubre 2026 · Confidencial · Para [Nombre del inversor]",
        "dear": "Estimado/a [Nombre del inversor]:",
        "intro": ("Le enviamos una breve actualización sobre PUDA: las decisiones tomadas, el trabajo de este mes, el "
                  "resultado del sistema sobre seis años de mercado real, lo que aún no está probado y los próximos pasos. "
                  "Adjuntamos la presentación completa para inversores."),
        "s1": "1. Decisiones tomadas",
        "s1p": [
            ("<b>Lanzamiento en dos fases.</b> Fase 1: auditoría del smart contract por una sola firma reconocida (CertiK, "
             "Hacken o SolidProof), preventa privada a inversores iniciales con KYC y marketing para llegar a ellos. Son los "
             "únicos costes iniciales. Fase 2, pagada en el lanzamiento con lo captado: la sociedad en El Salvador "
             "(S.A. de C.V.), los registros en la UIF y como emisor en la CNAD, el documento de la oferta (DIR) con un "
             "certificador independiente y la oferta pública a través de proveedores autorizados (PSAD)."),
            ("<b>La reserva que respalda PUDA nunca se arriesga.</b> Queda en custodia, fuera del exchange. El sistema de "
             "trading puede perder como mucho el rendimiento anual de la reserva (≈ 4%, según los tipos de interés). Un "
             "tope anual global en el software lo garantiza: al alcanzarlo, todos los agentes se paran hasta el año "
             "siguiente. El precio de mercado de PUDA puede bajar; el respaldo de cada token, no."),
            ("<b>Sistema de trading:</b> once agentes, uno por subcuenta. 1 M$ por subcuenta, como mucho el 20% en juego, "
             "desplegado por tramos desde 50.000 $ por agente y ampliado solo tras meses con beneficio. Apalancamiento 3x; "
             "operaciones de 48 horas como mucho (salvo los agentes de funding, DCA y collar); pausa al −10% y parada al −20% por subcuenta, "
             "que no vuelve a operar hasta corregir su estrategia."),
        ],
        "s2": "2. Trabajo realizado",
        "s2p": [
            ("Se revisó el software de trading de principio a fin y se completaron sus controles de riesgo: stop loss "
             "obligatorio, margen aislado, límite y rampa de capital, modos y límites por agente, paso de demo a real y el "
             "tope anual global de pérdidas. Todo cubierto por 91 pruebas automáticas."),
            ("<b>Backtest real de seis años</b> (octubre 2020 – agosto 2026): el mismo código de los agentes sobre el "
             "histórico real de 104 criptomonedas, con velas horarias de futuros y spot, comisiones, slippage, funding y "
             "datos macro reconstruidos día a día sin mirar al futuro. Cubre el mercado alcista de 2021, el bajista de "
             "2022 con LUNA y FTX, la recuperación y las caídas de 2024 a 2026."),
        ],
        "kpi": [("peor caída de la cartera en 6 años (BTC: {btc})", "dd"), ("peor año de la cartera", "wy"),
                ("durante LUNA y Celsius (BTC: {luna_btc})", "luna"), ("durante FTX (BTC: {ftx_btc})", "ftx")],
        "s2tab": "Año a año: cartera de 8 subcuentas frente a BTC",
        "th": ["", "Cartera", "BTC"],
        "s3": "3. Lo que aún no está probado",
        "s3p": [
            ("El backtest real confirma que el capital está protegido: en el peor año la cartera perdió un {wy}, dentro del "
             "tope anual del ≈ 4%. <b>No respalda todavía un objetivo de rentabilidad:</b> en seis años la cartera suma "
             "{total} sobre el capital total de las subcuentas, un {cagr} al año de media. Por eso retiramos el objetivo interno del 3–5% mensual "
             "hasta tener datos que lo sostengan."),
            ("Antes de usar dinero real se ajustan o retiran los agentes más débiles ({review} están en revisión), se repite el backtest y el sistema pasa 6 meses en el entorno demo de Bitget."),
        ],
        "s4": "4. Próximos pasos",
        "steps_th": ["Paso", "Qué"],
        "steps": [("Auditoría", "Presupuestos de CertiK, Hacken y SolidProof; se elige una."),
                  ("Marketing", "Plan y presupuesto para llegar a los inversores iniciales."),
                  ("Legal", "Abogado salvadoreño: confirmar la vía de la preventa privada bajo la LEAD y las reglas de "
                            "un token respaldado."),
                  ("Ajuste de agentes", "Revisar los agentes más débiles y repetir el backtest real."),
                  ("Demo", "Los once agentes en Bitget Demo con la configuración final."),
                  ("Socios", "Pacto de socios: capital, reparto y vesting.")],
        "s5": "5. En qué nos podría ayudar",
        "s5p": ["Su opinión sobre el tamaño de la preventa y sobre la firma de auditoría.",
                "Que nos presente a posibles inversores iniciales, cuando proceda.",
                "[Cualquier otro punto a acordar: papel, aportación, plazos.]"],
        "bye": "Un cordial saludo,", "name": "[Su nombre]", "org": "[Empresa / contacto]",
        "attach": ("Adjunto: PUDA · Presentación para inversores (PDF). Documento informativo: no es una oferta de valores "
                   "ni una recomendación de inversión. La preventa es privada y está sujeta a confirmación legal en El "
                   "Salvador; cualquier venta pública se hará solo tras el registro en la CNAD. Ningún resultado pasado o "
                   "simulado garantiza resultados futuros."),
        "foot": "PUDA · Carta de actualización · Octubre 2026",
        # Resumen
        "one_title": "PUDA · Resumen en una página (octubre 2026)",
        "one_kicker": "PeludaCoin (PUDA) · Resumen en una página · Octubre 2026",
        "one_h1": "Un token respaldado por una reserva intocable, lanzado paso a paso",
        "one_sub": "Emisor que se domiciliará en El Salvador · preventa auditada y, después, oferta registrada en la CNAD",
        "boxes": [
            ("El lanzamiento, en dos fases", "Fase 1: una auditoría de una firma reconocida (CertiK, Hacken o SolidProof), "
             "preventa privada a inversores iniciales con KYC y marketing. Fase 2, pagada con lo captado: sociedad, "
             "registros y oferta pública."),
            ("La reserva nunca se arriesga", "100% en custodia, fuera del exchange. El sistema de trading puede perder como "
             "mucho el rendimiento anual de la reserva (≈ 4%); un tope global detiene a todos los agentes al alcanzarse. En el "
             "peor año, la reserva termina al 100%."),
            ("Once agentes de trading", "Uno por subcuenta de 1 M$, como mucho 200.000 $ en juego cada uno, desplegados por "
             "tramos desde 50.000 $. Estrategias neutrales, largas con cobertura y en corto que se compensan entre sí."),
            ("Cinco capas de protección", "Stop loss en cada orden, margen aislado, tope del 20% y límites del −10% / −20% "
             "por subcuenta, el tope anual global y una reserva fuera del exchange."),
        ],
        "band": "Backtest real · 6 años de mercado (oct 2020 – ago 2026)",
        "band_kpi": [("peor caída de la cartera (BTC: {btc})", "dd"), ("peor año de la cartera", "wy"),
                     ("durante LUNA y Celsius (BTC: {luna_btc})", "luna"), ("durante FTX (BTC: {ftx_btc})", "ftx")],
        "band_note": ("8 subcuentas con la configuración del plan sobre el histórico real de 104 criptomonedas. El riesgo "
                      "está probado; la rentabilidad, no: {total} en seis años ({cagr} al año de media). El objetivo se fijará tras ajustar los "
                      "agentes y 6 meses en demo."),
        "tok_th": ["Token (61,74 M PUDA)", "%"], "use_th": ["Uso de los fondos", "%"],
        "tok": [("Venta", "15%"), ("Pool de liquidez", "5%"), ("Equipo y promoción (bloqueo de 1 año + liberación en 4)", "5%"),
                ("Tesorería (tramos solo con un nuevo registro)", "75%")],
        "use": [("Liquidez bloqueada (24 meses)", "30%"), ("Reserva del sistema de trading (solo su rendimiento en riesgo)", "25%"),
                ("Tesorería y reserva en oro", "20%"), ("Legal y auditorías · Desarrollo · Contingencia", "10 · 10 · 5%")],
        "time_th": ["Cuándo", "Qué"],
        "time": [("T4 2026", "Auditoría por una firma reconocida; informe publicado"),
                 ("T1 2027", "Preventa privada y marketing a inversores iniciales"),
                 ("T1–T2 2027", "Sociedad, UIF, registro de emisor en la CNAD, DIR y certificador"),
                 ("T3 2027", "Oferta pública vía PSAD; informes trimestrales desde entonces")],
        "one_legal": ("Documento informativo: no es una oferta de valores ni una recomendación de inversión. La preventa es privada y "
                      "está sujeta a confirmación legal en El Salvador; la venta pública se hará solo tras el registro en la "
                      "CNAD y no estará disponible en la UE ni en EE. UU. hasta cumplir su normativa. Ningún resultado pasado "
                      "o simulado garantiza resultados futuros. Contacto: [Su nombre · email]."),
    },
    "en": {
        "lang": "en", "dec": ".",
        "letter_title": "PUDA · Investor update (October 2026)",
        "letter_kicker": "PeludaCoin (PUDA) · Investor update",
        "letter_h1": "Where PUDA stands, and what comes next",
        "letter_sub": "October 2026 · Confidential · For [Investor name]",
        "dear": "Dear [Investor name],",
        "intro": ("Here is a short update on PUDA: the decisions we have taken, the work done this month, how the system "
                  "performed over six years of real markets, what is not proven yet and the next steps. The full investor "
                  "presentation is attached."),
        "s1": "1. Decisions made",
        "s1p": [
            ("<b>Launch in two phases.</b> Phase 1: a smart contract audit by a single recognised firm (CertiK, Hacken or "
             "SolidProof), a private presale to initial investors with KYC, and marketing to reach them. These are the only "
             "upfront costs. Phase 2, paid at launch with the funds raised: the company in El Salvador (S.A. de C.V.), UIF "
             "and CNAD issuer registration, the offering document (DIR) with an independent certifier, and the public "
             "offering through licensed providers (DASPs)."),
            ("<b>The reserve backing PUDA is never put at risk.</b> It stays in custody, off the exchange. The trading "
             "system can lose at most the reserve's annual yield (about 4%, depending on interest rates). A global annual "
             "cap in the trading software enforces it: once reached, every agent stops until the next year. PUDA's market "
             "price can fall; the backing of each token cannot."),
            ("<b>Trading system:</b> eleven agents, one per subaccount. $1M per subaccount, at most 20% at stake, deployed "
             "in tranches from $50,000 per agent and increased only after profitable months. 3x leverage; trades of at "
             "most 48 hours (except the funding, DCA and collar agents); a pause at −10% and a stop at −20% per subaccount, "
             "which does not trade again until its strategy is fixed."),
        ],
        "s2": "2. Work done",
        "s2p": [
            ("The trading software was reviewed end to end and its risk controls completed: mandatory stop losses, "
             "isolated margin, capital cap and ramp, per-agent modes and limits, demo-to-real graduation and the global "
             "annual loss cap. All covered by 91 automated tests."),
            ("<b>A six-year real backtest</b> (October 2020 – August 2026): the agents' own code on the real history of "
             "104 cryptocurrencies, with hourly futures and spot candles, fees, slippage, funding and macro data rebuilt "
             "day by day with no look-ahead bias. It covers the 2021 bull run, the 2022 bear market with LUNA and FTX, the "
             "recovery and the falls of 2024 to 2026."),
        ],
        "kpi": [("worst portfolio drawdown in 6 years (BTC: {btc})", "dd"), ("worst portfolio year", "wy"),
                ("during LUNA and Celsius (BTC: {luna_btc})", "luna"), ("during FTX (BTC: {ftx_btc})", "ftx")],
        "s2tab": "Year by year: 8-subaccount portfolio vs BTC",
        "th": ["", "Portfolio", "BTC"],
        "s3": "3. What is not proven yet",
        "s3p": [
            ("The real backtest confirms that the capital is protected: in its worst year the portfolio lost {wy}, within "
             "the ≈ 4% annual cap. <b>It does not yet support a return target:</b> over six years the portfolio made "
             "{total} on the subaccounts' total capital, {cagr} a year on average. We are therefore withdrawing the internal 3–5% monthly target "
             "until there is data to support it."),
            ("Before any real money is used, the weakest agents are adjusted or retired ({review} are under review), the backtest is run again and the system spends 6 months on Bitget's demo environment."),
        ],
        "s4": "4. Next steps",
        "steps_th": ["Step", "What"],
        "steps": [("Audit firm", "Quotes from CertiK, Hacken and SolidProof; we choose one."),
                  ("Marketing", "Plan and budget to reach the initial investors."),
                  ("Legal", "Salvadoran lawyer to confirm the private presale route under the LEAD and the rules for a "
                            "backed token."),
                  ("Agent adjustments", "Review the weakest agents and rerun the real backtest."),
                  ("Demo", "The eleven agents on Bitget Demo with the final configuration."),
                  ("Partners", "Shareholders' agreement: capital, split and vesting.")],
        "s5": "5. How you could help",
        "s5p": ["Your view on the presale size and on the audit firm.",
                "Introductions to potential initial investors, where appropriate.",
                "[Any other item to agree with you: role, contribution, timing.]"],
        "bye": "Best regards,", "name": "[Your name]", "org": "[Company / contact]",
        "attach": ("Attachment: PUDA · Investor presentation (PDF). For information only: this is not an offer of "
                   "securities or an investment recommendation. The presale is private and subject to legal confirmation in "
                   "El Salvador; any public sale will take place only after CNAD registration. No past or simulated result "
                   "guarantees future results."),
        "foot": "PUDA · Investor update · October 2026",
        "one_title": "PUDA · One-page summary (October 2026)",
        "one_kicker": "PeludaCoin (PUDA) · One-page summary · October 2026",
        "one_h1": "A token backed by an untouchable reserve, launched step by step",
        "one_sub": "Issuer to be domiciled in El Salvador · audited presale, then an offering registered with the CNAD",
        "boxes": [
            ("The launch, in two phases", "Phase 1: one audit by a recognised firm (CertiK, Hacken or SolidProof), a private "
             "presale to initial investors with KYC, and marketing. Phase 2, paid with the funds raised: company, "
             "registrations and the public offering."),
            ("The reserve is never put at risk", "100% in custody, off the exchange. The trading system can lose at most the "
             "reserve's annual yield (≈ 4%); a global cap stops every agent when it is reached. The worst year ends with "
             "the reserve at 100%."),
            ("Eleven trading agents", "One per subaccount of $1M, at most $200,000 at stake each, deployed in tranches from "
             "$50,000. Neutral, hedged long and short strategies that offset each other."),
            ("Five layers of protection", "Stop loss on every order, isolated margin, a 20% cap and −10% / −20% limits per "
             "subaccount, the global annual cap and a reserve kept off the exchange."),
        ],
        "band": "Real backtest · 6 years of markets (Oct 2020 – Aug 2026)",
        "band_kpi": [("worst portfolio drawdown (BTC: {btc})", "dd"), ("worst portfolio year", "wy"),
                     ("during LUNA and Celsius (BTC: {luna_btc})", "luna"), ("during FTX (BTC: {ftx_btc})", "ftx")],
        "band_note": ("8 subaccounts with the plan's settings on the real history of 104 cryptocurrencies. The risk is "
                      "proven; the returns are not: {total} over six years ({cagr} a year on average). The target will be set after adjusting the "
                      "agents and 6 months on demo."),
        "tok_th": ["Token (61.74M PUDA)", "%"], "use_th": ["Use of funds", "%"],
        "tok": [("Sale", "15%"), ("Liquidity pool", "5%"), ("Team and promotion (1-year lock + 4-year release)", "5%"),
                ("Treasury (tranches only with a new registration)", "75%")],
        "use": [("Locked liquidity (24 months)", "30%"), ("Trading system reserve (only its yield at risk)", "25%"),
                ("Treasury and gold reserve", "20%"), ("Legal, audits · Development · Contingency", "10 · 10 · 5%")],
        "time_th": ["When", "What"],
        "time": [("Q4 2026", "Audit by one recognised firm; audit report published"),
                 ("Q1 2027", "Private presale and marketing to initial investors"),
                 ("Q1–Q2 2027", "Company, UIF, CNAD issuer registration, DIR and certifier"),
                 ("Q3 2027", "Public offering via DASPs; quarterly reports from then on")],
        "one_legal": ("For information only: not an offer of securities or an investment recommendation. The presale is private and "
                      "subject to legal confirmation in El Salvador; the public sale happens only after CNAD registration "
                      "and is not available in the EU or the US until it complies with their rules. No past or simulated "
                      "result guarantees future results. Contact: [Your name · email]."),
    },
}


def head(title: str, lang: str) -> str:
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{title}</title>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@400..700'
            f'&family=Public+Sans:wght@400..700&display=swap"><style>{CSS}</style></head><body>')


def kpis(t: dict, key: str) -> str:
    vals = {"dd": DD, "wy": WORST_Y, "luna": c("luna", "PORT"), "ftx": c("ftx", "PORT")}
    fmt = {"btc": pct(BTC_DD, t["lang"]), "luna_btc": pct(c("luna", "BTC"), t["lang"]),
           "ftx_btc": pct(c("ftx", "BTC"), t["lang"])}
    return '<div class="kpis">' + "".join(
        f'<div class="kpi"><b>{pct(vals[k], t["lang"])}</b><span>{label.format(**fmt)}</span></div>'
        for label, k in t[key]) + "</div>"


def letter(lang: str) -> str:
    t = L[lang]
    review = [a for a in sorted(R["agents"], key=lambda a: R["total"][a])
              if R["total"][a] < 0 or R["max_dd"][a] < -20]
    joined = (", ".join(review[:-1]) + (" y " if lang == "es" else " and ") + review[-1]) if len(review) > 1 else "".join(review)
    fmt = {"wy": pct(abs(WORST_Y), lang).lstrip("+"), "total": pct(TOTAL, lang), "review": joined,
           "cagr": pct(R["cagr"]["PORT"], lang)}
    year_tab = (f'<table style="margin-top:2mm"><tr><th>{t["s2tab"]}</th>'
                + "".join(f'<th class="r">{a}</th>' for a in YEARS) + "</tr>"
                + f'<tr><td>{t["th"][1]}</td>' + "".join(f'<td class="r {cls(y(a, "PORT"))}">{pct(y(a, "PORT"), lang)}</td>' for a in YEARS) + "</tr>"
                + f'<tr><td>{t["th"][2]}</td>' + "".join(f'<td class="r {cls(y(a, "BTC"))}">{pct(y(a, "BTC"), lang)}</td>' for a in YEARS) + "</tr></table>")
    steps = "".join(f"<tr><td><b>{a}</b></td><td>{b}</td></tr>" for a, b in t["steps"])
    p1 = (f'<div class="page"><div class="bar"></div><p class="kicker">{t["letter_kicker"]}</p><h1>{t["letter_h1"]}</h1>'
          f'<p class="sub">{t["letter_sub"]}</p><p>{t["dear"]}</p><p>{t["intro"]}</p>'
          f'<h2>{t["s1"]}</h2>' + "".join(f"<p>{x}</p>" for x in t["s1p"])
          + f'<h2>{t["s2"]}</h2>' + "".join(f"<p>{x}</p>" for x in t["s2p"]) + kpis(t, "kpi") + year_tab
          + f'<div class="foot"><span>{t["foot"]}</span><span>1 / 2</span></div></div>')
    p2 = (f'<div class="page"><div class="bar"></div><h2 style="margin-top:0">{t["s3"]}</h2>'
          + "".join(f"<p>{x.format(**fmt)}</p>" for x in t["s3p"])
          + f'<h2>{t["s4"]}</h2><table><tr><th style="width:24%">{t["steps_th"][0]}</th><th>{t["steps_th"][1]}</th></tr>{steps}</table>'
          + f'<h2>{t["s5"]}</h2><ul style="margin:0;padding-left:5mm">' + "".join(f"<li>{x}</li>" for x in t["s5p"]) + "</ul>"
          + f'<p style="margin-top:7mm">{t["bye"]}</p><p>{t["name"]}<br>{t["org"]}</p>'
          + f'<p class="small" style="margin-top:8mm">{t["attach"]}</p>'
          + f'<div class="foot"><span>{t["foot"]}</span><span>2 / 2</span></div></div>')
    return head(t["letter_title"], lang) + p1 + p2 + "</body></html>"


def one_page(lang: str) -> str:
    t = L[lang]
    boxes = "".join(f'<div class="card"><h3>{a}</h3><p class="muted" style="margin:0">{b}</p></div>' for a, b in t["boxes"])
    tok = "".join(f'<tr><td>{a}</td><td class="r">{b}</td></tr>' for a, b in t["tok"])
    use = "".join(f'<tr><td>{a}</td><td class="r">{b}</td></tr>' for a, b in t["use"])
    tl = "".join(f'<tr><td style="white-space:nowrap"><b>{a}</b></td><td>{b}</td></tr>' for a, b in t["time"])
    body = (f'<div class="page one"><div class="bar"></div><p class="kicker">{t["one_kicker"]}</p><h1>{t["one_h1"]}</h1>'
            f'<p class="sub">{t["one_sub"]}</p><div class="grid2">{boxes}</div>'
            f'<h2>{t["band"]}</h2>' + kpis(t, "band_kpi")
            + f'<p class="small">{t["band_note"].format(total=pct(TOTAL, lang), cagr=pct(R["cagr"]["PORT"], lang))}</p>'
            + f'<div class="grid2" style="margin-top:4mm"><table><tr><th>{t["tok_th"][0]}</th><th class="r">{t["tok_th"][1]}</th></tr>{tok}</table>'
            + f'<table><tr><th>{t["use_th"][0]}</th><th class="r">{t["use_th"][1]}</th></tr>{use}</table></div>'
            + f'<table style="margin-top:4mm"><tr><th style="width:18%">{t["time_th"][0]}</th><th>{t["time_th"][1]}</th></tr>{tl}</table>'
            + f'<p class="small" style="margin-top:4mm">{t["one_legal"]}</p></div>')
    return head(t["one_title"], lang) + body + "</body></html>"


def nbsp(html: str) -> str:
    """Que «200.000 $», «1 M$» o «≈ 4%» no se partan al final de la línea."""
    return re.sub(r"(\d) (M?\$)", lambda m: m.group(1) + "&nbsp;" + m.group(2), html).replace("≈ ", "≈&nbsp;")


def main() -> None:
    SRC.mkdir(parents=True, exist_ok=True)
    names = {"es": ("2 - PUDA Carta de actualizacion (octubre 2026).html", "3 - PUDA Resumen en una pagina.html"),
             "en": ("2 - PUDA Investor update (October 2026).html", "3 - PUDA One-page summary.html")}
    for lang, (n_letter, n_one) in names.items():
        (SRC / n_letter).write_text(nbsp(letter(lang)), encoding="utf-8")
        (SRC / n_one).write_text(nbsp(one_page(lang)), encoding="utf-8")
        print(lang, n_letter, "·", n_one)


if __name__ == "__main__":
    main()
