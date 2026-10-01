# Sistema de cuentas con grids y agente diario · resultados del backtest (2/10/2026)

Objetivo planteado: ≥ 3% al mes por subcuenta con muchas compras y ventas pequeñas (grids tipo
Passivbot: neat, recursive, scalpers), un agente por cuenta que cada día elige moneda, modo y
cantidad según el mercado, cuentas de cortos para las caídas y una cuenta de DCA que siempre compra.

## Qué se ha construido

| Pieza | Archivo |
|---|---|
| Simulador de grids neat/recursive (entradas escalonadas, ventas en tramos, stop, 7x con liquidación, comisiones maker/taker, funding) y salida en dos fases (graceful stop → stop si sigue en contra) | `src/kriptty/backtest/gridsim.py` |
| Régimen diario (alcista / lateral / bajista / incertidumbre) con datos hasta el día anterior; selección diaria de altcoin entre las 40 más líquidas: choppiness, momentum, débiles y **vasos comunicantes** (retraso frente a BTC ajustado por beta); cuentas con pausa −10% y parada; exposición por régimen (0,04 / 0,07 / 0,08) | `scripts/sistema_grids.py` |
| Selección de cartera y robustez | `scripts/analizar_grids.py` |

Datos: 104 monedas de futuros USDT-M (velas de 1 h, funding) de oct 2020 a ago 2026, incluidas las que
acabaron mal (LUNA, FTT, SRM). Ajuste con 2020–2023; **prueba con 2024–2026** (no usado para elegir).

## Resultado

- 3.158 configuraciones de cuenta (2.067 con exposición fija y 1.443 con exposición por régimen,
  3–8 monedas y graceful + stop escalonado).
- **Ninguna llega al 3% mensual en 2024–2026.** Solo 2 pasan del 1% mensual (DCA, con caída del −45%,
  y un recursive).
- La correlación entre lo que una configuración ganó en 2020–2023 y lo que gana en 2024–2026 es ≈ 0,1:
  elegir «la que más ganó» no predice nada (sobreajuste). Ejemplo: cartera de 4 cuentas con +149%
  anual en el ajuste → +2,9% anual y −39% de caída en la prueba.

### Lo que sí aguanta en los dos periodos

| Cuenta | 2021 | 2022 | 2023 | 2024 | 2025 | 2026* |
|---|---|---|---|---|---|---|
| Recursive largo · vasos comunicantes · stop 12% | +29,1 | −15,8 | +61,9 | +31,0 | −5,1 | +6,4 |
| Recursive largo · momentum · sin stop | +39,8 | +0,3 | +28,3 | +7,7 | +8,1 | +3,3 |
| Cortos · vasos comunicantes · 5 monedas · graceful + 8% | +5,4 | +18,3 | −10,6 | +0,1 | +10,2 | +6,4 |
| DCA BTC/ETH 100.000 $/año | +10,0 | −17,4 | +25,0 | +41,1 | −5,4 | −8,0 |
| SUB5 macro-corto 40% · 3x | −1,4 | +29,3 | −3,1 | 0 | 0 | 0 |

Cartera con esas 5 cuentas (mismo capital en cada una):

| Exposición | Ajuste 2020–2023 | Prueba 2024–2026 |
|---|---|---|
| ×1 | +1,0%/mes · +12,9%/año · caída −6,2% | **+0,6%/mes · +7,1%/año · caída −8,8%** |
| ×2 | +2,0%/mes · +26,9%/año · caída −12,1% | **+1,2%/mes · +14,0%/año · caída −17,1%** |
| ×4,3 (caída −25% en el ajuste) | +4,4%/mes · +63%/año | +2,7%/mes (mediana 0,6%) · +28%/año · **caída −34%** |

Nota: estas 5 cuentas se eligieron por estabilidad en dos tramos del ajuste, pero después de haber visto
las tablas de la prueba; el resultado de prueba puede estar algo inflado.

### Stop loss

- Stops ajustados (3–5%) empeoran: el grid se sale en el ruido y vuelve a entrar.
- Recursive: stop al 8–12% o sin stop dan resultados parecidos; **graceful + stop al 8%** da la misma
  rentabilidad con menos caída (−8,7% frente a −12,5% sin stop).
- Scalpers neat siempre encendidos en altcoins pierden: las tendencias llenan el grid y lo atascan.

### Cortos

- Una cuenta de cortos con **vasos comunicantes** (las altcoins que aún no han caído cuando cae BTC)
  es la única de cortos positiva en la prueba (+6,2%/año, caída −7,7%).
- SUB5 macro-corto solo opera en mercados bajistas largos (2022: +29%); desde 2024 no ha entrado.
- Una segunda cuenta de cortos no mejora la cartera en el ajuste.

## Límites del backtest

- Velas de 1 hora: los scalpers reales cierran más ciclos dentro de la hora (estimación conservadora).
- Comisiones maker 0,02% / taker 0,06%; con descuentos VIP o rebates los grids mejoran.
- El agente que lee noticias no se puede probar con histórico (sabe lo que pasó); en vivo solo podrá
  reducir riesgo sobre las reglas.
- Faltan las configuraciones reales de Kriptty (tabla `grids` de MySQL o `configs/live` del servidor).
