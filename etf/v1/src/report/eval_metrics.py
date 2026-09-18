# -*- coding: utf-8 -*-
"""评估指标"""

import re
import numpy as np


class EvalMetrics:
    @staticmethod
    def extract_numbers(text):
        numbers = []
        for m in re.finditer(r"([+-]?\d+\.?\d*)\s*%", text):
            numbers.append({"value": float(m.group(1)) / 100,
                            "raw": m.group(0), "type": "percent"})
        return numbers

    @staticmethod
    def _flatten(data, prefix=""):
        result = []
        if isinstance(data, dict):
            for k, v in data.items():
                result.extend(EvalMetrics._flatten(v, f"{prefix}.{k}"))
        elif isinstance(data, list):
            for i, v in enumerate(data):
                result.extend(EvalMetrics._flatten(v, f"{prefix}[{i}]"))
        elif isinstance(data, (int, float)) and not isinstance(data, bool):
            result.append({"value": float(data), "key": prefix})
        return result

    @staticmethod
    def check_accuracy(text, raw_data):
        numbers = EvalMetrics.extract_numbers(text)
        true_values = EvalMetrics._flatten(raw_data)
        matched, halluc = 0, 0
        for num in numbers:
            if not true_values:
                halluc += 1
                continue
            diffs = [abs(num["value"] - tv["value"]) for tv in true_values]
            min_diff = min(diffs)
            closest = true_values[diffs.index(min_diff)]
            rel = min_diff / (abs(closest["value"]) + 1e-9)
            if rel < 0.05:
                matched += 1
            elif rel > 0.15:
                halluc += 1
        total = matched + halluc
        return {"total_numbers": total, "matched": matched,
                "hallucinations": halluc,
                "match_rate": matched / max(total, 1)}

    @staticmethod
    def check_coverage(text, raw_data):
        key_indicators = {
            "slippage": ["滑点", "slippage"],
            "latency": ["延迟", "latency"],
            "turnover": ["换手", "turnover"],
            "strategy": ["策略", "夏普", "sharpe"],
            "factor": ["因子", "factor"],
            "drawdown": ["回撤", "drawdown"],
            "risk": ["风险", "risk"]}
        text_lower = text.lower()
        covered = {k: any(kw.lower() in text_lower for kw in kws)
                   for k, kws in key_indicators.items()}
        return {"coverage_rate": sum(covered.values()) / len(covered)}

    @staticmethod
    def check_actionability(text):
        action_words = ["把", "将", "改成", "调整到", "移到", "换",
                        "降", "升", "删", "加", "启动", "重挖",
                        "更新", "替换", "暂停", "减仓", "加仓"]
        vague = ["关注", "留意", "注意", "观察", "重视"]
        m = re.search(r"##\s*✅?\s*行动建议(.*?)(?=##|\Z)",
                      text, re.DOTALL)
        if not m:
            return {"n_suggestions": 0, "actionable_rate": 0}
        suggestions = []
        for line in m.group(1).split("\n"):
            line = line.strip("- *1234567890. ")
            if line:
                suggestions.append(line)
        if not suggestions:
            return {"n_suggestions": 0, "actionable_rate": 0}
        actionable = sum(1 for s in suggestions
                         if any(w in s for w in action_words) and
                         not (any(v in s for v in vague) and
                              not any(w in s for w in action_words)))
        return {"n_suggestions": len(suggestions),
                "actionable_rate": actionable / len(suggestions)}

    @staticmethod
    def compute_objective_score(text, raw_data):
        acc = EvalMetrics.check_accuracy(text, raw_data)
        cov = EvalMetrics.check_coverage(text, raw_data)
        act = EvalMetrics.check_actionability(text)
        score = (acc["match_rate"] * 40 +
                 cov["coverage_rate"] * 30 +
                 act["actionable_rate"] * 30) * 100
        return {"objective_score": round(score, 1),
                "accuracy": acc, "coverage": cov,
                "actionability": act}