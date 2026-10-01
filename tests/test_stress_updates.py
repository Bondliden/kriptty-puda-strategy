"""Cambios de octubre de 2026: corte por drawdown, filtro de perpetuos no cripto,
filtro de tendencia de SUB7, comprobación previa de SUB6 y generador de estrés."""
import ccxt
import numpy as np
import pandas as pd
import pytest

from kriptty import clock
from kriptty.backtest import stress
from kriptty.exchange.client import is_crypto_market
from kriptty.risk.guard import OrderRejected
from kriptty.risk.models import OrderRequest
from kriptty.strategies.sub6_funding_arb import FundingArbStrategy

PERP = "BTC/USDT:USDT"


@pytest.fixture(autouse=True)
def reset_clock():
    yield
    clock.set_time(None)


async def test_drawdown_breaker_pauses_and_resumes(ctx):
    clock.set_time(1_800_000_000)
    client = ctx.router.client("SUB11")
    client.prices[PERP] = 100.0
    client.usdt = {"swap": 10_000.0, "spot": 0.0}
    assert await ctx.router.check_drawdown("SUB11")
    client.usdt["swap"] = 7_000  # −30% desde el máximo de 10.000
    assert not await ctx.router.check_drawdown("SUB11")
    clock.set_time(1_800_000_000 + 86_400)  # día siguiente: el kill-switch diario ya no aplica…
    with pytest.raises(OrderRejected, match="drawdown"):  # …pero la pausa por drawdown sí
        await ctx.router.execute("SUB11", OrderRequest(PERP, "buy", 1.0, stop_loss=95))
    clock.set_time(1_800_000_000 + 15 * 86_400)  # pasados 14 días: se reanuda con nuevo máximo
    assert await ctx.router.check_drawdown("SUB11")
    assert ctx.state.get("drawdown", "SUB11")["peak"] == pytest.approx(7_000)


async def test_hard_stop_does_not_reset(ctx):
    clock.set_time(1_800_000_000)
    client = ctx.router.client("SUB2")
    client.prices[PERP] = 100.0
    client.usdt = {"swap": 10_000.0, "spot": 0.0}
    assert await ctx.router.check_drawdown("SUB2")
    client.usdt["swap"] = 7_000  # −30%: pausa de 14 días
    assert not await ctx.router.check_drawdown("SUB2")
    clock.set_time(1_800_000_000 + 15 * 86_400)
    assert await ctx.router.check_drawdown("SUB2")  # reanuda (nuevo máximo de referencia 7.000)
    client.usdt["swap"] = 5_900  # −15.7% desde 7.000, pero −41% desde el máximo histórico
    assert not await ctx.router.check_drawdown("SUB2")
    clock.set_time(1_800_000_000 + 60 * 86_400)
    client.usdt["swap"] = 9_000
    assert not await ctx.router.check_drawdown("SUB2")  # sigue parada hasta revisión manual
    assert ctx.state.get("drawdown", "SUB2")["hard_stop"]


async def test_drawdown_breaker_exempts_dca(ctx):
    client = ctx.router.client("SUB8")
    client.prices["BTC/USDT"] = 100.0
    assert await ctx.router.check_drawdown("SUB8")
    client.usdt["swap"] = 1_000
    assert await ctx.router.check_drawdown("SUB8")


@pytest.mark.parametrize("symbol,info,expected", [
    ("BTC/USDT:USDT", {}, True),
    ("NVDA/USDT:USDT", {}, False),
    ("XAU/USDT:USDT", {}, False),
    ("FOO/USDT:USDT", {"isRwa": "YES"}, False),
    ("BAR/USDT:USDT", {"symbolType": "stock"}, False),
    ("SOL/USDT:USDT", {"isRwa": "NO", "symbolType": "perpetual"}, True),
])
def test_non_crypto_perps_are_excluded(symbol, info, expected):
    assert is_crypto_market(symbol, {"info": info}) is expected


def test_extra_excluded_bases():
    assert not is_crypto_market("PEPE/USDT:USDT", None, {"PEPE"})


