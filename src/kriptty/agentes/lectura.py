"""Lectura diaria del mercado: miedo y codicia, noticias (RSS de medios y de canales de YouTube) y una
revisión con Claude que **solo puede reducir riesgo**: subir el nivel de riesgo o vetar monedas, nunca
abrir más exposición ni proponer monedas nuevas.

Si no hay clave de la API de Anthropic o la llamada falla, el agente sigue solo con las reglas.
"""
from __future__ import annotations

import email.utils
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .mercado import _get

log = logging.getLogger("kriptty.agentes.lectura")

FUENTES_RSS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
    "Google News": "https://news.google.com/rss/search?q=crypto+OR+bitcoin+OR+altcoin+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Google News ES": "https://news.google.com/rss/search?q=criptomonedas+OR+bitcoin+when:1d&hl=es&gl=ES&ceid=ES:es",
}
YOUTUBE_API = "https://www.googleapis.com/youtube/v3/playlistItems"
NIVELES = ("normal", "elevado", "extremo")
MODELO = "claude-opus-5-5"


@dataclass
class Titular:
    fuente: str
    titulo: str
    fecha: datetime | None


@dataclass
class Lectura:
    fear_greed: int | None = None
    fear_greed_texto: str = ""
    titulares: list[Titular] = field(default_factory=list)
    riesgo: str = "normal"                 # normal | elevado | extremo (lo decide la revisión)
    vetadas: dict[str, str] = field(default_factory=dict)   # moneda → motivo
    resumen: str = ""
    revisada_por_llm: bool = False


def fear_greed() -> tuple[int | None, str]:
    try:
        d = _get("https://api.alternative.me/fng/", {"limit": 1, "format": "json"})["data"][0]
        return int(d["value"]), d.get("value_classification", "")
    except Exception as e:  # noqa: BLE001
        log.warning("Fear & Greed no disponible: %s", e)
        return None, ""


def _parse_fecha(txt: str | None) -> datetime | None:
    if not txt:
        return None
    try:
        return email.utils.parsedate_to_datetime(txt).astimezone(UTC)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def leer_rss(nombre: str, url: str, horas: int = 24, maximo: int = 30) -> list[Titular]:
    """Titulares de las últimas ``horas`` de un RSS/Atom (medios o canal de YouTube)."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 kriptty-agentes"})
        with urllib.request.urlopen(req, timeout=20) as r:
            root = ET.fromstring(r.read())
    except Exception as e:  # noqa: BLE001
        log.warning("RSS %s no disponible: %s", nombre, e)
        return []
    limite = datetime.now(UTC) - timedelta(hours=horas)
    out = []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//a:entry", ns)
    for it in items[:maximo]:
        titulo = (it.findtext("title") or it.findtext("a:title", namespaces=ns) or "").strip()
        fecha = _parse_fecha(it.findtext("pubDate") or it.findtext("a:published", namespaces=ns)
                             or it.findtext("a:updated", namespaces=ns))
        if titulo and (fecha is None or fecha >= limite):
            out.append(Titular(nombre, re.sub(r"\s+", " ", titulo), fecha))
    return out


def youtube_rss(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def youtube_titulares(canales: dict[str, str], horas: int = 24) -> list[Titular]:
    """Títulos de los vídeos recientes de cada canal (nombre → id «UC…»).

    Con ``YOUTUBE_API_KEY`` usa la API de datos de YouTube (1 unidad de cuota por canal y día: la lista de
    subidas del canal). Sin clave prueba el RSS público, que YouTube devuelve con 404 desde 2026 en muchos
    casos; si falla, el agente sigue con las demás fuentes.
    """
    if not canales:
        return []
    clave = os.environ.get("YOUTUBE_API_KEY", "")
    limite = datetime.now(UTC) - timedelta(hours=horas)
    out: list[Titular] = []
    for nombre, cid in canales.items():
        if not clave:
            out.extend(leer_rss(f"YouTube · {nombre}", youtube_rss(cid), horas))
            continue
        try:
            datos = _get(YOUTUBE_API, {"part": "snippet", "playlistId": "UU" + cid[2:], "maxResults": 15, "key": clave})
        except RuntimeError as e:
            log.warning("YouTube %s no disponible: %s", nombre, str(e).replace(clave, "***"))
            continue
        for it in datos.get("items", []):
            sn = it.get("snippet", {})
            fecha = _parse_fecha(sn.get("publishedAt"))
            titulo = re.sub(r"\s+", " ", sn.get("title", "")).strip()
            if titulo and (fecha is None or fecha >= limite):
                out.append(Titular(f"YouTube · {nombre}", titulo, fecha))
    return out


def leer_noticias(youtube: dict[str, str] | None = None, horas: int = 24) -> list[Titular]:
    out: list[Titular] = []
    for nombre, url in FUENTES_RSS.items():
        out.extend(leer_rss(nombre, url, horas))
    out.extend(youtube_titulares(youtube or {}, horas))
    # Google News repite titulares de los medios: fuera duplicados por texto
    vistos: set[str] = set()
    unicos = []
    for t in out:
        k = t.titulo.lower()[:80]
        if k not in vistos:
            vistos.add(k)
            unicos.append(t)
    return unicos


_SCHEMA = {
    "type": "object",
    "properties": {
        "riesgo": {"type": "string", "enum": list(NIVELES)},
        "vetadas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"moneda": {"type": "string"}, "motivo": {"type": "string"}},
                "required": ["moneda", "motivo"],
                "additionalProperties": False,
            },
        },
        "resumen": {"type": "string"},
    },
    "required": ["riesgo", "vetadas", "resumen"],
    "additionalProperties": False,
}

_SISTEMA = """Eres el supervisor de riesgo de un sistema de trading automático con grids (Passivbot) en futuros de
criptomonedas. Cada día unas reglas fijas, probadas con 6 años de histórico, eligen moneda y exposición para
cada cuenta. Tu papel es solo de freno: leer el contexto del día (régimen de mercado, índice de miedo y
codicia y titulares de las últimas 24 horas) y decidir si hay que ser más prudente.

