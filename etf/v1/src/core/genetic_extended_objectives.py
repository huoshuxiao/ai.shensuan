# -*- coding: utf-8 -*-
"""扩展多目标（5 目标）"""

import numpy as np
import pandas as pd
from config import EXTENDED_OBJECTIVES as CFG
from factor_dsl import safe_eval, compute_ic


def _stability_ic_ttest(factor, fwd, window=240, step=60):
    pair = pd.concat([factor, fwd], axis=1).dropna()
    if len(pair) < window:
        return 0.0
    ics = []
    for i in range(window, len(pair), step):
        sub = pair.iloc[i - window:i]
        a, b = sub.iloc[:, 0], sub.iloc[:, 1]
        if a.std() < 1e-9 or b.std() < 1e-9:
            continue
        ics.append(a.corr(b, method="spearman"))
    if len(ics) < 5:
        return 0.0
    ics = np.array(ics)
    if ics.std() < 1e-9:
        return 0.0
    t = abs(ics.mean()) / (ics.std() / np.sqrt(len(ics)))
    return min(1.0, t / 5.0)


def _simplicity_depth(expr_str):
    depth, max_depth = 0, 0
    for ch in expr_str:
        if ch == "(":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == ")":
            depth -= 1
    if max_depth <= 1:
        return 1.0
    return max(0.0, 1.0 - (max_depth - 1)
               / CFG["max_expr_depth_for_simplicity"])


def evaluate_extended(expr_str, pool, existing_factors=None, ref_code=None):
    ics, impl, turnover_vals, stability_vals = [], {}, [], []
    factor_series_dict = {}
    for code, df in pool.items():
        try:
            f = safe_eval(expr_str, df)
            fr = df["close"].pct_change().shift(-1)
            ic = compute_ic(f, fr)
            if np.isnan(ic):
                continue
            ics.append(ic)
            impl[code] = {"factor": f, "ic": ic}
            factor_series_dict[code] = f
            turnover_vals.append(f.diff().abs().mean())
            if code == ref_code:
                stability_vals.append(_stability_ic_ttest(f, fr))
        except Exception:
            continue

    if not ics:
        return {"ic": 0.0, "abs_ic": 0.0, "turnover": 1e9,
                "max_corr": 1.0, "stability": 0.0,
                "simplicity": 0.0, "impl": {}, "expr": expr_str}

    mean_ic = float(np.mean(ics))
    turnover = float(np.mean(turnover_vals)) if turnover_vals else 1e9
    stability = float(np.mean(stability_vals)) if stability_vals else 0.0
    simplicity = _simplicity_depth(expr_str)

    max_corr = 0.0
    if existing_factors and ref_code in factor_series_dict:
        new_s = factor_series_dict[ref_code]
        for ef in existing_factors:
            if ref_code not in ef.get("impl", {}):
                continue
            old = ef["impl"][ref_code]["factor"]
            pair = pd.concat([new_s, old], axis=1).dropna()
            if len(pair) < 30:
                continue
            corr = abs(pair.iloc[:, 0].corr(pair.iloc[:, 1],
                                             method="spearman"))
            max_corr = max(max_corr, corr)

    return {"ic": mean_ic, "abs_ic": abs(mean_ic),
            "turnover": turnover, "max_corr": max_corr,
            "stability": stability, "simplicity": simplicity,
            "impl": impl, "expr": expr_str}


def weighted_score(metrics):
    obj = CFG["objectives"]
    score = 0.0
    score += obj["ic"]["weight"] * min(1.0, metrics["abs_ic"] / 0.05)
    score += obj["low_turnover"]["weight"] * max(
        0, 1 - metrics["turnover"] / 0.5)
    score += obj["low_corr"]["weight"] * max(0, 1 - metrics["max_corr"])
    score += obj["stability"]["weight"] * metrics["stability"]
    score += obj["simplicity"]["weight"] * metrics["simplicity"]
    return score