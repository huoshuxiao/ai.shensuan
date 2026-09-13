"""回测引擎单元测试：信号规则、资金与收益计算、报告输出"""
import pandas as pd
import pytest

from src.config.settings import settings
from src.core.backtest import BacktestEngine
from src.core.risk import RiskManager
from src.core.scorer import ScoreResult
from src.core.selector import Selector
from src.output.backtest_report import save_daily_csv, save_report
from tests.conftest import MockFetcher

N_BARS = 70            # 总K线数（含预热期）
START_OFFSET = 45      # 回测起点在K线序列中的位置（预热期 45 根 >= 30）


def make_price_frame(closes: list[float], start: str = "2025-11-03") -> pd.DataFrame:
    """构造单边价格序列日线（open/high/low/close 相同，便于精确控制涨跌）"""
    dates = pd.date_range(start, periods=len(closes), freq="B")
    closes = [float(c) for c in closes]
    return pd.DataFrame({
        "date": dates,
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1e6] * len(closes),
    })


class FakeScorer:
    """固定分数评分器：不依赖真实因子，精确控制选择结果"""

    def __init__(self, scores: dict[str, float]):
        self.scores = scores

    def score_all(self, data_map, context=None):
        return [ScoreResult(code=code, total=self.scores.get(code, 10.0))
                for code in data_map]


def build_engine(daily_map: dict, scores: dict, start: str = None, end: str = None,
                 capital: float = 10000.0) -> BacktestEngine:
    fetcher = MockFetcher(daily_map=daily_map)
    return BacktestEngine(
        fetcher=fetcher, scorer=FakeScorer(scores),
        selector=Selector(threshold=60.0), risk=RiskManager(),
        start=start, end=end, capital=capital,
    )


def frame_dates(closes: list[float]) -> tuple[str, str]:
    """由价格序列推算回测起止日期（跳过预热期）"""
    dates = pd.date_range("2025-11-03", periods=len(closes), freq="B")
    return dates[START_OFFSET].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d")


def make_map(code: str, closes: list[float]) -> dict:
    return {code: make_price_frame(closes)}


# ================= 空仓与买入 =================

def test_all_empty_when_below_threshold():
    closes = [1.0] * N_BARS
    start, end = frame_dates(closes)
    engine = build_engine(make_map("510300", closes), {"510300": 30.0}, start, end)
    result = engine.run()

    assert result.buy_count == 0
    assert result.sell_count == 0
    assert result.final_assets == pytest.approx(10000.0)
    assert all(d.action == "EMPTY" for d in result.days)
    assert len(result.days) == N_BARS - START_OFFSET


def test_buy_full_lot_then_hold():
    closes = [1.0] * N_BARS
    start, end = frame_dates(closes)
    engine = build_engine(make_map("510300", closes), {"510300": 80.0}, start, end)
    result = engine.run()

    # 首日买入：整手（100份）满仓，扣除佣金 0.03%
    day0 = result.days[0]
    assert day0.action == "BUY"
    assert day0.code == "510300"
    assert day0.quantity == 9900          # int(10000/(1.0*100*1.0003)) = 99 手
    assert day0.price == pytest.approx(1.0)
    assert day0.cash == pytest.approx(10000.0 - 9900 * 1.0003, abs=0.01)
    # 次日起持有
    assert all(d.action == "HOLD" for d in result.days[1:])
    assert result.days[1].position_value == pytest.approx(9900.0)
    assert result.buy_count == 1 and result.sell_count == 0
    assert result.trades[0].commission == pytest.approx(2.97, abs=0.01)


# ================= 止盈止损 =================

def test_take_profit_trigger_sell():
    # 前45根 3.0（预热），之后 3.0 -> 3.02 -> 3.06 -> 3.12 -> 3.18(+6%)
    closes = [3.0] * 45 + [3.0, 3.02, 3.06, 3.12, 3.18] + [3.18] * (N_BARS - 50)
    start, end = frame_dates(closes)
    engine = build_engine(make_map("510300", closes), {"510300": 80.0}, start, end)
    result = engine.run()

    assert result.days[0].action == "BUY"
    assert result.days[1].action == "HOLD"   # +0.67%
    assert result.days[2].action == "HOLD"   # +2.0%
    assert result.days[3].action == "HOLD"   # +4.0%
    assert result.days[4].action == "SELL"   # +6.0% >= 止盈线 5%
    sell = [t for t in result.trades if t.action == "SELL"]
    assert len(sell) == 1
    assert sell[0].pnl > 0
    assert sell[0].price == pytest.approx(3.18)
    assert result.win_count == 1
    assert result.win_rate_pct == pytest.approx(100.0)
    # 卖出后现金回笼，期末空仓或再持有（价格平稳无新信号）
    assert result.days[4].quantity == 0
    assert result.final_assets > 10000.0


