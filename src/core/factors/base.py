"""因子基类：统一因子接口与得分约束"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src.config.settings import settings


@dataclass
class FactorResult:
    """单因子计算结果"""
    name: str
    score: float                      # 0-100，越高越值得买入
    detail: dict = field(default_factory=dict)   # 因子明细，用于输出理由
    available: bool = True            # 数据不足时为 False，评分时按中性处理


def clamp_score(score: float) -> float:
    """得分约束在 0-100"""
    lo, hi = settings.score_range
    return float(np.clip(score, lo, hi))


class BaseFactor(ABC):
    """因子抽象基类

    约定：
    - calculate(data, context) -> FactorResult
    - data: {"daily": DataFrame, "index_daily": DataFrame}
    - context: {"position": Position | None, "realtime": dict | None}
    - 得分语义统一：越高越值得买入（正向因子化）
    """

    name: str = "base"
    weight: float = 0.0

    @abstractmethod
    def calculate(self, data: dict[str, Any], context: dict[str, Any] | None = None) -> FactorResult:
        """计算因子得分"""

    @staticmethod
    def _rsi(close: pd.Series, period: int = 6) -> pd.Series:
        """RSI 指标（纯涨 -> 100，纯跌 -> 0，横盘 -> 50）"""
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - 100 / (1 + rs)
        rsi = rsi.mask((loss == 0) & (gain > 0), 100.0)  # 无下跌（全涨）-> 100
        rsi = rsi.mask((loss == 0) & (gain == 0), 50.0)  # 横盘 -> 50
        return rsi.fillna(50)

    @staticmethod
    def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
        """MACD：返回 (dif, dea, hist)，金叉时 hist 由负转正"""
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        hist = (dif - dea) * 2
        return dif, dea, hist

    @staticmethod
    def _kdj(df: pd.DataFrame, n: int = 9):
        """KDJ：返回 (k, d, j)"""
        low_n = df["low"].rolling(n, min_periods=1).min()
        high_n = df["high"].rolling(n, min_periods=1).max()
        rsv = (df["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
        rsv = rsv.fillna(50)
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        j = 3 * k - 2 * d
        return k, d, j
