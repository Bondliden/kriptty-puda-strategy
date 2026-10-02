# Agentes diarios de Kriptty

Un agente por cuenta. Cada día lee el mercado y decide, para cada bot de su cuenta, **qué moneda**, **qué
modo** (Normal, Gracefully stop, Manual, Panic) y **qué exposición** lleva. Un vigilante revisa cada hora
el stop en dos fases. Código: `src/kriptty/agentes/`. Pruebas: `tests/test_agentes.py`.

Las reglas son las del backtest de 6 años (`docs/SISTEMA_GRIDS.md`, `src/kriptty/agentes/senales.py`): lo
que se opera es lo que se probó.

## Qué hace cada día

1. **Universo de monedas**: futuros USDT de Bitget ∩ top 200 por capitalización (CoinGecko) ∩ listadas en
   Kraken, sin memecoins, sin monedas estables ni envoltorios, sin la lista negra y, si hay lista blanca
   (p. ej. auditadas por CertiK o Hacken), solo esas. Además, la moneda tiene que existir en todas las
   subcuentas de Kriptty que se usan.
2. **Régimen de BTC** con la última vela diaria cerrada: alcista, lateral, bajista o incertidumbre.
3. **Rankings** de las 60 monedas con más volumen:
   - `lag_long`: las rezagadas cuando sube BTC (vasos comunicantes);
   - `lag_short`: las que aún no han caído cuando cae BTC;
   - `momentum`, `scalper` (choppiness) y `weak`.
4. **Lectura del mercado**:
   - fuentes: índice de miedo y codicia, titulares de 24 h (CoinDesk, Cointelegraph, Decrypt, The Block,
     Google News en inglés y en español) y, con `YOUTUBE_API_KEY`, los títulos de los canales de YouTube
     que se configuren;
   - regla fija: miedo y codicia ≤ 10 → riesgo «elevado»;
   - con `ANTHROPIC_API_KEY`, Claude revisa todo eso y **solo puede frenar**: subir el riesgo («elevado»
     reduce la exposición a la mitad; «extremo» apaga todas las cuentas) o vetar candidatas con un
     titular concreto. No puede proponer monedas ni subir exposiciones. Una llamada al día.
5. **Decisión por cuenta**, en este orden:
   - si el régimen no es el de su estrategia, los bots sin posición pasan a Manual y los que tienen
     posición a Gracefully stop;
   - si la moneda del bot sigue entre las 2·n mejores, la conserva (histéresis);
   - si no, y tiene posición, pasa a Gracefully stop: **nunca cambia de moneda con una posición abierta**,
     en ningún lado;
   - si no tiene posición, toma la mejor candidata libre. Nunca la de otro bot de la misma subcuenta,
     aunque ese bot no lo gestione un agente.
   - en incertidumbre, las cuentas que la operan (las recursive) usan BTC y ETH, como en el backtest;
   - la exposición es la del régimen: lateral 0,08, alcista 0,07, incertidumbre 0,04, bajista 0,04.
6. **Informe** en Markdown en `informe_dir` (uno por día) y estado en `estado_agentes.json`.

## Vigilante (cada hora)

- **Fase 1**: un lado en Gracefully stop guarda el precio de referencia.
- **Fase 2**: si desde ese precio sigue en contra `graceful_sl` (8%; 5% en el scalper), pasa a **Panic**
  (Passivbot cierra a mercado).
- **Stop de catástrofe**: si el precio va `stop_catastrofe` (15%) en contra de la entrada, Panic sea cual
  sea el modo.
- Un lado en Panic que ya no tiene posición vuelve a Manual.
- El agente diario no toca un lado en Panic con posición: el vigilante lo está cerrando.

## Cuenta de memecoins (cada hora)

Va en su propia subcuenta, con poco dinero, y cierra **como mucho a las 24 horas**: el vigilante pasa a Panic
cualquier posición abierta más de `max_horas`. La orden es `kriptty-agentes memes`, cada hora.

**Universo:**

- la categoría «meme-token» de CoinGecko (se actualiza sola con las nuevas);
- la lista fija;
- cualquier moneda **recién listada** en Bitget (menos de 3 días).

Las memecoins que aguantan (DOGE, PEPE, SHIB, BONK…) siguen dentro.

**Señales:**

- **hype**: sube un 25% en 24 h con el doble de volumen que su media de la semana anterior, o es nueva, sube
  un 25% desde la primera vela y mueve más de 20 M$;
- **pico**: ha llegado a doblar en 24 h y ya cae entre un 10% y un 30% desde el máximo.

**Backtest** (`scripts/descargar_memes.py` + `scripts/meme_hype.py`, 33 memecoins de Bitget, de diciembre de
2023 a septiembre de 2026, 180 variantes):

