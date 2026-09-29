"""Análisis de sentimiento: VADER por defecto, FinBERT opcional (USE_FINBERT=true
e instalar el extra ``kriptty[finbert]``). Score normalizado 0 (bajista) → 1 (alcista)."""
from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger(__name__)


class SentimentAnalyzer:
    def __init__(self, use_finbert: bool = False):
        self._finbert = None
        if use_finbert:
            try:
                from transformers import pipeline

                self._finbert = pipeline("text-classification", model="ProsusAI/finbert", top_k=None)
                log.info("🧠 FinBERT cargado")
            except Exception as e:  # modelo no instalado / sin memoria
                log.warning("FinBERT no disponible (%s); se usa VADER", e)
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        self._vader = SentimentIntensityAnalyzer()
        self.score = lru_cache(maxsize=4096)(self._score)

    def _score(self, text: str) -> float:
        if not text or len(text.strip()) < 10:
            return 0.5
        if self._finbert is not None:
            try:
                scores = {r["label"].lower(): r["score"] for r in self._finbert(text[:512])[0]}
                return (scores.get("positive", 0) - scores.get("negative", 0) + 1) / 2
            except Exception as e:
                log.debug("FinBERT falló: %s", e)
        return (self._vader.polarity_scores(text)["compound"] + 1) / 2
