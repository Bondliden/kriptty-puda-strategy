import numpy as np
import pandas as pd
import pytest

from kriptty.data.macro import build_dashboard, score_cpi, score_dollar, score_equity, score_fed
from kriptty.indicators import atr, bollinger, ema, last, rsi

from .conftest import make_candles


def test_rsi_reflects_latest_data_not_oldest():
    # 30 subidas y después 20 bajadas: el RSI debe ser bajo (el original miraba las 14 primeras velas → 100).
    s = pd.Series(np.concatenate([np.linspace(100, 130, 30), np.linspace(130, 100, 20)]))
    assert last(rsi(s, 14)) < 30


def test_rsi_all_gains_is_100():
    assert last(rsi(pd.Series(np.arange(1, 40, dtype=float)), 14)) == pytest.approx(100)


def test_atr_and_bollinger():
    df = make_candles(100, 100.0, vol=0.02)
    assert last(atr(df, 14)) > 0
    lower, mid, upper = bollinger(df["close"])
    assert last(lower) < last(mid) < last(upper)
    assert np.isnan(ema(df["close"].head(5), 10).iloc[-1])


def test_cpi_uses_yoy_not_index_level():
    # Con CPIAUCSL ~320 el diseño original puntuaba SIEMPRE -2. Con YoY 2.9% bajando → alcista.
    assert score_cpi(2.9, 3.1) == 1
    assert score_cpi(5.5, 5.0) == -2


def test_strong_bullish_is_reachable():
    assert score_equity(3.5) == 2  # antes el umbral 1.5 se evaluaba primero y devolvía 1
    assert score_dollar(-1.0) == 2
    assert score_fed(-1.0) == 2 and score_fed(0.5) == -2


def test_dashboard_scale_and_regimes():
    raw = {"fed_rate": {"current": 5, "delta_6m": 0.5}, "cpi": {"yoy": 6, "yoy_prev": 5},
           "sp500": {"current": 1, "change_1d": -3}, "nasdaq": {"current": 1, "change_1d": -3},
           "dxy": {"current": 1, "change_1d": 1}, "vix": {"current": 35, "change_1d": 10},
           "fear_greed": {"current": 85}, "yield_curve": {"current": -1}}
    d = build_dashboard(raw)
    assert d.score == pytest.approx(-10) and d.regime == "CRISIS" and d.valid


def test_dashboard_fails_closed_with_missing_data():
    d = build_dashboard({"fear_greed": {"current": 85}, "yield_curve": {"current": -1}})
    assert not d.valid and d.regime == "UNKNOWN"
