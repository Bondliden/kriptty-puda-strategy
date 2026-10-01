# Kriptty Puda Strategy

Sistema de trading algorítmico multi-subcuenta para **Bitget**: 1 cuenta principal + 11
subcuentas, una estrategia independiente por subcuenta, **Stop Loss obligatorio** en toda
orden, un **backtester** que ejecuta el mismo código de las estrategias sobre histórico y un
**servidor MCP** para consultarlo (y opcionalmente operarlo) desde Antigravity, Claude u otro
cliente MCP.

> Revisión y cambios respecto al diseño original: [`docs/REVISION.md`](docs/REVISION.md).
> Explicación interactiva de las estrategias (abrir en el navegador): [`docs/estrategias-puda.html`](docs/estrategias-puda.html).
> Presentación en diapositivas, con la estructura del documento original: [`docs/presentacion-estrategias.html`](docs/presentacion-estrategias.html).
> Presentación interactiva con simuladores, test de estrés de 3 años y novedades de octubre 2026: [`docs/presentacion-interactiva.html`](docs/presentacion-interactiva.html).
> Hoja de ruta para lanzar el token PUDA en El Salvador (CNAD): [`docs/lanzamiento-el-salvador.html`](docs/lanzamiento-el-salvador.html).

## Estrategias

| Cuenta | Estrategia | Frecuencia | Stop Loss |
|---|---|---|---|
| SUB1 | News Sentiment (RSS + VADER/FinBERT) | cada 2H | 1.5·ATR(4H), trailing |
| SUB2 | Stat Arb "vasos comunicantes" | cada 4H | mínimo 48H − 0.5% |
| SUB3 | Copy trading nativo + guardián SL *(desactivada por defecto)* | 60 s | max(SL trader, 1.5·ATR) |
| SUB4 | Scalping 1m por WebSocket | cada vela 1m | 1.2·ATR(1m), breakeven/trailing |
| SUB5 | Macro-shorting (solo cortos) | cada 6H | máximo 7D + 0.3% |
| SUB6 | Funding rate arbitrage delta-neutral | 5 min / escaneo 30 min | basis > 1.5%, emergencia +10% |
| SUB7 | Grid adaptativo BB + ATR | 5 min | ruptura de rango |
| SUB8 | DCA inteligente spot (BTC/ETH) | diario (compra cada 48H) | max(medio − 20%, precio − 24%) |
| SUB9 | Collar dinámico por régimen macro | 15 min / rebalanceo 6H | EMA200 − 1% (máx. 15%) |
| SUB10 | Pairs trading por cointegración *(nueva, desactivada)* | cada 1H | z-score ±4, −3% del par, ±10% por pata |
| SUB11 | SuperTrend 4H stop-and-reverse *(nueva, desactivada)* | cada 4H | línea SuperTrend (trailing) |

