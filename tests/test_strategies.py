from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from kriptty.data.news import Article
from kriptty.indicators import atr, last
from kriptty.strategies.sub1_news_sentiment import NewsSentimentStrategy
from kriptty.strategies.sub2_stat_arb import StatArbStrategy
from kriptty.strategies.sub3_copy_guardian import CopyGuardianStrategy
from kriptty.strategies.sub4_scalping import ScalpingStrategy, detect_pattern
from kriptty.strategies.sub5_macro_short import MacroShortStrategy
from kriptty.strategies.sub6_funding_arb import FundingArbStrategy, annualized
from kriptty.strategies.sub7_grid import GridStrategy, build_levels
from kriptty.strategies.sub8_dca import SmartDCAStrategy, rsi_multiplier
from kriptty.strategies.sub9_collar import CollarStrategy

from .conftest import FakeMacro, make_candles

BTC = "BTC/USDT:USDT"


# ── SUB1 ────────────────────────────────────────────────────────────────
class FixedAnalyzer:
    def __init__(self, value):
        self.value = value

    def score(self, text):
        return self.value


class FakeCollector:
    def __init__(self, n):
        self.n = n

    async def collect(self, asset, lookback_hours=6):
        now = datetime.now(UTC)
        return [Article(f"{asset} news {i}", now - timedelta(hours=i), "rss", 1.0) for i in range(self.n)]


async def test_sub1_opens_long_with_atr_stops(ctx):
    strat = NewsSentimentStrategy(ctx, collector=FakeCollector(5), analyzer=FixedAnalyzer(0.8))
    strat.ASSETS = ["BTC"]
    client = strat.client
    client.prices[BTC] = 100.0
    candles = make_candles(60, 100.0, vol=0.01, tf="4h")
    client.candles[(BTC, "4h")] = candles
    await strat.run_cycle()
    (pos,) = await client.positions()
    a = last(atr(candles, 14))
    assert pos.side == "long"
    assert pos.stop_loss == pytest.approx(100 - 1.5 * a, rel=1e-3)
    assert pos.take_profit == pytest.approx(100 + 2.5 * a, rel=1e-3)  # 2.5·ATR, no 3.75·ATR


async def test_sub1_holds_without_enough_news(ctx):
    strat = NewsSentimentStrategy(ctx, collector=FakeCollector(2), analyzer=FixedAnalyzer(0.9))
    strat.ASSETS = ["BTC"]
    await strat.run_cycle()
    assert await strat.client.positions() == []


def test_sub1_time_decay_weights_recent_news_more(ctx):
    strat = NewsSentimentStrategy(ctx, collector=FakeCollector(0), analyzer=None)
    now = datetime.now(UTC)

    class ByTitle:
        def score(self, text):
            return 1.0 if "good" in text else 0.0

    strat._analyzer = ByTitle()
    arts = [Article("good", now, "a", 1.0), Article("bad", now - timedelta(hours=6), "a", 1.0)]
    assert strat.weighted_score(arts, now) == pytest.approx(0.8, abs=0.01)  # pesos 1 y 0.25


# ── SUB2 ────────────────────────────────────────────────────────────────
def test_sub2_lag_and_correlation():
    assert StatArbStrategy.lag_score(10, 2, 1, 0) == pytest.approx(0.7 * 8 + 0.3 * 1)
    base = make_candles(31, 100, vol=0.03, tf="1d", seed=7)["close"]
    basket = pd.DataFrame({"A": base, "B": base * 1.01})
    assert StatArbStrategy.correlation(base * 0.5, basket) > 0.99


# ── SUB3 ────────────────────────────────────────────────────────────────
def test_sub3_keeps_most_conservative_stop():
    assert CopyGuardianStrategy.conservative_sl("long", 100, 90, 2) == 97  # ATR más cerca
    assert CopyGuardianStrategy.conservative_sl("long", 100, 98, 2) == 98  # trader más cerca
    assert CopyGuardianStrategy.conservative_sl("short", 100, None, 2) == 103


# ── SUB4 ────────────────────────────────────────────────────────────────
def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])


