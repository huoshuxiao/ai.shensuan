# -*- coding: utf-8 -*-
"""日线回测的成本模型与净值恒等式。

单边成本 = max(金额×佣金率, 最低佣金) + 金额×滑点，买卖各计一次。
用恒定价格的合成池，可以让净值等式没有浮点噪声可藏。
"""

import pandas as pd
import pytest

from backtest_daily import DailyBacktester
from config import (COMMISSION_RATE, INIT_CAPITAL, MIN_COMMISSION,
                    MIN_TRADE_AMOUNT, SLIPPAGE)
from synth import make_flat_pool


def _signals(idx, code, buy_at, exit_at):
    """buy_at 起持有 code，exit_at 起空仓"""
    target = ["" if ts < idx[buy_at] or ts >= idx[exit_at] else code
              for ts in idx]
    return pd.DataFrame({"target_code": target}, index=idx)


def test_cost_formula_known_answers():
    big = 9500.0
    assert DailyBacktester._cost(big) == pytest.approx(
        big * COMMISSION_RATE + big * SLIPPAGE)
    small = 500.0
    assert DailyBacktester._cost(small) == pytest.approx(
        max(small * COMMISSION_RATE, MIN_COMMISSION) + small * SLIPPAGE)
    # 最低佣金拐点：0.1 / 1bp = 1 万元以下按最低佣金收
    assert DailyBacktester._cost(1000.0) == pytest.approx(
        MIN_COMMISSION + 1000.0 * SLIPPAGE)


def test_flat_round_trip_only_loses_costs():
    pool, idx = make_flat_pool(code="510300", n_bars=40, price=10.0)
    bt = DailyBacktester(pool, None)
    res = bt.run(_signals(idx, "510300", buy_at=5, exit_at=20))
    trades = res["trades"]
    assert list(trades["action"]) == ["BUY", "SELL"]
    buy_cost = trades.iloc[0]["cost"]
    sell_cost = trades.iloc[1]["cost"]
    assert buy_cost == pytest.approx(
        max(trades.iloc[0]["amount"] * COMMISSION_RATE, MIN_COMMISSION)
        + trades.iloc[0]["amount"] * SLIPPAGE)
    final_equity = res["equity"]["equity"].iloc[-1]
    # 价格不动 → 全部损益来自摩擦成本
    assert final_equity == pytest.approx(
        INIT_CAPITAL - buy_cost - sell_cost)
    assert trades.iloc[1]["date"] == idx[20]


def test_no_trade_on_buy_day_t_plus_1():
    """日线天然 T+1：买入当日不得卖出。"""
    pool, idx = make_flat_pool(code="510300", n_bars=30, price=10.0)
    bt = DailyBacktester(pool, None)
    res = bt.run(_signals(idx, "510300", buy_at=3, exit_at=4))
    trades = res["trades"]
    buy_date = trades.iloc[0]["date"]
    sell_date = trades.iloc[1]["date"]
    assert sell_date == idx[4] and sell_date > buy_date


def test_missing_bar_makes_exit_wait():
    """停牌日无 bar → 取不到价就跳过卖出，之后有 bar 才成交。"""
    pool, idx = make_flat_pool(code="510300", n_bars=30, price=10.0)
    pool["510300"] = pool["510300"].drop(idx[6])      # 模拟停牌一天
    bt = DailyBacktester(pool, None)
    res = bt.run(_signals(idx, "510300", buy_at=3, exit_at=6))
    assert res["trades"]["action"].tolist() == ["BUY", "SELL"]
    assert res["trades"].iloc[1]["date"] == idx[7]


def test_below_min_trade_amount_is_not_executed(monkeypatch):
    pool, idx = make_flat_pool(code="510300", n_bars=20, price=10.0)
    monkeypatch.setattr("backtest_daily.MIN_TRADE_AMOUNT", 1e9)
    bt = DailyBacktester(pool, None)
    res = bt.run(_signals(idx, "510300", buy_at=2, exit_at=10))
    assert res["trades"].empty
    assert res["equity"]["equity"].iloc[-1] == pytest.approx(INIT_CAPITAL)
    assert MIN_TRADE_AMOUNT == 100                     # 真实门槛未被改动


def test_unknown_target_never_buys():
    pool, idx = make_flat_pool(code="510300", n_bars=20)
    bt = DailyBacktester(pool, {"510300"}, None)
    res = bt.run(_signals(idx, "999999", buy_at=2, exit_at=10))
    assert res["trades"].empty, "信号标的不在池内不应成交"


def test_stats_use_daily_annualization():
    """夏普年化系数必须走 adapter 的 252，而不是分钟常量。"""
    pool, idx = make_flat_pool(code="510300", n_bars=60, price=10.0)
    bt = DailyBacktester(pool, None)
    res = bt.run(_signals(idx, "510300", buy_at=5, exit_at=40))
    assert res["stats"]["bars_per_year"] == 252