def test_stop_loss_trigger_sell():
    # 3.0 买入后跌至 2.90（-3.33%）触发止损线
    closes = [3.0] * 45 + [3.0, 2.98, 2.95, 2.90] + [2.90] * (N_BARS - 49)
    start, end = frame_dates(closes)
    engine = build_engine(make_map("510300", closes), {"510300": 80.0}, start, end)
    result = engine.run()

    assert result.days[0].action == "BUY"
    assert result.days[1].action == "HOLD"   # -0.67%
    assert result.days[2].action == "HOLD"   # -1.67%
    assert result.days[3].action == "SELL"   # -3.0% <= 止损线 -3%
    sell = [t for t in result.trades if t.action == "SELL"]
    assert len(sell) == 1
    assert sell[0].pnl < 0
    assert result.win_count == 0
    assert result.win_rate_pct == 0.0
    assert result.total_pnl < 0


def test_cooldown_blocks_buy_next_day(monkeypatch):
    # 亏损卖出后进入冷却期（cooldown_days=2），次日禁止买入
    monkeypatch.setattr(settings, "cooldown_days", 2)
    closes = [3.0] * 45 + [3.0, 2.90] + [2.90] * (N_BARS - 47)
    start, end = frame_dates(closes)
    engine = build_engine(make_map("510300", closes), {"510300": 80.0}, start, end)
    result = engine.run()

    assert result.days[0].action == "BUY"
    assert result.days[1].action == "SELL"   # 当日 -3% 止损
    day2 = result.days[2]
    assert day2.action == "EMPTY"            # 冷却期内禁止买入
    assert "风控禁止买入" in day2.reason


# ================= 指标与边界 =================

def test_result_metrics_consistency():
    closes = [1.0] * N_BARS
    start, end = frame_dates(closes)
    engine = build_engine(make_map("510300", closes), {"510300": 80.0}, start, end)
    result = engine.run()

    assert result.initial_capital == 10000.0
    assert result.final_assets == pytest.approx(result.days[-1].total_assets)
    assert result.total_return_pct == pytest.approx(
        (result.final_assets / 10000.0 - 1.0) * 100.0, abs=0.01)
    assert result.max_drawdown_pct <= 0.0
    # 价格平稳、仅佣金损耗：总收益与年化均为小幅负值
    assert result.total_return_pct < 0
    assert result.annualized_return_pct < 0
    # 每日累计收益率与总资产一致
    for d in result.days:
        assert d.cumulative_return_pct == pytest.approx(
            (d.total_assets / 10000.0 - 1.0) * 100.0, abs=0.01)


def test_run_raises_without_data():
    engine = build_engine({}, {})
    with pytest.raises(RuntimeError, match="全部获取失败"):
        engine.run()


def test_no_trading_days_in_range():
    closes = [1.0] * N_BARS
    engine = build_engine(make_map("510300", closes), {"510300": 80.0},
                          start="2025-01-01", end="2025-02-01")
    with pytest.raises(RuntimeError, match="无交易日"):
        engine.run()


# ================= 报告输出 =================

def test_report_output_files(monkeypatch, tmp_path):
    import src.output.backtest_report as br
    monkeypatch.setattr(br, "REPORT_DIR", tmp_path)

    closes = [1.0] * N_BARS
    start, end = frame_dates(closes)
    engine = build_engine(make_map("510300", closes), {"510300": 80.0}, start, end)
    result = engine.run()

    csv_path = save_daily_csv(result)
    md_path = save_report(result)

    assert csv_path.exists()
    assert md_path.exists()
    # CSV：表头 + 每日一行
    with csv_path.open(encoding="utf-8") as fh:
        lines = fh.read().strip().splitlines()
    assert len(lines) == 1 + len(result.days)
    assert lines[0].startswith("date,action,code,name,price")
    # MD 报告包含汇总与交易明细
    content = md_path.read_text(encoding="utf-8")
    assert "ETF策略回测报告" in content
    assert "总收益率" in content
    assert "每日净值与收益" in content
    assert "交易明细" in content
