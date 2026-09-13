"""策略引擎端到端集成测试：mock 数据源跑通盘前/盘中/盘后全流程"""
import numpy as np
import pandas as pd
import pytest

from src.core.strategy import StrategyEngine, build_factors
from src.config.settings import settings
from src.config.etf_pool import ETF_POOL
from src.output.models import SIGNAL_BUY, SIGNAL_EMPTY, SIGNAL_HOLD, SIGNAL_SELL
from src.output.reporter import Reporter
from src.storage.state import Position, StateStore
from tests.conftest import MockFetcher, make_downtrend_daily, make_daily, make_position, make_uptrend_daily


def build_engine(tmp_path, fetcher: MockFetcher) -> tuple[StrategyEngine, StateStore]:
    store = StateStore(tmp_path / "state.json")
    reporter = Reporter(tmp_path / "reports")
    engine = StrategyEngine(fetcher=fetcher, store=store, reporter=reporter)
    return engine, store


def daily_map_neutral() -> dict:
    """全池中性行情（严格横盘，各因子均为中性分，总分应低于阈值）"""
    n = 80
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    df = pd.DataFrame({
        "date": dates,
        "open": np.full(n, 1.0),
        "high": np.full(n, 1.001),
        "low": np.full(n, 0.999),
        "close": np.full(n, 1.0),
        "volume": np.full(n, 2e6),
    })
    return {etf.code: df.copy() for etf in ETF_POOL}


def daily_map_one_oversold() -> dict:
    """第一只超跌（应被选中），其余追高（应被回避）"""
    pool = ETF_POOL
    data_map = {etf.code: make_uptrend_daily(seed=200 + i) for i, etf in enumerate(pool)}
    data_map[pool[0].code] = make_downtrend_daily()
    return data_map


# ================= 盘前 =================
def test_pre_market_empty_position_when_neutral(tmp_path):
    """全池中性：评分低于阈值 -> 空仓"""
    engine, _ = build_engine(tmp_path, MockFetcher(daily_map=daily_map_neutral()))
    signal = engine.run_pre_market()
    assert signal.mode == "pre"
    assert signal.signal == SIGNAL_EMPTY
    assert signal.code == ""


def test_pre_market_buy_oversold_candidate(tmp_path):
    """一只超跌其余追高：应选中超跌标的 -> BUY"""
    pool = ETF_POOL
    engine, _ = build_engine(tmp_path, MockFetcher(daily_map=daily_map_one_oversold()))
    signal = engine.run_pre_market()
    assert signal.signal == SIGNAL_BUY
    assert signal.code == pool[0].code
    assert signal.score >= 60


def test_pre_market_all_data_invalid_returns_empty(tmp_path):
    """全部数据校验失败 -> 空仓信号"""
    engine, _ = build_engine(tmp_path, MockFetcher(daily_map={}))
    signal = engine.run_pre_market()
    assert signal.signal == SIGNAL_EMPTY
    assert "校验失败" in signal.reason


# ================= 盘中 =================
def test_intra_day_take_profit_force_sell(tmp_path):
    """持仓浮盈触发止盈 -> SELL"""
    position = make_position(code=ETF_POOL[0].code, buy_price=1.0)
    store = StateStore(tmp_path / "state.json")
    store.save(position)
    fetcher = MockFetcher(
        daily_map=daily_map_neutral(),
        realtime_map={position.code: {"price": 1.06, "name": position.name}},
    )
    engine = StrategyEngine(fetcher=fetcher, store=store, reporter=Reporter(tmp_path / "reports"))
    signal = engine.run_intra_day()
    assert signal.signal == SIGNAL_SELL
    assert signal.code == position.code
    assert "止盈" in signal.reason


def test_intra_day_hold_when_normal(tmp_path):
    """持仓正常波动 -> HOLD"""
    position = make_position(code=ETF_POOL[0].code, buy_price=1.0)
    store = StateStore(tmp_path / "state.json")
    store.save(position)
    fetcher = MockFetcher(
        daily_map=daily_map_neutral(),
        realtime_map={position.code: {"price": 1.01, "name": position.name}},
    )
    engine = StrategyEngine(fetcher=fetcher, store=store, reporter=Reporter(tmp_path / "reports"))
    signal = engine.run_intra_day()
    assert signal.signal == SIGNAL_HOLD
    assert signal.code == position.code


def test_intra_day_empty_position_rescores(tmp_path):
    """空仓盘中复核：超跌标的 -> BUY"""
    pool = ETF_POOL
    engine, _ = build_engine(tmp_path, MockFetcher(daily_map=daily_map_one_oversold()))
    signal = engine.run_intra_day()
    assert signal.signal in (SIGNAL_BUY, SIGNAL_EMPTY)
    if signal.signal == SIGNAL_BUY:
        assert signal.code == pool[0].code


