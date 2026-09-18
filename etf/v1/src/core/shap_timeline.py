# -*- coding: utf-8 -*-
"""SHAP 时间序列"""

import os
import numpy as np
import pandas as pd
from datetime import datetime
from config import DECAY_EXPLAIN
from factor_decay_predict import extract_ic_series
from decay_explain import build_features, shap_explain


def compute_shap_at(factor, pool, timestamp=None):
    ic_series = extract_ic_series(factor, pool)
    if len(ic_series) < 20:
        return {}
    X, y, names = build_features(
        ic_series, DECAY_EXPLAIN["n_lags"],
        DECAY_EXPLAIN["extra_features"], pool, next(iter(pool)))
    if X is None:
        return {}
    shap_result = shap_explain(X, y, names)
    imp = shap_result["importance"]
    return {"timestamp": timestamp or datetime.now().strftime(
                "%Y-%m-%d %H:%M"),
            "importance": {row["feature"]: row["importance"]
                           for _, row in imp.iterrows()}}


class SHAPTimelineTracker:
    def __init__(self, path="shap_timeline.csv"):
        self.path = path
        self.records = self._load()

    def _load(self):
        if os.path.exists(self.path):
            return pd.read_csv(self.path).to_dict("records")
        return []

    def track(self, factors, pool):
        print("\n========== SHAP 时间序列追踪 ==========")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        added = 0
        for f in factors:
            result = compute_shap_at(f, pool, now)
            if not result:
                continue
            for feat, imp in result["importance"].items():
                self.records.append({"timestamp": now,
                                     "factor": f["name"],
                                     "feature": feat,
                                     "importance": float(imp)})
            added += 1
        print(f"  ✅ 追踪 {added} 个因子 @ {now}")
        if self.records:
            pd.DataFrame(self.records).to_csv(
                self.path, index=False, encoding="utf-8-sig")
        return added

    def get_timeline(self, factor, feature=None):
        df = pd.DataFrame(self.records)
        if df.empty:
            return pd.DataFrame()
        sub = df[df["factor"] == factor]
        if feature:
            sub = sub[sub["feature"] == feature]
        return sub

    def analyze_trend(self, factor, top_n=5):
        df = self.get_timeline(factor)
        if df.empty:
            return pd.DataFrame()
        rows = []
        for feat, g in df.groupby("feature"):
            g = g.sort_values("timestamp")
            vals = g["importance"].values
            if len(vals) < 3:
                continue
            x = np.arange(len(vals))
            slope = np.polyfit(x, vals, 1)[0]
            change = vals[-1] - vals[0]
            rel = change / (vals[0] + 1e-9)
            if rel > 0.2:
                trend = "rising"
            elif rel < -0.2:
                trend = "falling"
            else:
                trend = "stable"
            rows.append({"feature": feat, "current": float(vals[-1]),
                         "change": float(change),
                         "rel_change": float(rel),
                         "slope": float(slope), "trend": trend})
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).sort_values(
            "current", ascending=False).head(top_n * 2)


def analyze_all_shap_trends(factors):
    tracker = SHAPTimelineTracker()
    rows = []
    for f in factors:
        trend = tracker.analyze_trend(f["name"])
        if trend.empty:
            continue
        rising = trend[trend["trend"] == "rising"]
        falling = trend[trend["trend"] == "falling"]
        rows.append({"factor": f["name"],
                     "top_feature": trend.iloc[0]["feature"],
                     "top_importance": trend.iloc[0]["current"],
                     "n_rising": len(rising),
                     "n_falling": len(falling)})
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv("shap_trends.csv", index=False,
                  encoding="utf-8-sig")
    return df