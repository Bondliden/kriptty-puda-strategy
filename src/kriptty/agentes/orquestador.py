"""Orquestación diaria y horaria de los agentes.

    kriptty-agentes mercado                       # régimen y rankings de hoy (no necesita Kriptty)
    kriptty-agentes plan    -c agentes.toml       # decisiones e informe, sin tocar nada
    kriptty-agentes diario  -c agentes.toml       # igual, y aplica si general.modo = "aplicar"
    kriptty-agentes vigilar -c agentes.toml       # cada hora: stop en dos fases (gracefully stop → panic)
    kriptty-agentes memes   -c agentes.toml       # cada hora: señales de memecoins y cuenta de memecoins
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .agente import Contexto, Decision, decidir_cuenta
from .config import Config
from .informe import guardar, informe_diario, informe_memes, informe_vigilancia
from .kriptty_api import BotInfo, Kriptty, KripttyError, Posicion
from .lectura import Lectura, lectura_del_dia
from .memes import detectar, memes_coingecko
from .mercado import MEMECOINS, NO_ALTS, Bitget, base_coin, kraken_assets, top_marketcap
from .riesgo import vigilar
from .senales import bull_extremo, features, rank_day, regimes

log = logging.getLogger("kriptty.agentes")
MAX_UNIVERSO = 60          # monedas con más volumen (tras los filtros) de las que se calculan señales


def universo(cfg: Config, bitget: Bitget, simbolos_kriptty: set[str] | None = None) -> tuple[set[str], dict]:
    tick = bitget.tickers()
    top = top_marketcap(cfg.top_marketcap)
    kraken = kraken_assets() if cfg.exigir_kraken else None
    memes = set(MEMECOINS)
    if cfg.excluir_memes:
        try:
            memes |= memes_coingecko()          # también las memecoins nuevas que no están en la lista fija
        except RuntimeError as e:
            log.warning("categoría de memecoins de CoinGecko no disponible: %s", e)
    disp = set()
    for coin in tick:
        b = base_coin(coin)
        if coin in ("BTC", "ETH") or b in NO_ALTS or b in cfg.lista_negra or coin in cfg.lista_negra:
            continue
        if b not in top or (kraken is not None and b not in kraken):
            continue
        if cfg.excluir_memes and b in memes and b not in cfg.lista_blanca:
            continue
        if cfg.lista_blanca and b not in cfg.lista_blanca and coin not in cfg.lista_blanca:
            continue
        if simbolos_kriptty and f"{coin}USDT" not in simbolos_kriptty:
            continue
        disp.add(coin)
    return disp, tick


def senales_del_dia(bitget: Bitget, disponibles: set[str], tickers: dict) -> tuple[str, dict[str, list[str]], bool]:
    btc = bitget.daily("BTC", days=700)
    btc_f = features(btc, btc["close"])
    regimen = str(regimes(btc_f, desplazar=False).iloc[-1])
    euforia = bool(bull_extremo(btc_f, desplazar=False).iloc[-1])
    elegidas = sorted(disponibles, key=lambda c: -tickers[c].usdt_volume_24h)[:MAX_UNIVERSO]
    filas = {}
    for coin in elegidas:
        try:
            d = bitget.daily(coin, days=90)
        except RuntimeError as e:
            log.warning("sin velas de %s: %s", coin, e)
            continue
        if len(d) < 65:
            continue
        f = features(d, btc["close"].reindex(d.index))
        filas[coin] = f.iloc[-1][["liq", "chop", "ret30", "atr_pct", "lag7"]]
    ranking = rank_day(pd.DataFrame(filas).T.astype(float)) if filas else {}
    return regimen, ranking, euforia


def _cargar_estado(path: str) -> dict:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _guardar_estado(path: str, estado: dict) -> None:
    Path(path).write_text(json.dumps(estado, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _posiciones(lista: list[Posicion]) -> dict[tuple[str, str], Posicion]:
    out = {}
    for p in lista:
        coin = p.symbol[:-4] if p.symbol.endswith("USDT") else p.symbol
        out[(coin, p.side)] = p
    return out


def _simbolos(cfg: Config, api: Kriptty, cuentas) -> set[str] | None:
    """Solo monedas que existen en todas las subcuentas de esas cuentas."""
    simbolos = None
    for ex in cfg.exchanges(cuentas):
        s = api.simbolos(ex)
        simbolos = s if simbolos is None else simbolos & s
    return simbolos


def contexto(cfg: Config, api: Kriptty | None, usar_llm: bool | None = None) -> tuple[Contexto, dict]:
    bitget = Bitget()
    simbolos = _simbolos(cfg, api, cfg.cuentas_diarias()) if api else None
    disp, tick = universo(cfg, bitget, simbolos)
    regimen, ranking, euforia = senales_del_dia(bitget, disp, tick)
    criterios = {cu.criterio for cu in cfg.cuentas_diarias()} or set(ranking)
    candidatas = sorted({c for crit in criterios for c in ranking.get(crit, [])[:15]})
    llm = cfg.usar_llm if usar_llm is None else usar_llm
    lec = lectura_del_dia(regimen, candidatas, cfg.youtube, usar_llm=llm)
    return Contexto(datetime.now(UTC).date(), regimen, lec, ranking, disp, euforia), tick


def _estado_kriptty(cfg: Config, api: Kriptty, cuentas=None) -> tuple[dict[int, BotInfo], dict[int, dict]]:
    """Bots configurados y posiciones abiertas de cada subcuenta."""
    bots: dict[int, BotInfo] = {}
    pos: dict[int, dict] = {}
    for ex, ids in cfg.exchanges(cuentas).items():
        bots.update(api.bots(ex, ids))
        pos[ex] = _posiciones(api.posiciones(ex))
    return bots, pos


def _aplicar(api: Kriptty, decisiones: list[Decision]) -> list[str]:
    errores = []
    for d in decisiones:
        if not d.hay_cambios:
            continue
        try:
            if d.cambios:
                api.actualizar_bot(d.bot_id, d.cambios, reiniciar=d.reiniciar)
            if d.arrancar:
                api.arrancar_bot(d.bot_id)
        except KripttyError as e:
            errores.append(f"bot {d.bot_id}: {e}")
    return errores


def diario(cfg: Config, aplicar: bool) -> tuple[str, list[Decision]]:
    """Cuentas con estrategia diaria. Las de memecoins van aparte, cada hora (``memes``)."""
    api = Kriptty(cfg.kriptty_url)
    ctx, _ = contexto(cfg, api)
    cuentas = cfg.cuentas_diarias()
    bots, pos = _estado_kriptty(cfg, api, cuentas)
    ocupadas = {ex: set(api.monedas_en_uso(ex).values()) for ex in cfg.exchanges(cuentas)}
    decisiones: list[Decision] = []
    for cuenta in cuentas:
        ex = cfg.exchange_de(cuenta)
        decisiones += decidir_cuenta(cuenta, cfg, ctx, bots, pos.get(ex, {}), ocupadas[ex])
    hacer = aplicar and cfg.aplicar
    errores = _aplicar(api, decisiones) if hacer else []
    estado = _cargar_estado(cfg.estado)
    estado["ultimo_diario"] = {"fecha": ctx.fecha.isoformat(), "regimen": ctx.regimen, "riesgo": ctx.lectura.riesgo,
                               "vetadas": ctx.lectura.vetadas, "aplicado": hacer,
                               "decisiones": [d.__dict__ for d in decisiones]}
    _guardar_estado(cfg.estado, estado)
    texto = informe_diario(ctx, decisiones, hacer, cfg.permitir_normal, errores)
    guardar(texto, cfg.informe_dir, f"{ctx.fecha.isoformat()}.md")
    return texto, decisiones


def memes(cfg: Config, aplicar: bool) -> str:
    """Cada hora: señales de memecoins y, si hay cuentas de memecoins, sus decisiones (24 h como máximo,
    lo cierra ``vigilar``). Sin cuentas configuradas, solo informa de las señales."""
    senales = detectar(Bitget(), cfg.memes)
    cuentas = cfg.cuentas_memes()
    decisiones: list[Decision] = []
    errores: list[str] = []
    hacer = aplicar and cfg.aplicar
    if cuentas:
        api = Kriptty(cfg.kriptty_url)
        simbolos = _simbolos(cfg, api, cuentas) or set()
        ranking = {k: [s.coin for s in senales[k] if f"{s.coin}USDT" in simbolos] for k in ("hype", "pico")}
        estado = _cargar_estado(cfg.estado)
        dia = estado.get("ultimo_diario", {})
        lec = Lectura(riesgo=dia.get("riesgo", "normal"), vetadas=dict(dia.get("vetadas", {})))
        ctx = Contexto(datetime.now(UTC).date(), dia.get("regimen", "lateral"), lec, ranking,
                       {c for v in ranking.values() for c in v})
        bots, pos = _estado_kriptty(cfg, api, cuentas)
        ocupadas = {ex: set(api.monedas_en_uso(ex).values()) for ex in cfg.exchanges(cuentas)}
        for cuenta in cuentas:
            ex = cfg.exchange_de(cuenta)
            decisiones += decidir_cuenta(cuenta, cfg, ctx, bots, pos.get(ex, {}), ocupadas[ex])
        if hacer:
            errores = _aplicar(api, decisiones)
    texto = informe_memes(senales, decisiones, hacer, errores)
    if senales["hype"] or senales["pico"] or any(d.hay_cambios for d in decisiones):
        guardar(texto, cfg.informe_dir, f"memes-{datetime.now(UTC).date().isoformat()}.md", anexar=True)
    return texto


def vigilancia(cfg: Config, aplicar: bool) -> str:
    api = Kriptty(cfg.kriptty_url)
    bots, pos = _estado_kriptty(cfg, api)
    tick = Bitget().tickers()
    precios = {c: t.last for c, t in tick.items()}
    estado = _cargar_estado(cfg.estado)
    alertas = vigilar(cfg, bots, pos, precios, estado)
    hacer = aplicar and cfg.aplicar
    if hacer:
        for a in alertas:
            if a.cambios:
                try:
                    api.actualizar_bot(a.bot_id, a.cambios, reiniciar=bots[a.bot_id].running)
                except KripttyError as e:
                    log.error("bot %s: %s", a.bot_id, e)
    _guardar_estado(cfg.estado, estado)
    texto = informe_vigilancia(alertas, hacer)
    if texto:
        guardar(texto, cfg.informe_dir, f"{datetime.now(UTC).date().isoformat()}.md", anexar=True)
    return texto


def solo_mercado(cfg: Config, usar_llm: bool = False) -> str:
    ctx, _ = contexto(cfg, None, usar_llm=usar_llm)
    return informe_diario(ctx, [], False, cfg.permitir_normal)


__all__ = ["diario", "memes", "vigilancia", "solo_mercado", "Lectura"]
