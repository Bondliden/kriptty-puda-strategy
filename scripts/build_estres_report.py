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

TEMA_PELUDA = """
html, body { background: #06080F; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: 'Inter', Arial, sans-serif; color: #D7DBE4; }
.page { background: #06080F; }
.bar { background: linear-gradient(180deg, #FBD641, #F5A524); }
.kicker { color: #FBD641; }
h1, h2 { font-family: 'Sora', 'Inter', sans-serif; color: #FFFFFF; letter-spacing: -.01em; }
h1 { font-weight: 700; } h2 { font-weight: 600; } h3 { color: #FFFFFF; }
.sub, .muted { color: #A3AABA; } .small { color: #8B93A7; }
.kpi, .card { background: #0F1422; border: 1px solid #262E42; }
.kpi b { font-family: 'Sora', 'Inter', sans-serif; color: #FBD641; }
.kpi span { color: #A3AABA; }
th { color: #8B93A7; } th, td { border-bottom: 1px solid rgba(255,255,255,.10); }
.pos { color: #4ADE80; } .neg { color: #F87171; }
tr.tot td { background: #1A2133; color: #FFFFFF; }
tr.btc td { color: #7C8498; }
.foot { color: #6B7389; }
.fase { display: grid; grid-template-columns: 27mm 1fr; gap: 0 4mm; padding: 2.2mm 0; border-bottom: 1px solid rgba(255,255,255,.10); }
.fase .cuando { font-weight: 700; color: #FBD641; font-size: 9pt; }
.fase .estado { display: inline-block; font-size: 7.4pt; letter-spacing: .06em; text-transform: uppercase; border-radius: 99px; padding: .3mm 2mm; margin-left: 2mm; border: 1px solid #FBD641; color: #FBD641; }
.fase .estado.hecho { background: #FBD641; color: #06080F; }
.fase h3 { margin: 0 0 .6mm; } .fase p { margin: 0; color: #A3AABA; font-size: 9pt; }
.compacto { gap: 3mm 5mm; } .compacto .card { padding: 2.6mm 3.4mm; } .compacto .card p { font-size: 8.5pt; line-height: 1.36; }
.compacto .card h3 { font-size: 9.6pt; }
.langbar button { background: #0F1422; color: #FFFFFF; border-color: #262E42; }
.langbar button.on { background: #FBD641; color: #06080F; border-color: #FBD641; }
@media screen { .page { box-shadow: 0 2px 22px rgba(0,0,0,.6); } }
"""
FUENTES = ("https://fonts.googleapis.com/css2?family=Sora:wght@400..700&family=Inter:wght@400..700&display=swap")


def cabecera(titulo: str, lang: str) -> str:
    html = head(titulo, lang).replace(CSS, CSS + EXTRA + TEMA_PELUDA)
    return __import__("re").sub(r'href="https://fonts\.googleapis\.com[^"]*"', f'href="{FUENTES}"', html, count=1)


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
        "port": "Portfolio (weighted)", "btc": "BTC (reference)",
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
        "port": "Cartera (ponderada)", "btc": "BTC (referencia)",
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


