# Revisión de estrategias — 29/09/2026

Revisión del diseño recogido en `🤖 Sistema de Trading Algorítmico.docx` (arquitectura
MCP + Bitget, 9 subcuentas) y su actualización al estado actual de las APIs y librerías.
El documento contenía el código completo, pero el repositorio no tenía nada implementado.
Esta versión lo implementa, corrige los fallos encontrados y sustituye las piezas hechas a
mano por librerías mantenidas.

## 1. Qué ha cambiado en el ecosistema

| Área | Diseño original | Situación hoy | Decisión |
|---|---|---|---|
| Cliente Bitget | REST/WS artesanal (HMAC, retry, parseo) | **ccxt 4.5.x** soporta API v2 **y UTA v3**, Demo Trading (`paptrading`), precisión por mercado | Capa de exchange sobre `ccxt.async_support` + `ccxt.pro` para WebSocket |
| Cuentas | Clásicas v2 | Bitget empuja la **Unified Trading Account (v3)** | `BITGET_UTA=true` activa las rutas UTA en ccxt |
| Servidor MCP | WebSocket propio con JSON-RPC ad hoc | **SDK oficial `mcp` v2** (`MCPServer`, spec 2026-07-28, Streamable HTTP) | Reescrito con el SDK; stdio o HTTP |
| MCP oficial de Bitget | — | Existe **Bitget Agent Hub** (MCP oficial, UTA v3, `--read-only`, `--paper-trading`) | Útil para explorar desde el IDE, pero **no aplica nuestro guardián de SL**; el MCP propio sigue siendo la puerta para operar |
| Noticias | CryptoPanic API v1 gratuita | CryptoPanic pasó a `/api/<plan>/v2/` y **eliminó el plan gratuito** | RSS (CoinDesk, Cointelegraph, Decrypt, The Block) como base; CryptoPanic/Newsdata opcionales |
| Funding | "Cada 8H" fijo | Bitget ajusta el **intervalo por par** (1h/4h/8h) | APY calculado con el intervalo real que devuelve la API |
| Datos macro | FRED + Yahoo Finance no oficial | Yahoo bloquea clientes automáticos | Todo desde FRED (SP500, NASDAQCOM, VIXCLS, DTWEXBGS) + alternative.me |
| Python / libs | websockets, `datetime.utcnow`, compose `version:` | websockets ≥ 14 eliminó `InvalidStatusCode`; `utcnow` obsoleto; `version` obsoleto en Compose | Eliminados |

## 2. Fallos encontrados en el código del documento

### Críticos (hacían perder dinero o dejaban la estrategia inoperante)

1. **Take profit nunca colocado.** Se enviaba `presetTakeProfitPrice`; en la API v2 el campo es
   `presetStopSurplusPrice`. Ningún TP de SUB1–SUB9 llegaba al exchange.
2. **SUB3 imposible.** Los endpoints `copy/mix-trader/trader-list`, `trader-detail`,
   `trader-open-positions` y `order-history` **no existen**. La API de copy trading solo da
   acceso a la propia cuenta. → Rediseñado (ver §3).
3. **SUB5 siempre bajista.** `CPIAUCSL` es un índice (~320), no un % interanual: la CPI
   puntuaba "muy bajista" siempre (−4 puntos fijos), así que SUB5 casi siempre quería ponerse
   corto y SUB9 se cubría al máximo.
4. **Sizing que multiplicaba el riesgo.** `max(round(contratos, 2), 0.01)`: con cuentas pequeñas
   0.004 BTC se convertía en 0.01 BTC (2,5× el riesgo previsto). Además ignoraba la precisión y
   el mínimo de cada mercado (órdenes rechazadas en altcoins).
5. **Equity siempre 0 en SUB1.** Leía `available` de `/account/all-account-balance`, que no
   tiene ese campo → nunca operaba.
6. **SUB6 no era delta-neutral en la práctica.** El "SL de emergencia ±3%" en la pata de
   futuros se activaba con volatilidad normal y dejaba la pata spot desnuda; la revisión del par
   era cada 8H.
7. **Deduplicación de noticias permanente (SUB1).** Una noticia vista una vez no volvía a contar
   nunca; tras el primer ciclo casi no había artículos y no había señal. `lookback_hours` se
   recibía pero no se usaba.
