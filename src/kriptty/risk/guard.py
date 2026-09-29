"""Guardián de Stop Loss — la regla inquebrantable del sistema.

Una sola implementación compartida por el AccountRouter y el servidor MCP
(en el diseño original había dos validadores distintos con reglas diferentes).
"""
from __future__ import annotations

import math

from .models import OrderRequest


class OrderRejected(ValueError):
    """La orden viola una regla de riesgo y NO se envía al exchange."""


def validate_order(order: OrderRequest, reference_price: float, max_sl_distance_pct: float = 0.25) -> None:
    if not math.isfinite(order.amount) or order.amount <= 0:
        raise OrderRejected(f"Cantidad inválida: {order.amount}")
    if order.order_type == "limit" and (order.price is None or order.price <= 0):
        raise OrderRejected("Orden limit sin precio válido")
    if reference_price <= 0:
        raise OrderRejected("Precio de referencia inválido")

    # Cerrar/reducir riesgo nunca requiere SL.
    if order.reduce_only or (order.is_spot and order.side == "sell"):
        return

    if order.stop_loss is None:
        if order.sl_exempt_reason:
            return
        raise OrderRejected(
            f"🚫 ORDEN RECHAZADA [{order.tag}] {order.symbol}: stop_loss es OBLIGATORIO."
        )

    sl = float(order.stop_loss)
    if not math.isfinite(sl) or sl <= 0:
        raise OrderRejected(f"stop_loss inválido: {order.stop_loss}")

    is_long = order.side == "buy"
    if is_long and sl >= reference_price:
        raise OrderRejected(f"SL de un LONG debe estar por debajo del precio ({sl} >= {reference_price})")
    if not is_long and sl <= reference_price:
        raise OrderRejected(f"SL de un SHORT debe estar por encima del precio ({sl} <= {reference_price})")

    distance = abs(sl - reference_price) / reference_price
    if distance > max_sl_distance_pct:
        raise OrderRejected(
            f"SL a {distance:.1%} del precio (máximo {max_sl_distance_pct:.0%}). Revisa el cálculo."
        )

    if order.take_profit is not None:
        tp = float(order.take_profit)
        if (is_long and tp <= reference_price) or (not is_long and tp >= reference_price):
            raise OrderRejected(f"TP en el lado equivocado del precio: {tp} vs {reference_price}")