SISTEMA = {
    "en": {
        "s_kicker": "Kriptty · How the system works · October 2026 · Confidential · For {name}",
        "s_h1": "Seven subaccounts, seven AI agents, one portfolio",
        "s_sub": ("The capital is split into seven subaccounts on the exchange. Each one has a single strategy and its own "
                  "AI agent, which decides every day which coin to trade, how much and whether to trade at all. A risk "
                  "watcher checks every position every hour. A person keeps the final say on adding risk."),
        "s_h_day": "What each agent does, every day",
        "s_day": [("Reads the market", "Classifies Bitcoin's trend as bull, sideways, bear or uncertain. Each subaccount only "
                   "trades in the market it was built for and stays out of the rest."),
                  ("Picks the coins", "Ranks the most liquid altcoins among the top 200 by market capitalisation, listed on "
                   "Kraken, by the criterion of its strategy. Memecoins only enter the memecoin subaccount."),
                  ("Reads the news", "Fear & greed index, specialised press, Google News, CoinGecko and CoinMarketCap trends. "
                   "An AI reviewer can only brake: raise the risk level or veto a coin, never add risk."),
                  ("Checks each coin's file", "Age, size, token unlocks, exchanges where it trades, backers, security scan, "
                   "number of wallets and whether a single wallet could crash the price. Risky coins are blocked; "
                   "solid projects get more exposure."),
                  ("Decides and reports", "Coin, mode and size for each trading bot of its subaccount, with a daily report "
                   "of what it did and why.")],
        "s_h_risk": "Risk rules, every hour",
        "s_risk": ["A coin that stops qualifying first goes to a gradual exit: no new entries, the position closes with its own "
                   "take-profits. If the price keeps going against it, it is closed.",
                   "A hard stop closes any position far from its entry price. Memecoin trades are always closed within 24 hours.",
                   "A subaccount pauses for two weeks after a 10% fall and stops completely after 25%, until a person reviews it.",
                   "An agent never changes coin while a position is open, never trades a coin used by another bot of the same "
                   "subaccount and never adds risk without human approval."],
        "s_h_alloc": "The seven subaccounts",
        "s_th": ["Subaccount", "Strategy", "Trades when", "Weight"],
        "when": {"mom": "Bull and uncertain markets", "short": "Bear markets", "rot": "Bull and uncertain markets",
                 "bull": "Strong bull runs only", "scalp": "Strong bull runs only", "meme": "Any market, 24 h per trade",
                 "dca": "Always (weekly)"},
        "d_h": "Each subaccount in detail",
        "d": {"mom": ("Rides the altcoins with the strongest recent trend, buying in small steps as the price dips and selling "
                      "in small steps as it recovers. It is the main engine of the portfolio in rising markets."),
              "short": ("When Bitcoin falls, it shorts the altcoins that have not fallen yet and usually follow. It is the "
                        "portfolio's protection in bear markets: 2022 was its best year."),
              "rot": ("When Bitcoin rises, it buys the altcoins that are lagging behind and usually catch up. It runs with a "
                      "small weight while it proves itself."),
              "bull": ("Only switches on when Bitcoin rises more than 20% in 30 days, about one day in six, with a larger size. "
                       "It is the strongest piece of the portfolio."),
              "scalp": ("The same strong-bull days, with very fast in-and-out trades. It is under test with a small weight."),
              "meme": ("Watches memecoin hype every hour and shorts the ones that have just doubled and start to fall, closing "
                       "every trade within 24 hours. Small and experimental."),
              "dca": ("Buys Bitcoin and Ethereum every week, without leverage and without selling. It is the long-term saving "
                      "of the portfolio.")},
        "d_res": "Result: {cagr} a year · maximum fall {dd}",
        "w_h": "Weighted average of all subaccounts, year by year",
        "w_note": ("Each cell is the subaccount's return that year multiplied by its weight; the bottom row adds them up. "
                   "The weights are restored every 1 January."),
        "w_row": "Weighted average", "w_avg": "Average 2021–2025",
        "foot_s": "Kriptty · How the system works · October 2026",
    },
    "es": {
        "s_kicker": "Kriptty · Cómo funciona el sistema · Octubre 2026 · Confidencial · Para {name}",
        "s_h1": "Siete subcuentas, siete agentes de IA, una cartera",
        "s_sub": ("El capital se reparte en siete subcuentas del exchange. Cada una tiene una sola estrategia y su propio agente "
                  "de IA, que decide cada día qué moneda operar, cuánto y si operar o no. Un vigilante de riesgo revisa cada "
                  "posición cada hora. Una persona tiene siempre la última palabra para añadir riesgo."),
        "s_h_day": "Qué hace cada agente, cada día",
        "s_day": [("Lee el mercado", "Clasifica la tendencia de Bitcoin en alcista, lateral, bajista o incierta. Cada subcuenta "
                   "solo opera en el mercado para el que está hecha y se queda fuera del resto."),
                  ("Elige las monedas", "Ordena las altcoins más líquidas del top 200 por capitalización, listadas en Kraken, "
                   "según el criterio de su estrategia. Las memecoins solo entran en la subcuenta de memecoins."),
                  ("Lee las noticias", "Índice de miedo y codicia, prensa especializada, Google News y tendencias de CoinGecko y "
                   "CoinMarketCap. Un revisor de IA solo puede frenar: subir el nivel de riesgo o vetar una moneda, nunca añadir riesgo."),
                  ("Revisa la ficha de cada moneda", "Antigüedad, tamaño, desbloqueos de tokens, exchanges donde cotiza, fondos "
                   "detrás, escaneo de seguridad, número de carteras y si una sola cartera podría tumbar el precio. Las "
                   "peligrosas se bloquean; los proyectos sólidos reciben más exposición."),
                  ("Decide e informa", "Moneda, modo y tamaño de cada bot de su subcuenta, con un informe diario de lo que ha "
                   "hecho y por qué.")],
        "s_h_risk": "Reglas de riesgo, cada hora",
        "s_risk": ["Una moneda que deja de cumplir pasa primero a una salida gradual: no entra más y la posición se cierra con sus "
                   "propias ventas. Si el precio sigue en contra, se cierra.",
                   "Un stop duro cierra cualquier posición que se aleje mucho de su precio de entrada. Las operaciones con "
                   "memecoins se cierran siempre en 24 horas.",
                   "Una subcuenta se pausa dos semanas si cae un 10% y se para del todo si cae un 25%, hasta que la revise una persona.",
                   "Un agente nunca cambia de moneda con una posición abierta, nunca usa la moneda de otro bot de la misma "
                   "subcuenta y nunca añade riesgo sin aprobación de una persona."],
        "s_h_alloc": "Las siete subcuentas",
        "s_th": ["Subcuenta", "Estrategia", "Opera cuando", "Peso"],
        "when": {"mom": "Mercado alcista e incierto", "short": "Mercado bajista", "rot": "Mercado alcista e incierto",
                 "bull": "Solo en bull run fuerte", "scalp": "Solo en bull run fuerte", "meme": "Siempre, 24 h por operación",
                 "dca": "Siempre (cada semana)"},
        "d_h": "Cada subcuenta en detalle",
        "d": {"mom": ("Sigue a las altcoins con la tendencia reciente más fuerte, comprando en pequeños tramos cuando el precio "
                      "baja y vendiendo en pequeños tramos cuando se recupera. Es el motor principal de la cartera cuando el mercado sube."),
              "short": ("Cuando Bitcoin cae, abre cortos en las altcoins que aún no han caído y suelen seguirle. Es la protección "
                        "de la cartera en mercado bajista: 2022 fue su mejor año."),
              "rot": ("Cuando Bitcoin sube, compra las altcoins que se han quedado atrás y suelen alcanzarle. Funciona con poco "
                      "peso mientras se demuestra."),
              "bull": ("Solo se enciende cuando Bitcoin sube más de un 20% en 30 días, uno de cada seis días, con más tamaño. Es "
                       "la pieza más fuerte de la cartera."),
              "scalp": ("Los mismos días de bull run fuerte, con entradas y salidas muy rápidas. Está en prueba, con poco peso."),
              "meme": ("Vigila cada hora el hype de las memecoins y abre cortos en las que acaban de doblar y empiezan a caer; "
                       "cierra cada operación en 24 horas. Pequeña y experimental."),
              "dca": ("Compra Bitcoin y Ethereum cada semana, sin apalancamiento y sin vender. Es el ahorro a largo plazo de la cartera.")},
        "d_res": "Resultado: {cagr} al año · caída máxima {dd}",
        "w_h": "Media ponderada de todas las subcuentas, año a año",
        "w_note": ("Cada celda es la rentabilidad de la subcuenta ese año multiplicada por su peso; la última fila las suma. Los "
                   "pesos se reajustan cada 1 de enero."),
        "w_row": "Media ponderada", "w_avg": "Media 2021–2025",
        "foot_s": "Kriptty · Cómo funciona el sistema · Octubre 2026",
    },
}