8. **Servidor MCP no compatible con MCP.** No hacía `initialize`, enviaba `tools/list` como
   mensaje no solicitado y los esquemas estaban vacíos: Antigravity/Claude no podían usarlo.

### Importantes

9. RSI de SUB5 calculado con las **14 velas más antiguas** de la serie, no las recientes.
10. Umbrales mal ordenados en el scoring macro (`>= 1.5` antes que `>= 3.0`): STRONG_BULLISH inalcanzable.
11. Si una fuente macro fallaba se usaban valores fijos de 2023 (Fed 5.33%) o 0.0 → sesgo silencioso.
12. La escala macro real era ±20, no ±10; los umbrales −3/−5 no significaban lo documentado.
13. SUB5 con datos insuficientes permitía la entrada "por defecto" (fail-open).
14. SUB1: TP = 2.5 × distancia del SL = 3.75 × ATR (se documentó 2.5 × ATR, R:R 1.67).
15. No se configuraba modo de posición, margen ni apalancamiento; se enviaba `tradeSide`
    (solo válido en modo hedge).
16. `BitgetRestClient` compartía una sesión HTTP que cada `async with` cerraba: con tareas
    concurrentes una estrategia cerraba la sesión de otra.
17. `websockets.InvalidStatusCode` ya no existe → el bucle de reconexión de SUB4 se rompía.
18. SUB4: en 1m el TP (2·ATR ≈ 0.1–0.2% en BTC) apenas cubría las comisiones (0.12% ida y vuelta).
19. SUB9: LONG + SHORT del mismo perpetuo en la misma cuenta = un LONG más pequeño con
    comisiones y margen dobles, y exige modo hedge (incompatible con el resto).
20. SUB9: el SL en EMA200 − 1% puede quedar a >25% del precio en tendencias fuertes: el
    guardián lo rechaza y la estrategia no abría nunca (detectado al testear).
21. docker-compose publicaba Redis (6379), la API del motor (8000) y el MCP (8765) en todas las interfaces;
    Postgres/Redis/Grafana se desplegaban pero el código no los usaba.
22. Riesgo agregado: SUB1 arriesgaba 3% por operación en hasta 5 criptos muy correlacionadas.

## 3. Estrategias: estado y cambios

| Sub | Estrategia | Estado | Cambios principales |
|---|---|---|---|
| SUB1 | News Sentiment | ✅ | RSS + VADER por defecto (FinBERT opcional), ventana temporal real, trailing SL por ATR solo a favor, TP 2.5·ATR, riesgo 1.5% y máx. 2 posiciones |
| SUB2 | Stat Arb | ✅ | Universo = perpetuos Bitget con volumen (sin CoinGecko), R:R ≥ 1.5, SL ≤ 12%, salida por tiempo a las 48H |
| SUB3 | Copy trading | ⚠️ rediseñada, desactivada | Copias con el **copy trading nativo** (traders elegidos en la web) y un **guardián** que impone `SL = max(SL_trader, entry − 1.5·ATR)` vía `mix-follower/setting-tpsl`. Verificar campos en Demo antes de activar |
| SUB4 | Scalping | ✅ | ccxt.pro `watch_ohlcv`, filtro de comisiones, trailing/breakeven, límites diarios |
| SUB5 | Macro-short | ✅ | Scoring corregido (CPI YoY, escala ±10, fail-closed), RSI correcto, TP escalonado reduce-only, cierre si el régimen pasa a alcista |
| SUB6 | Funding arb | ✅ | Intervalo real por par, solo funding positivo, SL emergencia +10% (2x), vigilancia cada 5 min, filtro de rentabilidad neta de comisiones, rollback si falla una pata |
| SUB7 | Grid | ✅ | SL adjunto en cada compra, pérdida máxima del grid ≤ 4% del equity, detección de ejecución por estado de orden |
| SUB8 | DCA | ✅ | Stop único sobre todo el saldo (fail-safe si no se puede colocar), TP parciales una vez por ciclo, reset tras SL |
| SUB9 | Collar | ✅ | Exposición neta directa (un solo perpetuo), SL EMA200 acotado a 15%, sigue a la EMA, circuit breaker con 24H de pausa |

## 4. Capa de riesgo común

- **Un único guardián** (`risk/guard.py`) usado por estrategias y MCP: SL obligatorio, en el
  lado correcto, a ≤ 25% del precio; TP en el lado correcto. Solo exentos: órdenes reduce-only,
  ventas spot y la pata spot de SUB6 (declarada explícitamente).
