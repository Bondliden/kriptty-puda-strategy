from .base import Context, Strategy
from .sub1_news_sentiment import NewsSentimentStrategy
from .sub2_stat_arb import StatArbStrategy
from .sub3_copy_guardian import CopyGuardianStrategy
from .sub4_scalping import ScalpingStrategy
from .sub5_macro_short import MacroShortStrategy
from .sub6_funding_arb import FundingArbStrategy
from .sub7_grid import GridStrategy
from .sub8_dca import SmartDCAStrategy
from .sub9_collar import CollarStrategy
from .sub10_pairs import PairsTradingStrategy
from .sub11_supertrend import SuperTrendStrategy

REGISTRY: dict[str, type[Strategy]] = {
    cls.account_id: cls
    for cls in (NewsSentimentStrategy, StatArbStrategy, CopyGuardianStrategy, ScalpingStrategy,
                MacroShortStrategy, FundingArbStrategy, GridStrategy, SmartDCAStrategy, CollarStrategy,
                PairsTradingStrategy, SuperTrendStrategy)
}

__all__ = ["Context", "Strategy", "REGISTRY"]