def test_sub4_patterns():
    hammer = _df([[10, 10.5, 9.5, 10, 1], [10, 10.5, 9.5, 10, 1], [10, 10.03, 9.0, 10.02, 1]])
    assert detect_pattern(hammer) == "bull"
    engulf_bear = _df([[10, 10.5, 9.5, 10, 1], [10, 10.6, 9.9, 10.4, 1], [10.5, 10.6, 9.8, 9.9, 1]])
    assert detect_pattern(engulf_bear) == "bear"


def test_sub4_fee_filter_blocks_low_volatility(ctx):
    strat = ScalpingStrategy(ctx)
    calm = make_candles(100, 100_000, vol=0.0001, tf="1m")
    side, _, reason = strat.evaluate(calm, make_candles(60, 100_000, tf="5m"))
    assert side is None and "comisiones" in reason


# ── SUB5 ────────────────────────────────────────────────────────────────
def test_sub5_fails_closed_without_history():
    ok, why = MacroShortStrategy.technical_confirmation(make_candles(100, 100, tf="1d"), 100)
    assert not ok and "insuficientes" in why


async def test_sub5_shorts_with_tp_ladder_in_bear_macro(ctx):
    ctx.macro = FakeMacro(score=-4)
    strat = MacroShortStrategy(ctx)
    strat.ASSETS = ["BTC"]
    client = strat.client
    daily = make_candles(260, 100, drift=-0.002, vol=0.01, tf="1d", seed=3)
    daily.iloc[-3:, daily.columns.get_loc("open")] = daily["close"].iloc[-3:] * 1.01  # velas bajistas
    client.candles[(BTC, "1d")] = daily
    client.prices[BTC] = float(daily["close"].iloc[-1])
    strat.technical_confirmation = staticmethod(lambda d, p: (True, "forzado"))
    await strat.run_cycle()
    (pos,) = await client.positions()
    assert pos.side == "short" and pos.stop_loss > client.prices[BTC]
    assert len(client.limits) == 3 and all(o["reduce_only"] for o in client.limits)


async def test_sub5_does_nothing_with_invalid_macro(ctx):
    ctx.macro = FakeMacro(score=-8, coverage=0.3)
    strat = MacroShortStrategy(ctx)
    await strat.run_cycle()
    assert await strat.client.positions() == []


# ── SUB6 ────────────────────────────────────────────────────────────────
def test_sub6_apy_uses_real_interval(ctx):
    assert annualized(0.0001, 8) == pytest.approx(0.1095)
    assert annualized(0.0001, 1) == pytest.approx(0.876)
    strat = FundingArbStrategy(ctx)
    assert not strat.has_edge(0.0001, 8)  # ~11% APY no paga las comisiones de 2 patas
    assert strat.has_edge(0.0005, 8)


async def test_sub6_opens_pair_and_unwinds_spot_when_perp_leg_disappears(ctx):
    strat = FundingArbStrategy(ctx)
    client = strat.client
    client.prices.update({"ETH/USDT:USDT": 3000.0, "ETH/USDT": 3000.0})
    client.funding["ETH/USDT:USDT"] = {"rate": 0.0006, "interval_hours": 8.0}
    await strat.scan_and_open()
    (pos,) = await client.positions()
    assert pos.side == "short" and client.coins["ETH"] == pytest.approx(pos.amount)
    assert pos.stop_loss == pytest.approx(3300)  # SL de emergencia +10%, no ±3%
    client.book.clear()  # simula que saltó el SL de la pata perp
    await strat.manage_pairs()
    assert client.coins["ETH"] == pytest.approx(0)
    assert strat.get_state("pairs") == {}


# ── SUB7 ────────────────────────────────────────────────────────────────
def test_sub7_levels_are_clamped():
    levels, step = build_levels(90, 110, cell=0.1)
    assert len(levels) == 21 and step == pytest.approx(1.0)
    levels, _ = build_levels(90, 110, cell=50)
    assert len(levels) == 5


