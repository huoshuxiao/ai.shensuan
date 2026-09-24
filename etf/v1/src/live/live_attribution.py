# -*- coding: utf-8 -*-
"""实盘归因"""

import os
import json
import numpy as np
import pandas as pd
from datetime import datetime
from config import RESULTS_DIR
from config_live import LIVE_ATTRIBUTION, STORAGE


class LiveAttributionTracker:
    def __init__(self):
        self.p = LIVE_ATTRIBUTION
        self.snapshots = []
        self.last_snapshot_time = None
        self.save_dir = self.p["save_dir"]
        os.makedirs(self.save_dir, exist_ok=True)

    def track(self, account, positions, target, prices):
        if not self.p["enabled"]:
            return
        now = datetime.now()
        if self.last_snapshot_time:
            elapsed = (now - self.last_snapshot_time).total_seconds() / 60
            if elapsed < self.p["snapshot_interval_minutes"]:
                return
        self.last_snapshot_time = now
        tgt = set((target or {}).get("targets") or {})
        held = {p.code for p in positions}
        snapshot = {
            "time": now.isoformat(),
            "total_asset": account.total_asset,
            "cash": account.cash,
            "market_value": account.market_value,
            "pnl": account.pnl, "pnl_pct": account.pnl_pct,
            "n_positions": len(positions),
            "position_codes": ",".join(p.code for p in positions),
            "position_mv": sum(p.market_value for p in positions),
            "target_codes": ",".join(sorted(tgt)),
            # 持仓与目标组合的 Jaccard 重合度（两边都空仓 = 1.0 已对齐）
            "target_overlap": round(len(held & tgt) / len(held | tgt), 4)
                              if (held or tgt) else 1.0}
        self.snapshots.append(snapshot)

    def save(self):
        if not self.snapshots:
            return
        df = pd.DataFrame(self.snapshots)
        path = f"{STORAGE['dir']}/{STORAGE['attribution']}"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  💾 归因已保存: {path}")


def decompose_live_vs_backtest(
        live_orders_path=f"{STORAGE['dir']}/{STORAGE['orders']}",
        backtest_trades_path=f"{RESULTS_DIR}/trades.csv"):
    """实盘成交 vs 回测成交的逐笔分解（按时间最近配对）。

    与 SlippageAnalyzer 的分工：后者以"同向最后一笔回测价"为基准算执行
    价差，本函数以时间上最近的一笔回测成交为基准并给出配对时滞
    lag_days（正=实盘晚于回测），用来区分分歧来自成交价还是成交时点。

    价差口径与滑点分析一致（正数一律代表执行劣于回测假设）：
      BUY  slip = (实盘价 - 回测价) / 回测价     买贵为正
      SELL slip = (回测价 - 实盘价) / 回测价     卖贱为正
    明细落盘 live_vs_backtest.csv；无实盘记录或无回测成交时返回空表。"""
    if not os.path.exists(live_orders_path) or \
            not os.path.exists(backtest_trades_path):
        return pd.DataFrame()
    live = pd.read_csv(live_orders_path)
    live = live[live["status"].isin(["FILLED", "DRY_RUN"])].copy()
    bt = pd.read_csv(backtest_trades_path)
    if live.empty or bt.empty:
        return pd.DataFrame()
    live["t"] = pd.to_datetime(live["time"], errors="coerce")
    # 日线成交表的时间列是 date、分钟线是 datetime
    ts_col = next((c for c in ("datetime", "date") if c in bt.columns), None)
    if ts_col is None:
        return pd.DataFrame()
    bt["t"] = pd.to_datetime(bt[ts_col], errors="coerce")
    rows = []
    for _, lo in live.iterrows():
        if lo.get("side") not in ("BUY", "SELL") or pd.isna(lo["t"]):
            continue
        cand = bt[(bt["code"] == lo["code"]) & (bt["action"] == lo["side"])]
        cand = cand.dropna(subset=["t"])
        if cand.empty:
            continue
        b = bt.loc[(cand["t"] - lo["t"]).abs().idxmin()]
        bt_price = float(b["price"])
        if bt_price <= 0:
            continue
        # 正数=执行更差：买贵 / 卖贱
        slip = ((lo["price"] - bt_price) / bt_price) if lo["side"] == "BUY" \
            else ((bt_price - lo["price"]) / bt_price)
        rows.append({"time": lo["time"], "code": lo["code"],
                     "side": lo["side"], "live_price": float(lo["price"]),
                     "bt_price": bt_price, "slippage": float(slip),
                     "amount": float(lo["amount"]),
                     "bt_time": str(b["t"].date()),
                     "lag_days": float(
                         (lo["t"] - b["t"]).total_seconds() / 86400)})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    path = f"{STORAGE['dir']}/live_vs_backtest.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  💾 实盘 vs 回测逐笔分解: {len(df)} 笔配对 → {path}")
    return df
