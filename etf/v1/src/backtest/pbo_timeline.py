# -*- coding: utf-8 -*-
"""PBO 时间演化"""

import numpy as np
import pandas as pd
from config import PBO_TIMELINE
from pbo import cscv_pbo


def compute_pbo_timeline(equity_dict, window_bars=None,
                         step_bars=None, n_splits=None,
                         max_combinations=None):
    cfg = PBO_TIMELINE
    window_bars = window_bars or cfg["window_bars"]
    step_bars = step_bars or cfg["step_bars"]
    n_splits = n_splits or cfg["n_splits"]
    max_combinations = max_combinations or cfg["max_combinations"]
    if not equity_dict:
        return pd.DataFrame()
    eq_df = pd.DataFrame(equity_dict).sort_index().ffill().dropna(how="all")
    rows = []
    n = len(eq_df)
    for start in range(0, n - window_bars + 1, step_bars):
        end = start + window_bars
        sub = eq_df.iloc[start:end]
        rets = sub.pct_change().dropna()
        if len(rets) < 50 or sub.shape[1] < 3:
            continue
        try:
            r = cscv_pbo(rets, n_splits=n_splits,
                         max_combinations=max_combinations)
            rows.append({"datetime": sub.index[-1],
                         "pbo": r.get("pbo", 1.0),
                         "n_configs": sub.shape[1]})
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("datetime")


def detect_trend(pbo_df, trend_window=None, trend_threshold=None):
    cfg = PBO_TIMELINE
    trend_window = trend_window or cfg["trend_window"]
    trend_threshold = trend_threshold or cfg["trend_threshold"]
    if pbo_df.empty or len(pbo_df) < trend_window * 2:
        return {"trend": "unknown", "is_rising": False,
                "is_falling": False, "delta": 0.0,
                "current": 0.0, "alert": "unknown"}
    recent = pbo_df["pbo"].tail(trend_window).mean()
    prev = pbo_df["pbo"].iloc[-2 * trend_window:-trend_window].mean()
    delta = recent - prev
    current = float(pbo_df["pbo"].iloc[-1])
    if delta > trend_threshold:
        trend = "up"
    elif delta < -trend_threshold:
        trend = "down"
    else:
        trend = "stable"
    if current >= cfg["pbo_danger"]:
        alert = "danger"
    elif current >= cfg["pbo_warn"]:
        alert = "warn"
    else:
        alert = "ok"
    return {"is_rising": delta > trend_threshold,
            "is_falling": delta < -trend_threshold,
            "delta": float(delta), "current": current,
            "trend": trend, "alert": alert}