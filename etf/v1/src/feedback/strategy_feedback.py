# -*- coding: utf-8 -*-
"""策略级反馈"""

import os
import numpy as np
import pandas as pd


class StrategyFeedback:
    def __init__(self, live_attribution_path="live_data/live_attribution.csv",
                 multi_summary_path="multi_summary.csv"):
        self.live_path = live_attribution_path
        self.summary_path = multi_summary_path

    def analyze(self):
        if not os.path.exists(self.live_path):
            return {}
        live = pd.read_csv(self.live_path)
        if live.empty:
            return {}
        live["time"] = pd.to_datetime(live["time"])
        live_ret = live["total_asset"].pct_change().dropna()
        stats = {"live_return": float(
                    live["total_asset"].iloc[-1] /
                    live["total_asset"].iloc[0] - 1),
                 "live_sharpe": float(
                    live_ret.mean() / (live_ret.std() + 1e-9) *
                    np.sqrt(240 * 252))}
        if os.path.exists(self.summary_path):
            summary = pd.read_csv(self.summary_path)
            base = summary[summary["策略"].str.contains("基准", na=False)]
            if not base.empty:
                bt_sharpe = float(base.iloc[0]["夏普"])
                stats["bt_sharpe"] = bt_sharpe
                stats["sharpe_ratio"] = (stats["live_sharpe"] /
                                          (bt_sharpe + 1e-9))
                if stats["sharpe_ratio"] < 0.5:
                    stats["action"] = "retrain"
                elif stats["sharpe_ratio"] < 0.8:
                    stats["action"] = "watch"
                else:
                    stats["action"] = "keep"
        return stats