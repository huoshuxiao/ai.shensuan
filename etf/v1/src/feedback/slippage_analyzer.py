# -*- coding: utf-8 -*-
"""滑点分析"""

import os
import numpy as np
import pandas as pd


class SlippageAnalyzer:
    def __init__(self, live_orders_path="live_data/live_orders.csv",
                 backtest_trades_path="trades.csv"):
        self.live_path = live_orders_path
        self.bt_path = backtest_trades_path
        self.df = None

    def load(self):
        if not os.path.exists(self.live_path):
            return pd.DataFrame()
        live = pd.read_csv(self.live_path)
        live = live[live["status"].isin(["FILLED", "DRY_RUN"])]
        if live.empty:
            return pd.DataFrame()
        bt = pd.DataFrame()
        if os.path.exists(self.bt_path):
            bt = pd.read_csv(self.bt_path)
        rows = []
        for _, lo in live.iterrows():
            if lo["side"] not in ("BUY", "SELL"):
                continue
            bt_price = None
            if not bt.empty:
                mask = (bt["code"] == lo["code"]) & \
                       (bt["action"] == lo["side"])
                if mask.any():
                    bt_price = bt[mask].iloc[-1]["price"]
            if bt_price is None or bt_price <= 0:
                continue
            if lo["side"] == "BUY":
                slip = (lo["price"] - bt_price) / bt_price
            else:
                slip = (bt_price - lo["price"]) / bt_price
            rows.append({"time": lo["time"], "code": lo["code"],
                         "side": lo["side"], "live_price": float(lo["price"]),
                         "bt_price": float(bt_price), "slippage": slip,
                         "amount": float(lo["amount"])})
        self.df = pd.DataFrame(rows)
        return self.df

    def analyze(self):
        if self.df is None or self.df.empty:
            return {}
        df = self.df
        stats = {
            "n_trades": len(df),
            "avg_slippage": float(df["slippage"].mean()),
            "p95_slippage": float(df["slippage"].quantile(0.95)),
            "suggested_slippage_conservative": float(
                abs(df["slippage"]).quantile(0.95))}
        return stats