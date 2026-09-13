"""风控规则单元测试：止盈止损边界、冷却期"""
from datetime import datetime

import pytest

from src.core.risk import RiskManager
from src.storage.state import Position
from tests.conftest import make_position


@pytest.fixture
def risk() -> RiskManager:
    return RiskManager()


def test_no_position_no_constraint(risk):
    decision = risk.check(None, None)
    assert not decision.force_sell
    assert not decision.block_buy


def test_take_profit_triggered(risk):
    """浮盈 +6% 超过止盈线 +5% -> 强制卖出"""
    position = make_position(buy_price=1.0)
    decision = risk.check(position, current_price=1.06)
    assert decision.force_sell
    assert "止盈" in decision.reason


def test_stop_loss_triggered(risk):
    """浮亏 -4% 超过止损线 -3% -> 强制卖出"""
    position = make_position(buy_price=1.0)
    decision = risk.check(position, current_price=0.96)
    assert decision.force_sell
    assert "止损" in decision.reason


def test_boundary_exactly_take_profit(risk):
    """边界：恰好 +5% 触发止盈（>=）"""
    position = make_position(buy_price=1.0)
    decision = risk.check(position, current_price=1.05)
    assert decision.force_sell


def test_normal_range_no_force_sell(risk):
    """正常波动不触发卖出，但持仓时禁止买入"""
    position = make_position(buy_price=1.0)
    decision = risk.check(position, current_price=1.01)
    assert not decision.force_sell
    assert decision.block_buy  # 每次只持有一只ETF


def test_cooldown_blocks_buy(risk):
    """连续亏损 + 冷却期内 -> 禁止买入（空仓状态下连续亏损记录仍保留）"""
    position = Position.empty()
    position.consecutive_losses = 2
    position.last_trade_date = "2026-09-10"
    decision = risk.check(position, None, today=datetime(2026, 9, 10))  # 当天，冷却期内
    assert decision.block_buy


def test_cooldown_expired_allows_buy(risk):
    """冷却期结束 -> 恢复可买"""
    position = Position.empty()
    position.consecutive_losses = 2
    position.last_trade_date = "2026-09-01"
    decision = risk.check(position, None, today=datetime(2026, 9, 10))
    assert not decision.block_buy


def test_price_unavailable_conservative(risk):
    """有持仓但拿不到现价：保守不强制卖，但禁止买入"""
    position = make_position(buy_price=1.0)
    decision = risk.check(position, None)
    assert not decision.force_sell
    assert decision.block_buy