async def test_sub7_builds_grid_with_stops_and_places_sell_after_fill(ctx):
    strat = GridStrategy(ctx)
    client = strat.client
    h4 = make_candles(60, 100, vol=0.01, tf="4h", seed=11)
    client.candles[(BTC, "4h")] = h4
    client.candles[(BTC, "1d")] = make_candles(30, 100, vol=0.02, tf="1d", seed=12)
    client.prices[BTC] = float(h4["close"].iloc[-20:].mean())
    await strat.run_cycle()
    grid = strat.get_state("grid")
    assert grid and client.limits and all(o["sl"] == pytest.approx(grid["sl"], abs=1e-3) for o in client.limits)
    top_buy = max(o["price"] for o in client.limits)
    client.prices[BTC] = top_buy - 0.01  # se ejecuta la compra más alta
    await strat.run_cycle()
    sells = [o for o in client.limits if o["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["reduce_only"]
    assert sells[0]["price"] == pytest.approx(top_buy + grid["step"], abs=1e-3)


# ── SUB8 ────────────────────────────────────────────────────────────────
def test_sub8_rsi_multiplier():
    assert [rsi_multiplier(r) for r in (65, 50, 35, 20)] == [0, 1, 1.5, 2]


async def test_sub8_buys_and_protects_whole_holding(ctx):
    strat = SmartDCAStrategy(ctx)
    strat.ALLOCATION = {"BTC": 1.0}
    client = strat.client
    client.prices["BTC/USDT"] = 100.0
    weekly = make_candles(260, 5, drift=0.002, vol=0.01, tf="1w")  # EMA200 semanal muy por debajo
    client.candles[("BTC/USDT", "1w")] = weekly
    daily = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "volume": 1.0,
                          "close": 100.0 + np.arange(120) % 2}, index=make_candles(120, 100, tf="1d").index)
    client.candles[("BTC/USDT", "1d")] = daily  # RSI ~50 → multiplicador 1
    await strat.run_cycle()
    st = strat.get_state("BTC")
    assert st["entries"] == 1 and client.coins["BTC"] == pytest.approx(st["qty"])
    assert len(client.spot_stops) == 1  # un único stop para todo el saldo
    assert client.spot_stops[0]["trigger"] == pytest.approx(st["cost"] / st["qty"] * 0.8, rel=1e-3)
    await strat.run_cycle()  # < 48H: no compra otra vez
    assert strat.get_state("BTC")["entries"] == 1


# ── SUB9 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("score,expected_ratio", [(4, 0.30), (0, 0.50), (-4, 0.70), (-6, 0.90)])
async def test_sub9_target_exposure_by_regime(ctx, score, expected_ratio):
    ctx.macro = FakeMacro(score=score)
    strat = CollarStrategy(ctx)
    strat.ASSETS = ["BTC"]
    client = strat.client
    client.candles[(BTC, "1d")] = make_candles(260, 50, drift=0.004, vol=0.005, tf="1d", seed=5)
    client.prices[BTC] = float(client.candles[(BTC, "1d")]["close"].iloc[-1])
    await strat.run_cycle()
    (pos,) = await client.positions()
    assert pos.notional == pytest.approx(10_000 * 0.8 * (1 - expected_ratio), rel=0.01)
    assert pos.stop_loss is not None and pos.stop_loss < client.prices[BTC]


async def test_sub9_circuit_breaker_closes_and_cools_down(ctx):
    strat = CollarStrategy(ctx)
    strat.ASSETS = ["BTC"]
    client = strat.client
    client.candles[(BTC, "1d")] = make_candles(260, 50, drift=0.004, vol=0.005, tf="1d", seed=5)
    price = float(client.candles[(BTC, "1d")]["close"].iloc[-1])
    client.prices[BTC] = price
    await strat.run_cycle()
    (pos,) = await client.positions()
    client.book[BTC].stop_loss = None  # que el SL no salte antes que el circuit breaker
    client.prices[BTC] = price * 0.9  # −10% sobre 50% de exposición = −5% del capital
    await strat.run_cycle()
    assert await client.positions() == []
    assert strat.get_state("state")["cooldown_until"] > 0
