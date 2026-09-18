# -*- coding: utf-8 -*-
"""换手分析"""

import os
import pandas as pd


class TurnoverAnalyzer:
    def __init__(self, live_orders_path="live_data/live_orders.csv",
                 backtest_trades_path="trades.csv"):
        self.live_path = live_orders_path
        self.bt_path = backtest_trades_path

    def analyze(self):
        if not os.path.exists(self.live_path):
            return {}
        live = pd.read_csv(self.live_path)
        live = live[live["status"].isin(["FILLED", "DRY_RUN"])]
        if live.empty:
            return {}
        live["time"] = pd.to_datetime(live["time"])
        live["date"] = live["time"].dt.date
        daily = live.groupby("date")["amount"].sum()
        stats = {"live_total_orders": len(live),
                 "live_days": len(daily),
                 "live_avg_daily_orders": float(
                     live.groupby("date").size().mean())}
        if os.path.exists(self.bt_path):
            bt = pd.read_csv(self.bt_path)
            if not bt.empty:
                bt["datetime"] = pd.to_datetime(bt["datetime"])
                bt["date"] = bt["datetime"].dt.date
                stats["bt_avg_daily_orders"] = float(
                    bt.groupby("date").size().mean())
                if stats["bt_avg_daily_orders"] > 0:
                    stats["order_ratio"] = (stats["live_avg_daily_orders"] /
                                            stats["bt_avg_daily_orders"])
        return stats