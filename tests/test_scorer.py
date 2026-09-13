"""评分器与选择器单元测试"""
import pytest

from src.core.factors.downtrend_guard import DowntrendGuardFactor
from src.core.factors.oversold import OversoldFactor
from src.core.factors.position import PositionFactor
from src.core.factors.sentiment import SentimentFactor
from src.core.factors.timing import TimingFactor
from src.core.scorer import Scorer
from src.core.selector import Selector
from tests.conftest import make_downtrend_daily, make_daily, make_uptrend_daily


@pytest.fixture
def scorer() -> Scorer:
    factors = [
        SentimentFactor(), OversoldFactor(), TimingFactor(),
        PositionFactor(), DowntrendGuardFactor(),
    ]
    for f in factors:
        f.weight = 0.2
    return Scorer(factors)


def test_score_range_within_0_100(scorer):
    """任何输入下总分都应在 0-100 之间"""
    data_map = {
        "A": {"daily": make_downtrend_daily()},
        "B": {"daily": make_uptrend_daily()},
        "C": {"daily": make_daily()},
    }
    results = scorer.score_all(data_map)
    assert len(results) == 3
    for r in results:
        assert 0 <= r.total <= 100


def test_score_weighted_average(scorer):
    """总分应是各因子加权合成"""
    data = {"daily": make_daily()}
    result = scorer.score_one(data)
    manual = sum(fr.score * 0.2 for fr in result.factor_results.values())
    assert abs(result.total - round(manual, 2)) < 0.01


def test_unavailable_factor_neutral(scorer):
    """数据不足因子按 50 分中性计入"""
    data = {"daily": make_daily(n=10)}  # 低于因子最低要求
    result = scorer.score_one(data)
    assert result.total == 50.0
    # 趋势保护因子数据不足时应 unavailable 中性
    assert result.factor_results["downtrend_guard"].available is False


def test_selector_picks_highest_above_threshold():
    from src.core.scorer import ScoreResult
    selector = Selector(threshold=60)
    results = [
        ScoreResult("A", 70.0),
        ScoreResult("B", 85.5),
        ScoreResult("C", 62.0),
    ]
    selection = selector.select(results)
    assert selection.best is not None
    assert selection.best.code == "B"
    assert selection.ranking[0].code == "B"


def test_selector_empty_position_below_threshold():
    """低于阈值 -> 空仓（没有好的投资标的时可空仓）"""
    from src.core.scorer import ScoreResult
    selector = Selector(threshold=60)
    results = [ScoreResult("A", 30.0), ScoreResult("B", 55.0)]
    selection = selector.select(results)
    assert selection.best is None
    assert "空仓" in selection.reason


def test_selector_threshold_boundary():
    """边界：等于阈值时选中"""
    from src.core.scorer import ScoreResult
    selector = Selector(threshold=60)
    selection = selector.select([ScoreResult("A", 60.0)])
    assert selection.best is not None
    assert selection.best.code == "A"


def test_selector_empty_input():
    selector = Selector(threshold=60)
    selection = selector.select([])
    assert selection.best is None
