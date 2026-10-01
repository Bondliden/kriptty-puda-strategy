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


async def test_hard_stop_review_restarts_after_delay(ctx):
    ctx.router.settings.hard_stop_review_days = 90
    clock.set_time(1_800_000_000)
    client = ctx.router.client("SUB2")
    client.prices[PERP] = 100.0
    client.usdt = {"swap": 10_000.0, "spot": 0.0}
    assert await ctx.router.check_drawdown("SUB2")
    client.usdt["swap"] = 5_500  # −45% desde el máximo histórico: parada dura
    assert not await ctx.router.check_drawdown("SUB2")
    clock.set_time(1_800_000_000 + 60 * 86_400)
    assert not await ctx.router.check_drawdown("SUB2")  # antes de los 90 días sigue parada
    clock.set_time(1_800_000_000 + 91 * 86_400)
    assert await ctx.router.check_drawdown("SUB2")  # revisada: vuelve con nuevo máximo
    rec = ctx.state.get("drawdown", "SUB2")
    assert not rec.get("hard_stop") and rec["hwm"] == pytest.approx(5_500) and rec["restarts"] == 1


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


async def test_margin_cap_rejects_orders_over_limit(ctx):
    ctx.settings.max_margin_pct = 0.20
    client = ctx.router.client("SUB11")
    client.usdt = {"swap": 10_000.0, "spot": 0.0}
    client.prices[PERP] = 100.0
    # 7x: 1.4× el equity de nocional = 20% de margen → permitido
    await ctx.router.execute("SUB11", OrderRequest(PERP, "buy", 140.0, stop_loss=95), leverage=7)
    with pytest.raises(OrderRejected, match="margen"):
        await ctx.router.execute("SUB11", OrderRequest(PERP, "buy", 10.0, stop_loss=95), leverage=7)
    # reducir la posición nunca se bloquea
    await ctx.router.execute("SUB11", OrderRequest(PERP, "sell", 50.0, reduce_only=True), leverage=7)


def test_leverage_overrides_scale_exposure():
    from kriptty.backtest.stress_run import leverage_overrides
    assert leverage_overrides("SUB11", 7) == {"leverage": 7, "RISK_PCT": pytest.approx(0.01 * 7 / 3)}
    assert leverage_overrides("SUB11", 7, scale=False) == {"leverage": 7}
    assert leverage_overrides("SUB6", 7) == {"leverage": 7}
    assert leverage_overrides("SUB8", 7) == {}


def test_sub5_leverage_scales_both_risk_levels():
    from kriptty.backtest.stress_run import leverage_overrides
    out = leverage_overrides("SUB5", 6)
    assert out == {"leverage": 6, "RISK_PCT": pytest.approx(0.045), "RISK_PCT_STRONG": pytest.approx(0.06)}


async def test_spot_capital_respects_max_margin(ctx):
    from kriptty.strategies.sub8_dca import SmartDCAStrategy
    ctx.settings.max_margin_pct = 0.2
    strat = SmartDCAStrategy(ctx)
    assert strat.capital_limit == 0.2
    ctx.settings.max_margin_pct = 1.0
    assert strat.capital_limit == 1.0


async def test_capital_ramp_steps_up_and_down(ctx):
    from kriptty import clock
    ctx.settings.max_margin_pct = 0.2
    ctx.settings.capital_ramp = "0.25,0.5,1"
    router, client = ctx.router, ctx.router.client("SUB9")
    try:
        clock.set_time(1_800_000_000)
        await router.update_ramp("SUB9")
        assert router.capital_limit("SUB9") == pytest.approx(0.05)  # 25% de 200k = 50k de 1 M$
        clock.set_time(1_800_000_000 + 31 * 86400)
        client.usdt["swap"] += 100
        await router.update_ramp("SUB9")  # escalón en beneficio → sube
        assert router.capital_limit("SUB9") == pytest.approx(0.10)
        client.usdt["swap"] -= 2_000  # −10% del equity total desde el inicio del escalón → baja
        await router.update_ramp("SUB9")
        assert router.capital_limit("SUB9") == pytest.approx(0.05)
    finally:
        clock.set_time(None)


def test_engine_applies_leverage_setting(ctx):
    from kriptty.engine import build_strategies
    ctx.settings.leverage = "SUB10=3, SUB5=3"
    (s10,) = build_strategies(ctx, {"SUB10"})
    assert s10.leverage == 3 and s10.CAPITAL_PER_PAIR == pytest.approx(0.25 * 3 / 2)
    (s5,) = build_strategies(ctx, {"SUB5"})
    assert s5.leverage == 3 and s5.RISK_PCT == pytest.approx(0.0225)


