"""仓位控制因子：戒贪戒燥，控制风险

- 无持仓：中性分
- 持仓浮盈超过止盈线：低分（应高抛，戒贪）
- 持仓浮亏接近止损线：低分（防范风险）
- 持仓浮亏较大：该标的不建议继续买（减分）
"""
from typing import Any

from src.core.factors.base import BaseFactor, FactorResult, clamp_score
from src.config.settings import settings


class PositionFactor(BaseFactor):
    name = "position"
    weight = 0.20

    def calculate(self, data: dict[str, Any], context: dict[str, Any] | None = None) -> FactorResult:
        context = context or {}
        position = context.get("position")
        realtime = context.get("realtime")

        # 无持仓：中性
        if position is None:
            return FactorResult(self.name, 50, {"note": "无持仓，中性"})

        # 有持仓时，用实时价或最新日线收盘价评估浮盈
        price = None
        if realtime and realtime.get("price"):
            price = float(realtime["price"])
        else:
            daily = data.get("daily")
            if daily is not None and len(daily) > 0:
                price = float(daily["close"].iloc[-1])

        if price is None or position.buy_price <= 0:
            return FactorResult(self.name, 50, {"note": "持仓但无法获取现价，中性"}, available=False)

        pnl_pct = price / position.buy_price - 1.0
        score = 50.0

        # 浮盈 >= 止盈线：戒贪，及时兑现 -> 低分（抑制继续持有/买入）
        if pnl_pct >= settings.take_profit_pct:
            score -= 40
        elif pnl_pct >= settings.take_profit_pct * 0.6:
            score -= 20
        # 浮亏接近止损线：防风险扩大 -> 低分
        elif pnl_pct <= settings.stop_loss_pct:
            score -= 40
        elif pnl_pct <= settings.stop_loss_pct * 0.6:
            score -= 20
        # 小幅浮盈/浮亏：正常持有
        elif pnl_pct > 0:
            score += 5
        else:
            score -= 5

        detail = {
            "pnl_pct": round(pnl_pct, 4),
            "buy_price": position.buy_price,
            "hold_days": position.hold_days,
        }
        return FactorResult(self.name, clamp_score(score), detail)
