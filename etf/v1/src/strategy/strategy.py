# -*- coding: utf-8 -*-
"""策略：参数按频率自适应"""

import pandas as pd
import numpy as np
from config import LOOKBACK_BARS, FREQ
from frequency_adapter import get_adapter


class IntradayRotationStrategy:
    def __init__(self, factors, pool, universe,
                 lookback=None, factor_weights=None):
        self.factors = factors
        self.pool = pool
        self.universe = universe
        self.adapter = get_adapter()
        self.lookback = lookback or LOOKBACK_BARS
        self.factor_weights = factor_weights
        # 根据频率自动调换仓频率
        self.default_rebalance = max(1, self.adapter.bars_per_day // 8) \
            if self.adapter.is_intraday else 1

    def _get_weight(self, f):
        if self.factor_weights is not None:
            return abs(self.factor_weights.get(f["name"], 0.0))
        return abs(f.get("mean_ic", f.get("ic", 0.01)))

    def _score_one(self, code, ts):
        if code not in self.pool:
            return np.nan
        df = self.pool[code].loc[:ts].tail(self.lookback)
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
            if sd < 1e-9 or np.isnan(sd):
                continue
            w = self._get_weight(f)
            score += w * ((sub.iloc[-1] - mu) / sd)
            w_sum += w
        return score / w_sum if w_sum > 0 else np.nan

    def generate_signals(self, timestamps, rebalance_every=None):
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