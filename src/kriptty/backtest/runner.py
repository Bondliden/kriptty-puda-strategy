"""Ejecuta una estrategia sobre datos históricos y calcula métricas."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .. import clock
from ..config import Settings
from ..data.macro import MacroDashboard
from ..exchange.router import AccountRouter
from ..state import StateStore
from ..strategies import REGISTRY, Context, Strategy
from .client import BacktestClient
from .market import HistoricalMarket

log = logging.getLogger(__name__)

# Estrategias que dependen de datos sin histórico reproducible.
UNSUPPORTED = {
    "SUB1": "no hay histórico de noticias reproducible",
    "SUB3": "depende del copy trading nativo de Bitget",
}

# Reparto inicial del capital entre futuros y spot.
CAPITAL_SPLIT = {"SUB6": (1 / 3, 2 / 3), "SUB8": (0.0, 1.0)}

DEFAULT_SYMBOLS = {
    "SUB2": [f"{a}/USDT:USDT" for a in ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC",
                                         "BCH", "TRX", "NEAR", "APT", "ARB", "OP", "SUI", "INJ", "AAVE", "UNI")],
    "SUB4": ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"],
    "SUB5": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
    "SUB6": [f"{a}/USDT{s}" for a in ("BTC", "ETH", "SOL", "XRP", "DOGE", "LINK") for s in (":USDT", "")],
    "SUB7": ["BTC/USDT:USDT"],
    "SUB8": ["BTC/USDT", "ETH/USDT"],
    "SUB9": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
    "SUB10": sorted({f"{a}/USDT:USDT" for pair in (("BTC", "ETH"), ("ETH", "SOL"), ("SOL", "AVAX"), ("LINK", "DOT"))
                     for a in pair}),
    "SUB11": ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"],
}


class FixedMacro:
    """El histórico macro no se reproduce: régimen fijo elegido por el usuario."""

    def __init__(self, score: float):
        self.dashboard = MacroDashboard(score=score, coverage=1.0)

    async def get(self) -> MacroDashboard:
        return self.dashboard


@dataclass
class BacktestResult:
    account_id: str
    start: datetime
    end: datetime
    initial_equity: float
    equity: pd.Series
    trades: list = field(default_factory=list)
    fees: float = 0.0
    funding: float = 0.0
    orders: int = 0

    @property
    def metrics(self) -> dict:
        eq = self.equity
        final = float(eq.iloc[-1])
        years = max((self.end - self.start).total_seconds() / (365.25 * 86_400), 1e-9)
        drawdown = float((eq / eq.cummax() - 1).min())
        rets = eq.pct_change().dropna()
        bars_per_year = len(eq) / years
        sharpe = float(rets.mean() / rets.std() * math.sqrt(bars_per_year)) if rets.std() > 0 else 0.0
        pnls = [t.pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        return {
            "total_return_pct": (final / self.initial_equity - 1) * 100,
            "cagr_pct": ((final / self.initial_equity) ** (1 / years) - 1) * 100 if final > 0 else -100.0,
            "max_drawdown_pct": drawdown * 100,
            "sharpe": sharpe,
            "closed_trades": len(pnls),
            "win_rate_pct": len(wins) / len(pnls) * 100 if pnls else 0.0,
            "profit_factor": sum(wins) / abs(sum(losses)) if losses and sum(losses) else float("inf") if wins else 0.0,
            "orders_sent": self.orders,
            "fees_usdt": self.fees,
            "funding_paid_usdt": self.funding,
            "final_equity": final,
        }


def fit_to_symbols(strategy: Strategy, symbols: list[str]) -> None:
    """Limita los activos de la estrategia a los que tienen datos en el backtest."""
    bases = {s.split("/")[0] for s in symbols}
    if hasattr(strategy, "PAIRS"):
        strategy.PAIRS = [p for p in strategy.PAIRS if set(p) <= bases]
    if hasattr(strategy, "ASSETS"):
        strategy.ASSETS = [a for a in strategy.ASSETS if a in bases]
    if hasattr(strategy, "ASSET") and strategy.ASSET not in bases and bases:
        strategy.ASSET = sorted(bases)[0]
    if hasattr(strategy, "ALLOCATION"):
        alloc = {a: w for a, w in strategy.ALLOCATION.items() if a in bases}
        total = sum(alloc.values())
        strategy.ALLOCATION = {a: w / total for a, w in alloc.items()} if total else {}


def build_trigger(strategy: Strategy, start: datetime, base_s: int):
    if strategy.event_driven:
        return IntervalTrigger(seconds=base_s, start_date=start, timezone=UTC)
    sched = dict(strategy.schedule)
    kind = sched.pop("trigger")
    if kind == "cron":
        return CronTrigger(timezone=UTC, **sched)
    return IntervalTrigger(start_date=start, timezone=UTC, **sched)


async def run_backtest(account_id: str, market: HistoricalMarket, start: datetime, end: datetime, *,
                       equity: float = 10_000.0, macro_score: float = 0.0, fee_rate: float = 0.0006,
                       slippage_bps: float = 2.0, maker_fee: float = 0.0002,
                       overrides: dict | None = None, macro=None,
                       settings_overrides: dict | None = None) -> BacktestResult:
    account_id = account_id.upper()
    if account_id in UNSUPPORTED:
        raise ValueError(f"{account_id} no es backtesteable: {UNSUPPORTED[account_id]}")
    settings = Settings(trading_mode="dry_run", state_path=":memory:", dry_run_equity=equity,
                        **(settings_overrides or {}))
    state = StateStore(":memory:")
    router = AccountRouter(settings, state)
    client = BacktestClient(account_id, settings, market, fee_rate, slippage_bps, maker_fee)
    swap_frac, spot_frac = CAPITAL_SPLIT.get(account_id, (1.0, 0.0))
    client.usdt = {"swap": equity * swap_frac, "spot": equity * spot_frac}
    router.client = lambda _acc: client  # type: ignore[method-assign]
    ctx = Context(settings=settings, router=router, state=state, macro=macro or FixedMacro(macro_score))
    strategy = REGISTRY[account_id](ctx)
    fit_to_symbols(strategy, market.symbols)
    for key, value in (overrides or {}).items():
        setattr(strategy, key, value)

    trigger = build_trigger(strategy, start, market.base_s)
    first_t = math.ceil(start.timestamp() / market.base_s) * market.base_s
    end_t = end.timestamp()
    next_fire = trigger.get_next_fire_time(None, start)
    curve: dict[pd.Timestamp, float] = {}
    t = first_t
    try:
        while t <= end_t:
            clock.set_time(t)
            now = datetime.fromtimestamp(t, UTC)
            client.process_bar(t)
            if next_fire is not None and next_fire <= now:
                await strategy.safe_run()
                while next_fire is not None and next_fire <= now:
                    next_fire = trigger.get_next_fire_time(next_fire, now)
            curve[pd.Timestamp(now)] = client.total_equity(t)
            t += market.base_s
    finally:
        clock.set_time(None)
    orders = sum(1 for r in state.recent_journal(1_000_000) if r["status"] == "sent" and r["side"] in ("buy", "sell"))
    return BacktestResult(account_id, start, end, equity, pd.Series(curve, dtype=float), client.trades,
                          client.fees_paid, client.funding_paid, orders)


def summarize(result: BacktestResult) -> str:
    m = result.metrics
    lines = [f"Backtest {result.account_id}: {result.start:%Y-%m-%d} → {result.end:%Y-%m-%d}",
             f"  Retorno total     {m['total_return_pct']:+.2f}%   (CAGR {m['cagr_pct']:+.2f}%)",
             f"  Máx. drawdown     {m['max_drawdown_pct']:.2f}%",
             f"  Sharpe            {m['sharpe']:.2f}",
             f"  Operaciones       {m['closed_trades']} cerradas · {m['win_rate_pct']:.1f}% ganadoras · "
             f"PF {m['profit_factor']:.2f}",
             f"  Órdenes enviadas  {m['orders_sent']}",
             f"  Comisiones        {m['fees_usdt']:.2f} USDT · funding pagado {m['funding_paid_usdt']:+.2f} USDT",
             f"  Equity final      {m['final_equity']:.2f} USDT (inicial {result.initial_equity:.2f})"]
    return "\n".join(lines)


def buy_and_hold(market: HistoricalMarket, symbol: str, start: datetime, end: datetime) -> float:
    """Referencia: retorno % de comprar y mantener en el mismo periodo."""
    p0 = market.price(symbol, start.timestamp())
    p1 = market.price(symbol, end.timestamp())
    return float(np.round((p1 / p0 - 1) * 100, 2))