- **Comprar el hype pierde.** 115 de 120 variantes acaban en negativo: aciertan un 35% de las veces y la
  caída mediana es del −77%. En TRUMP y MELANIA, los stops saltaban en la primera hora por la volatilidad.
- **Cortos tras el pico** (`meme_pico`: stop del 20%, objetivo del 20%, 24 h): es la única regla positiva
  en el ajuste y en la prueba. Da +24,6% y una caída máxima del −16%, pero con solo 21 operaciones.

Estrategias:

- `meme_pico`: cortos tras el pico (recomendada);
- `meme_hype`: largos en el hype (no recomendada).

Sin cuentas de memecoins en la configuración, `memes` solo escribe las señales en `memes-AAAA-MM-DD.md`.

## Seguridad

- **`permitir_normal = false`** (por defecto). El agente aplica solo lo que reduce riesgo:
  - pasar a Gracefully stop, Manual o Panic;
  - bajar exposición;
  - dejar preparada la moneda nueva en un bot en Manual.

  Poner un bot en Normal (dinero real), subir exposición o cambiar la moneda de un bot que opera queda
  como **propuesta** en el informe. Activarlo es decisión de una persona.
- **`modo = "simulacion"`** (por defecto): no envía nada a Kriptty, solo escribe el informe. Con
  `modo = "aplicar"` envía los cambios del punto anterior.
- Los agentes **solo tocan los bots que aparecen en la configuración**: Kriptty tiene más de 500 bots en
  varias subcuentas. Un bot no puede estar en dos cuentas.
- Cada cuenta opera un solo lado. Si el lado contrario de uno de sus bots está en Normal, pasa a
  Gracefully stop (con posición) o a Manual (sin ella).
- Los secretos van solo en variables de entorno, nunca en archivos ni informes:
  - `KRIPTTY_ADMIN_TOKEN`
  - `ANTHROPIC_API_KEY`
  - `YOUTUBE_API_KEY`

## Estrategias

| Estrategia | Lado | Elige por | Activa en | Stop tras graceful |
|---|---|---|---|---|
| `recursive_vasos` | largo | `lag_long` (rezagadas frente a BTC) | alcista e incertidumbre | 8% |
| `recursive_momentum` | largo | `momentum` (30 días) | alcista e incertidumbre | 8% |
| `cortos_vasos` | corto | `lag_short` (aún no han caído) | bajista | 8% |
| `scalper_lateral` | largo | `scalper` (choppiness) | lateral | 5% |

Cada cuenta puede cambiar cualquier valor:

- `lado`, `criterio`, `regimenes`, `incertidumbre`;
- `graceful_sl`, `stop_catastrofe`;
- `exposicion = { alcista = 0.08, … }`;
- `exchange_id` (la subcuenta).

La cuenta de **DCA** queda fuera de los agentes: compra siempre, sin decisión diaria.

## Uso

Requisitos:

- Python ≥ 3.11.
- `pip install -e ".[agentes]"`. Solo hacen falta numpy y pandas; el extra añade `anthropic`.

```bash
cp config/agentes.example.toml config/agentes.toml     # y poner bots y subcuentas reales
kriptty-agentes mercado                               # régimen, rankings y lectura de hoy (sin Kriptty)
kriptty-agentes mercado --llm                         # lo mismo, con la revisión de Claude
kriptty-agentes plan    -c config/agentes.toml        # decisiones e informe, sin tocar nada
kriptty-agentes diario  -c config/agentes.toml        # igual, y aplica si modo = "aplicar"
kriptty-agentes vigilar -c config/agentes.toml        # stop en dos fases
```

Para el Programador de tareas o cron, `scripts/ejecutar_agentes.py` carga los secretos de `.env` (fuera de
git) y ejecuta la orden:

```bash
python scripts/ejecutar_agentes.py diario -c config/agentes.toml
```

En el PC hay tres tareas de Windows programadas:

- «Kriptty agentes (simulacion)», todos los días a las 02:20;
- «Kriptty memes (simulacion)», cada hora en el minuto 5;
- «Kriptty vigilante (simulacion)», cada hora en el minuto 10. Lo
que aplica lo decide `general.modo`. El servidor de Kriptty (n1, CentOS 7) tiene Python 3.6 y no puede
ejecutar los agentes.

Programación recomendada (UTC):

- `diario` a las 00:20, después del cierre de la vela diaria;
- `vigilar` cada hora, en el minuto 5.

## Lo que necesita Kriptty

La API de administración de la rama `agentes-api` del repositorio de Antbot:

- token en `ADMIN_API_TOKEN` del `.env`;
- `GET /bots?exchange_id=` y `GET /bots/{id}/status`;
- `PUT /bots/{id}` validado, con `symbol` por nombre corto y `restart`;
- `POST /bots/{id}/start`;
- `GET /exchanges/{id}/symbols` y `GET /exchanges/{id}/positions`;
- `GET /grids` y `GET /grids/{id}`.
