"""Orquestador del motor de trading.

    kriptty-engine                 # arranca todas las estrategias habilitadas
    kriptty-engine --once SUB5     # un único ciclo de una estrategia (pruebas)

Estrategias por horario → APScheduler (max_instances=1, coalesce: nunca dos
ciclos solapados de la misma subcuenta). SUB4 (event-driven) → task asyncio
supervisada con reinicio y backoff exponencial (el main original reiniciaba en
bucle cada 30 s sin límite y perdía la referencia de las tasks).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import ACCOUNT_DESCRIPTIONS, get_settings
from .data.macro import MacroProvider
from .exchange.router import AccountRouter
from .state import StateStore
from .strategies import REGISTRY, Context, Strategy

log = logging.getLogger("kriptty.engine")


def build_context() -> Context:
    settings = get_settings()
    state = StateStore(settings.state_path)
    return Context(settings=settings, router=AccountRouter(settings, state), state=state,
                   macro=MacroProvider(settings.fred_api_key))


def build_strategies(ctx: Context, only: set[str] | None = None) -> list[Strategy]:
    wanted = only or ctx.settings.enabled
    return [cls(ctx) for acc, cls in REGISTRY.items() if acc in wanted]


async def supervise(strategy: Strategy) -> None:
    delay = 5
    while True:
        try:
            await strategy.start()
            delay = 5
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            strategy.last_error = repr(e)
            log.exception("❌ %s cayó: %s. Reinicio en %ss", strategy.account_id, e, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 300)


async def check_clock(ctx, strategies) -> None:
    """Avisa si el reloj local se desvía del de Bitget (firmas rechazadas, velas mal cortadas)."""
    if not strategies:
        return
    try:
        offset = await ctx.router.client(strategies[0].account_id).clock_offset_ms()
    except Exception as e:  # noqa: BLE001 — sin red no se bloquea el arranque
        log.warning("No se pudo comprobar la hora de Bitget: %s", e)
        return
    if abs(offset) > ctx.settings.max_clock_offset_ms:
        log.warning("⏰ El reloj local se desvía %.0f ms del de Bitget: sincroniza con NTP", offset)
    else:
        log.info("⏰ Reloj sincronizado con Bitget (desfase %.0f ms)", offset)


async def run(only: set[str] | None = None) -> None:
    ctx = build_context()
    strategies = build_strategies(ctx, only)
    log.info("🚀 Kriptty engine | modo=%s | UTA=%s | estrategias=%s", ctx.settings.trading_mode,
             ctx.settings.bitget_uta, [s.account_id for s in strategies])
    if ctx.settings.trading_mode == "live":
        log.warning("⚠️  MODO LIVE: se enviarán órdenes con dinero real")
    await check_clock(ctx, strategies)

    scheduler = AsyncIOScheduler(timezone="UTC")
    tasks: list[asyncio.Task] = []
    for s in strategies:
        log.info("  %s — %s", s.account_id, ACCOUNT_DESCRIPTIONS[s.account_id])
        if s.event_driven:
            tasks.append(asyncio.create_task(supervise(s), name=s.account_id))
        else:
            sched = dict(s.schedule)
            trigger = sched.pop("trigger")
            scheduler.add_job(s.safe_run, trigger, id=s.account_id, max_instances=1, coalesce=True,
                              misfire_grace_time=300, **sched)
    scheduler.start()
    for s in strategies:  # primer ciclo inmediato para no esperar a la primera franja
        if not s.event_driven:
            tasks.append(asyncio.create_task(s.safe_run(), name=f"{s.account_id}:first"))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()
    log.info("🛑 Parando motor…")
    scheduler.shutdown(wait=False)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await ctx.router.close()


async def run_once(account_id: str) -> None:
    ctx = build_context()
    (strategy,) = build_strategies(ctx, {account_id.upper()})
    try:
        await strategy.run_cycle()
    finally:
        await ctx.router.close()


def cli() -> None:
    parser = argparse.ArgumentParser(description="Kriptty Puda Strategy — motor de trading")
    parser.add_argument("--once", metavar="SUBx", help="ejecuta un único ciclo de una estrategia")
    parser.add_argument("--only", metavar="SUB1,SUB2", help="limita las estrategias a arrancar")
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level,
                        format="%(asctime)s | %(name)-28s | %(levelname)-7s | %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    if args.once:
        asyncio.run(run_once(args.once))
    else:
        only = {s.strip().upper() for s in args.only.split(",")} if args.only else None
        asyncio.run(run(only))


if __name__ == "__main__":
    cli()
