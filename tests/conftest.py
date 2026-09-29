"""Fixtures: exchange simulado sin red (datos sintéticos) sobre el PaperExchangeClient."""
from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("STATE_PATH", ":memory:")
os.environ.setdefault("TRADING_MODE", "dry_run")

from kriptty.config import Settings  # noqa: E402
from kriptty.data.macro import MacroDashboard  # noqa: E402
from kriptty.exchange.paper import PaperExchangeClient  # noqa: E402
from kriptty.exchange.router import AccountRouter  # noqa: E402
from kriptty.state import StateStore  # noqa: E402
from kriptty.strategies import Context  # noqa: E402

TF_MS = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}


def make_candles(n: int, start: float, drift: float = 0.0, vol: float = 0.01, tf: str = "1h",
                 seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = start * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    opens = np.concatenate([[start], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + vol / 2)
    lows = np.minimum(opens, closes) * (1 - vol / 2)
    end = int(time.time() * 1000) // TF_MS[tf] * TF_MS[tf] - TF_MS[tf]
    idx = pd.to_datetime([end - (n - 1 - i) * TF_MS[tf] for i in range(n)], unit="ms", utc=True)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                         "volume": rng.uniform(100, 200, n)}, index=idx)


class FakeClient(PaperExchangeClient):
    """Paper broker con mercado sintético controlable desde el test."""

    def __init__(self, account_id: str, settings: Settings):
        super().__init__(account_id, settings)
        self.prices: dict[str, float] = {}
        self.candles: dict[tuple[str, str], pd.DataFrame] = {}
        self.funding: dict[str, dict] = {}
        self.ticker_extra: dict[str, dict] = {}

    async def load_markets(self) -> None:
        return None

    def market(self, symbol: str) -> dict:
        return {"limits": {"amount": {"min": 0.0001}, "cost": {"min": 5}}}

    def amount_to_precision(self, symbol: str, amount: float, price: float | None = None) -> float:
        rounded = np.floor(amount * 10_000) / 10_000
        if rounded < 0.0001 or (price and rounded * price < 5):
            return 0.0
        return float(rounded)

    def price_to_precision(self, symbol: str, price: float) -> float:
        return round(price, 4)

    async def ohlcv(self, symbol, timeframe, limit=200, closed_only=True):
        df = self.candles.get((symbol, timeframe))
        if df is None:
            df = make_candles(max(limit, 60), self.prices.get(symbol, 100.0), tf=timeframe)
        return df.tail(limit)

    async def ticker(self, symbol):
        p = self.prices.get(symbol, 100.0)
        return {"symbol": symbol, "last": p, "bid": p * 0.99999, "ask": p * 1.00001,
                "quoteVolume": 1e9, "percentage": 0.0, **self.ticker_extra.get(symbol, {})}

    async def last_price(self, symbol):
        return self.prices.get(symbol, 100.0)

    async def tickers(self, market_type="swap"):
        out = {}
        for s in self.prices:
            if (":" in s) == (market_type == "swap"):
                out[s] = await self.ticker(s)
        return out

    async def funding_rate(self, symbol):
        return self.funding.get(symbol, {"rate": 0.0001, "interval_hours": 8.0, "next_ts": None,
                                         "mark": None, "index": None})


class FakeMacro:
    def __init__(self, score: float = 0.0, coverage: float = 1.0):
        self.dashboard = MacroDashboard(score=score, coverage=coverage)

    async def get(self) -> MacroDashboard:
        return self.dashboard


@pytest.fixture
def settings() -> Settings:
    return Settings(trading_mode="dry_run", state_path=":memory:", dry_run_equity=10_000)


@pytest.fixture
def ctx(settings):
    state = StateStore(":memory:")
    router = AccountRouter(settings, state)
    clients: dict[str, FakeClient] = {}

    def client(account_id: str) -> FakeClient:
        account_id = account_id.upper()
        if account_id not in clients:
            clients[account_id] = FakeClient(account_id, settings)
        return clients[account_id]

    router.client = client  # type: ignore[method-assign]
    return Context(settings=settings, router=router, state=state, macro=FakeMacro())
