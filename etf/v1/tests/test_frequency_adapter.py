# -*- coding: utf-8 -*-
"""频率适配器：bar ↔ 天 ↔ 年化的统一换算口径。

年化夏普 SR_ann = SR_bar · sqrt(bars_per_year)；
年化收益 R_ann = (1 + R_total)^(bars_per_year / n_bars) - 1。
"""

import numpy as np
import pandas as pd
import pytest

from config import FREQ_MAP
from frequency_adapter import get_adapter


@pytest.mark.parametrize("freq,bpd,bpy", [
    ("daily", 1, 252), ("1min", 240, 240 * 252), ("5min", 48, 48 * 252),
    ("15min", 16, 16 * 252), ("30min", 8, 8 * 252), ("60min", 4, 4 * 252),
])
def test_bars_per_year_equals_bars_per_day_times_trading_days(freq, bpd, bpy):
    a = get_adapter(freq)
    assert (a.bars_per_day, a.bars_per_year) == (bpd, bpy)
    assert a.bars_per_year == bpd * 252 == FREQ_MAP[freq]["bars_per_year"]


def test_is_intraday_flag():
    assert get_adapter("daily").is_intraday is False
    assert get_adapter("5min").is_intraday is True


def test_annualize_sharpe_scales_with_sqrt_of_bars():
    assert get_adapter("daily").annualize_sharpe(0.1) == pytest.approx(
        0.1 * np.sqrt(252))
    assert get_adapter("1min").annualize_sharpe(0.1) == pytest.approx(
        0.1 * np.sqrt(240 * 252))


def test_annualize_return_known_answer():
    a = get_adapter("daily")
    # 两年（504 根日线）翻倍 → 年化 2^(1/2)-1 ≈ 41.42%
    assert a.annualize_return(1.0, 504) == pytest.approx(2 ** 0.5 - 1,
                                                        rel=1e-9)
    # 半年赚 10% → 年化 (1.1)^2 - 1
    assert a.annualize_return(0.1, 126) == pytest.approx(0.21, rel=1e-9)
    assert a.annualize_return(0.1, 0) == 0.0


def test_sharpe_from_returns_matches_manual_annualization():
    r = np.array([0.01, -0.02, 0.03, 0.00, 0.02, -0.01, 0.04, 0.01])
    a = get_adapter("daily")
    # 约定：波动率用 pandas 的样本标准差（ddof=1）
    want = (r.mean() * 252) / (pd.Series(r).std() * np.sqrt(252))
    assert a.sharpe_from_returns(pd.Series(r)) == pytest.approx(want)


def test_zero_volatility_series_is_not_divided_by():
    a = get_adapter("daily")
    assert a.sharpe_from_returns(pd.Series(np.full(10, 0.001))) == 0.0


def test_day_bar_conversion():
    assert get_adapter("daily").days_to_bars(20) == 20
    assert get_adapter("1min").days_to_bars(20) == 4800
    assert get_adapter("1min").bars_to_days(480) == 2.0
    assert get_adapter("1min").bars_between_trades(0.5) == 120


def test_default_adapter_follows_env_freq():
    """ETF_FREQ 决定默认适配器：run_daily/minute_backtest 靠这一条切换。"""
    assert get_adapter().freq in FREQ_MAP
    assert get_adapter().bars_per_year == FREQ_MAP[get_adapter().freq][
        "bars_per_year"]
