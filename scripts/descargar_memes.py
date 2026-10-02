"""Descarga velas de 1 hora de las memecoins de futuros USDT de Bitget (API pública, sin claves).

    python scripts/descargar_memes.py --out data/memes

Para el backtest de la cuenta de «hype» de memecoins (``scripts/meme_hype.py``). Pagina hacia atrás desde
hoy hasta el inicio de cotización de cada moneda.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kriptty.agentes.mercado import BITGET, MEMECOINS, PRODUCT, Bitget, _get  # noqa: E402
from kriptty.agentes.orquestador import base_coin  # noqa: E402

COLS = ["ts", "open", "high", "low", "close", "base_vol", "quote_vol"]


def velas_1h(simbolo: str, pausa: float = 0.08) -> pd.DataFrame:
    filas: list[list] = []
    fin = None
    while True:
        params = {"symbol": simbolo, "productType": PRODUCT, "granularity": "1H", "limit": 200}
        if fin is not None:
            params["endTime"] = fin
        lote = _get(f"{BITGET}/api/v2/mix/market/history-candles", params)["data"]
        if not lote:
            break
        filas = lote + filas
        primero = int(lote[0][0])
        if fin is not None and primero >= fin:
            break
        fin = primero - 1
        time.sleep(pausa)
        if len(lote) < 200:
            break
    if not filas:
        return pd.DataFrame(columns=COLS[1:])
    df = pd.DataFrame([f[:7] for f in filas], columns=COLS)
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df = df.drop_duplicates("ts").set_index("ts").sort_index().astype(float)
    return df[df.index < pd.Timestamp.now(tz="UTC").floor("h")]          # solo velas cerradas


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="data/memes")
    p.add_argument("--al-reves", action="store_true", help="empezar por el final de la lista (para ir en paralelo)")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    monedas = sorted((c for c in Bitget().tickers() if base_coin(c) in MEMECOINS), reverse=a.al_reves)
    for c in monedas:
        if (out / f"{c}_1h.csv").exists() or (out / f"{c}_1h.parcial").exists():
            continue
        (out / f"{c}_1h.parcial").touch()
        df = velas_1h(f"{c}USDT")
        (out / f"{c}_1h.parcial").unlink(missing_ok=True)
        df.to_csv(out / f"{c}_1h.csv")
        print(f"{c}: {len(df)} velas desde {df.index.min() if len(df) else '—'}", flush=True)


if __name__ == "__main__":
    main()
