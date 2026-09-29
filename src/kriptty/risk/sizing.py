"""Position sizing por riesgo fijo.

Corrige el diseño original, que redondeaba a 2 decimales y forzaba un mínimo de
0.01 contratos: con cuentas pequeñas eso multiplicaba el riesgo real (p. ej.
0.004 BTC -> 0.01 BTC = 2.5x el riesgo previsto). Aquí, si la cantidad
redondeada a la precisión del mercado queda por debajo del mínimo, NO se opera.
"""
from __future__ import annotations


def risk_based_amount(
    equity: float,
    risk_pct: float,
    entry: float,
    stop: float,
    *,
    max_notional: float | None = None,
) -> float:
    """Cantidad (activo base) tal que tocar el SL pierde ``equity * risk_pct``."""
    distance = abs(entry - stop)
    if equity <= 0 or risk_pct <= 0 or distance <= 0 or entry <= 0:
        return 0.0
    amount = equity * risk_pct / distance
    if max_notional is not None and amount * entry > max_notional:
        amount = max_notional / entry
    return amount


def notional_amount(notional: float, price: float) -> float:
    return notional / price if price > 0 and notional > 0 else 0.0
