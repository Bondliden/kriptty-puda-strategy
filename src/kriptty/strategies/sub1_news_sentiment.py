"""SUB1 — News Sentiment (NLP).

Señal: score de sentimiento ponderado por fuente y frescura (half-life 3h).
    score > 0.65 → LONG · score < 0.35 → SHORT · resto → nada.
SL: entry ∓ 1.5 × ATR(14, 4H). Trailing: se recalcula cada ciclo y solo se
mueve a favor. TP: entry ± 2.5 × ATR (R:R 1.67, como se documentó; el código
original aplicaba 2.5 sobre la distancia del SL, es decir 3.75 × ATR).
"""
from __future__ import annotations

import math
from datetime import UTC, datetime

from ..data.news import Article, NewsCollector
from ..data.sentiment import SentimentAnalyzer
from ..exchange.client import perp
from ..indicators import atr, last
from .base import Strategy


class NewsSentimentStrategy(Strategy):
    account_id = "SUB1"
    name = "News Sentiment NLP"
    schedule = {"trigger": "cron", "hour": "*/2", "minute": 2}
    leverage = 3

    ASSETS = ["BTC", "ETH", "SOL", "BNB"]
    BUY_THRESHOLD = 0.65
    SELL_THRESHOLD = 0.35
    ATR_PERIOD = 14
    SL_ATR = 1.5
    TP_ATR = 2.5
    LOOKBACK_HOURS = 6
    HALF_LIFE_H = 3.0
    MIN_ARTICLES = 3
    MIN_VOLUME_USDT = 5_000_000
    # El diseño original arriesgaba 3% por operación con hasta 5 símbolos muy
    # correlacionados (15% del capital expuesto a la vez). Se reduce y se limita.
    RISK_PCT = 0.015
    MAX_OPEN = 2

    def __init__(self, ctx, collector: NewsCollector | None = None,
                 analyzer: SentimentAnalyzer | None = None):
        super().__init__(ctx)
        s = ctx.settings
        self.collector = collector or NewsCollector(s.cryptopanic_api_key, s.cryptopanic_plan,
                                                    s.newsdata_api_key)
        self._analyzer = analyzer

    @property
    def analyzer(self) -> SentimentAnalyzer:
        if self._analyzer is None:
            self._analyzer = SentimentAnalyzer(self.ctx.settings.use_finbert)
        return self._analyzer

    def weighted_score(self, articles: list[Article], now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        total_w = weighted = 0.0
        for a in articles:
            age_h = max((now - a.published).total_seconds() / 3600, 0)
            w = a.weight * math.exp(-math.log(2) * age_h / self.HALF_LIFE_H)
            weighted += self.analyzer.score(a.title) * w
            total_w += w
        return weighted / total_w if total_w else 0.5

    async def _trail(self, pos, atr_value: float) -> None:
        stored = self.get_state(f"sl:{pos.symbol}")
        if pos.side == "long":
            new_sl = pos.mark_price - self.SL_ATR * atr_value
            better = stored is None or new_sl > stored
        else:
            new_sl = pos.mark_price + self.SL_ATR * atr_value
            better = stored is None or new_sl < stored
        if better:
            await self.ctx.router.update_stop_loss(self.account_id, pos, new_sl, tag=self.account_id)
            self.set_state(f"sl:{pos.symbol}", new_sl)

    async def run_cycle(self) -> None:
        client = self.client
        open_positions = {p.symbol: p for p in await self.positions()}
        for asset in self.ASSETS:
            symbol = perp(asset)
            candles = await client.ohlcv(symbol, "4h", limit=60)
            atr_value = last(atr(candles, self.ATR_PERIOD))

            if symbol in open_positions:
                await self._trail(open_positions[symbol], atr_value)
                continue
            self.ctx.state.delete(self.account_id, f"sl:{symbol}")  # posición ya cerrada
            if len(open_positions) >= self.MAX_OPEN:
                continue

            ticker = await client.ticker(symbol)
            if float(ticker.get("quoteVolume") or 0) < self.MIN_VOLUME_USDT:
                self.log.info("%s: volumen 24h insuficiente", symbol)
                continue

            articles = await self.collector.collect(asset, self.LOOKBACK_HOURS)
            if len(articles) < self.MIN_ARTICLES:
                self.log.info("%s: %d noticias < %d, sin señal", asset, len(articles), self.MIN_ARTICLES)
                continue
            score = self.weighted_score(articles)
            self.log.info("%s: score=%.3f (%d noticias)", asset, score, len(articles))
            if self.SELL_THRESHOLD <= score <= self.BUY_THRESHOLD:
                continue

            entry = float(ticker["last"])
            if score > self.BUY_THRESHOLD:
                side, sl, tp = "buy", entry - self.SL_ATR * atr_value, entry + self.TP_ATR * atr_value
            else:
                side, sl, tp = "sell", entry + self.SL_ATR * atr_value, entry - self.TP_ATR * atr_value
            result = await self.open_position(symbol, side, entry, sl, tp, self.RISK_PCT,
                                              reason=f"sentiment={score:.3f} n={len(articles)}")
            if result:
                self.set_state(f"sl:{symbol}", sl)
                open_positions[symbol] = None  # cuenta para MAX_OPEN
