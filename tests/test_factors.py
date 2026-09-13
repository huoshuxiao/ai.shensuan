"""因子逻辑单元测试：验证已知输入 -> 期望输出方向"""
import numpy as np
import pandas as pd
import pytest

from src.core.factors.base import BaseFactor, FactorResult
from src.core.factors.downtrend_guard import DowntrendGuardFactor
from src.core.factors.oversold import OversoldFactor
from src.core.factors.position import PositionFactor
from src.core.factors.sentiment import SentimentFactor
from src.core.factors.timing import TimingFactor
from tests.conftest import make_position


# ---------- RSI 指标正确性 ----------
def test_rsi_calculation_correctness():
    import pandas as pd
    # 纯上涨序列 -> RSI 接近 100
    up = pd.Series(list(range(1, 21)), dtype=float)
    rsi = BaseFactor._rsi(up, 6)
    assert rsi.iloc[-1] > 90
    # 纯下跌序列 -> RSI 接近 0
    down = pd.Series(list(range(20, 0, -1)), dtype=float)
    rsi = BaseFactor._rsi(down, 6)
    assert rsi.iloc[-1] < 10


# ---------- 超跌因子：下跌行情得分应显著高于上涨行情 ----------
def test_oversold_factor_downtrend_scores_high(downtrend_daily, uptrend_daily):
    factor = OversoldFactor()
    data_down = {"daily": downtrend_daily}
    data_up = {"daily": uptrend_daily}
    score_down = factor.calculate(data_down).score
    score_up = factor.calculate(data_up).score
    assert score_down > 60, f"超跌行情应高分，实际 {score_down}"
    assert score_up < 40, f"追高行情应低分，实际 {score_up}"
    assert score_down > score_up


def test_oversold_factor_insufficient_data_neutral():
    import pandas as pd
    factor = OversoldFactor()
    result = factor.calculate({"daily": pd.DataFrame({"close": [1, 2]})})
    assert result.available is False
    assert result.score == 50


# ---------- 情绪因子：别人贪婪时恐惧（放量上涨低分），恐惧时贪婪（缩量下跌高分） ----------
def test_sentiment_factor_greed_scores_low(uptrend_daily):
    """放量上涨 = 贪婪 -> 低分"""
    factor = SentimentFactor()
    result = factor.calculate({"daily": uptrend_daily})
    assert result.score < 50


def test_sentiment_factor_fear_scores_high(downtrend_daily):
    """缩量下跌 = 恐惧 -> 高分"""
    factor = SentimentFactor()
    result = factor.calculate({"daily": downtrend_daily})
    assert result.score > 50


# ---------- 择时因子：布林带下轨/超卖 高分 ----------
def test_timing_factor_downtrend_scores_higher(downtrend_daily, uptrend_daily):
    factor = TimingFactor()
    down = factor.calculate({"daily": downtrend_daily})
    up = factor.calculate({"daily": uptrend_daily})
    assert down.score > up.score


def test_timing_factor_insufficient_data_neutral():
    import pandas as pd
    factor = TimingFactor()
    result = factor.calculate({"daily": pd.DataFrame({"close": [1] * 10})})
    assert result.available is False


# ---------- 仓位因子：戒贪（浮盈过高 -> 低分） ----------
def test_position_factor_no_position_neutral():
    factor = PositionFactor()
    result = factor.calculate({"daily": None}, context={"position": None})
    assert result.score == 50


def test_position_factor_take_profit_low_score():
    """浮盈触发止盈线 -> 低分（戒贪，应高抛）"""
    factor = PositionFactor()
    position = make_position(buy_price=1.0)
    context = {"position": position, "realtime": {"price": 1.06}}  # +6% 超止盈线
    result = factor.calculate({"daily": None}, context=context)
    assert result.score < 50


def test_position_factor_stop_loss_low_score():
    """浮亏触发止损线 -> 低分（防风险）"""
    factor = PositionFactor()
    position = make_position(buy_price=1.0)
    context = {"position": position, "realtime": {"price": 0.96}}  # -4% 超止损线
    result = factor.calculate({"daily": None}, context=context)
    assert result.score < 50


def test_position_factor_small_profit_ok():
    factor = PositionFactor()
    position = make_position(buy_price=1.0)
    context = {"position": position, "realtime": {"price": 1.01}}  # +1% 正常
    result = factor.calculate({"daily": None}, context=context)
    assert result.score >= 50


# ---------- 防越低越买因子：下跌趋势低分（防接飞刀），上涨趋势加分 ----------
def make_trend_daily(n: int = 80, direction: str = "down") -> pd.DataFrame:
    """构造明确趋势行情：down = 持续下跌；up = 持续上涨"""
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    if direction == "down":
        close = 3.0 - np.linspace(0, 0.9, n)     # 3.0 -> 2.1 单边下跌
    else:
        close = 1.0 + np.linspace(0, 0.9, n)     # 1.0 -> 1.9 单边上涨
    close = pd.Series(close)
    return pd.DataFrame({
        "date": dates,
        "open": close * 0.995,
        "high": close * 1.005,
        "low": close * 0.985,
        "close": close,
        "volume": np.full(n, 2e6),
    })


def test_downtrend_guard_bearish_scores_low():
    """单边下跌（空头排列+MA60下行+新低）-> 低分，防止越跌越买"""
    factor = DowntrendGuardFactor()
    result = factor.calculate({"daily": make_trend_daily(direction="down")})
    assert result.score <= 30, f"下跌趋势应重扣分，实际 {result.score}"
    assert result.detail["bearish_align"] is True
    assert result.detail["ma60_falling"] is True
    assert result.detail["new_low_60d"] is True


def test_downtrend_guard_bullish_scores_high():
    """单边上涨（多头排列+MA60上行）-> 高分，趋势良好可安心低吸"""
    factor = DowntrendGuardFactor()
    result = factor.calculate({"daily": make_trend_daily(direction="up")})
    assert result.score >= 60, f"上涨趋势应加分，实际 {result.score}"


def test_downtrend_guard_bearish_vs_bullish():
    """下跌趋势得分应显著低于上涨趋势"""
    factor = DowntrendGuardFactor()
    down = factor.calculate({"daily": make_trend_daily(direction="down")}).score
    up = factor.calculate({"daily": make_trend_daily(direction="up")}).score
    assert down < up


def test_downtrend_guard_insufficient_data_neutral():
    """数据不足 65 根时 unavailable 中性"""
    factor = DowntrendGuardFactor()
    result = factor.calculate({"daily": pd.DataFrame({"close": range(10)})})
    assert result.available is False
    assert result.score == 50


def test_downtrend_guard_sideways_neutral():
    """横盘行情（无明确趋势）-> 中性附近"""
    factor = DowntrendGuardFactor()
    n = 80
    close = pd.Series(np.full(n, 1.0)) + pd.Series(np.random.default_rng(3).normal(0, 0.001, n))
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="B"),
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 2e6),
    })
    result = factor.calculate({"daily": df})
    # 横盘：非空头排列（MA20≈MA60），无新低 → 50 附近
    assert 30 <= result.score <= 65, f"横盘应中性附近，实际 {result.score}"