- **Kill-switch diario** por cuenta (`MAX_DAILY_LOSS_PCT`, 5% por defecto).
- **Compras spot**: si no se puede colocar el stop, la compra se deshace.
- **Diario de órdenes** en SQLite (enviadas, rechazadas y errores), consultable por MCP.
- **Modos**: `dry_run` (por defecto) → `demo` (Bitget Demo Trading) → `live` (requiere
  `CONFIRM_LIVE_TRADING=yes`).

## 5. Pendiente / a validar en Demo Trading

- Campos exactos de `mix-follower/query-current-orders` y `setting-tpsl` (SUB3).
- Comportamiento de los SL adjuntos a órdenes límite del grid en modo one-way (SUB7).
- Órdenes plan de venta spot como stop (SUB8) — confirmar que no bloquean saldo.
- Capital: SUB6 necesita USDT en spot (≈ 2/3) y en futuros (≈ 1/3) de la subcuenta.
- Ninguna estrategia está backtesteada: los rendimientos del documento original
  (Sharpe 3–5, APY 15–40%…) son estimaciones sin respaldo. Antes de dinero real, al menos
  2–4 semanas en `demo` por estrategia.

## 6. Ampliación: backtesting y nuevas estrategias (29/09/2026)

### Backtester (`kriptty-backtest`)
Ejecuta el **mismo código** de las estrategias sobre histórico de Bitget con un reloj
simulado (`kriptty/clock.py`), el calendario real de cada estrategia y el mismo guardián de
riesgo. Ejecución conservadora: fills intrabarra con máximo/mínimo, SL antes que TP en la
misma vela, gaps a la apertura, taker 0.06% / maker 0.02%, slippage 2 pb y funding histórico.
Métricas: retorno, CAGR, drawdown, Sharpe, % aciertos, profit factor, comisiones, funding y
comparación con comprar y mantener.

Limitaciones: SUB1 (sin histórico de noticias) y SUB3 (copy trading nativo) no se pueden
backtestear; SUB5/SUB9 usan un score macro fijo; SUB4 exige velas de 1m (descargas grandes).

### Nuevas estrategias (inspiradas en controladores de Hummingbot v2)
| Sub | Origen | Adaptación |
|---|---|---|
| SUB10 Pairs trading | `stat_arb` (1m, 20x, modo hedge, TP 0.08% por pata) | 1H, 2x, one-way; β por MCO sobre log-precios, filtro ADF < −3.34 y vida media 2–72h; entrada \|z\| ≥ 2, salida \|z\| ≤ 0.5, stop \|z\| ≥ 4 o −3% del par, 5 días máx.; un símbolo no puede estar en dos pares |
| SUB11 SuperTrend | `supertrend_v1` (3m, entrada a < 1% de la línea) | 4H, SuperTrend(20, 4) stop-and-reverse, SL = línea (trailing), riesgo 1%, filtro anti-persecución ≤ 5 ATR |

Ambas están **desactivadas por defecto**: activarlas en `ENABLED_STRATEGIES` solo tras
backtestearlas con datos reales.

### Fallos encontrados gracias al backtester
23. **SUB8**: tras una subida fuerte, "precio medio − 20%" quedaba a > 25% del precio y el
    guardián rechazaba todas las compras. El stop pasa a `max(medio − 20%, precio − 24%)`,
    que además sigue al precio y protege beneficios.
24. **SUB7**: con el kill-switch diario activo, la reconstrucción del grid lanzaba excepciones
    en cada ciclo en lugar de pausarse limpiamente.
25. **SUB11** (diseño Hummingbot): con 4×ATR la línea se mantiene a 3.4–4.6 ATR del precio en
    tendencia; el filtro de proximidad casi nunca se cumplía → se cambió a stop-and-reverse.
26. El grid (SUB7) pagaba comisión taker en las órdenes límite en la simulación: se modela
    maker, que es lo que cobra Bitget a una límite que reposa en el libro.

### Pendiente
- Ejecutar los backtests con **datos reales** (este entorno de desarrollo no tiene acceso a
  Bitget; el backtester se ha validado con series sintéticas y 60 tests).

## 5. Octubre 2026 — test de estrés de 3 años y novedades

