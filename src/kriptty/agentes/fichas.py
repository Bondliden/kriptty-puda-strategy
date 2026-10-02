"""Ficha de cada moneda: ¿hay un buen proyecto detrás? Puntuación de 0 a 10 con datos públicos, para dar más
exposición a las monedas sólidas y menos a las especulativas.

Fuentes:

- **CoinGecko** (gratis): puesto por capitalización, valoración totalmente diluida, categorías, opinión de
  la comunidad y usuarios que la siguen;
- **CoinMarketCap** (con ``CMC_API_KEY``, también la gratuita): fecha de alta, etiquetas (fondos que han
  invertido, memes…), libro blanco y código fuente;
- **Bitget y Kraken**: liquidez y acceso.

Reglas (transparentes, sin IA):

| Criterio | Puntos |
|---|---|
| Antigüedad: ≥ 3 años / ≥ 1 año / < 90 días | +2 / +1 / −2 |
| Capitalización: top 50 / top 150 / fuera del top 300 o sin dato | +2 / +1 / −1 |
| Valoración diluida / capitalización ≤ 1,3 (pocos desbloqueos pendientes) / > 2,5 | +1 / −1 |
| Listada en Kraken · futuros de Bitget con más de 20 M$ al día | +1 · +1 |
| Libro blanco o código fuente publicados | +1 |
| Respaldo de fondos conocidos (a16z, Coinbase Ventures, Binance Labs, Paradigm…) | +1 |
| Opinión positiva en CoinGecko ≥ 70% | +0,5 |
| Cotiza en 3 o más exchanges grandes (Binance, Coinbase, Kraken, OKX, Bybit, Bitget, Upbit) | +1 |
| Escaneo de seguridad de CoinMarketCap (GoPlus) «safe» / cualquier otro nivel | +0,5 / −3 |
| Aviso de CoinMarketCap en la ficha (hackeo, migración, sospecha…) | −1 |
| Capitalización declarada por el propio proyecto, sin verificar | −0,5 |
| Carteras: ≥ 100.000 / < 10.000 / < 1.000 | +1 / −1 / −3 |
| Una sola cartera personal (no contrato) con > 8% / > 15% del suministro | −1 / −2 |
| Las 10 mayores carteras con > 70% del suministro | −1 |
| Memecoin | −2 |

Si el escaneo marca riesgo alto («danger», «high», «scam», «honeypot»), si tiene menos de 1.000 carteras o si
una sola cartera personal tiene más del 30% (podría tumbar el precio), la moneda queda **bloqueada**: el agente
no la opera (factor 0).

Carteras y concentración: GoPlus (gratis, la misma fuente que el escaneo de CoinMarketCap), sobre el contrato
principal de la moneda. Los contratos (puentes, staking, tesorerías, exchanges) no cuentan como cartera
personal; en Solana no se distinguen y el umbral sube. Las monedas nativas (BTC, ETH, ZEC…) no se miran: sus
«contratos» son versiones envueltas en otras cadenas.

Parte de 2 y queda entre 0 y 10. **Sólida** (≥ 7): exposición ×1,5. **Normal** (4–7): ×1.
**Especulativa** (< 4): ×0,5. Subir exposición sigue siendo una propuesta mientras no haya
``permitir_normal``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .mercado import COINGECKO, MEMECOINS, _get

log = logging.getLogger("kriptty.agentes.fichas")
CMC = "https://pro-api.coinmarketcap.com"
FONDOS = ("a16z", "coinbase-ventures", "binance-labs", "paradigm", "polychain", "pantera", "multicoin",
          "electric-capital", "dragonfly", "jump-crypto", "framework-ventures", "yzi-labs")
SOLIDA, ESPECULATIVA = 7.0, 4.0
GRANDES = {"binance", "coinbase-exchange", "kraken", "okx", "bybit", "bitget", "upbit"}
RIESGO_ALTO = ("danger", "high", "scam", "honeypot", "malicious")
FACTORES = {"sólida": 1.5, "normal": 1.0, "especulativa": 0.5}


@dataclass
class Ficha:
    coin: str
    nombre: str = ""
    puntos: float = 0.0
    clase: str = "normal"                  # sólida | normal | especulativa
    motivos: list[str] = field(default_factory=list)
    puesto: int | None = None
    antiguedad_dias: int | None = None
    meme: bool = False
    riesgo: str = ""                       # escaneo de seguridad de CMC: safe, warning, danger…
    bloqueada: bool = False
    carteras: int | None = None            # número de carteras (holders)
    mayor_cartera: float | None = None     # % del suministro de la mayor cartera personal
    top10: float | None = None             # % de las 10 mayores carteras

    @property
    def factor(self) -> float:
        return 0.0 if self.bloqueada else FACTORES[self.clase]


def _cache() -> Path:
    return Path(os.environ.get("KRIPTTY_CACHE", "data")) / "fichas_cache.json"


def _cmc_info(simbolos: list[str]) -> dict[str, dict]:
    """Ficha de CoinMarketCap de cada símbolo. Si varias monedas comparten símbolo (HYPE, PUMP…), la de mejor
    puesto en el ranking. Añade ``first_historical_data`` (primer precio) para medir la antigüedad."""
    clave = os.environ.get("CMC_API_KEY", "")
    if not clave or not simbolos:
        return {}
    h = {"X-CMC_PRO_API_KEY": clave}
    elegidos: dict[str, dict] = {}
    validos = [s for s in simbolos if re.fullmatch(r"[A-Z0-9]{1,20}", s)]   # CMC rechaza el lote entero si hay uno raro
    try:
        mapa = []
        for i in range(0, len(validos), 100):
            lote = validos[i:i + 100]
            try:
                mapa += _get(f"{CMC}/v1/cryptocurrency/map", {"symbol": ",".join(lote)}, headers=h, retries=1).get("data", [])
            except RuntimeError:
                for s in lote:                                   # uno a uno: los que no existan, fuera
                    try:
                        mapa += _get(f"{CMC}/v1/cryptocurrency/map", {"symbol": s}, headers=h, retries=1).get("data", [])
                    except RuntimeError:
                        continue
        for m in mapa:
            s = str(m.get("symbol", "")).upper()
            if not m.get("is_active", 1):
                continue
            actual = elegidos.get(s)
            if actual is None or (m.get("rank") or 10**9) < (actual.get("rank") or 10**9):
                elegidos[s] = m
        ids = [str(m["id"]) for m in elegidos.values()]
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 100):
            datos = _get(f"{CMC}/v2/cryptocurrency/info", {"id": ",".join(ids[i:i + 100])}, headers=h,
                         retries=1).get("data", {})
            for d in datos.values():
                s = str(d.get("symbol", "")).upper()
                d["first_historical_data"] = (elegidos.get(s) or {}).get("first_historical_data")
                d["cmc_rank"] = (elegidos.get(s) or {}).get("rank")
                out[s] = d
        return out
    except RuntimeError as e:
        log.warning("CoinMarketCap (info) no disponible: %s", str(e).replace(clave, "***"))
        return {}


def _contrato_principal(info: dict) -> tuple[str, str] | None:
    """(plataforma, dirección) del contrato principal; si no consta, el primero de la lista."""
    plat = (info or {}).get("platform") or {}
    if plat.get("token_address"):
        return str(plat.get("slug") or plat.get("name", "")).lower(), plat["token_address"]
    for c in (info or {}).get("contract_address") or []:
        p = ((c.get("platform") or {}).get("coin") or {}).get("slug") or (c.get("platform") or {}).get("name", "")
        if c.get("contract_address"):
            return str(p).lower(), c["contract_address"]
    return None


GOPLUS_CADENAS = {"ethereum": "1", "bnb": "56", "bsc": "56", "bnb-smart-chain-bep20": "56", "base": "8453",
                  "arbitrum": "42161", "arbitrum-one": "42161", "polygon": "137", "polygon-pos": "137",
                  "avalanche": "43114", "avalanche-c-chain": "43114", "optimism": "10", "tron": "tron"}


def _goplus(contrato: tuple[str, str] | None) -> dict:
    """Carteras y concentración del contrato (GoPlus, gratis)."""
    if not contrato:
        return {}
    plat, addr = contrato
    try:
        if plat == "solana":
            r = _get("https://api.gopluslabs.io/api/v1/solana/token_security", {"contract_addresses": addr}, retries=1)
            res = list((r.get("result") or {}).values())
            return {**res[0], "_solana": True} if res else {}
        cadena = GOPLUS_CADENAS.get(plat)
        if not cadena:
            return {}
        r = _get(f"https://api.gopluslabs.io/api/v1/token_security/{cadena}", {"contract_addresses": addr}, retries=1)
        return (r.get("result") or {}).get(addr.lower()) or {}
    except RuntimeError:
        return {}


def _cmc_dex(info: dict) -> dict:
    """Ficha de DEX de CoinMarketCap del contrato principal: nivel de riesgo (``rl``) y exchanges donde cotiza."""
    clave = os.environ.get("CMC_API_KEY", "")
    contrato = _contrato_principal(info)
    if not clave or not contrato:
        return {}
    try:
        return _get(f"{CMC}/v1/dex/token", {"platform": contrato[0], "address": contrato[1]},
                    headers={"X-CMC_PRO_API_KEY": clave}, retries=1).get("data") or {}
    except RuntimeError:
        return {}


def _coingecko(simbolos: list[str]) -> dict[str, dict]:
    """Datos de mercado de CoinGecko de esas monedas (las 500 con más capitalización)."""
    quiero = {s.upper() for s in simbolos}
    out: dict[str, dict] = {}
    for pagina in (1, 2):
        try:
            datos = _get(COINGECKO, {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": pagina})
        except RuntimeError:
            break
        for c in datos:
            s = str(c.get("symbol", "")).upper()
            if s in quiero and s not in out:
                out[s] = c
    return out


def _detalle_cg(cg_id: str) -> dict:
    try:
        return _get(f"https://api.coingecko.com/api/v3/coins/{cg_id}",
                    {"localization": "false", "tickers": "false", "market_data": "false",
                     "community_data": "false", "developer_data": "false"})
    except RuntimeError:
        return {}


def puntuar(coin: str, cg: dict | None, det: dict | None, cmc: dict | None, kraken: set[str] | None,
            vol_bitget: float | None, ahora: datetime | None = None, dex: dict | None = None,
            goplus: dict | None = None) -> Ficha:
    ahora = ahora or datetime.now(UTC)
    f = Ficha(coin, nombre=(cmc or {}).get("name") or (cg or {}).get("name") or coin)
    p = 2.0
    alta = (cmc or {}).get("first_historical_data") or (cmc or {}).get("date_added") or (det or {}).get("genesis_date")
    if alta:
        dias = (ahora - datetime.fromisoformat(str(alta).replace("Z", "+00:00")).astimezone(UTC)).days \
            if "T" in str(alta) else (ahora.date() - datetime.fromisoformat(str(alta)).date()).days
        f.antiguedad_dias = dias
        if dias >= 3 * 365:
            p += 2
            f.motivos.append(f"{dias // 365} años en el mercado")
        elif dias >= 365:
            p += 1
            f.motivos.append(f"{dias // 365} año(s) en el mercado")
        elif dias < 90:
            p -= 2
            f.motivos.append(f"muy nueva ({dias} días)")
    puesto = (cg or {}).get("market_cap_rank")
    f.puesto = puesto
    if puesto and puesto <= 50:
        p += 2
        f.motivos.append(f"top {puesto} por capitalización")
    elif puesto and puesto <= 150:
        p += 1
        f.motivos.append(f"puesto {puesto} por capitalización")
    elif not puesto or puesto > 300:
        p -= 1
        f.motivos.append("fuera del top 300" if puesto else "sin capitalización conocida")
    mc, fdv = (cg or {}).get("market_cap") or 0, (cg or {}).get("fully_diluted_valuation") or 0
    if mc and fdv:
        r = fdv / mc
        if r <= 1.3:
            p += 1
            f.motivos.append("casi todo el suministro ya en circulación")
        elif r > 2.5:
            p -= 1
            f.motivos.append(f"valoración diluida {r:.1f} veces la capitalización (desbloqueos pendientes)")
    if kraken is not None and coin in kraken:
        p += 1
        f.motivos.append("listada en Kraken")
    if vol_bitget and vol_bitget >= 20e6:
        p += 1
        f.motivos.append(f"futuros en Bitget con {vol_bitget / 1e6:,.0f} M$ al día")
    urls = (cmc or {}).get("urls") or {}
    if urls.get("technical_doc") or urls.get("source_code"):
        p += 1
        f.motivos.append("libro blanco o código publicados")
    tags = [str(t).lower() for t in ((cmc or {}).get("tags") or [])]
    fondos = [t for t in tags if any(x in t for x in FONDOS)]
    if fondos:
        p += 1
        f.motivos.append("respaldo: " + ", ".join(sorted({t.replace("-portfolio", "") for t in fondos}))[:80])
    voto = (det or {}).get("sentiment_votes_up_percentage")
    if voto and voto >= 70:
        p += 0.5
        f.motivos.append(f"{voto:.0f}% de opiniones positivas")
    grandes = {str(x.get("slug", "")).lower() for x in ((dex or {}).get("cexs") or [])} & GRANDES
    if len(grandes) >= 3:
        p += 1
        f.motivos.append(f"cotiza en {len(grandes)} exchanges grandes")
    rl = str((dex or {}).get("rl") or "").lower()
    f.riesgo = rl
    if rl == "safe":
        p += 0.5
        f.motivos.append("escaneo de seguridad de CMC: seguro")
    elif rl:
        p -= 3
        f.motivos.append(f"escaneo de seguridad de CMC: {rl}")
        f.bloqueada = any(x in rl for x in RIESGO_ALTO)
    aviso = str((cmc or {}).get("notice") or "").strip()
    if aviso:
        p -= 1
        f.motivos.append("aviso de CMC: " + aviso[:100])
    if (cmc or {}).get("self_reported_market_cap"):
        p -= 0.5
        f.motivos.append("capitalización declarada por el proyecto, sin verificar")
    bloqueo_carteras = _carteras(f, goplus)
    p += bloqueo_carteras[0]
    categorias = " ".join(str(c).lower() for c in ((det or {}).get("categories") or []))
    f.meme = coin in MEMECOINS or "meme" in categorias or "memes" in tags
    if f.meme:
        p -= 2
        f.motivos.append("memecoin")
    f.bloqueada = f.bloqueada or bloqueo_carteras[1]
    f.puntos = round(max(0.0, min(10.0, p)), 1)
    f.clase = "sólida" if f.puntos >= SOLIDA else "especulativa" if f.puntos < ESPECULATIVA else "normal"
    return f


def _carteras(f: Ficha, gp: dict | None) -> tuple[float, bool]:
    """(puntos, bloquear) por número de carteras y concentración."""
    if not gp:
        return 0.0, False
    pts, bloquear = 0.0, False
    try:
        n = int(float(gp.get("holder_count") or 0))
    except ValueError:
        n = 0
    if n:
        f.carteras = n
        if n >= 100_000:
            pts += 1
            f.motivos.append(f"{n:,} carteras".replace(",", "."))
        elif n < 1_000:
            pts -= 3
            bloquear = True
            f.motivos.append(f"solo {n} carteras")
        elif n < 10_000:
            pts -= 1
            f.motivos.append(f"pocas carteras ({n:,})".replace(",", "."))
    solana = bool(gp.get("_solana"))
    holders = gp.get("holders") or []
    libres = [h for h in holders if str(h.get("is_locked", "0")) != "1"]
    personales = libres if solana else [h for h in libres if str(h.get("is_contract", "0")) != "1"]
    mayor = max((float(h.get("percent") or 0) for h in personales), default=0.0)
    top10 = sum(float(h.get("percent") or 0) for h in libres[:10])
    f.mayor_cartera = round(mayor * 100, 1)
    f.top10 = round(top10 * 100, 1)
    limite_bloqueo, limite_fuerte = (0.40, 0.25) if solana else (0.30, 0.15)
    quien = "la mayor cartera" if solana else "una sola cartera personal"
    if mayor > limite_bloqueo:
        bloquear = True
        pts -= 2
        f.motivos.append(f"{quien} tiene el {mayor:.0%}: puede tumbar el precio")
    elif mayor > limite_fuerte:
        pts -= 2
        f.motivos.append(f"{quien} tiene el {mayor:.0%}")
    elif mayor > 0.08 and not solana:
        pts -= 1
        f.motivos.append(f"{quien} tiene el {mayor:.0%}")
    if top10 > 0.70:
        pts -= 1
        f.motivos.append(f"las 10 mayores carteras tienen el {top10:.0%}")
    return pts, bloquear


def fichas(monedas: list[str], kraken: set[str] | None = None, volumenes: dict[str, float] | None = None,
           detalle_max: int = 25) -> dict[str, Ficha]:
    """Fichas de esas monedas, guardadas un día en ``data/fichas_cache.json``."""
    hoy = datetime.now(UTC).date().isoformat()
    ruta = _cache()
    try:
        cache = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if cache.get("dia") != hoy:
        cache = {"dia": hoy, "fichas": {}}
    out = {c: Ficha(**v) for c, v in cache["fichas"].items() if c in monedas}
    faltan = [c for c in monedas if c not in out]
    if faltan:
        cg = _coingecko(faltan)
        cmc = _cmc_info(faltan)
        for i, c in enumerate(faltan):
            det = _detalle_cg(cg[c]["id"]) if c in cg and i < detalle_max else {}
            if det:
                time.sleep(2.0)                     # CoinGecko gratis: ~30 peticiones por minuto
            # monedas nativas (BTC, ETH, ZEC…): sus «contratos» son versiones envueltas en otras cadenas, donde el
            # puente tiene casi todo el suministro; no dicen nada de la moneda
            token = c in cmc and str(cmc[c].get("category", "")).lower() != "coin"
            dex = _cmc_dex(cmc[c]) if token else {}
            gp = _goplus(_contrato_principal(cmc[c])) if token else {}
            out[c] = puntuar(c, cg.get(c), det, cmc.get(c), kraken, (volumenes or {}).get(c), dex=dex, goplus=gp)
            cache["fichas"][c] = asdict(out[c])
        try:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return out
