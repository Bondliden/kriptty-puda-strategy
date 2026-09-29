"""Recolector de noticias.

Cambios respecto al diseño original:
  * CryptoPanic migró a /api/<plan>/v2/posts/ y retiró el plan gratuito: ahora es
    opcional (CRYPTOPANIC_API_KEY + CRYPTOPANIC_PLAN). Las RSS no requieren clave.
  * La deduplicación era global y permanente: tras el primer ciclo casi ninguna
    noticia volvía a contar y la estrategia se quedaba sin señal. Ahora se
    deduplica dentro de cada consulta y se filtra por ventana temporal real.
  * Coincidencia por palabra completa ("sol" ya no coincide con "solution").
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp

log = logging.getLogger(__name__)

RSS_FEEDS = {
    "coindesk": ("https://www.coindesk.com/arc/outboundfeeds/rss/", 0.9),
    "cointelegraph": ("https://cointelegraph.com/rss", 0.85),
    "decrypt": ("https://decrypt.co/feed", 0.8),
    "theblock": ("https://www.theblock.co/rss.xml", 0.85),
}

ASSET_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "ether", "eth"],
    "SOL": ["solana"],
    "BNB": ["bnb", "bnb chain"],
    "XRP": ["xrp", "ripple"],
}


@dataclass
class Article:
    title: str
    published: datetime
    source: str
    weight: float

    @property
    def key(self) -> str:
        return hashlib.sha1(self.title.lower().strip().encode()).hexdigest()


def _matches(text: str, keywords: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(k)}\b", text, re.IGNORECASE) for k in keywords)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        from dateutil import parser

        dt = parser.parse(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None


class NewsCollector:
    def __init__(self, cryptopanic_key: str = "", cryptopanic_plan: str = "developer",
                 newsdata_key: str = ""):
        self.cryptopanic_key = cryptopanic_key
        self.cryptopanic_plan = cryptopanic_plan
        self.newsdata_key = newsdata_key

    async def _rss(self, session: aiohttp.ClientSession, name: str, url: str, weight: float) -> list[Article]:
        import feedparser

        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            feed = feedparser.parse(await r.text())
        out = []
        for e in feed.entries[:50]:
            published = _parse_date(e.get("published") or e.get("updated"))
            if published:
                out.append(Article(f"{e.get('title', '')}. {e.get('summary', '')}"[:600], published, name, weight))
        return out

    async def _cryptopanic(self, session: aiohttp.ClientSession, currency: str) -> list[Article]:
        if not self.cryptopanic_key:
            return []
        url = f"https://cryptopanic.com/api/{self.cryptopanic_plan}/v2/posts/"
        params = {"auth_token": self.cryptopanic_key, "currencies": currency, "kind": "news", "public": "true"}
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
        return [
            Article(p.get("title", ""), d, "cryptopanic", 1.0)
            for p in data.get("results", [])
            if (d := _parse_date(p.get("published_at")))
        ]

    async def _newsdata(self, session: aiohttp.ClientSession, keyword: str) -> list[Article]:
        if not self.newsdata_key:
            return []
        params = {"apikey": self.newsdata_key, "q": keyword, "language": "en"}
        async with session.get("https://newsdata.io/api/1/latest", params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
        return [
            Article(f"{a.get('title') or ''}. {a.get('description') or ''}"[:600], d, "newsdata", 0.8)
            for a in data.get("results", []) or []
            if (d := _parse_date(a.get("pubDate")))
        ]

    async def collect(self, asset: str, lookback_hours: float = 6) -> list[Article]:
        keywords = ASSET_KEYWORDS.get(asset, [asset.lower()])
        async with aiohttp.ClientSession(headers={"User-Agent": "kriptty/0.2"}) as session:
            tasks = [self._rss(session, n, u, w) for n, (u, w) in RSS_FEEDS.items()]
            tasks += [self._cryptopanic(session, asset), self._newsdata(session, keywords[0])]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        seen: set[str] = set()
        articles: list[Article] = []
        for res in results:
            if isinstance(res, Exception):
                log.debug("Fuente de noticias falló: %r", res)
                continue
            for a in res:
                if a.published < cutoff or a.key in seen:
                    continue
                if a.source != "cryptopanic" and not _matches(a.title, keywords):
                    continue
                seen.add(a.key)
                articles.append(a)
        log.info("📰 %s: %d noticias en las últimas %.0fh", asset, len(articles), lookback_hours)
        return articles