### Test de estrés (`kriptty-stress`)
Desde el entorno de desarrollo no había acceso a la API de Bitget, así que se generaron mercados
sintéticos de 3 años (`backtest/stress.py`): ciclo completo con crashes de un día tipo LUNA/FTX,
bear market prolongado y lateral con flash crashes, 3 trayectorias Monte Carlo cada uno y una
variante con comisiones y slippage ×2. Se ejecutaron las 9 estrategias backtesteables con el mismo
código que opera en vivo (162 ejecuciones). Mide supervivencia y control del riesgo, no rentabilidad.

| | Antes | Después |
|---|---|---|
| Drawdown de la cartera combinada (8 subcuentas) | −51% a −85% | −16% a −34% |
| Peor drawdown de una subcuenta | −100% (SUB7) | −56% (SUB8, exenta por diseño); −48% el resto |
| SUB2 · drawdown mediano | −88% (con retornos de +14.800% por apalancamiento oculto) | −44% |
| SUB7 · retorno mediano | −95% | −39% (parada dura) |

### Fallos encontrados y corregidos
27. **Apalancamiento oculto** (`Strategy.open_position`): el tope de nocional era por posición;
    con 3 posiciones SUB2 y SUB11 llegaban a 9× el equity. Ahora cuenta lo abierto y cada posición
    tiene como mucho su parte del total.
28. **Backtester sin margen**: no comprobaba el margen inicial ni descontaba el margen usado del saldo
    disponible. Ahora lanza `InsufficientFunds` como el exchange.
29. **SUB6** abría una pata y deshacía el par en bucle cuando una de sus carteras estaba bloqueada.
30. **SUB7** encadenaba pérdidas del 4% en tendencia bajista (filtro de tendencia EMA20).
31. **Pausas por drawdown encadenadas**: nuevo corte del 25% (14 días) y **parada dura del 40%**
    desde el máximo histórico, que no se reinicia (requiere revisión manual).
32. **SUB4 y SUB7 desactivadas por defecto**: perdían en todos los escenarios.
33. **SUB5 parada para siempre**: su circuit breaker del 5% no tenía salida (el máximo nunca se
    reiniciaba), así que tras el primer −5% la cuenta short dejaba de operar para siempre. En el test
    de estrés se paró en las 9 trayectorias a los 1–13 meses, antes de las caídas que debía cubrir.
    Ahora pausa 7 días y reinicia el máximo; la parada dura del router (−40%) sigue por encima.

### Apalancamiento de la cuenta short (SUB5)
`scripts/hedge_short.py` combina SUB5 a 2x, 3x, 5x, 7x y 10x (margen ≤ 20%, riesgo escalado) con las
demás cuentas. La beta a la baja de las demás (días con BTC < −3%) suma ≈ 0,47 por cuenta
(SUB2 0,21 · SUB8 0,19 · SUB9 0,11 · SUB11 −0,04 · SUB6 y SUB10 ≈ 0); peor caso ≈ 0,95. Con margen
del 20%, el apalancamiento que la cubre es 0,47 / 0,20 ≈ 2,4 → **3x** (5x como máximo para el peor
caso). Por encima, el short no cubre más (entra tarde: la señal macro va con 30 días de retraso) y solo
aumenta su propio drawdown: −28% a 3x, −37% a 5x, −46% a 7x (riesgo escalado en ambos niveles de convicción).

### Novedades del ecosistema aplicadas
- ccxt ≥ 4.5.84 (ledger de la cuenta unificada UTA v3).
- SDK MCP 2.2: servidor HTTP sin estado (las sesiones con estado caducan a los 30 min).
- Aviso al arrancar si el reloj local se desvía del de Bitget (como Freqtrade 2026.9).
- Perpetuos de acciones, metales e índices fuera de los universos de SUB2 y SUB6.

### A vigilar
- **MiCA**: según varias fuentes, Bitget no tenía licencia MiCA a mediados de septiembre de 2026, y la
  CNMV trata los perpetuos como CFD para minoristas. Confirmar el acceso desde España antes de `live`.
- Migración automática a UTA desde el 15/09: `BITGET_UTA` debe coincidir con cada subcuenta.
- API de seguidores de copy trading «temporalmente» no disponible: SUB3 sigue desactivada.
- Repetir el test con **histórico real** en cuanto haya acceso a `api.bitget.com`.
