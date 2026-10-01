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

# Atributos que escalan la exposición al cambiar el apalancamiento (ver ``leverage_overrides``).
_EXPOSURE_ATTRS = ("RISK_PCT", "RISK_PCT_STRONG", "CAPITAL_FRACTION", "CAPITAL_PER_PAIR")


def leverage_overrides(account: str, leverage: float | None, scale: bool = True) -> dict:
    """Atributos para operar ``account`` con otro apalancamiento: fija ``leverage`` y escala en la
    misma proporción el riesgo por operación y la fracción de capital expuesta (el tamaño de las
    posiciones crece igual que el apalancamiento; el límite de capital y la rampa siguen acotándolo).
    SUB6 solo cambia el apalancamiento de la pata de futuros: su pata spot no se apalanca. SUB8 opera
    en spot y no cambia. Lo usan el motor (ajuste ``LEVERAGE``) y el test de estrés (``--leverage``)."""
    if not leverage or account == "SUB8":
        return {}
    cls = REGISTRY[account]
    factor = leverage / cls.leverage
    out: dict = {"leverage": int(leverage)}
    if scale and account != "SUB6":
        for attr in _EXPOSURE_ATTRS:
            if hasattr(cls, attr):
                out[attr] = getattr(cls, attr) * factor
    return out


__all__ = ["Context", "Strategy", "REGISTRY", "leverage_overrides"]
