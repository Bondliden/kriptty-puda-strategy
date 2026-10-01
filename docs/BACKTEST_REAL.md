# Backtest real de 6 años · cómo continuar en el PC

Estado (1/10/2026): ejecutado con las 104 monedas y el macro; resultados en el Plan PUDA, en las
presentaciones (ES/EN), en la carta, en el resumen y en PUDA.zip.

## Datos (en el Escritorio del PC)

| Carpeta | Qué es | Script que la crea |
|---|---|---|
| `historico_puda` | 20 monedas, un .zip por moneda (BTC.zip…) | primera versión de la descarga |
| `historico_puda_104` | 104 monedas en lotes (lote01.zip…), incluye LUNA, FTT y SRM | `scripts/descargar_historico.ps1` |
| `historico_macro` | FRED (Fed, CPI, S&P 500, Nasdaq, dólar, VIX, curva) y Fear & Greed | `scripts/descargar_macro.ps1` |

Fuente: data.binance.vision (futuros USDT-M, funding y spot, velas de 1H). Los precios coinciden con
Bitget salvo céntimos y Binance tiene el histórico completo desde 2019.

## Pasos

```bash
pip install -e .
python scripts/importar_historico.py "<Escritorio>/historico_puda_104" --out data/history
python scripts/importar_historico.py "<Escritorio>/historico_puda" --out data/history   # si faltan monedas
python scripts/backtest_real.py --macro "<Escritorio>/historico_macro" --out data/real
python scripts/analizar_real.py data/real --out estrategia/datos/backtest_real.json
```

- `backtest_real.py`: configuración definitiva del plan:
  - Núcleo SUB5/6/8/9/10 a 3x, con margen ≤ 20%.
  - Satélites SUB2/7/11 con margen ≤ 10%.
  - Para todas: rampa 25/50/75/100%, pausa al −10%, parada al −20% y 48H por operación.
  - Periodo: oct 2020 – ago 2026.
  - Sin `--macro` no ejecuta SUB5 ni SUB9.
- `analizar_real.py`:
  - Cartera combinada con 1 M$ por subcuenta y rentabilidad mensual y por año.
  - Crisis: China 2021, bear 2022, LUNA, FTX, yen ago 2024, 10/10/2025 y las 5 peores caídas de BTC de 30 días.
  - Correlación entre agentes.

## Resultado (1/10/2026)

Ejecutado en el PC con las 104 monedas y el macro (`historico/puda_104`, `historico/BTC.zip`,
`historico/ETH.zip`, `historico/macro`). Cifras completas en `estrategia/datos/backtest_real_resumen.json`.

| Agente | 6 años | Caída máxima | Mejor año | Decisión |
|---|---|---|---|---|
| SUB5 macro-cortos | +15,3% | −7,5% | 2022 · +19,9% | se mantiene |
| SUB11 SuperTrend | +10,0% | −20,2% | 2021 · +23,6% | se revisa (parada del −20% en oct 2022) |
| SUB7 grid | +8,1% | −11,3% | 2021 · +6,3% | se mantiene |
| SUB6 funding | +3,6% | −2,7% | 2021 · +3,1% | se mantiene |
| SUB8 DCA | −2,1% | −6,7% | 2024 · +4,5% | se revisa: con el 20% en juego compra muy poco |
| SUB9 collar | −3,1% | −22,8% | 2020 · +7,4% | se revisa (parada en jun 2023) |
| SUB2 stat arb | −3,8% | −20,3% | 2021 · +2,6% | se revisa (parada en ene 2022) |
| SUB10 pares | −17,5% | −19,6% | — | candidato a retirar |
| **Cartera (8 × 1 M$)** | **+1,3%** | **−7,8%** | peor año −2,5% | — |

- Protege el capital: peor caída −7,8% frente a −76,7% de BTC; LUNA +1,9% (BTC −48,1%), FTX −0,1%
  (BTC −24,5%), bear 2022 −6,3% (BTC −75,7%). Ningún año supera el tope de pérdidas del ≈ 4%.
- No respalda todavía el 3–5% mensual: se retira de la presentación hasta tener datos.
- SUB5 no opera desde oct 2023 porque el macro no ha vuelto a ser bajista (es su diseño, no un fallo).
- **La parada dura del −20% es la que protege.** Reactivar a los 90 días (`--review-days 90`,
  resultados en `data/real_reactivacion_90d`) lo empeora: SUB2 −49,7%, SUB9 −12,1%, SUB11 +6,5%.
  En vivo sigue «hasta revisión manual» (`HARD_STOP_REVIEW_DAYS=0`).
- Arreglo de medida: las ventas en spot de SUB8 cuentan ahora como operaciones (51; antes salían 0).

## Paquete para inversores

```bash
python scripts/embed_backtest_real.py data/real --restart data/real_reactivacion_90d   # diapositivas ES/EN y Plan PUDA
python scripts/build_investor_docs.py                                                  # carta y resumen ES/EN (HTML)
# PDF con Edge: --headless --print-to-pdf --no-pdf-header-footer (ver estrategia/LEEME.md)
python scripts/build_puda_zip.py                                                       # PUDA.zip
```

## Qué falta después

1. Ajustar o retirar SUB10, SUB2, SUB9 y SUB8 (y revisar la caída de SUB11) y repetir el backtest.
2. 3–6 meses en Bitget Demo con la configuración final; el objetivo de rentabilidad se fija con esos datos.
