# -*- coding: utf-8 -*-
"""滑点分析"""

import os
import numpy as np
import pandas as pd
from config import LIVE_DATA_DIR, RESULTS_DIR


class SlippageAnalyzer:
    """实盘成交价 vs 回测假设价的偏离（真实执行成本）：
    BUY  slip = (实盘价 - 回测价) / 回测价   （买贵为正）
    SELL slip = (回测价 - 实盘价) / 回测价   （卖贱为正）"""

    def __init__(self, live_orders_path=f"{LIVE_DATA_DIR}/live_orders.csv",
                 backtest_trades_path=f"{RESULTS_DIR}/trades.csv"):
        self.live_path = live_orders_path
        self.bt_path = backtest_trades_path
        self.df = None

    def load(self):
        """按 (code, side) 用回测最近一笔价格做基准配对，
        明细落盘 slippage_detail.csv 供 L2/看板复算"""
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
        if not self.df.empty:
            self.df.to_csv(f"{LIVE_DATA_DIR}/slippage_detail.csv",
                           index=False, encoding="utf-8-sig")
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