Puedes hacer dos cosas, y nada más:
1. Subir el nivel de riesgo general: "normal" (no cambia nada), "elevado" (las cuentas reducen su exposición
   a la mitad y no abren posiciones nuevas en monedas con noticias dudosas) o "extremo" (todas las cuentas
   pasan a no abrir ciclos nuevos; solo se gestionan las posiciones abiertas).
2. Vetar monedas concretas de la lista de candidatas cuando haya un motivo específico en las noticias:
   hackeo o exploit, retirada de un exchange, problemas regulatorios, desanclaje, fallo del proyecto,
   desbloqueo masivo de tokens o manipulación evidente.

No puedes proponer monedas nuevas, subir exposiciones ni recomendar comprar o vender. Si no hay nada
relevante, responde "normal" sin vetos. No inventes noticias: veta solo con un titular concreto que lo
justifique y cítalo en el motivo. Responde en español."""


def revisar_con_llm(lectura: Lectura, regimen: str, candidatas: list[str], modelo: str = MODELO) -> Lectura:
    """Pide a Claude el nivel de riesgo y los vetos del día. Sin SDK, sin credenciales o con error, deja la
    lectura como está (reglas solas)."""
    try:
        import anthropic
    except ImportError:
        log.info("Paquete anthropic no instalado: revisión de noticias desactivada")
        return lectura
    titulares = "\n".join(f"- [{t.fuente}] {t.titulo}" for t in lectura.titulares[:120]) or "(sin titulares)"
    usuario = (f"Régimen del mercado según las reglas: {regimen}\n"
               f"Índice de miedo y codicia: {lectura.fear_greed} ({lectura.fear_greed_texto})\n"
               f"Monedas candidatas hoy: {', '.join(sorted(set(candidatas)))}\n\n"
               f"Titulares de las últimas 24 horas:\n{titulares}")
    try:
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=modelo,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=_SISTEMA,
            messages=[{"role": "user", "content": usuario}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _SCHEMA}},
        )
    except anthropic.AuthenticationError:
        log.warning("Sin credenciales válidas de Anthropic: revisión de noticias desactivada")
        return lectura
    except anthropic.RateLimitError:
        log.warning("Límite de peticiones de Anthropic: hoy solo reglas")
        return lectura
    except anthropic.APIStatusError as e:
        log.warning("Error de la API de Anthropic (%s): hoy solo reglas", e.status_code)
        return lectura
    except anthropic.APIConnectionError:
        log.warning("Sin conexión con Anthropic: hoy solo reglas")
        return lectura
    if resp.stop_reason == "refusal":
        log.warning("Revisión rechazada por el modelo: hoy solo reglas")
        return lectura
    texto = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        log.warning("Respuesta de la revisión no válida: hoy solo reglas")
        return lectura
    nivel = datos.get("riesgo", "normal")
    # el supervisor solo puede endurecer: nunca baja un nivel ya fijado por las reglas
    if nivel in NIVELES and NIVELES.index(nivel) > NIVELES.index(lectura.riesgo):
        lectura.riesgo = nivel
    cand = {c.upper() for c in candidatas}
    for v in datos.get("vetadas", []):
        m = str(v.get("moneda", "")).upper().replace("USDT", "")
        if m in cand:
            lectura.vetadas[m] = str(v.get("motivo", ""))[:300]
    lectura.resumen = str(datos.get("resumen", ""))[:2000]
    lectura.revisada_por_llm = True
    return lectura


def lectura_del_dia(regimen: str, candidatas: list[str], youtube: dict[str, str] | None = None,
                    usar_llm: bool = True) -> Lectura:
    fg, fg_txt = fear_greed()
    lec = Lectura(fear_greed=fg, fear_greed_texto=fg_txt, titulares=leer_noticias(youtube))
    # regla fija, sin IA: pánico extremo en el índice → riesgo elevado
    if fg is not None and fg <= 10:
        lec.riesgo = "elevado"
    if usar_llm:
        lec = revisar_con_llm(lec, regimen, candidatas)
    return lec