RUTA = {
    "en": {
        "r_kicker": "PeludaCoin (PUDA) · Roadmap · October 2026 · Confidential · For {name}",
        "r_h1": "From here to launch: the steps",
        "r_sub": ("PUDA will be issued under El Salvador's Digital Asset Issuance Law once registered with the CNAD. Part of "
                  "the funds raised will go to the trading system described in this report, with audited monthly results. "
                  "Dates are estimates: they depend on the audit and on the CNAD registration."),
        "fases": [("Q3 2026", "done", "Website", "Public website in English and Spanish with the project's rules and documentation."),
                  ("Q4 2026", "now", "Company and clean-up", "Set up the issuing company and its legal structure; remove any promise of "
                   "return from the website and documents."),
                  ("Q4 2026 – Q1 2027", "next", "Audit and bot in demo", "Hire an independent auditor (CertiK or Hacken) to certify "
                   "the token contract: supply, vesting, liquidity lock and per-wallet sale limits. Meanwhile the AI agents run on "
                   "demo accounts and their results start being published."),
                  ("Q1 – Q2 2027", "next", "Registration in El Salvador", "Issuer registration with the CNAD, anti-money-laundering "
                   "registration with the UIF, and review of the offering document (DIR) by an authorised certifier."),
                  ("From Q1 2027", "next", "Marketing", "Interest list, community and content in English and Spanish, always "
                   "without promising returns, and the audit report public before any sale."),
                  ("Q2 – Q3 2027", "next", "Registered pre-sale", "Sale of the registered tranche (15%) only through licensed PSAD "
                   "providers, with KYC and per-wallet caps."),
                  ("Q3 2027", "next", "Launch and liquidity", "Listing with the liquidity pool locked by contract for 24 months, "
                   "monthly bot results and quarterly reports."),
                  ("2028", "next", "Governance", "Gradual move to governance by token holders, within what the regulation allows.")],
        "estado": {"done": "Completed", "now": "In progress", "next": "Planned"},
        "r_note": ("The pre-sale only opens after the audit, the CNAD registration and the certified offering document. "
                   "Nothing in this document is an offer of tokens or investment advice."),
        "u_h": "Annual profit of all subaccounts together (portfolio of $1,000,000)",
        "u_note": "Profit of each subaccount with its weight of the capital at the start of each year; the bottom row is the total.",
        "u_tot": "All subaccounts",
        "foot_r": "PeludaCoin (PUDA) · Roadmap · October 2026",
    },
    "es": {
        "r_kicker": "PeludaCoin (PUDA) · Hoja de ruta · Octubre 2026 · Confidencial · Para {name}",
        "r_h1": "De aquí al lanzamiento: los pasos",
        "r_sub": ("PUDA se emitirá bajo la Ley de Emisión de Activos Digitales de El Salvador una vez registrada en la CNAD. "
                  "Parte de los fondos irá al sistema de trading de este informe, con resultados mensuales auditados. Las "
                  "fechas son estimaciones: dependen de la auditoría y del registro en la CNAD."),
        "fases": [("T3 2026", "done", "Web", "Web pública en inglés y español con las reglas y la documentación del proyecto."),
                  ("T4 2026", "now", "Sociedad y limpieza", "Constituir la sociedad emisora y su estructura legal; quitar de la web "
                   "y los documentos cualquier promesa de rentabilidad."),
                  ("T4 2026 – T1 2027", "next", "Auditoría y bot en demo", "Contratar un auditor independiente (CertiK o Hacken) "
                   "que certifique el contrato del token: suministro, vesting, bloqueo de liquidez y límites de venta por cartera. "
                   "Mientras, los agentes de IA operan en cuentas demo y se empiezan a publicar sus resultados."),
                  ("T1 – T2 2027", "next", "Registro en El Salvador", "Registro del emisor en la CNAD, registro antiblanqueo en la "
                   "UIF y revisión del documento de oferta (DIR) por un certificador autorizado."),
                  ("Desde T1 2027", "next", "Marketing", "Lista de interesados, comunidad y contenidos en inglés y español, siempre "
                   "sin prometer rentabilidad, y el informe de auditoría público antes de cualquier venta."),
                  ("T2 – T3 2027", "next", "Preventa registrada", "Venta del tramo registrado (15%) solo a través de proveedores "
                   "PSAD con licencia, con KYC y límites por cartera."),
                  ("T3 2027", "next", "Lanzamiento y liquidez", "Cotización con el pool de liquidez bloqueado por contrato 24 "
                   "meses, resultados mensuales del bot e informes trimestrales."),
                  ("2028", "next", "Gobernanza", "Paso gradual a la gobernanza de los poseedores del token, dentro de lo que permita la regulación.")],
        "estado": {"done": "Hecho", "now": "En curso", "next": "Previsto"},
        "r_note": ("La preventa solo se abre después de la auditoría, el registro en la CNAD y el documento de oferta certificado. "
                   "Nada de este documento es una oferta de tokens ni una recomendación de inversión."),
        "u_h": "Beneficio anual de todas las subcuentas juntas (cartera de 1.000.000 $)",
        "u_note": "Beneficio de cada subcuenta con su peso del capital al empezar cada año; la última fila es el total.",
        "u_tot": "Todas las subcuentas",
        "foot_r": "PeludaCoin (PUDA) · Hoja de ruta · Octubre 2026",
    },
}


