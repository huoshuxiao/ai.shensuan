# -*- coding: utf-8 -*-
"""Walk-forward 验证"""

import numpy as np
import pandas as pd
from config import WALK_FORWARD, DSR, PBO as PBO_CFG
from dsr import deflated_sharpe_ratio, annualize_sharpe, TrialCounter
from pbo import cscv_pbo, build_returns_matrix


def make_splits(timestamps):
    n = len(timestamps)
    n_splits = WALK_FORWARD["n_splits"]
    train_ratio = WALK_FORWARD["train_ratio"]
    embargo = WALK_FORWARD["embargo_bars"]
    window = n // n_splits
    splits = []
    for i in range(n_splits):
        start = i * window
        end = start + window if i < n_splits - 1 else n
        train_end = start + int((end - start) * train_ratio)
        test_start = min(train_end + embargo, end - 1)
        if test_start >= end - 1:
            continue
        splits.append((timestamps[start], timestamps[train_end - 1],
                       timestamps[test_start], timestamps[end - 1]))
    return splits


def _dsr_from_equity(equity, n_trials):
    rets = equity.pct_change().dropna().values
    if len(rets) < 30:
        return {"dsr": 0.0, "passed": False}
    r = deflated_sharpe_ratio(rets, n_trials=n_trials)
    if "sr_observed" in r:
        r["sharpe_annual"] = annualize_sharpe(r["sr_observed"])
    return r


def walk_forward_run(pool, universe, factor_fn, backtest_fn,
                     trial_counter=None):
    print("\n========== Walk-forward + DSR + PBO ==========")
    ref_code = next(iter(pool))
    all_ts = pool[ref_code].index
    splits = make_splits(all_ts)
    tc = trial_counter or TrialCounter()
    all_stats, all_dsr, fold_equities = [], [], {}

    for i, (tr_s, tr_e, te_s, te_e) in enumerate(splits):
        print(f"\n--- 折 {i + 1}/{len(splits)} ---")
        train_pool = {c: df.loc[tr_s:tr_e] for c, df in pool.items()}
        train_pool = {c: df for c, df in train_pool.items()
                      if len(df) > 240}
        if not train_pool:
            continue
        factors = factor_fn(train_pool,
                            train_pool[next(iter(train_pool))].index)
        if not factors:
            continue
        tc.add(len(factors))

        from strategy import IntradayRotationStrategy
        test_ts = all_ts[(all_ts >= te_s) & (all_ts <= te_e)]
        strategy = IntradayRotationStrategy(factors, pool, universe)
        signals = strategy.generate_signals(test_ts)
        result = backtest_fn(pool, signals, universe, None)
        stats = result["stats"]
        equity = result["equity"]["equity"]
        fold_equities[f"fold_{i+1}"] = equity
        dsr_result = _dsr_from_equity(equity, DSR.get("n_trials") or tc.get())
        print(f"  收益={stats.get('总收益率')}  "
              f"DSR={dsr_result.get('dsr', 0):.4f}")
        stats["fold"] = i + 1
        stats["DSR"] = round(dsr_result.get("dsr", 0), 4)
        stats["DSR通过"] = dsr_result.get("passed", False)
        all_stats.append(stats)
        all_dsr.append(dsr_result.get("dsr", 0))

    summary = {}
    if all_stats:
        summary = {
            "折数": len(all_stats),
            "平均收益": np.mean([float(s["总收益率"].strip("%"))
                              for s in all_stats]),
            "平均夏普": np.mean([float(s["夏普比率"]) for s in all_stats]),
            "平均DSR": np.mean(all_dsr),
            "DSR通过折数": sum(1 for d in all_dsr if d > 0.95)}

    pbo_result = {}
    if PBO_CFG["enabled"] and len(fold_equities) >= 3:
        rets_df = build_returns_matrix(fold_equities)
        pbo_result = cscv_pbo(rets_df, n_splits=PBO_CFG["n_splits"],
                              max_combinations=PBO_CFG["max_combinations"])
        summary["PBO"] = round(pbo_result.get("pbo", 1), 4)
        summary["PBO通过"] = pbo_result.get("passed", False)

    print("\n--- Walk-forward 汇总 ---")
    for k, v in summary.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    return {"folds": all_stats, "summary": summary,
            "dsr_list": all_dsr, "pbo": pbo_result}