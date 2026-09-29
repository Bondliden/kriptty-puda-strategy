"""Backtester: sin mirar al futuro, modelo de ejecución y ejecución de estrategias completas."""
import sys
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from kriptty import clock
from kriptty.backtest import HistoricalMarket, run_backtest
from kriptty.backtest.client import BacktestClient
from kriptty.config import Settings
from kriptty.risk.models import OrderRequest

BTC = "BTC/USDT:USDT"
START = pd.Timestamp("2026-01-05", tz="UTC")  # lunes


def frame(closes, highs=None, lows=None, opens=None, tf="1h", start=START, volume=1000.0) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    opens = np.r_[closes[0], closes[:-1]] if opens is None else np.asarray(opens, dtype=float)
    highs = np.maximum(opens, closes) * 1.001 if highs is None else np.asarray(highs, dtype=float)
    lows = np.minimum(opens, closes) * 0.999 if lows is None else np.asarray(lows, dtype=float)
    idx = pd.date_range(start, periods=len(closes), freq=tf, tz="UTC")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume}, index=idx)


def ts(s: str) -> float:
    return pd.Timestamp(s, tz="UTC").timestamp()


@pytest.fixture(autouse=True)
def reset_clock():
    yield
    clock.set_time(None)


def make_client(market, **kw) -> BacktestClient:
    c = BacktestClient("SUB11", Settings(state_path=":memory:"), market, **kw)
    c.usdt = {"swap": 10_000.0, "spot": 0.0}
    return c


# ── Acceso a datos ──────────────────────────────────────────────────────
def test_no_lookahead_and_higher_timeframes():
    m = HistoricalMarket({BTC: frame(np.arange(1, 49))}, "1h")
    t = ts("2026-01-05 09:30")
    assert m.ohlcv(BTC, "1h", t, 1).index[-1] == pd.Timestamp("2026-01-05 08:00", tz="UTC")
    assert m.price(BTC, t) == 9.0  # la vela de 08:00 cierra a las 09:00 con close=9
    h4 = m.ohlcv(BTC, "4h", t, 5)
    assert list(h4.index) == [pd.Timestamp("2026-01-05 00:00", tz="UTC"), pd.Timestamp("2026-01-05 04:00", tz="UTC")]
    assert h4["close"].iloc[-1] == 8.0 and h4["high"].iloc[-1] == pytest.approx(8 * 1.001)
    assert m.ohlcv(BTC, "1d", t, 5).empty  # el día aún no ha cerrado


# ── Modelo de ejecución ─────────────────────────────────────────────────
async def test_limit_order_fills_on_wick_with_maker_fee():
    m = HistoricalMarket({BTC: frame([100, 100, 100], lows=[99.5, 98.0, 99.5])}, "1h")
    c = make_client(m)
    clock.set_time(ts("2026-01-05 01:00"))
    await c.place(OrderRequest(BTC, "buy", 1.0, "limit", 99.0, stop_loss=90))
    c.process_bar(ts("2026-01-05 02:00"))  # low 98 toca 99
    (pos,) = c.book.values()
    assert pos.entry_price == 99.0 and pos.stop_loss == 90
    assert c.fees_paid == pytest.approx(99.0 * 0.0002)


async def test_stop_loss_wins_when_bar_touches_both():
    m = HistoricalMarket({BTC: frame([100, 100], highs=[101, 115], lows=[99, 90])}, "1h")
    c = make_client(m)
    clock.set_time(ts("2026-01-05 01:00"))
    await c.place(OrderRequest(BTC, "buy", 1.0, stop_loss=95, take_profit=110))
    c.process_bar(ts("2026-01-05 02:00"))
    assert not c.book and c.trades[-1].exit == 95


async def test_gap_through_stop_fills_at_open():
    m = HistoricalMarket({BTC: frame([100, 88], opens=[100, 90], lows=[99, 85])}, "1h")
    c = make_client(m)
    clock.set_time(ts("2026-01-05 01:00"))
    await c.place(OrderRequest(BTC, "buy", 1.0, stop_loss=95))
    c.process_bar(ts("2026-01-05 02:00"))
    assert c.trades[-1].exit == 90


