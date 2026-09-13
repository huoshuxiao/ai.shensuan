"""多因子综合评分器：加权合成总分"""
from dataclasses import dataclass, field
from typing import Any

from src.core.factors.base import BaseFactor, FactorResult


@dataclass
class ScoreResult:
    """单只ETF的综合评分结果"""
    code: str
    total: float = 0.0
    factor_results: dict[str, FactorResult] = field(default_factory=dict)
    note: str = ""


class Scorer:
    """对单只ETF执行全部因子计算并加权合成

    不可用因子按中性分（50）计入，且权重重新归一化到可用因子，
    保证数据不足时不会畸变总分。
    """

    def __init__(self, factors: list[BaseFactor]):
        self.factors = factors

    def score_one(self, data: dict[str, Any], context: dict[str, Any] | None = None) -> ScoreResult:
        code = str(context.get("code", "")) if context else ""
        results: dict[str, FactorResult] = {}
        total = 0.0
        weight_sum = 0.0

        for factor in self.factors:
            result = factor.calculate(data, context)
            results[factor.name] = result
            if result.available:
                total += result.score * factor.weight
                weight_sum += factor.weight
            else:
                # 不可用因子按中性分计入（权重不归一）
                total += 50.0 * factor.weight
                weight_sum += factor.weight

        if weight_sum > 0:
            total = total / weight_sum

        return ScoreResult(code=code, total=round(total, 2), factor_results=results)

    def score_all(self, data_map: dict[str, dict[str, Any]],
                  context: dict[str, Any] | None = None) -> list[ScoreResult]:
        """批量评分，data_map: {code: data}"""
        out: list[ScoreResult] = []
        for code, data in data_map.items():
            ctx = dict(context or {})
            ctx["code"] = code
            out.append(self.score_one(data, ctx))
        return out