def pagina_ruta(lang: str, name: str) -> str:
    r = RUTA[lang]
    fases = "".join(
        f'<div class="fase"><div class="cuando">{c}</div><div><h3>{titulo}<span class="estado {"hecho" if e == "done" else ""}">'
        f'{r["estado"][e]}</span></h3><p>{texto}</p></div></div>' for c, e, titulo, texto in r["fases"])
    return (f'<div class="page"><div class="bar"></div><p class="kicker">{r["r_kicker"].format(name=name)}</p>'
            f'<h1>{r["r_h1"]}</h1><p class="sub">{r["r_sub"]}</p>{fases}'
            f'<p class="small" style="margin-top:4mm">{r["r_note"]}</p>'
            f'<div class="foot"><span>{r["foot_r"]}</span><span>6 / 6</span></div></div>')


def tabla_beneficios(lang: str, M: dict) -> str:
    t, r = T[lang], RUTA[lang]
    years = M["years"]
    money = lambda v: (("+" if v > 0 else "−" if v < 0 else "") + (f"${abs(v):,.0f}" if lang == "en" else f"{abs(v):,.0f}".replace(",", ".") + " $"))  # noqa: E731
    capital, por = 1_000_000.0, {k: [] for _, k, _ in CUENTAS}
    totales = []
    for y in years:
        suma = 0.0
        for col, k, w in CUENTAS:
            b = capital * w * M["anual"][col].get(y, 0.0) / 100
            por[k].append(b)
            suma += b
        totales.append(suma)
        capital += suma
    filas = "".join(f'<tr><td>{t["names"][k][0]}</td>' + "".join(f'<td class="r {cls(v)}">{money(v)}</td>' for v in vals)
                    + f'<td class="r {cls(sum(vals))}">{money(sum(vals))}</td></tr>' for k, vals in por.items())
    filas += (f'<tr class="tot"><td>{r["u_tot"]}</td>' + "".join(f'<td class="r">{money(v)}</td>' for v in totales)
              + f'<td class="r">{money(sum(totales))}</td></tr>')
    cab = "".join(f'<th class="r">{y}</th>' for y in years)
    return (f'<h2>{r["u_h"]}</h2><table class="wide"><colgroup><col style="width:17%">' + '<col style="width:10.4%">' * len(years)
            + f'<col style="width:10.2%"></colgroup><tr><th></th>{cab}<th class="r">{t["total"]}</th></tr>{filas}</table>'
            f'<p class="small" style="margin-top:2mm">{r["u_note"]}</p>')


