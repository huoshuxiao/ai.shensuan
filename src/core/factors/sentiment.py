"""市场情绪因子：别人贪婪时恐惧，别人恐惧时贪婪

逆向逻辑：
- 放量上涨 / 大盘大涨 = 市场贪婪 -> 我们恐惧 -> 低分（回避）
- 缩量下跌 / 大盘大跌 = 市场恐惧 -> 我们贪婪 -> 高分（机会）
"""
from typing import Any

import pandas as pd

from src.core.factors.base import BaseFactor, FactorResult, clamp_score


class SentimentFactor(BaseFactor):
    name = "sentiment"
    weight = 0.25

    def calculate(self, data: dict[str, Any], context: dict[str, Any] | None = None) -> FactorResult:
        df: pd.DataFrame = data.get("daily")
        if df is None or len(df) < 6:
            return FactorResult(self.name, 50, {"note": "数据不足，中性"}, available=False)

        close = df["close"]
        volume = df["volume"]

        # ETF 自身量价
        latest_change = float(close.pct_change().iloc[-1] or 0.0)
        vol_ratio = float(volume.iloc[-1] / volume.iloc[-5:].mean()) if volume.iloc[-5:].mean() > 0 else 1.0

        # 大盘情绪（可选）
        index_change = None
        index_df = data.get("index_daily")
        if index_df is not None and len(index_df) > 2:
            index_change = float(index_df["close"].pct_change().iloc[-1] or 0.0)

        score = 50.0

        # ETF 自身：放量上涨 = 贪婪 -> 降分；缩量下跌 = 恐惧 -> 加分
        if latest_change > 0.02 and vol_ratio > 1.5:
            score -= 30
        elif latest_change > 0.01 and vol_ratio > 1.2:
            score -= 15
        elif latest_change < -0.02 and vol_ratio < 0.8:
            score += 30
        elif latest_change < -0.01 and vol_ratio < 1.0:
            score += 15

        # 大盘：大涨警惕（贪婪），大跌机会（恐惧）
        if index_change is not None:
            if index_change > 0.02:
                score -= 15
            elif index_change < -0.02:
                score += 15

        detail = {
            "latest_change": round(latest_change, 4),
            "vol_ratio": round(vol_ratio, 2),
            "index_change": round(index_change, 4) if index_change is not None else None,
        }
        return FactorResult(self.name, clamp_score(score), detail)