async def test_sub6_does_not_open_when_a_wallet_is_blocked(ctx, monkeypatch):
    strategy = FundingArbStrategy(ctx)
    calls = []

    async def blocked(account_id, account="swap"):
        calls.append(account)
        return account != "swap"

    monkeypatch.setattr(ctx.router, "can_open", blocked)

    async def boom(*a, **k):  # no debe llegar a pedir tickers ni a abrir patas
        raise AssertionError("no debería escanear")

    monkeypatch.setattr(ctx.router.client("SUB6"), "tickers", boom)
    await strategy.scan_and_open()
    assert calls == ["swap"]


def test_stress_market_is_consistent():
    m = stress.generate("lateral", seed=3, assets=["BTC", "ETH", "LINK", "DOT"])
    btc = m.candles["BTC/USDT:USDT"]
    assert (btc["high"] >= btc[["open", "close"]].max(axis=1)).all()
    assert (btc["low"] <= btc[["open", "close"]].min(axis=1)).all()
    assert btc.index.freq == "h" or btc.index.to_series().diff().dropna().eq(pd.Timedelta("1h")).all()
    days = stress.SCENARIOS["lateral"].days + stress.WARMUP.days
    assert len(btc) == days * 24
    assert m.funding["BTC/USDT:USDT"].abs().max() <= 0.003
    assert "BTC/USDT" in m.candles and "DOT/USDT" not in m.candles
    # El macro observable va retrasado: no puede anticipar un crash programado.
    assert m.macro.index[0] == btc.index[0]
    r = np.log(btc["close"]).diff().dropna()
    assert 0.2 < r.std() * np.sqrt(24 * 365) < 1.5


def test_stress_is_reproducible():
    a = stress.generate("bear", seed=7, assets=["BTC"])
    b = stress.generate("bear", seed=7, assets=["BTC"])
    pd.testing.assert_frame_equal(a.candles["BTC/USDT:USDT"], b.candles["BTC/USDT:USDT"])


async def test_series_macro_reads_score_at_simulated_time():
    idx = pd.date_range("2026-01-01", periods=3, freq="1D", tz="UTC")
    macro = stress.SeriesMacro(pd.Series([1.0, -4.0, -6.0], index=idx))
    clock.set_time(pd.Timestamp("2026-01-02 12:00", tz="UTC").timestamp())
    assert (await macro.get()).regime == "BEAR"


async def test_open_position_caps_total_exposure(ctx):
    from kriptty.strategies.sub2_stat_arb import StatArbStrategy
    strategy = StatArbStrategy(ctx)  # apalancamiento 3x
    client = ctx.router.client("SUB2")
    client.usdt = {"swap": 10_000.0, "spot": 0.0}
    for sym in ("SOL/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT"):
        client.prices[sym] = 100.0
    # SL al 0.5%: por riesgo pediría 2% / 0.5% = 4× el equity; el tope total es 3×.
    await strategy.open_position("SOL/USDT:USDT", "buy", 100.0, 99.5, 110.0, 0.02)
    await strategy.open_position("AVAX/USDT:USDT", "buy", 100.0, 99.5, 110.0, 0.02)
    await strategy.open_position("LINK/USDT:USDT", "buy", 100.0, 99.5, 110.0, 0.02)
    sizes = [p.amount * p.mark_price for p in await client.positions()]
    assert sum(sizes) <= 10_000 * 3 * 1.01
    assert max(sizes) <= 10_000 * 3 / 3 * 1.01  # cada una, como mucho su parte (MAX_POSITIONS = 3)


async def test_backtest_rejects_orders_without_margin():
    from kriptty.backtest import HistoricalMarket
    from kriptty.backtest.client import BacktestClient
    from kriptty.config import Settings
    idx = pd.date_range("2026-01-05", periods=5, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6}, index=idx)
    m = HistoricalMarket({PERP: df}, "1h")
    c = BacktestClient("SUB2", Settings(state_path=":memory:"), m)
    c.usdt = {"swap": 1_000.0, "spot": 0.0}
    clock.set_time(idx[2].timestamp())
    await c.ensure_setup(PERP, 3)
    ok = await c.place(OrderRequest(PERP, "buy", 25.0, stop_loss=95))     # 2.500 nocional → 833 margen
    assert ok["status"] == "closed"
    with pytest.raises(ccxt.InsufficientFunds):  # +333 margen > equity, como en el exchange
        await c.place(OrderRequest(PERP, "buy", 10.0, stop_loss=95))
    assert c.rejected_margin == 1