def cartera_reajustada(curvas: pd.DataFrame) -> pd.Series:
    """Cartera con los pesos de CUENTAS reajustados cada 1 de enero: su rentabilidad de cada año es
    exactamente la media ponderada de las subcuentas."""
    curvas = curvas.ffill().fillna(1.0)
    valor, partes = 1.0, []
    for _, tramo in curvas.groupby(curvas.index.year):
        base = tramo.iloc[0]
        anterior = curvas[curvas.index < tramo.index[0]]
        if len(anterior):
            base = anterior.iloc[-1]
        seg = sum(w * tramo[col] / base[col] for col, _, w in CUENTAS) * valor
        partes.append(seg)
        valor = float(seg.iloc[-1])
    return pd.concat(partes)


def metricas(curvas: pd.DataFrame) -> dict:
    curvas = curvas.ffill()
    curvas[PORT] = cartera_reajustada(curvas[[c for c, _, _ in CUENTAS]])
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

    e = M["anual"][PORT]
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
          f'<div class="foot"><span>{t["foot"]}</span><span>3 / 6</span></div></div>')
    ponderada = tabla_ponderada(lang, M).replace("<h2>", '<h2 style="margin-top:0">', 1)
    p2 = (f'<div class="page"><div class="bar"></div>{ponderada}{tabla_beneficios(lang, M)}'
          f'<div class="foot"><span>{t["foot"]}</span><span>4 / 6</span></div></div>'
          f'<div class="page"><div class="bar"></div><h2 style="margin-top:0">{t["h_crisis"]}</h2>{crisis}'
          f'<h2>{t["h_read"]}</h2><div class="grid2">{cards}</div>'
          f'<h2>{t["h_next"]}</h2><ol class="steps">' + "".join(f"<li>{x}</li>" for x in t["next"]) + "</ol>"
          f'<p class="small" style="margin-top:6mm">{t["legal"]}</p>'
          f'<div class="foot"><span>{t["foot"]}</span><span>5 / 6</span></div></div>')
    return p1 + p2


