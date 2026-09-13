"""防越低越买因子（趋势保护）：勿燥、戒贪

超跌反弹因子天然倾向"越跌越买"，若标的处于长期下跌趋势，
反复超跌买入即为"接飞刀"。本因子从趋势角度提供反向约束：
- 空头排列（MA20 < MA60）：中期趋势向下，扣分
- MA60 自身下行：长期趋势确认向下，扣分
- 收盘价创 60 日新低：趋势破位，重扣
- 多头排列且 MA60 上行：趋势良好，加分（可安心低吸）

得分语义与其他因子一致：越高越值得买入。
"""
from typing import Any

from src.core.factors.base import BaseFactor, FactorResult, clamp_score


class DowntrendGuardFactor(BaseFactor):
    name = "downtrend_guard"
    weight = 0.20

    MIN_BARS = 65  # MA60 需要 60 根，另需 5 根计算均线斜率

    # 均线纠缠容差：偏离幅度低于该阈值视为无明显排列，避免噪声误判
    ALIGN_TOLERANCE = 0.005   # 0.5%：MA20 与 MA60 相对偏差
    SLOPE_TOLERANCE = 0.002   # 0.2%：MA60 五日斜率阈值

    def calculate(self, data: dict[str, Any], context: dict[str, Any] | None = None) -> FactorResult:
        df = data.get("daily")
        if df is None or len(df) < self.MIN_BARS:
            return FactorResult(self.name, 50, {"note": "数据不足，中性"}, available=False)

        close = df["close"]
        last = float(close.iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1])
        ma60_prev = float(close.rolling(60).mean().iloc[-6])  # 5 个交易日前的 MA60
        low_60 = float(close.iloc[-60:].min())

        bearish_align = ma20 < ma60 * (1 - self.ALIGN_TOLERANCE)       # 空头排列：中期趋势向下
        bullish_align = ma20 > ma60 * (1 + self.ALIGN_TOLERANCE)       # 多头排列：中期趋势向上
        ma60_falling = ma60 < ma60_prev * (1 - self.SLOPE_TOLERANCE)   # 长期均线方向向下
        ma60_rising = ma60 > ma60_prev * (1 + self.SLOPE_TOLERANCE)    # 长期均线方向向上
        new_low = last <= low_60 * 1.001  # 创 60 日新低（1‰ 容差）

        score = 50.0
        if bearish_align:
            score -= 20
        if ma60_falling:
            score -= 15
        if new_low:
            score -= 15
        if bullish_align and ma60_rising:
            score += 15

        detail = {
            "ma20": round(ma20, 4),
            "ma60": round(ma60, 4),
            "bearish_align": bool(bearish_align),
            "ma60_falling": bool(ma60_falling),
            "new_low_60d": bool(new_low),
        }
        return FactorResult(self.name, clamp_score(score), detail)
