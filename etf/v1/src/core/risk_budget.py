# -*- coding: utf-8 -*-
"""风险预算加权"""

import numpy as np
import pandas as pd
from config import RISK_BUDGET


def rolling_ic(factor, forward_ret, window=4800, min_periods=100):
    df = pd.concat([factor, forward_ret], axis=1).dropna()
    if df.empty:
        return pd.Series(dtype=float)

    step = max(1, window // 20)
    vals, idx = [], []
    for i in range(window, len(df), step):
        sub = df.iloc[i - window:i]
        a, b = sub.iloc[:, 0], sub.iloc[:, 1]
        if a.std() < 1e-9 or b.std() < 1e-9:
            continue
        vals.append(a.corr(b, method="spearman"))
        idx.append(df.index[i])
    return pd.Series(vals, index=idx)


def _equal_weights(names):
    n = len(names)
    return {name: 1.0 / n for name in names}


def _ic_ir_weights(ic_series, names, decay_halflife=None):
    weights_raw = {}
    for name in names:
        s = ic_series.get(name)
        if s is None or len(s) < 20:
            weights_raw[name] = 0.0
            continue
        s = s.dropna()
        if len(s) < 20:
            weights_raw[name] = 0.0
            continue
        icir = s.mean() / (s.std() + 1e-9)
        weights_raw[name] = abs(icir)
    total = sum(weights_raw.values())
    if total < 1e-9:
        return _equal_weights(names)
    return {name: weights_raw[name] / total for name in names}


def _cap_weights(weights, min_w, max_w):
    capped = {k: max(min_w, min(max_w, v)) for k, v in weights.items()}
    total = sum(capped.values())
    if total < 1e-9:
        return weights
    return {k: v / total for k, v in capped.items()}


def compute_factor_weights(factors, pool, method=None):
    cfg = RISK_BUDGET
    method = method or cfg["weight_method"]
    names = [f["name"] for f in factors]

    if method == "equal" or len(names) < 2:
        return _equal_weights(names)

    if method in ("ic", "ic_ir", "ic_ir_capped"):
        ref_code = next(iter(pool))
        df = pool[ref_code]
        fwd_ret = df["close"].pct_change().shift(-1)
        ic_series = {}
        for f in factors:
            impl = f.get("impl", {})
            if ref_code in impl:
                ic_series[f["name"]] = rolling_ic(
                    impl[ref_code]["factor"], fwd_ret,
                    window=cfg["ic_lookback_bars"])

        w = _ic_ir_weights(ic_series, names,
                           decay_halflife=cfg["decay_halflife_bars"])
        if method == "ic_ir_capped":
            w = _cap_weights(w, cfg["min_weight_per_factor"],
                             cfg["max_weight_per_factor"])
        return w

    return _equal_weights(names)