def paginas_sistema(lang: str, name: str, M: dict) -> str:
    t, s = T[lang], SISTEMA[lang]
    p = lambda v: pct(v, lang)  # noqa: E731
    absp = lambda v: pct(abs(v), lang).lstrip("+")  # noqa: E731
    pasos = "".join(f"<li><b>{a}.</b> {b}</li>" for a, b in s["s_day"])
    riesgo = "".join(f"<li>{x}</li>" for x in s["s_risk"])
    filas = "".join(f'<tr><td><b>{t["names"][k][0]}</b></td><td>{t["names"][k][1]}</td><td>{s["when"][k]}</td>'
                    f'<td class="r">{w:.0%}</td></tr>' for _, k, w in CUENTAS)
    reparto = (f'<table><tr><th>{s["s_th"][0]}</th><th>{s["s_th"][1]}</th><th>{s["s_th"][2]}</th>'
               f'<th class="r">{s["s_th"][3]}</th></tr>{filas}</table>')
    p1 = (f'<div class="page"><div class="bar"></div><p class="kicker">{s["s_kicker"].format(name=name)}</p>'
          f'<h1>{s["s_h1"]}</h1><p class="sub">{s["s_sub"]}</p>'
          f'<h2>{s["s_h_day"]}</h2><ol class="steps">{pasos}</ol>'
          f'<h2>{s["s_h_risk"]}</h2><ul style="margin:0;padding-left:5mm">{riesgo}</ul>'
          f'<div class="foot"><span>{s["foot_s"]}</span><span>1 / 6</span></div></div>')
    tarjetas = "".join(
        f'<div class="card"><h3>{t["names"][k][0]} · {w:.0%}</h3><p class="muted" style="margin:0 0 2mm">{s["d"][k]}</p>'
        f'<p style="margin:0;font-size:8.6pt"><b>{s["d_res"].format(cagr=p(M["cagr"][col]), dd=absp(M["dd"][col]))}</b></p></div>'
        for col, k, w in CUENTAS)
    p2 = (f'<div class="page"><div class="bar"></div>'
          f'<h2 style="margin-top:0">{s["s_h_alloc"]}</h2>{reparto}'
          f'<h2>{s["d_h"]}</h2><div class="grid2 compacto">{tarjetas}</div>'
          f'<div class="foot"><span>{s["foot_s"]}</span><span>2 / 6</span></div></div>')
    return p1 + p2


