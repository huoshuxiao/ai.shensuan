# -*- coding: utf-8 -*-
"""换手分析"""

import os
import pandas as pd
from config import LIVE_DATA_DIR, RESULTS_DIR


class TurnoverAnalyzer:
    """实盘 vs 回测的下单频率对比：order_ratio =
    实盘日均订单数 / 回测日均订单数（>1 实盘更频繁，成本被低估）"""

    def __init__(self, live_orders_path=f"{LIVE_DATA_DIR}/live_orders.csv",
                 backtest_trades_path=f"{RESULTS_DIR}/trades.csv"):
        self.live_path = live_orders_path
        self.bt_path = backtest_trades_path

    def analyze(self):
        """只统计已成交/干跑单（FILLED/DRY_RUN），撤单废单不计换手"""
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
            # 日线回测的成交表用 date、分钟线用 datetime，
            # 写死任一个都会让另一种频率下整条反馈链 KeyError
            ts_col = next((c for c in ("datetime", "date")
                           if c in bt.columns), None)
            if not bt.empty and ts_col:
                bt["date"] = pd.to_datetime(bt[ts_col]).dt.date
                stats["bt_avg_daily_orders"] = float(
                    bt.groupby("date").size().mean())
                if stats["bt_avg_daily_orders"] > 0:
                    stats["order_ratio"] = (stats["live_avg_daily_orders"] /
                                            stats["bt_avg_daily_orders"])
        return stats