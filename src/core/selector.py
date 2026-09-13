"""标的选择器：每次只选一只，无达标则空仓"""
from dataclasses import dataclass

from src.core.scorer import ScoreResult
from src.config.settings import settings


@dataclass
class Selection:
    """选择结果"""
    best: ScoreResult | None      # 最优标的；None 表示空仓
    ranking: list[ScoreResult]    # 全池排名
    reason: str = ""


class Selector:
    """从评分结果中选出唯一标的

    规则：
    - 按总分降序
    - 最高分 >= 阈值 -> 选中该标的
    - 最高分 < 阈值 -> 空仓（没有好的投资标的时可空仓）
    """

    def __init__(self, threshold: float | None = None):
        self.threshold = threshold if threshold is not None else settings.score_threshold

    def select(self, score_results: list[ScoreResult]) -> Selection:
        if not score_results:
            return Selection(None, [], "无候选标的（全部数据校验失败）")

        ranking = sorted(score_results, key=lambda s: s.total, reverse=True)
        top = ranking[0]

        if top.total >= self.threshold:
            reason = (f"{top.code} 综合评分 {top.total} >= 阈值 {self.threshold}，"
                      f"优于第二名 {ranking[1].code}({ranking[1].total})" if len(ranking) > 1
                      else f"{top.code} 综合评分 {top.total} >= 阈值 {self.threshold}")
            return Selection(top, ranking, reason)

        reason = f"最高评分 {top.code}({top.total}) 低于阈值 {self.threshold}，空仓观望"
        return Selection(None, ranking, reason)