SUB10 y SUB11 están inspiradas en los controladores `stat_arb` y `supertrend_v1` de
[Hummingbot](https://github.com/hummingbot/hummingbot/tree/master/controllers), adaptadas al
sistema (timeframes mayores, apalancamiento bajo, SL obligatorio).

## Arquitectura

```
src/kriptty/
├── config.py            # .env → Settings (dry_run | demo | live)
├── exchange/
│   ├── client.py        # ccxt async: Bitget v2 / UTA v3, demo, precisión
│   ├── paper.py         # broker simulado para dry_run
│   └── router.py        # AccountRouter: guardián SL, kill-switch, diario
├── risk/                # guard.py (regla SL), sizing.py, models.py
├── data/                # news.py, sentiment.py, macro.py (dashboard SUB5/SUB9)
├── strategies/          # sub1 … sub11
├── backtest/            # histórico Bitget + simulación con el mismo código de estrategias
├── clock.py             # reloj real o simulado (backtest)
├── engine.py            # APScheduler + tareas event-driven supervisadas
├── mcp_server.py        # SDK oficial MCP v2 (stdio / Streamable HTTP)
└── state.py             # SQLite: estado de estrategias + diario de órdenes
```

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # TRADING_MODE=dry_run por defecto
pytest                          # 75 tests, sin red
kriptty-engine --once SUB5      # un ciclo de una estrategia
kriptty-engine                  # todas las habilitadas
```

Con Docker:

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f engine
```

## Backtesting

```bash
kriptty-backtest --strategy SUB11 --start 2025-01-01 --end 2026-09-01
kriptty-backtest --strategy SUB10 --start 2025-06-01 --symbols BTC/USDT:USDT,ETH/USDT:USDT
kriptty-backtest --strategy SUB5 --start 2025-01-01 --macro-score -4   # régimen macro fijo
```

- Descarga el histórico de Bitget (OHLCV y funding, endpoints públicos, sin API key) y lo
  guarda en `data/history/`; `--offline` reutiliza la caché.
- Ejecuta **el mismo código** de la estrategia con el reloj simulado y el calendario real de
  cada una (cron/intervalo), pasando por el mismo guardián de riesgo.
- Ejecución conservadora: límites y SL/TP con el máximo/mínimo de la vela; si una vela toca SL
  y TP se asume el SL; gaps se ejecutan a la apertura; comisión taker 0.06% / maker 0.02%;
  slippage 2 pb; funding histórico aplicado a las posiciones.
- Resultado: retorno, CAGR, drawdown máximo, Sharpe, % de aciertos, profit factor, comisiones
  y funding, frente a comprar y mantener. Curva de equity y operaciones en `data/backtests/`.
- No backtesteables: SUB1 (no hay histórico de noticias) y SUB3 (copy trading nativo).
  SUB5/SUB9 usan un score macro fijo (`--macro-score`): el histórico macro no se reproduce.
- Calentamiento por defecto: 300 días para SUB5/SUB9 (EMA200 diaria) y ~4 años para SUB8
  (EMA200 semanal).

### Test de estrés de 3 años

```bash
kriptty-stress --scenarios ciclo,bear,lateral --seeds 3 --out data/stress          # código actual
kriptty-stress --scenarios ciclo --seeds 1 --cost-mult 2 --out data/stress_x2       # costes ×2
kriptty-stress --report despues=data/stress --out data/stress_report.json          # informe agregado
python scripts/embed_stress.py data/stress_report.json                              # → presentación
```

Ejecuta las 9 estrategias backtesteables con su calendario real y el guardián de riesgo sobre
mercados **sintéticos** de 3 años (`backtest/stress.py`): ciclo completo con crashes tipo LUNA/FTX,
bear market prolongado y lateral con flash crashes, varias trayectorias Monte Carlo cada uno.
Modelo: factor de mercado con volatilidad agrupada, colas gruesas y crashes correlacionados;
componente idiosincrático con reversión parcial; funding persistente ligado al régimen; spot con
basis; macro observable con 30 días de retraso. SUB4 se prueba en velas de 1m en el mes del mayor
crash de cada trayectoria. Mide supervivencia y control del riesgo, **no** predice rentabilidad.
Resultados y hallazgos: [`docs/presentacion-interactiva.html`](docs/presentacion-interactiva.html).

### Camino recomendado hacia real

1. **`backtest`**: al menos 12–24 meses por estrategia, con comisiones reales.
2. **`dry_run`**: precios reales, órdenes simuladas. Revisa logs y `get_order_journal`.
3. **`demo`**: crea API keys de *Demo Trading* en Bitget para cada subcuenta. Mínimo 2–4
   semanas por estrategia.
4. **`live`**: `TRADING_MODE=live` + `CONFIRM_LIVE_TRADING=yes`. API keys **sin permiso de
   retiro** y con lista blanca de IP. Empieza con pocas estrategias y capital reducido.

Capital por subcuenta: la mayoría opera en futuros USDT-M; **SUB8** opera en spot y **SUB6**
necesita USDT en spot (≈ 2/3) y en futuros (≈ 1/3).

## Servidor MCP

```bash
kriptty-mcp                                   # stdio
kriptty-mcp --transport streamable-http      # http://127.0.0.1:8765/mcp
```

Herramientas: `list_accounts`, `get_market_data`, `get_funding_rate`, `get_account_state`,
`get_macro_signals`, `get_sentiment_score`, `get_order_journal`. Con `MCP_READ_ONLY=false`
se añaden `place_order` (con `stop_loss` obligatorio y el mismo guardián de riesgo) y
`close_position`. Ejemplo de configuración para Antigravity: [`docs/antigravity-mcp.json`](docs/antigravity-mcp.json).

En `dry_run`, el broker simulado vive en memoria de cada proceso: el MCP ve el diario y el
estado compartidos (SQLite) pero no las posiciones simuladas del motor.

## Aviso

Software experimental. Los rendimientos del diseño original son estimaciones sin respaldo:
ejecuta el backtester sobre histórico real antes de activar cualquier estrategia. Operar con apalancamiento puede suponer la pérdida total del capital.