async def test_short_receives_positive_funding():
    funding = pd.Series([0.001], index=[pd.Timestamp("2026-01-05 08:00", tz="UTC")])
    m = HistoricalMarket({BTC: frame([100] * 12)}, "1h", {BTC: funding}, {BTC: 8.0})
    c = make_client(m, slippage_bps=0)
    clock.set_time(ts("2026-01-05 01:00"))
    await c.place(OrderRequest(BTC, "sell", 10.0, stop_loss=110))
    before = c.usdt["swap"]
    for h in range(2, 12):
        c.process_bar(ts(f"2026-01-05 {h:02d}:00"))
    assert c.usdt["swap"] - before == pytest.approx(10 * 100 * 0.001)


# ── Estrategias completas ───────────────────────────────────────────────
async def test_backtest_rejects_unsupported_strategy():
    m = HistoricalMarket({BTC: frame([100] * 10)}, "1h")
    with pytest.raises(ValueError, match="noticias"):
        await run_backtest("SUB1", m, START.to_pydatetime(), (START + timedelta(hours=5)).to_pydatetime())


async def test_supertrend_backtest_trades_a_trend():
    n = 24 * 120
    x = np.arange(n)
    closes = 100 * np.exp(0.0006 * x + 0.03 * np.sin(x / 40))  # tendencia con retrocesos
    m = HistoricalMarket({BTC: frame(closes)}, "1h")
    start = (START + timedelta(days=30)).to_pydatetime()
    r = await run_backtest("SUB11", m, start, (START + timedelta(days=119)).to_pydatetime())
    assert r.orders > 0 and len(r.equity) > 2000
    assert r.metrics["final_equity"] > 10_000  # en una tendencia limpia debe ganar
    assert set(r.metrics) >= {"total_return_pct", "max_drawdown_pct", "sharpe", "win_rate_pct"}


async def test_pairs_backtest_trades_cointegrated_pair():
    rng = np.random.default_rng(3)
    n = 24 * 60
    log_b = np.log(50) + np.cumsum(rng.normal(0, 0.004, n))
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = 0.9 * spread[i - 1] + rng.normal(0, 0.004)
    a, b = np.exp(np.log(2) + log_b + spread), np.exp(log_b)
    m = HistoricalMarket({"BTC/USDT:USDT": frame(a), "ETH/USDT:USDT": frame(b)}, "1h")
    start = (START + timedelta(days=12)).to_pydatetime()
    r = await run_backtest("SUB10", m, start, (START + timedelta(days=59)).to_pydatetime())
    assert r.metrics["closed_trades"] >= 4  # cada par cerrado son 2 operaciones
    assert {t.side for t in r.trades} == {"long", "short"}


async def test_funding_arb_backtest_collects_funding():
    closes = 100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.003, 24 * 20)))
    funding = pd.Series(0.0005, index=pd.date_range(START, periods=60, freq="8h", tz="UTC"))
    m = HistoricalMarket({BTC: frame(closes, volume=1e6), "BTC/USDT": frame(closes, volume=1e6)}, "1h",
                         {BTC: funding}, {BTC: 8.0})
    r = await run_backtest("SUB6", m, (START + timedelta(days=2)).to_pydatetime(),
                           (START + timedelta(days=19)).to_pydatetime())
    assert r.funding < 0  # pagado negativo = cobrado por el short
    assert r.metrics["final_equity"] > 10_000 * 0.99


def test_cli_offline(tmp_path, monkeypatch, capsys):
    from kriptty.backtest import cli

    x = np.arange(24 * 90)
    df = frame(100 * np.exp(0.0006 * x + 0.03 * np.sin(x / 40)), start=pd.Timestamp("2026-01-01", tz="UTC"))
    data_dir = tmp_path / "hist"
    data_dir.mkdir()
    df.to_csv(data_dir / "BTC-USDT_USDT__1h.csv")
    monkeypatch.setattr(sys, "argv", ["kriptty-backtest", "--strategy", "SUB11", "--start", "2026-02-15",
                                      "--end", "2026-03-30", "--symbols", BTC, "--offline",
                                      "--data-dir", str(data_dir), "--out", str(tmp_path / "out")])
    cli()
    out = capsys.readouterr().out
    assert "Backtest SUB11" in out and "Referencia" in out
    assert list((tmp_path / "out").glob("SUB11_*_metrics.json"))
