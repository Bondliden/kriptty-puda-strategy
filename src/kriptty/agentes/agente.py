"""Decisión diaria de cada agente: qué moneda, qué modo y qué exposición tiene cada bot de su cuenta.

Funciones puras (sin red): reciben el contexto del día y el estado de los bots y devuelven decisiones.
Reglas de seguridad, en este orden:

1. Un bot con posición abierta (en cualquiera de los dos lados) **nunca cambia de moneda**: primero pasa a
   «gracefully stop» y espera a que la posición se cierre sola; el cambio de moneda llega otro día.
2. Sin ``permitir_normal``, el agente solo aplica cambios que **reducen riesgo** (gracefully stop, manual
   sin posición, bajar exposición, monedas nuevas en bots en manual). Activar un bot (Normal), subir
   exposición o cambiar la moneda de un bot que opera quedan como propuesta en el informe.
3. Riesgo «extremo» de la lectura del día: ninguna cuenta abre ciclos nuevos.
4. Un lado en Panic con posición no se toca: lo puso el vigilante y Passivbot está cerrando.
5. Cada cuenta opera un solo lado: si el lado contrario de uno de sus bots está en Normal, pasa a
   gracefully stop (con posición) o a manual (sin ella).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .config import Config, Cuenta
from .kriptty_api import BotInfo, Posicion
from .lectura import Lectura

MODOS = {"n": "Normal", "m": "Manual", "gs": "Gracefully stop", "p": "Panic", "t": "Solo take profit"}
REDUCEN = {"gs", "p", "m", "t"}
INCERTIDUMBRE = ("BTC", "ETH")            # monedas de las cuentas con modo incertidumbre


@dataclass
class Contexto:
    fecha: date
    regimen: str
    lectura: Lectura
    ranking: dict[str, list[str]]            # criterio → monedas de mejor a peor (ya filtradas)
    disponibles: set[str] = field(default_factory=set)
    bull_extremo: bool = False               # BTC +20% en 30 días y por encima de su media de 50


@dataclass
class Decision:
    cuenta: str
    bot_id: int
    lado: str
    moneda_actual: str
    moneda: str
    modo_actual: str
    modo: str
    expo_actual: float
    expo: float
    motivo: str
    cambios: dict = field(default_factory=dict)        # lo que se envía a la API si se aplica
    propuesta: dict = field(default_factory=dict)      # lo que haría con permiso
    reiniciar: bool = False
    arrancar: bool = False                              # bot parado que pasa a Normal (solo con permiso)

    @property
    def hay_cambios(self) -> bool:
        return bool(self.cambios) or self.arrancar


def _claves(lado: str) -> tuple[str, str]:
    return ("lm", "lwe") if lado == "long" else ("sm", "swe")


def exposicion_del_dia(cuenta: Cuenta, cfg: Config, ctx: Contexto) -> float:
    tabla = cuenta.exposicion or cfg.exposicion
    e = float(tabla.get(ctx.regimen, 0.0))
    if ctx.lectura.riesgo == "elevado":
        e *= 0.5
    return round(e, 4)


def activa(cuenta: Cuenta, ctx: Contexto) -> bool:
    if ctx.lectura.riesgo == "extremo":
        return False
    if cuenta.solo_bull_extremo:
        return ctx.bull_extremo
    return ctx.regimen in cuenta.regimenes or (cuenta.incertidumbre and ctx.regimen == "incertidumbre")


def decidir_cuenta(cuenta: Cuenta, cfg: Config, ctx: Contexto, bots: dict[int, BotInfo],
                   posiciones: dict[tuple[str, str], Posicion], ocupadas: set[str] | None = None) -> list[Decision]:
    """``ocupadas``: monedas que ya tiene algún bot de la misma subcuenta (de esta cuenta o de otras). Se
    actualiza al asignar monedas nuevas, así que hay que pasar el mismo conjunto a todas las cuentas que
    comparten subcuenta."""
    modo_k, expo_k = _claves(cuenta.lado)
    otro_lado = "short" if cuenta.lado == "long" else "long"
    otro_k = _claves(otro_lado)[0]
    on = activa(cuenta, ctx)
    expo = exposicion_del_dia(cuenta, cfg, ctx)
    vetadas = set(ctx.lectura.vetadas)
    if ctx.regimen == "incertidumbre" and cuenta.incertidumbre:
        # como en el backtest: en incertidumbre, recursive prudente en BTC y ETH (exposición de incertidumbre)
        candidatas = [c for c in INCERTIDUMBRE if c not in vetadas]
    else:
        candidatas = [c for c in ctx.ranking.get(cuenta.criterio, [])
                      if c not in vetadas and (not ctx.disponibles or c in ctx.disponibles)]
    n = len(cuenta.bots)
    mantener = set(candidatas[: 2 * n])                 # histéresis: no cambiar por una caída leve en el ranking
    # un bot libre no puede quitarle la moneda a otro bot de la misma subcuenta
    if ocupadas is None:
        ocupadas = set()
    ocupadas |= {bots[b].coin for b in cuenta.bots if b in bots}
    asignadas: set[str] = set()
    decisiones: list[Decision] = []

    # primero los bots que conservan moneda, para no dársela a otro
    orden = sorted(cuenta.bots, key=lambda b: 0 if bots.get(b) and bots[b].coin in mantener else 1)
    for bot_id in orden:
        b = bots.get(bot_id)
        if b is None:
            decisiones.append(Decision(cuenta.nombre, bot_id, cuenta.lado, "?", "?", "?", "?", 0, 0,
                                       "bot no encontrado en Kriptty"))
            continue
        modo_actual = getattr(b, modo_k)
        expo_actual = getattr(b, expo_k)
        con_pos = _abierta(posiciones.get((b.coin, cuenta.lado)))
        con_pos_otro = _abierta(posiciones.get((b.coin, otro_lado)))
        moneda, modo, e, motivo = b.coin, modo_actual, expo_actual, ""

        if modo_actual == "p" and con_pos:
            motivo = "Panic en curso: no se toca hasta que la posición se cierre"
        elif not on:
            causa = ("sin bull run fuerte" if cuenta.solo_bull_extremo and ctx.lectura.riesgo != "extremo"
                     else f"régimen {ctx.regimen} / riesgo {ctx.lectura.riesgo}")
            if con_pos:
                modo, motivo = "gs", f"{causa}: cerrar con calma"
            else:
                modo, motivo = "m", f"{causa}: sin ciclos nuevos"
        elif b.coin in vetadas:
            modo = "gs" if con_pos else "m"
            motivo = f"{b.coin} vetada: {ctx.lectura.vetadas[b.coin]}"
        elif b.coin in mantener and b.coin not in asignadas:
            modo, e, motivo = "n", expo, f"{b.coin} sigue entre las mejores ({cuenta.criterio})"
        elif con_pos:
            modo, motivo = "gs", f"{b.coin} ya no está entre las mejores: se cierra antes de cambiar de moneda"
        elif con_pos_otro:
            modo, motivo = "m", f"{b.coin} tiene posición {otro_lado}: se cierra antes de cambiar de moneda"
        else:
            libre = next((c for c in candidatas if c not in asignadas and c not in ocupadas), None)
            if libre is None:
                modo, motivo = "m", "sin candidatas disponibles hoy"
            else:
                moneda, modo, e = libre, "n", expo
                motivo = f"nueva moneda {libre} ({cuenta.criterio}, puesto {candidatas.index(libre) + 1})"
                ocupadas.discard(b.coin)
                ocupadas.add(libre)
        asignadas.add(moneda)

        d = Decision(cuenta.nombre, bot_id, cuenta.lado, b.coin, moneda, modo_actual, modo, expo_actual, e, motivo)
        _permisos(d, b, cfg, modo_k, expo_k, con_pos or con_pos_otro, cuenta.grid_id)
        _renombrar(d, b)
        _lado_contrario(d, b, otro_lado, otro_k, con_pos_otro)
        d.reiniciar = bool(d.cambios) and b.running
        d.arrancar = cfg.permitir_normal and d.modo == "n" and not b.running
        decisiones.append(d)
    return decisiones


def _abierta(p: Posicion | None) -> bool:
    return p is not None and p.size > 0


def _permisos(d: Decision, b: BotInfo, cfg: Config, modo_k: str, expo_k: str, bloqueada: bool,
              grid_id: int | None = None) -> None:
    """Reparte los cambios entre lo que se aplica y lo que queda como propuesta."""
    deseado: dict = {}
    if d.moneda != d.moneda_actual:
        deseado["symbol"] = f"{d.moneda}USDT"
    if grid_id and b.grid_id != grid_id:
        deseado["grid_id"] = grid_id            # configuración de grid de la estrategia de la cuenta
    if d.modo != d.modo_actual:
        deseado[modo_k] = d.modo
    if abs(d.expo - d.expo_actual) > 1e-9:
        deseado[expo_k] = d.expo
    if bloqueada:                       # nunca: moneda y grid esperan a que no haya posición
        deseado.pop("symbol", None)
        deseado.pop("grid_id", None)

    if cfg.permitir_normal:
        d.cambios = deseado
        return
    operando = d.modo_actual == "n"
    for k, v in deseado.items():
        if k == modo_k:
            (d.cambios if v in REDUCEN else d.propuesta)[k] = v
        elif k == expo_k:
            (d.cambios if v < d.expo_actual else d.propuesta)[k] = v
        elif k in ("symbol", "grid_id"):
            # cambiar moneda o grid solo es inocuo si el bot no opera o deja de operar con este cambio
            seguro = not operando or d.modo == "m"
            (d.cambios if seguro else d.propuesta)[k] = v
    # sin permiso, un bot que debía activarse se queda en manual con la moneda y exposición listas
    if d.propuesta.get(modo_k) == "n" and expo_k in d.propuesta and not operando:
        d.cambios[expo_k] = d.propuesta.pop(expo_k)


def _renombrar(d: Decision, b: BotInfo) -> None:
    """Si el bot se llama como su moneda (p. ej. «ALGO»), el nombre sigue a la moneda nueva."""
    if b.name.strip().upper() != d.moneda_actual:
        return
    for destino in (d.cambios, d.propuesta):
        if "symbol" in destino:
            destino["name"] = d.moneda


def _lado_contrario(d: Decision, b: BotInfo, otro_lado: str, otro_k: str, con_pos_otro: bool) -> None:
    """La cuenta opera un solo lado: el contrario no abre ciclos (reduce riesgo, se aplica siempre)."""
    actual = getattr(b, otro_k)
    if actual == "n":
        nuevo = "gs" if con_pos_otro else "m"
    elif actual == "p" and not con_pos_otro:
        nuevo = "m"
    else:
        return
    d.cambios[otro_k] = nuevo
    d.motivo += f"; lado {otro_lado}: {MODOS[actual]} → {MODOS[nuevo]}"
