# -*- coding: utf-8 -*-
"""日线风控：日止损、回撤熔断与"回撤越深仓位越轻"的缩仓公式。"""

import pandas as pd
import pytest

from backtest_daily import DailyRiskController
from config import RISK_CONTROL


def _day(i):
    return pd.bdate_range("2024-01-01", periods=i + 1)[i]


def test_position_ratio_formula():
    """ratio = single_position_max · (1 + dd·5)，限幅 [0.2, 上限]。
    dd 为当前净值相对历史峰值的回撤（负值）。"""
    r = DailyRiskController()
    assert r.adjust_position_ratio(100.0) == pytest.approx(
        RISK_CONTROL["single_position_max"]), "无回撤时按上限开仓"
    r.peak_equity = 100.0
    # dd=-10% → 0.95·0.5
    assert r.adjust_position_ratio(90.0) == pytest.approx(0.95 * 0.5)
    # dd=-20% → 0.95·0=0，被 2 成仓硬底托住
    assert r.adjust_position_ratio(80.0) == pytest.approx(0.2)
    assert r.adjust_position_ratio(50.0) == pytest.approx(0.2)


def test_daily_stop_loss_blocks_new_position():
    r = DailyRiskController()
    r.on_new_day(_day(0), 100.0)
    ok, reason = r.can_trade(_day(0), 0, 100.0 * (1 + RISK_CONTROL["daily_stop_loss"] - 0.001))
    assert not ok and "日止损" in reason


def test_drawdown_triggers_cooldown_days():
    """回撤熔断要慢慢跌：单日跌 11% 会先被日止损（-3%）短路。"""
    r = DailyRiskController()
    for i, eq in enumerate((100.0, 97.0, 94.0, 88.5)):
        r.on_new_day(_day(i), eq)          # 当日起始净值=当日净值 → 日止损不触发
        ok, reason = r.can_trade(_day(i), i * 10, eq)
        if i < 3:
            assert ok, f"第 {i} 天不该被拒：{reason}"
    assert "回撤熔断" in reason
    r.on_new_day(_day(4), 88.0)
    still, reason2 = r.can_trade(_day(4), 40, 88.0)
    assert not still and "熔断冷却" in reason2


def test_cooldown_expiry_rebases_peak():
    """熔断冷却到期后必须恢复交易：以当前净值重设峰值。

    回归场景：强制清仓之后净值不再变化，相对旧峰值的回撤恒在阈值之下，
    旧实现会在每一根 bar 上重新触发熔断，整段回测此后再没有一笔买入
    （实测 2010-07 起锁死到 2026）。"""
    r = DailyRiskController({"cooldown_days": 1})
    for i, eq in enumerate((100.0, 97.0, 94.0, 88.5)):
        r.on_new_day(_day(i), eq)
        r.can_trade(_day(i), i * 10, eq)
    assert r.cooldown_until is not None, "跌到 -11.5% 应已触发熔断"
    r.on_new_day(_day(6), 88.5)                      # 冷却期满
    ok, reason = r.can_trade(_day(6), 60, 88.5)
    assert ok, f"冷却到期后应恢复交易，实际被拒：{reason}"
    assert r.peak_equity == pytest.approx(88.5)
    for i in range(7, 12):                           # 此后一路持平
        r.on_new_day(_day(i), 88.5)
        assert r.can_trade(_day(i), i * 10, 88.5)[0]


def test_max_trades_per_day_on_daily():
    """当日笔数闸门本身：显式给一档小上限，不依赖 RISK_CONTROL 的默认值
    （截面组合形态下默认已按 top_k 放大，见 config.PORTFOLIO）。"""
    r = DailyRiskController({"max_trades_per_day": 1})
    r.on_new_day(_day(0), 100.0)
    assert r.can_trade(_day(0), 0, 100.0)[0] is True
    r.on_trade(0)
    ok, reason = r.can_trade(_day(0), 10, 100.0)
    assert not ok and "当日交易次数超限" in reason
    r.on_new_day(_day(1), 100.0)              # 跨日复位
    assert r.can_trade(_day(1), 10, 100.0)[0] is True


def test_min_bars_between_trades_is_one_on_daily():
    """日线：间隔门槛为 1 根 bar（次日即可再交易）。
    次数上限按组合只数放大，一次调仓的 2·top_k 笔不该互相挡。"""
    assert RISK_CONTROL["min_bars_between_trades"] == 1
    assert RISK_CONTROL["max_trades_per_day"] >= 1
    r = DailyRiskController()
    r.on_new_day(_day(0), 100.0)
    for i in range(RISK_CONTROL["max_trades_per_day"]):
        r.on_trade(0)
    r.on_new_day(_day(1), 100.0)
    assert r.can_trade(_day(1), 1, 100.0)[0] is True
