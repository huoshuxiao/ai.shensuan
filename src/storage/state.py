"""持仓状态持久化：JSON 文件 + 文件锁，保证盘中/盘后互不冲突"""
import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from src.config.settings import STATE_FILE

logger = logging.getLogger(__name__)

_lock = threading.Lock()


@dataclass
class Position:
    """当前持仓（每次至多一只ETF）"""
    code: str = ""                  # ETF代码
    name: str = ""                  # ETF名称
    buy_price: float = 0.0          # 买入价
    quantity: int = 0               # 持有份额
    buy_date: str = ""              # 买入日期 YYYY-MM-DD
    hold_days: int = 0              # 已持有交易日数
    last_trade_date: str = ""       # 最近一次交易日期（卖出/买入）
    consecutive_losses: int = 0     # 连续亏损次数（触发冷却期）
    realized_pnl: float = 0.0       # 已实现累计盈亏（金额）

    @property
    def is_empty(self) -> bool:
        return self.code == ""

    @classmethod
    def empty(cls) -> "Position":
        return cls()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(**{k: d.get(k, 0) for k in cls.__dataclass_fields__})


class StateStore:
    """持仓状态读写（JSON 持久化）"""

    def __init__(self, path: Path = STATE_FILE):
        self.path = Path(path)

    def load(self) -> Position:
        """读取持仓状态；文件不存在/损坏时返回空仓"""
        with _lock:
            try:
                if not self.path.exists():
                    return Position.empty()
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return Position.from_dict(data)
            except (json.JSONDecodeError, OSError, TypeError) as err:
                logger.warning("持仓状态文件损坏，按空仓处理: %s", err)
                return Position.empty()

    def save(self, position: Position) -> bool:
        """保存持仓状态（原子写入：先写临时文件再替换）"""
        with _lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(position.to_dict(), f, ensure_ascii=False, indent=2)
                tmp.replace(self.path)
                return True
            except OSError as err:
                logger.error("持仓状态保存失败: %s", err)
                return False


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")
