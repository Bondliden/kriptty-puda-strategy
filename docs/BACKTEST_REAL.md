# Backtest real de 6 años · cómo continuar en el PC

Estado (1/10/2026): el pipeline está hecho y probado con BTC y ETH. Falta ejecutarlo con todas las
monedas y el macro, y llevar los resultados al Plan PUDA, a las presentaciones y a PUDA.zip.

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

## Qué falta después

1. Revisar agente por agente. Si alguno pierde más de lo que permite la regla de la reserva, se ajusta o se
   quita, con el resultado a la vista: «se puede tener una caída de precio, pero no perder las cuentas».
   La regla es el presupuesto anual de pérdidas de ≈ 4%.
2. Añadir una pestaña «Backtest real 6 años» a `estrategia/estrategia.html` y `estrategia.en.html`
   (`scripts/embed_plan.py`).
3. Añadir diapositivas a las presentaciones para inversores en español e inglés (`estrategia/` y
   `estrategia/investor-pack/`) y regenerar los PDF.
4. Regenerar `PUDA.zip` con nombres sin acentos (Python zipfile).

Primeros resultados (solo BTC/ETH, sin macro):

| Agente | Rentabilidad | Caída máxima | Crisis (LUNA, FTX, bear 2022) |
|---|---|---|---|
| SUB7 grid | +8,1% | −11,9% | entre −1% y +0,5% |
| SUB8 DCA | −2,2% | −6,8% | 0 operaciones cerradas: revisar |
