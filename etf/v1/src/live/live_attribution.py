# -*- coding: utf-8 -*-
"""实盘归因"""

import os
import json
import numpy as np
import pandas as pd
from datetime import datetime
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
        snapshot = {
            "time": now.isoformat(),
            "total_asset": account.total_asset,
            "cash": account.cash,
            "market_value": account.market_value,
            "pnl": account.pnl, "pnl_pct": account.pnl_pct,
            "n_positions": len(positions),
            "position_codes": ",".join(p.code for p in positions),
            "position_mv": sum(p.market_value for p in positions),
            "target_code": target.get("target_code", ""),
            "target_match": (positions[0].code == target.get("target_code")
                             if positions and target.get("target_code")
                             else False)}
        self.snapshots.append(snapshot)

    def save(self):
        if not self.snapshots:
            return
        df = pd.DataFrame(self.snapshots)
        path = f"{STORAGE['dir']}/{STORAGE['attribution']}"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  💾 归因已保存: {path}")


def decompose_live_vs_backtest(live_orders_path, backtest_trades_path):
    if not os.path.exists(live_orders_path) or \
            not os.path.exists(backtest_trades_path):
        return pd.DataFrame()
    live = pd.read_csv(live_orders_path)
    bt = pd.read_csv(backtest_trades_path)
    rows = []
    for _, lo in live.iterrows():
        if lo.get("status") not in ("FILLED", "DRY_RUN"):
            continue
        match = bt[(bt["code"] == lo["code"]) &
                   (bt["action"] == lo["side"])]
        if match.empty:
            continue
        bt_price = match.iloc[0]["price"]
        slip = (lo["price"] - bt_price) / bt_price
        rows.append({"time": lo["time"], "code": lo["code"],
                     "side": lo["side"], "live_price": lo["price"],
                     "bt_price": bt_price, "slippage": slip,
                     "amount": lo["amount"]})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.to_csv(f"{STORAGE['dir']}/live_slippage.csv",
              index=False, encoding="utf-8-sig")
    return df