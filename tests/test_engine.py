from apscheduler.schedulers.asyncio import AsyncIOScheduler

from kriptty.strategies import REGISTRY


async def test_every_scheduled_strategy_has_a_valid_trigger(ctx):
    scheduler = AsyncIOScheduler(timezone="UTC")
    for account_id, cls in REGISTRY.items():
        strategy = cls(ctx)
        assert strategy.account_id == account_id
        if strategy.event_driven:
            continue
        sched = dict(strategy.schedule)
        scheduler.add_job(strategy.safe_run, sched.pop("trigger"), id=account_id, **sched)
    assert len(scheduler.get_jobs()) == 10  # SUB4 es event-driven


async def test_safe_run_captures_errors(ctx):
    strategy = REGISTRY["SUB7"](ctx)

    async def boom():
        raise RuntimeError("fallo de red")

    strategy.run_cycle = boom
    await strategy.safe_run()
    assert "fallo de red" in strategy.last_error and strategy.last_run
