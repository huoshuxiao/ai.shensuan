# -*- coding: utf-8 -*-
"""策略：参数按频率自适应

截面组合：每根调仓 bar 对全部可交易 ETF 打分
（各因子时间序列 z-score 的加权和），取分数最高且 > min_score 的前 top_k 只，
按 PORTFOLIO["weighting"] 给出目标权重。"""

import pandas as pd
import numpy as np
from config import LOOKBACK_BARS, FREQ, PORTFOLIO
from frequency_adapter import get_adapter
from data_loader import PointInTimeData


def select_weights(scores, cfg=None):
    """分数 → 目标权重（占组合净值比例，Σw = 1）。

    1) 绝对过滤：score <= min_score 的一律剔除（全被剔除即空仓）；
    2) 取前 top_k；
    3) equal: w_i = 1/k；score_prop: w_i = s_i / Σ s（s 已 > 0）；
    4) 单标的上限 max_weight：超出部分按其余权重的比例回摊，
       迭代到无标的越限（water-filling）。

    研究侧回测与实盘侧下单共用此函数，权重口径不会两处漂移。"""
    cfg = cfg or PORTFOLIO
    pos = {c: float(s) for c, s in scores.items()
           if np.isfinite(s) and s > cfg["min_score"]}
    if not pos:
        return {}
    top = sorted(pos, key=lambda c: -pos[c])[:cfg["top_k"]]
    if cfg["weighting"] == "score_prop":
        tot = sum(pos[c] for c in top)
        w = {c: pos[c] / tot for c in top} if tot > 0 else \
            {c: 1.0 / len(top) for c in top}
    else:
        w = {c: 1.0 / len(top) for c in top}
    cap = cfg.get("max_weight")
    if cap and len(top) * cap < 1.0:
        for _ in range(len(top)):
            over = {c: w[c] - cap for c in w if w[c] > cap + 1e-12}
            if not over:
                break
            excess = sum(over.values())
            under = {c: w[c] for c in w if c not in over}
            base = sum(under.values())
            for c in over:
                w[c] = cap
            if base <= 0:
                break
            for c in under:
                w[c] += excess * w[c] / base
    return w


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
        """逐 bar 生成目标持仓信号（**长表契约**）。

        返回 index=时间戳、columns=[code, weight, score] 的 DataFrame，
        **只在调仓 bar 出行**；两个调仓日之间的权重由回测引擎沿用上次的
        目标权重（持有不操作）。全部标的都被绝对过滤剔除的那根 bar 出一行
        `code=""` 的空仓哨兵（引擎靠它定位时间轴，见 portfolio_engine）。"""
        rebalance_every = rebalance_every or self.default_rebalance
        records = []
        for i, ts in enumerate(timestamps):
            if i % rebalance_every:
                continue
            tradable = (self.universe.get_tradable_at(ts)
                        if self.universe else list(self.pool.keys()))
            scores = {c: s for c in tradable
                      if (s := self._score_one(c, ts)) is not None
                      and not np.isnan(s)}
            weights = select_weights(scores)
            if not weights:
                records.append({"datetime": ts, "code": "",
                                "weight": 0.0, "score": np.nan})
            for code, wt in weights.items():
                records.append({"datetime": ts, "code": code,
                                "weight": wt, "score": scores[code]})
        if not records:
            return pd.DataFrame(columns=["code", "weight", "score"]) \
                .rename_axis("datetime")
        return pd.DataFrame(records).set_index("datetime")
