"""风控模块：止盈止损、冷却期、仓位上限"""
from dataclasses import dataclass
from datetime import datetime

from src.config.settings import settings


@dataclass
class RiskDecision:
    """风控检查结果"""
    force_sell: bool = False       # 是否强制卖出（止盈/止损触发）
    block_buy: bool = False        # 是否禁止买入（冷却期/已持仓）
    reason: str = ""


class RiskManager:
    """风控规则检查

    规则（对应"误燥，戒贪"）：
    - 浮盈 >= 止盈线 -> 强制卖出（高抛）
    - 浮亏 <= 止损线 -> 强制卖出（截断亏损）
    - 连续亏损后进入冷却期，禁止买入
    - 每次只持有一只ETF：已持仓时禁止买入其他标的
    """

    def __init__(self):
        self.take_profit = settings.take_profit_pct
        self.stop_loss = settings.stop_loss_pct
        self.cooldown_days = settings.cooldown_days

    def check(self, position, current_price: float | None,
              today: datetime | None = None) -> RiskDecision:
        """综合风控检查

        Args:
            position: 当前持仓状态对象（空仓时为 Position.empty() 或 None，
                      其 consecutive_losses 仍保留用于冷却期判断）
            current_price: 当前价格（有持仓且为 None 时无法判断，保守不强制卖）
            today: 当前日期（用于冷却期判断）
        """
        decision = RiskDecision()
        today = today or datetime.now()
        is_holding = position is not None and not position.is_empty

        # ---- 冷却期（连续亏损记录保留在状态中，无论是否持仓都需检查）----
        if position is not None and position.consecutive_losses > 0:
            last_trade = datetime.strptime(position.last_trade_date, "%Y-%m-%d")
            days_passed = (today - last_trade).days
            if days_passed < self.cooldown_days:
                decision.block_buy = True
                decision.reason = (f"连续亏损 {position.consecutive_losses} 次，"
                                   f"冷却期还剩 {self.cooldown_days - days_passed} 天")

        # ---- 持仓检查 ----
        if not is_holding:
            return decision

        if current_price is None or current_price <= 0:
            decision.reason = (decision.reason + "；" if decision.reason else "") + "无法获取现价，跳过止盈止损判断"
            decision.block_buy = True  # 已持仓：不同标的禁止买入
            return decision

        pnl_pct = current_price / position.buy_price - 1.0

        if pnl_pct >= self.take_profit:
            decision.force_sell = True
            decision.reason = f"浮盈 {pnl_pct:.2%} 触发止盈线 {self.take_profit:.2%}，高抛兑现"
        elif pnl_pct <= self.stop_loss:
            decision.force_sell = True
            decision.reason = f"浮亏 {pnl_pct:.2%} 触发止损线 {self.stop_loss:.2%}，截断亏损"

        # 每次只持有一只ETF：已持仓时不能买入（换仓需先卖出）
        decision.block_buy = True

        return decision