def tabla_ponderada(lang: str, M: dict) -> str:
    t, s = T[lang], SISTEMA[lang]
    p = lambda v: pct(v, lang)  # noqa: E731
    years = M["years"]
    filas = []
    for col, k, w in CUENTAS:
        filas.append(f'<tr><td>{t["names"][k][0]} <span class="muted">({w:.0%})</span></td>'
                     + "".join(f'<td class="r {cls(w * M["anual"][col].get(y, 0))}">{p(w * M["anual"][col].get(y, 0))}</td>' for y in years) + "</tr>")
    tot = {y: sum(w * M["anual"][col].get(y, 0) for col, _, w in CUENTAS) for y in years}
    completos = [tot[y] for y in years if 2021 <= y <= 2025]
    media = sum(completos) / len(completos)
    filas.append(f'<tr class="tot"><td>{s["w_row"]}</td>' + "".join(f'<td class="r {cls(tot[y])}"><b>{p(tot[y])}</b></td>' for y in years) + "</tr>")
    cab = "".join(f'<th class="r">{y}</th>' for y in years)
    return (f'<h2>{s["w_h"]}</h2><table class="wide"><colgroup><col style="width:23%">' + '<col style="width:11%">' * len(years)
            + f'</colgroup><tr><th></th>{cab}</tr>{"".join(filas)}</table>'
            f'<p class="small" style="margin-top:2mm">{s["w_note"]} <b>{s["w_avg"]}: {p(media)}</b>.</p>')


def doc(lang: str, name: str, M: dict) -> str:
    html = cabecera(T[lang]["title"], lang)
    return nbsp(html + paginas_sistema(lang, name, M) + pages(lang, name, M) + pagina_ruta(lang, name) + "</body></html>")


def bilingual(name: str, M: dict) -> str:
    html = cabecera(T["en"]["title"], "en")
    menu = ('<nav class="langbar" aria-label="Language"><button type="button" data-l="en" class="on">English</button>'
            '<button type="button" data-l="es">Español</button></nav>')
    script = ("<script>document.querySelectorAll('.langbar button').forEach(b=>b.onclick=()=>{"
              "document.querySelectorAll('.langbar button').forEach(x=>x.classList.toggle('on',x===b));"
              "document.querySelectorAll('[data-lang]').forEach(d=>d.hidden=d.dataset.lang!==b.dataset.l);"
              "document.documentElement.lang=b.dataset.l;document.title=b.dataset.l==='en'?"
              f"{T['en']['title']!r}:{T['es']['title']!r};}});</script>")
    body = (menu + f'<div data-lang="en">{paginas_sistema("en", name, M)}{pages("en", name, M)}{pagina_ruta("en", name)}</div>'
            f'<div data-lang="es" hidden>{paginas_sistema("es", name, M)}{pages("es", name, M)}{pagina_ruta("es", name)}</div>' + script)
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
    archivos = {f"Update for {slug} - Kriptty and PUDA (October 2026).html": doc("en", a.name, M),
                f"Actualizacion para {slug} - Kriptty y PUDA (octubre 2026).html": doc("es", a.name, M),
                f"Update for {slug} - Kriptty and PUDA (October 2026) (EN-ES).html": bilingual(a.name, M)}
    for nombre, html in archivos.items():
        ruta = out / nombre
        ruta.write_text(html, encoding="utf-8")
        hecho = pdf(ruta) if "(EN-ES)" not in nombre else None
        print(nombre, "· PDF" if hecho else "")


if __name__ == "__main__":
    main()
