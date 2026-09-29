import pytest

from kriptty.risk.guard import OrderRejected, validate_order
from kriptty.risk.models import OrderRequest
from kriptty.risk.sizing import risk_based_amount

PERP = "BTC/USDT:USDT"


def test_rejects_order_without_stop_loss():
    with pytest.raises(OrderRejected, match="OBLIGATORIO"):
        validate_order(OrderRequest(PERP, "buy", 0.01), 100_000)


@pytest.mark.parametrize("side,sl", [("buy", 101_000), ("sell", 99_000)])
def test_rejects_stop_on_wrong_side(side, sl):
    with pytest.raises(OrderRejected):
        validate_order(OrderRequest(PERP, side, 0.01, stop_loss=sl), 100_000)


def test_rejects_stop_too_far():
    with pytest.raises(OrderRejected, match="máximo"):
        validate_order(OrderRequest(PERP, "buy", 0.01, stop_loss=70_000), 100_000)


def test_rejects_take_profit_on_wrong_side():
    with pytest.raises(OrderRejected, match="TP"):
        validate_order(OrderRequest(PERP, "buy", 0.01, stop_loss=98_000, take_profit=99_000), 100_000)


def test_accepts_valid_orders_and_exemptions():
    validate_order(OrderRequest(PERP, "buy", 0.01, stop_loss=98_000, take_profit=103_000), 100_000)
    validate_order(OrderRequest(PERP, "sell", 0.01, stop_loss=102_000), 100_000)
    validate_order(OrderRequest(PERP, "sell", 0.01, reduce_only=True), 100_000)
    validate_order(OrderRequest("BTC/USDT", "sell", 0.01), 100_000)  # vender spot reduce riesgo
    validate_order(OrderRequest("BTC/USDT", "buy", 0.01, sl_exempt_reason="hedge"), 100_000)


def test_risk_based_amount_loses_exactly_risk_at_stop():
    amount = risk_based_amount(10_000, 0.01, entry=100, stop=95)
    assert amount * 5 == pytest.approx(100)


def test_risk_based_amount_caps_notional():
    amount = risk_based_amount(10_000, 0.5, entry=100, stop=99.9, max_notional=30_000)
    assert amount * 100 == pytest.approx(30_000)


def test_risk_based_amount_invalid_inputs():
    assert risk_based_amount(10_000, 0.01, 100, 100) == 0
    assert risk_based_amount(0, 0.01, 100, 95) == 0


async def test_router_rejects_and_journals(ctx):
    with pytest.raises(OrderRejected):
        await ctx.router.execute("SUB1", OrderRequest(PERP, "buy", 0.01, tag="t"))
    entry = ctx.state.recent_journal(1)[0]
    assert entry["status"] == "rejected" and entry["account"] == "SUB1"


async def test_router_executes_in_dry_run(ctx):
    ctx.router.client("SUB1").prices[PERP] = 100.0
    res = await ctx.router.execute("SUB1", OrderRequest(PERP, "buy", 1.0, stop_loss=95, take_profit=110))
    assert res["status"] == "closed"
    (pos,) = await ctx.router.client("SUB1").positions()
    assert pos.side == "long" and pos.stop_loss == 95


async def test_paper_stop_loss_triggers(ctx):
    client = ctx.router.client("SUB1")
    client.prices[PERP] = 100.0
    await ctx.router.execute("SUB1", OrderRequest(PERP, "buy", 10.0, stop_loss=95))
    client.prices[PERP] = 94.0
    assert await client.positions() == []
    assert client.usdt["swap"] == pytest.approx(10_000 - 50 - 10 * 100 * 0.0006 - 10 * 95 * 0.0006)


async def test_daily_kill_switch_blocks_new_positions(ctx):
    client = ctx.router.client("SUB2")
    client.prices[PERP] = 100.0
    assert await ctx.router.check_daily_loss("SUB2")
    client.usdt["swap"] = 9_000  # -10% en el día
    assert not await ctx.router.check_daily_loss("SUB2")
    with pytest.raises(OrderRejected, match="kill-switch"):
        await ctx.router.execute("SUB2", OrderRequest(PERP, "buy", 1.0, stop_loss=95))


async def test_spot_buy_gets_protective_stop(ctx):
    client = ctx.router.client("SUB8")
    client.prices["BTC/USDT"] = 100.0
    res = await ctx.router.execute("SUB8", OrderRequest("BTC/USDT", "buy", 1.0, stop_loss=80))
    assert res["stop_order"]["id"]
    assert client.spot_stops[0]["trigger"] == 80