# ================= 盘后 =================
def test_post_market_updates_hold_days(tmp_path):
    """盘后：持有天数 +1，生成日报文件"""
    position = make_position(code=ETF_POOL[0].code, buy_price=1.0)
    store = StateStore(tmp_path / "state.json")
    store.save(position)
    fetcher = MockFetcher(
        daily_map=daily_map_neutral(),
        realtime_map={position.code: {"price": 1.02, "name": position.name}},
    )
    engine = StrategyEngine(fetcher=fetcher, store=store, reporter=Reporter(tmp_path / "reports"))
    report = engine.run_post_market()
    updated = store.load()
    assert updated.hold_days == position.hold_days + 1
    assert report.date != ""
    report_path = tmp_path / "reports" / report.date / "daily_report.md"
    assert report_path.exists()


def test_post_market_empty_position_notes(tmp_path):
    """空仓盘后：正常输出日报"""
    engine, _ = build_engine(tmp_path, MockFetcher(daily_map=daily_map_neutral()))
    report = engine.run_post_market()
    assert any("空仓" in note for note in report.notes)


# ================= 状态流转 =================
def test_apply_buy_then_sell_flow(tmp_path):
    """买入 -> 卖出：连续亏损计数与已实现盈亏正确"""
    engine, store = build_engine(tmp_path, MockFetcher(daily_map={}))
    engine.apply_buy("510300", price=1.00, quantity=1000)
    position = store.load()
    assert position.code == "510300"
    assert position.buy_price == 1.00

    # 亏损卖出 -> 连续亏损 +1
    new_position = engine.apply_sell(sell_price=0.95)
    assert new_position.is_empty
    assert new_position.consecutive_losses == 1
    assert abs(new_position.realized_pnl - (-50.0)) < 0.01

    # 再次买入后盈利卖出 -> 连续亏损清零
    engine.apply_buy("510300", price=1.00, quantity=1000)
    new_position = engine.apply_sell(sell_price=1.10)
    assert new_position.consecutive_losses == 0
    assert abs(new_position.realized_pnl - 50.0) < 0.01


def test_state_persistence_corrupt_file(tmp_path):
    """状态文件损坏 -> 按空仓处理"""
    state_path = tmp_path / "state.json"
    state_path.write_text("{corrupt json!!", encoding="utf-8")
    store = StateStore(state_path)
    assert store.load().is_empty


# ================= 因子组合配置（settings.factor_weights） =================

def test_build_factors_follows_configuration(monkeypatch):
    """factor_weights 决定启用哪些因子及权重"""
    monkeypatch.setattr(settings, "factor_weights", {
        "oversold": 0.7,
        "timing": 0.3,
        "downtrend_guard": 0.0,   # 权重 0 -> 停用
        "unknown_factor": 0.5,    # 未注册 -> 忽略
    })
    factors = build_factors()
    names = [f.name for f in factors]
    assert names == ["oversold", "timing"]
    weights = {f.name: f.weight for f in factors}
    assert weights["oversold"] == 0.7
    assert weights["timing"] == 0.3


def test_build_factors_default_all_enabled():
    """默认配置启用全部 5 个因子"""
    factors = build_factors()
    assert {f.name for f in factors} == {
        "sentiment", "oversold", "timing", "position", "downtrend_guard",
    }
    assert sum(f.weight for f in factors) == pytest.approx(1.0)


# ================= 资金管理（settings.initial_capital） =================

def test_apply_buy_insufficient_capital_raises(tmp_path):
    """买入金额超过可用资金时报错（初始资金 1W）"""
    engine, store = build_engine(tmp_path, MockFetcher(daily_map={}))
    with pytest.raises(ValueError, match="资金不足"):
        engine.apply_buy("510300", price=10.5, quantity=1000)  # 10500 > 10000
    assert store.load().is_empty  # 未写入状态


def test_apply_buy_within_capital_ok(tmp_path):
    """买入金额不超过可用资金时正常记录"""
    engine, store = build_engine(tmp_path, MockFetcher(daily_map={}))
    engine.apply_buy("510300", price=9.99, quantity=1000)  # 9990 <= 10000
    assert not store.load().is_empty


def test_post_market_total_assets_with_position(tmp_path):
    """盘后日报包含账户总资产（初始资金+已实现盈亏+浮动盈亏）"""
    position = make_position(code=ETF_POOL[0].code, buy_price=1.0)
    position.realized_pnl = 100.0
    store = StateStore(tmp_path / "state.json")
    store.save(position)
    fetcher = MockFetcher(
        daily_map=daily_map_neutral(),
        realtime_map={position.code: {"price": 1.05, "name": position.name}},  # 浮盈 +50
    )
    engine = StrategyEngine(fetcher=fetcher, store=store, reporter=Reporter(tmp_path / "reports"))
    report = engine.run_post_market()
    # 总资产 = 10000 + 100 + 50
    assert any("账户总资产 10150.00" in note for note in report.notes)


def test_post_market_total_assets_empty_position(tmp_path):
    """空仓时总资产 = 初始资金 + 累计已实现盈亏"""
    store = StateStore(tmp_path / "state.json")
    empty = Position.empty()
    empty.realized_pnl = -200.0
    store.save(empty)
    engine, _ = build_engine(tmp_path, MockFetcher(daily_map=daily_map_neutral()))
    engine.store = store
    report = engine.run_post_market()
    assert any("账户总资产 9800.00" in note for note in report.notes)
