# -*- coding: utf-8 -*-
"""频率适配器：统一不同频率的参数换算

核心约定：所有统计量内部按 bar 计算，年化统一用
年化收益 = (1+R_total)^(bars_per_year/n_bars) - 1，
年化波动 = σ_bar · sqrt(bars_per_year)，夏普同理乘 sqrt(bars_per_year)。"""

from config import FREQ, FREQ_MAP, LOOKBACK_DAYS


class FrequencyAdapter:
    """频率自适应参数计算"""

    def __init__(self, freq: str = None):
        self.freq = freq or FREQ
        self.cfg = FREQ_MAP[self.freq]
        self.is_intraday = self.cfg["is_intraday"]
        self.bars_per_day = self.cfg["bars_per_day"]
        self.bars_per_year = self.cfg["bars_per_year"]

    # ---------- 换算 ----------
    def days_to_bars(self, days: int) -> int:
        """N 天 → N bar"""
        return days * self.bars_per_day

    def bars_to_days(self, bars: int) -> float:
        """N bar → N 天"""
        return bars / self.bars_per_day

    # ---------- 年化 ----------
    def annualize_return(self, total_return: float,
                         n_bars: int) -> float:
        """总收益 → 年化"""
        years = n_bars / self.bars_per_year
        if years <= 0:
            return 0.0
        return (1 + total_return) ** (1 / years) - 1

    def annualize_vol(self, bar_returns) -> float:
        """bar 级波动率 → 年化"""
        import numpy as np
        if len(bar_returns) < 2:
            return 0.0
        return float(bar_returns.std() * np.sqrt(self.bars_per_year))

    def annualize_sharpe(self, bar_sharpe: float) -> float:
        """bar 级夏普 → 年化"""
        import numpy as np
        return bar_sharpe * np.sqrt(self.bars_per_year)

    def sharpe_from_returns(self, returns) -> float:
        """从收益序列算年化夏普"""
        import numpy as np
        if len(returns) < 2:
            return 0.0
        ann_ret = returns.mean() * self.bars_per_year
        ann_vol = self.annualize_vol(returns)
        if ann_vol < 1e-9:
            return 0.0
        return float(ann_ret / ann_vol)

    # ---------- 成本 ----------
    def bars_between_trades(self, target_days: float) -> int:
        """目标交易间隔天数 → bar"""
        return max(1, int(target_days * self.bars_per_day))

    def max_trades_per_period(self, target_per_day: int,
                              period_days: int = 1) -> int:
        """每周期最多几笔"""
        return max(1, int(target_per_day * period_days))


def get_adapter(freq: str = None) -> FrequencyAdapter:
    return FrequencyAdapter(freq)