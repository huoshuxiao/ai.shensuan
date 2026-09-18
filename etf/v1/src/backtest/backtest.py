# -*- coding: utf-8 -*-
"""回测统一入口：根据 FREQ 自动选择日线或分钟线"""

from config import FREQ
from frequency_adapter import get_adapter
from backtest_daily import DailyBacktester
from backtest_minute import MinuteBacktester


def get_backtester(pool, universe=None, risk_params=None, freq=None):
    """工厂：根据频率返回对应回测器"""
    freq = freq or FREQ
    if freq == "daily":
        return DailyBacktester(pool, universe, risk_params)
    else:
        return MinuteBacktester(pool, universe, risk_params)


def backtest_with_params(pool, signals, universe, risk_params):
    """统一接口（供风控优化器调用）"""
    bt = get_backtester(pool, universe, risk_params)
    return bt.run(signals)["stats"]