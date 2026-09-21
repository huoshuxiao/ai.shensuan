# -*- coding: utf-8 -*-
"""策略：参数按频率自适应

单一持仓轮动：每根 bar 对全部可交易 ETF 打分
（各因子时间序列 z-score 的加权和），持有得分最高且 > 0 的标的。"""

import pandas as pd
import numpy as np
from config import LOOKBACK_BARS, FREQ
from frequency_adapter import get_adapter
from data_loader import PointInTimeData


class IntradayRotationStrategy:
    """截面轮动策略。factors 为因子结构列表
    [{name, mean_ic, impl: {code: {factor: Series}}}]，
    factor_weights 为 None 时退化为按 |IC| 加权。"""

    def __init__(self, factors, pool, universe,
                 lookback=None, factor_weights=None):
        self.factors = factors
        self.pool = pool
        self.universe = universe
        self.adapter = get_adapter()
        self.lookback = lookback or LOOKBACK_BARS
        self.factor_weights = factor_weights
        # 打分取行情窗口统一走时点访问器（只允许看到 ts 及之前的 bar）
        self.pit = PointInTimeData(pool)
        # 根据频率自动调换仓频率
        # 分钟线：每 1/8 个交易日调一次；日线：每根 bar（每天）
        self.default_rebalance = max(1, self.adapter.bars_per_day // 8) \
            if self.adapter.is_intraday else 1

    def _get_weight(self, f):
        """因子权重：优先用外部传入的风险预算权重，否则 |IC| 代理"""
        if self.factor_weights is not None:
            return abs(self.factor_weights.get(f["name"], 0.0))
        return abs(f.get("mean_ic", f.get("ic", 0.01)))

    def _score_one(self, code, ts):
        """单只 ETF 在 ts 时刻的综合打分：
        score = Σᵢ wᵢ · (fᵢ,t - mean(fᵢ, 回看窗口)) / std(fᵢ, 回看窗口) / Σ wᵢ
        即每个因子先做自身时间序列 z-score（消除量纲），再加权平均。
        只用 ts 及以前的数据（PointInTimeData 统一截断），无未来函数。"""
        df = self.pit.get_history(code, ts, self.lookback)
        if len(df) < max(20, self.lookback // 4):
            return np.nan
        score, w_sum = 0.0, 0.0
        for f in self.factors:
            impl = f.get("impl", {})
            if code not in impl:
                continue
            sub = impl[code]["factor"].loc[:ts].tail(self.lookback)
            if len(sub.dropna()) < 10:
                continue
            mu, sd = sub.mean(), sub.std()
            if sd < 1e-9 or np.isnan(sd):   # 常量因子无区分度
                continue
            w = self._get_weight(f)
            score += w * ((sub.iloc[-1] - mu) / sd)
            w_sum += w
        return score / w_sum if w_sum > 0 else np.nan

    def generate_signals(self, timestamps, rebalance_every=None):
        """逐 bar 生成目标持仓信号。每 rebalance_every 根 bar 重新
        选一次最优标的，其余 bar 沿用上次结果（降低换手）。
        最高分 <= 0 时空仓（绝对收益过滤，而非必然满仓轮动）。"""
        rebalance_every = rebalance_every or self.default_rebalance
        records = []
        last_target = ""
        for i, ts in enumerate(timestamps):
            if i % rebalance_every == 0:
                tradable = (self.universe.get_tradable_at(ts)
                            if self.universe else list(self.pool.keys()))
                best_code, best_score = "", -np.inf
                for code in tradable:
                    s = self._score_one(code, ts)
                    if not np.isnan(s) and s > best_score:
                        best_score = s
                        best_code = code
                last_target = best_code if best_score > 0 else ""
            records.append({"datetime": ts,
                            "target_code": last_target,
                            "bar_idx": i})
        return pd.DataFrame(records).set_index("datetime")