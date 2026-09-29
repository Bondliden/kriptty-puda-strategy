# Kriptty Puda Strategy

Sistema de trading algorítmico multi-subcuenta para **Bitget**: 1 cuenta principal + 9
subcuentas, una estrategia independiente por subcuenta, **Stop Loss obligatorio** en toda
orden y un **servidor MCP** para consultarlo (y opcionalmente operarlo) desde Antigravity,
Claude u otro cliente MCP.

> Revisión y cambios respecto al diseño original: [`docs/REVISION.md`](docs/REVISION.md).

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
| SUB8 | DCA inteligente spot (BTC/ETH) | diario (compra cada 48H) | precio medio − 20% |
| SUB9 | Collar dinámico por régimen macro | 15 min / rebalanceo 6H | EMA200 − 1% (máx. 15%) |

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
├── strategies/          # sub1 … sub9
├── engine.py            # APScheduler + tareas event-driven supervisadas
├── mcp_server.py        # SDK oficial MCP v2 (stdio / Streamable HTTP)
└── state.py             # SQLite: estado de estrategias + diario de órdenes
```

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # TRADING_MODE=dry_run por defecto
pytest                          # 46 tests, sin red
kriptty-engine --once SUB5      # un ciclo de una estrategia
kriptty-engine                  # todas las habilitadas
```

Con Docker:

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f engine
```

### Camino recomendado hacia real

1. **`dry_run`**: precios reales, órdenes simuladas. Revisa logs y `get_order_journal`.
2. **`demo`**: crea API keys de *Demo Trading* en Bitget para cada subcuenta. Mínimo 2–4
   semanas por estrategia.
3. **`live`**: `TRADING_MODE=live` + `CONFIRM_LIVE_TRADING=yes`. API keys **sin permiso de
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

Software experimental. Ninguna estrategia está backtesteada y los rendimientos del diseño
original son estimaciones. Operar con apalancamiento puede suponer la pérdida total del capital.
