# Estrategia PUDA · presentación y test de estrés

| Archivo | Qué es |
|---|---|
| `estrategia.html` | Presentación completa en español, en pestañas: Resumen, Sociedad, ICO, Bot y backtest, Test de estrés, Rentabilidad, Mi estrategia, Riesgos y pasos. Se abre con doble clic. |
| `estrategia.en.html` | La misma presentación en inglés (botón ES/EN arriba a la derecha). |
| `datos/ejecuciones.csv` | Todas las ejecuciones del test de estrés, una fila por cuenta, escenario, trayectoria y configuración. Se abre en Excel (separador `;`). |
| `datos/cobertura_short.json` | Cálculo del apalancamiento de la cuenta short (SUB5) frente a las demás. |
| `datos/rentabilidad_mensual.json` | Rentabilidad mes a mes de cada cartera y configuración. |
| `datos/mi_estrategia.json` | La propuesta: cinco agentes con 1 M$ por subcuenta, 20% en juego por tramos y operaciones de 48H como mucho. |

Navegación: pestañas arriba (también con las flechas ← → del teclado sobre ellas) y botones de
pestaña anterior/siguiente al final de cada una. Se recuerda la última pestaña abierta.

## Cómo comprobar el test de estrés

1. **En la presentación**: pestaña «Test de estrés», sección «Compruébalo tú». Muestra cada
   ejecución: elige la configuración (antes / después / 7x con margen del 20% / SUB5 de 2x a 10x /
   mi estrategia), el escenario y la trayectoria.
2. **En Excel**: abre `datos/ejecuciones.csv`. Columnas principales:
   - `conjunto`: `antes` (código original), `despues` (con los arreglos), `x7m20` (7x, margen ≤ 20%,
     mismo tamaño de posición), `x7m20r` (7x, margen ≤ 20%, posiciones escaladas al apalancamiento),
     `sub5` (la cuenta short de 2x a 10x), `mi` (la propuesta: 3x, margen ≤ 20%, rampa, pausa al 10%,
     parada dura al 20% y operaciones de 48H como mucho).
   - `escenario` / `semilla`: mercado sintético. Misma semilla = mismo mercado para todas las cuentas.
   - `rentabilidad_pct`, `max_drawdown_pct`, `sharpe`, `operaciones`, `comisiones_usdt`: resultado de
     una cuenta de 10.000 USDT durante 3 años (oct 2023 – oct 2026). Los porcentajes valen igual para
     una subcuenta de 1 M$ (multiplica las cifras en USDT por 100).
   - `archivo`: el JSON con la curva diaria completa que genera `kriptty-stress`.
3. **Reproduciéndolo** (Python 3.11+, desde la carpeta del repositorio). Es determinista: la misma
   semilla da exactamente las mismas cifras.

```bash
pip install -e .
kriptty-stress --out data/stress_despues                                             # 9 estrategias × 3 escenarios × 3 semillas
kriptty-stress --scenarios ciclo --seeds 1 --cost-mult 2 --out data/stress_despues    # costes ×2
kriptty-stress --strategies SUB2,SUB5,SUB6,SUB9,SUB10,SUB11 --leverage 7 --max-margin 0.2 --out data/stress_x7m20
kriptty-stress --strategies SUB2,SUB9,SUB10,SUB11 --leverage 7 --max-margin 0.2 --scale-risk --out data/stress_x7m20r
for L in 2 3 5 7 10; do kriptty-stress --strategies SUB5 --leverage $L --max-margin 0.2 --scale-risk --out data/stress_sub5; done
python scripts/hedge_short.py --others data/stress_x7m20 --sub8 data/stress_despues --sweep data/stress_sub5
kriptty-stress --strategies SUB5,SUB6,SUB8,SUB9,SUB10 --leverage 3 --max-margin 0.2 --scale-risk \
  --set capital_ramp=0.25,0.5,0.75,1 --set max_drawdown_pct=0.1 --set max_total_drawdown_pct=0.2 \
  --set max_hold_hours=48 --tag mi --out data/stress_mi
python scripts/plan_agents.py data/stress_mi
python scripts/export_runs.py despues=data/stress_despues x7m20=data/stress_x7m20 sub5=data/stress_sub5 mi=data/stress_mi
```

Para comparar con el código original («antes»), ejecuta lo mismo desde el commit anterior a los
arreglos (`git log -- src/kriptty/backtest/stress.py`).

## Qué mide y qué no

- Mide el **riesgo**: cuánto se pierde en los peores meses, en los crashes y cuántas veces salta la
  parada dura. Los escenarios incluyen crashes de un día tipo LUNA/FTX, un bear de 3 años y un
  lateral con flash crashes.
- **No mide la rentabilidad real**: el mercado sintético no tiene ventaja explotable (salvo la
  reversión a la media que aprovecha SUB2, que es un efecto del generador). La rentabilidad se valida
  con el backtest sobre histórico real de Bitget y con la demo.

## Cuenta short (SUB5)

Es la cuenta que opera en corto cuando hay caída y las demás se paran. Apalancamiento recomendado:
**3x** con margen ≤ 20% (hasta 0,6× la cuenta en corto). Cálculo: la beta a la baja de las demás
cuentas suma ≈ 0,47 (pierden 0,47% de una cuenta por cada 1% que cae BTC) → 0,47 / 0,20 ≈ 2,4 → 3x.
Más de 5x no cubre más y solo aumenta el riesgo de la propia SUB5. Detalle en la pestaña «Mi estrategia».

## Configuración propuesta (`.env`)

Las 11 subcuentas, un agente en cada una: SUB5, SUB6, SUB8, SUB9 y SUB10 con dinero real desde el
día 1; SUB1, SUB2, SUB4, SUB7 y SUB11 en Bitget Demo hasta graduarse (90 días, en beneficio y con caída
≤ 10%), después con como mucho 100.000 $; SUB3 en espera de la API de copy trading de Bitget.
`kriptty-engine --status` muestra el modo, el límite, los días, el resultado y la graduación de cada una.

```
TRADING_MODE=live                 # con CONFIRM_LIVE_TRADING=yes
ENABLED_STRATEGIES=SUB1,SUB2,SUB4,SUB5,SUB6,SUB7,SUB8,SUB9,SUB10,SUB11
ACCOUNT_MODES=SUB1=demo,SUB2=demo,SUB4=demo,SUB7=demo,SUB11=demo
MAX_MARGIN_BY_ACCOUNT=SUB1=0.1,SUB2=0.1,SUB4=0.1,SUB7=0.1,SUB11=0.1
MAX_MARGIN_PCT=0.2                # 200.000 $ de cada subcuenta de 1 M$
CAPITAL_RAMP=0.25,0.5,0.75,1      # 50k → 100k → 150k → 200k, un escalón por mes en beneficio
LEVERAGE=SUB6=3,SUB9=3,SUB10=3    # SUB5 ya va a 3x; SUB8 opera en spot
MAX_DRAWDOWN_PCT=0.10             # pausa de 14 días al −10%
MAX_TOTAL_DRAWDOWN_PCT=0.20       # parada dura al −20%: como mucho 200.000 $ por subcuenta
MAX_HOLD_HOURS=48                 # compras y ventas en 48H (SUB6, SUB8 y SUB9 exentas por diseño)
```
