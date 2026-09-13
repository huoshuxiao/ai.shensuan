"""输出数据模型：交易信号与日报"""
from dataclasses import dataclass, field
from typing import Any

# 信号类型
SIGNAL_BUY = "BUY"      # 买入
SIGNAL_SELL = "SELL"    # 卖出
SIGNAL_HOLD = "HOLD"    # 持有
SIGNAL_EMPTY = "EMPTY"  # 空仓


@dataclass
class TradeSignal:
    """一次运行输出的交易信号"""
    mode: str                       # pre / intra / post
    signal: str                     # BUY/SELL/HOLD/EMPTY
    code: str = ""                  # 标的代码（空仓时为空）
    name: str = ""                  # 标的名称
    price: float | None = None      # 参考价格
    score: float | None = None      # 综合评分
    reason: str = ""                # 信号理由
    detail: dict[str, Any] = field(default_factory=dict)  # 因子明细
    generated_at: str = ""          # 生成时间


@dataclass
class DailyReport:
    """日报（盘后总结）"""
    date: str
    mode: str = "post"
    signal: TradeSignal | None = None
    position: dict[str, Any] = field(default_factory=dict)
    realized_pnl: float = 0.0
    notes: list[str] = field(default_factory=list)
    ranking: list[dict[str, Any]] = field(default_factory=list)
