"""择时因子：高抛低吸

- 布林带位置：接近下轨 = 低吸点 -> 高分；接近上轨 = 高抛点 -> 低分
- MACD：金叉加分，死叉减分
- KDJ：超卖区（J<20）加分，超买区（J>80）减分
"""
from typing import Any

import numpy as np
import pandas as pd

from src.core.factors.base import BaseFactor, FactorResult, clamp_score


class TimingFactor(BaseFactor):
    name = "timing"
    weight = 0.25

    def calculate(self, data: dict[str, Any], context: dict[str, Any] | None = None) -> FactorResult:
        df: pd.DataFrame = data.get("daily")
        if df is None or len(df) < 26:
            return FactorResult(self.name, 50, {"note": "数据不足，中性"}, available=False)

        close = df["close"]
        last = float(close.iloc[-1])

        score = 50.0

        # ---- 布林带（20日，2倍标准差）----
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        u, l = float(upper.iloc[-1]), float(lower.iloc[-1])
        if u > l:
            band_pos = (last - l) / (u - l)   # 0=下轨, 1=上轨
            if band_pos < 0.15:
                score += 25      # 触及下轨 -> 低吸
            elif band_pos < 0.35:
                score += 12
            elif band_pos > 0.85:
                score -= 25      # 触及上轨 -> 高抛
            elif band_pos > 0.65:
                score -= 12
        else:
            band_pos = np.nan

        # ---- MACD ----
        _, _, hist = self._macd(close)
        hist_now = float(hist.iloc[-1])
        hist_prev = float(hist.iloc[-2])
        golden_cross = hist_prev < 0 <= hist_now      # 金叉
        death_cross = hist_prev > 0 >= hist_now       # 死叉
        if golden_cross:
            score += 15
        elif death_cross:
            score -= 15
        elif hist_now > 0:
            score += 5
        else:
            score -= 5

        # ---- KDJ ----
        _, _, j = self._kdj(df)
        j_now = float(j.iloc[-1])
        if j_now < 20:
            score += 15     # 超卖
        elif j_now > 80:
            score -= 15     # 超买

        detail = {
            "boll_pos": round(band_pos, 3) if not np.isnan(band_pos) else None,
            "macd_golden_cross": golden_cross,
            "macd_death_cross": death_cross,
            "kdj_j": round(j_now, 2),
        }
        return FactorResult(self.name, clamp_score(score), detail)
