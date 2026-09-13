"""超跌反弹因子：别人恐惧时贪婪

- RSI(6) 超卖（<30）加分
- 价格偏离 MA20 越深（向下）加分
- 连续下跌天数越多加分（恐慌出清）
"""
from typing import Any

import pandas as pd

from src.core.factors.base import BaseFactor, FactorResult, clamp_score


class OversoldFactor(BaseFactor):
    name = "oversold"
    weight = 0.30

    def calculate(self, data: dict[str, Any], context: dict[str, Any] | None = None) -> FactorResult:
        df: pd.DataFrame = data.get("daily")
        if df is None or len(df) < 21:
            return FactorResult(self.name, 50, {"note": "数据不足，中性"}, available=False)

        close = df["close"]

        # RSI(6)
        rsi = float(self._rsi(close, 6).iloc[-1])

        # 偏离 MA20（负 = 低于均线 = 超跌）
        ma20 = float(close.rolling(20).mean().iloc[-1])
        deviation = (float(close.iloc[-1]) / ma20 - 1.0) if ma20 > 0 else 0.0

        # 连续下跌天数
        changes = close.pct_change().dropna()
        down_streak = 0
        for chg in changes.iloc[::-1]:
            if chg < 0:
                down_streak += 1
            else:
                break

        score = 50.0

        # RSI 超卖
        if rsi < 20:
            score += 25
        elif rsi < 30:
            score += 20
        elif rsi < 40:
            score += 10
        elif rsi > 70:      # 超买 = 追高风险
            score -= 25
        elif rsi > 60:
            score -= 10

        # 偏离 MA20
        if deviation < -0.08:
            score += 25
        elif deviation < -0.05:
            score += 20
        elif deviation < -0.02:
            score += 10
        elif deviation > 0.08:
            score -= 25
        elif deviation > 0.05:
            score -= 15

        # 连续下跌（恐慌出清信号）
        score += min(down_streak, 5) * 3

        detail = {
            "rsi6": round(rsi, 2),
            "deviation_ma20": round(deviation, 4),
            "down_streak": down_streak,
        }
        return FactorResult(self.name, clamp_score(score), detail)
