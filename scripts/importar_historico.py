"""Importa el histórico descargado con ``scripts/descargar_historico.ps1`` (archivos mensuales de
data.binance.vision) a la caché del backtester (``data/history``), en el mismo formato que
``kriptty.backtest.data``: ``BTC-USDT_USDT__1h.csv`` (futuros), ``BTC-USDT__1h.csv`` (spot) y
``BTC-USDT_USDT__funding.csv``.

    python scripts/importar_historico.py historico --out data/history

Acepta los .zip de una moneda (``BTC.zip`` con ``perp-AAAA-MM.zip``…) y los lotes (``lote01.zip``
con carpetas ``BTC/``, ``ETH/``…). Si una moneda aparece en varios, se unen sin duplicados.
Binance publica las velas spot desde 2025 con marcas de tiempo en microsegundos: se normalizan.
"""
from __future__ import annotations

import argparse
import io
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

COLS = ["timestamp", "open", "high", "low", "close", "volume"]


def _csv(raw: bytes) -> pd.DataFrame:
    """CSV de un mensual de Binance (con o sin cabecera)."""
    first = raw.split(b"\n", 1)[0]
    header = 0 if first[:1].isalpha() else None
    return pd.read_csv(io.BytesIO(raw), header=header)


def _ts(v: pd.Series) -> pd.DatetimeIndex:
    v = v.astype("int64")
    unit = "us" if v.iloc[0] > 10**14 else "ms"
    return pd.DatetimeIndex(pd.to_datetime(v, unit=unit, utc=True))


def _klines(raw: bytes) -> pd.DataFrame:
    df = _csv(raw).iloc[:, :6]
    df.columns = COLS
    df["timestamp"] = _ts(df["timestamp"])
    return df.set_index("timestamp").astype(float)


def _funding(raw: bytes) -> pd.Series:
    df = _csv(raw)
    idx = _ts(df.iloc[:, 0]).floor("min")
    return pd.Series(df.iloc[:, -1].astype(float).to_numpy(), index=idx, name="rate")


def _inner(outer: zipfile.ZipFile):
    """(moneda, tipo, bytes del CSV) de cada mensual dentro de un .zip de moneda o de lote."""
    default = Path(outer.filename).stem.upper()
    for name in outer.namelist():
        p = Path(name)
        if p.suffix != ".zip":
            continue
        coin = p.parent.name.upper() if p.parent.name else default
        kind = p.name.split("-")[0]
        try:
            with zipfile.ZipFile(io.BytesIO(outer.read(name))) as z:
                yield coin, kind, z.read(z.namelist()[0])
        except (zipfile.BadZipFile, IndexError):
            print(f"  ⚠️  {outer.filename}:{name} dañado, se ignora")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    p.add_argument("--out", type=Path, default=Path("data/history"))
    a = p.parse_args()
    parts: dict[tuple[str, str], list] = defaultdict(list)
    for f in sorted(a.folder.glob("*.zip")):
        with zipfile.ZipFile(f) as outer:
            for coin, kind, raw in _inner(outer):
                parts[(coin, kind)].append(_funding(raw) if kind == "funding" else _klines(raw))
    a.out.mkdir(parents=True, exist_ok=True)
    for (coin, kind), frames in sorted(parts.items()):
        data = pd.concat(frames).sort_index()
        data = data[~data.index.duplicated()]
        safe = f"{coin}-USDT" + ("" if kind == "spot" else "_USDT")
        path = a.out / f"{safe}__{'funding' if kind == 'funding' else '1h'}.csv"
        data.index.name = "timestamp"
        data.to_csv(path)
        print(f"{coin:9s} {kind:8s} {len(data):7d} filas  {data.index[0]:%Y-%m-%d} → {data.index[-1]:%Y-%m-%d}")


if __name__ == "__main__":
    main()
