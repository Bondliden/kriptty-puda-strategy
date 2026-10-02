"""Vigilante de riesgo (cada hora): el stop en dos fases que usáis a mano, automatizado.

* Fase 1 — **gracefully stop**: el bot no abre ciclos nuevos y deja que la posición se cierre con sus
  ventas. Al entrar en esta fase se guarda el precio de referencia.
* Fase 2 — **panic**: si desde ese precio la moneda sigue en contra ``graceful_sl`` (8% por defecto),
  se pasa el lado a Panic, que en Passivbot cierra la posición a mercado.
* Stop de catástrofe: si el precio va ``stop_catastrofe`` (15%) en contra del precio de entrada,
  Panic directamente, esté en el modo que esté.
* Un lado en Panic sin posición vuelve a Manual (si no, Passivbot sigue «en pánico» sin nada que cerrar).

Todo lo que hace este módulo reduce riesgo, así que en modo «aplicar» se ejecuta sin ``permitir_normal``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .config import Config
from .kriptty_api import BotInfo, Posicion


@dataclass
class Alerta:
    cuenta: str
    bot_id: int
    moneda: str
    lado: str
    accion: str          # "panic" | "manual" | "registrar_graceful"
    motivo: str
    cambios: dict


def _adverso(lado: str, ref: float, precio: float) -> float:
    """Movimiento en contra desde ``ref`` (positivo = pérdida)."""
    if ref <= 0:
        return 0.0
    return (ref - precio) / ref if lado == "long" else (precio - ref) / ref


def vigilar(cfg: Config, bots: dict[int, BotInfo], posiciones: dict[int, dict[tuple[str, str], Posicion]],
            precios: dict[str, float], estado: dict) -> list[Alerta]:
    """``posiciones``: subcuenta (exchange_id) → {(moneda, lado): posición}."""
    graceful = estado.setdefault("graceful", {})
    alertas: list[Alerta] = []
    ahora = datetime.now(UTC).isoformat(timespec="minutes")
    for cuenta in cfg.cuentas:
        modo_k = "lm" if cuenta.lado == "long" else "sm"
        pos_cuenta = posiciones.get(cfg.exchange_de(cuenta), {})
        for bot_id in cuenta.bots:
            b = bots.get(bot_id)
            if b is None:
                continue
            clave = f"{bot_id}:{cuenta.lado}"
            modo = getattr(b, modo_k)
            pos = pos_cuenta.get((b.coin, cuenta.lado))
            con_pos = pos is not None and pos.size > 0
            precio = precios.get(b.coin)

            if not con_pos:
                graceful.pop(clave, None)
                if modo == "p":
                    alertas.append(Alerta(cuenta.nombre, bot_id, b.coin, cuenta.lado, "manual",
                                          "posición cerrada tras Panic: vuelve a Manual", {modo_k: "m"}))
                continue
            if precio is None:
                continue
            if modo == "gs":
                ref = graceful.get(clave)
                if ref is None or ref.get("moneda") != b.coin:
                    graceful[clave] = {"moneda": b.coin, "precio": precio, "desde": ahora}
                    alertas.append(Alerta(cuenta.nombre, bot_id, b.coin, cuenta.lado, "registrar_graceful",
                                          f"en gracefully stop: referencia {precio:g}", {}))
                    ref = graceful[clave]
                mov = _adverso(cuenta.lado, ref["precio"], precio)
                if mov >= cuenta.graceful_sl:
                    alertas.append(Alerta(cuenta.nombre, bot_id, b.coin, cuenta.lado, "panic",
                                          f"sigue en contra {mov:.1%} desde el gracefully stop "
                                          f"(límite {cuenta.graceful_sl:.0%})", {modo_k: "p"}))
                    continue
            else:
                graceful.pop(clave, None)
            if pos.entry_price > 0 and modo != "p":
                mov = _adverso(cuenta.lado, pos.entry_price, precio)
                if mov >= cuenta.stop_catastrofe:
                    alertas.append(Alerta(cuenta.nombre, bot_id, b.coin, cuenta.lado, "panic",
                                          f"stop de catástrofe: {mov:.1%} en contra de la entrada "
                                          f"(límite {cuenta.stop_catastrofe:.0%})", {modo_k: "p"}))
    return alertas
