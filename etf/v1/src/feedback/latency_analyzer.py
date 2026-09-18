# -*- coding: utf-8 -*-
"""延迟分析"""

import os
import pandas as pd
import numpy as np


class LatencyAnalyzer:
    def __init__(self, live_orders_path="live_data/live_orders.csv"):
        self.path = live_orders_path

    def analyze(self):
        if not os.path.exists(self.path):
            return {}
        orders = pd.read_csv(self.path)
        orders = orders[orders["status"].isin(["FILLED", "DRY_RUN"])]
        if orders.empty:
            return {}
        orders["time"] = pd.to_datetime(orders["time"])
        orders = orders.sort_values("time")
        intervals = orders["time"].diff().dt.total_seconds().dropna()
        if intervals.empty:
            return {}
        return {"n_samples": len(intervals),
                "avg_latency_sec": float(intervals.mean()),
                "p95_latency_sec": float(intervals.quantile(0.95))}