async def test_max_hold_closes_old_positions(ctx):
    from kriptty import clock
    from kriptty.strategies.sub11_supertrend import SuperTrendStrategy
    ctx.settings.max_hold_hours = 48
    strat = SuperTrendStrategy(ctx)
    client = strat.client
    client.prices[PERP] = 100.0
    try:
        clock.set_time(1_800_000_000)
        await ctx.router.execute("SUB11", OrderRequest(PERP, "buy", 1.0, stop_loss=95), leverage=3)
        await strat.enforce_max_hold()
        assert len(await client.positions()) == 1
        clock.set_time(1_800_000_000 + 49 * 3600)
        await strat.enforce_max_hold()
        assert await client.positions() == []
    finally:
        clock.set_time(None)


def test_account_mode_never_more_real_than_global():
    from kriptty.config import Settings
    s = Settings(trading_mode="live", confirm_live_trading="yes", account_modes="SUB4=demo, SUB1=dry_run")
    assert (s.mode_for("SUB4"), s.mode_for("sub1"), s.mode_for("SUB5")) == ("demo", "dry_run", "live")
    s = Settings(trading_mode="demo", account_modes="SUB4=live")
    assert s.mode_for("SUB4") == "demo"  # nunca más real que TRADING_MODE
    with pytest.raises(ValueError):
        Settings(account_modes="SUB4=real").mode_for("SUB4")


def test_demo_account_uses_sandbox_client(monkeypatch):
    from kriptty.config import Settings
    from kriptty.exchange.router import AccountRouter
    from kriptty.state import StateStore
    for acc in ("SUB4", "SUB5"):
        for part in ("API_KEY", "SECRET", "PASSPHRASE"):
            monkeypatch.setenv(f"BITGET_{acc}_{part}", "x")
    router = AccountRouter(Settings(trading_mode="live", confirm_live_trading="yes", account_modes="SUB4=demo",
                                    state_path=":memory:"), StateStore(":memory:"))
    assert router.client("SUB4").mode == "demo" and router.client("SUB5").mode == "live"


def test_margin_limit_per_account(ctx):
    ctx.settings.max_margin_pct = 0.2
    ctx.settings.max_margin_by_account = "SUB2=0.1"
    assert ctx.router.capital_limit("SUB2") == pytest.approx(0.1)
    assert ctx.router.capital_limit("SUB9") == pytest.approx(0.2)


async def test_graduation_flags_demo_account(ctx):
    from kriptty import clock
    ctx.settings.trading_mode = "demo"
    ctx.settings.graduation_days = 90
    router, client = ctx.router, ctx.router.client("SUB4")
    try:
        clock.set_time(1_800_000_000)
        assert not (await router.track_stage("SUB4"))["ready"]
        client.usdt["swap"] += 500  # gana un 2,5% sin drawdown
        clock.set_time(1_800_000_000 + 60 * 86400)
        assert not (await router.track_stage("SUB4"))["ready"]  # aún no han pasado 90 días
        clock.set_time(1_800_000_000 + 91 * 86400)
        assert (await router.track_stage("SUB4"))["ready"]
        (st,) = router.agents_status(["SUB4"])
        assert st["mode"] == "demo" and st["ready_to_graduate"] and st["return_pct"] == pytest.approx(2.5)
    finally:
        clock.set_time(None)


async def test_annual_loss_budget_stops_every_account(ctx):
    from kriptty import clock
    from kriptty.risk.guard import OrderRejected
    ctx.settings.enabled_strategies = "SUB5,SUB6"
    ctx.settings.annual_loss_budget_usd = 1_000
    router = ctx.router
    try:
        clock.set_time(1_800_000_000)
        assert await router.check_loss_budget()  # referencia: 2 subcuentas × 20.000 = 40.000
        router.client("SUB5").usdt["swap"] -= 600
        clock.set_time(1_800_000_000 + 600)
        assert await router.check_loss_budget()  # −600 < 1.000
        router.client("SUB6").usdt["spot"] -= 500
        clock.set_time(1_800_000_000 + 1200)
        assert not await router.check_loss_budget()  # −1.100 ≥ 1.000: se para todo
        router.client("SUB5").prices[PERP] = 100.0
        with pytest.raises(OrderRejected, match="presupuesto"):
            await router.execute("SUB5", OrderRequest(PERP, "sell", 1.0, stop_loss=105))
        assert not await router.can_open("SUB6")
        clock.set_time(1_800_000_000 + 400 * 86400)  # año siguiente: nueva referencia
        assert await router.check_loss_budget()
    finally:
        clock.set_time(None)


async def test_loss_budget_ignores_deposits(ctx):
    from kriptty import clock
    ctx.settings.enabled_strategies = "SUB5"
    ctx.settings.annual_loss_budget_usd = 1_000
    router = ctx.router
    try:
        clock.set_time(1_800_000_000)
        assert await router.check_loss_budget()
        router.client("SUB5").usdt["swap"] -= 5_000  # retirada de 5.000, no es pérdida
        router.record_deposit(-5_000)
        clock.set_time(1_800_000_000 + 600)
        assert await router.check_loss_budget()
    finally:
        clock.set_time(None)
