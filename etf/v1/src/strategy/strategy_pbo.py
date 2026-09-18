# -*- coding: utf-8 -*-
"""策略级 PBO"""

import numpy as np
import pandas as pd
from config import STRATEGY_PBO
from pbo import cscv_pbo


def _make_pseudo_configs(rets, n_configs=6):
    vol = rets.rolling(60).std()
    vol = vol.fillna(vol.median())
    configs = {}
    for i, q in enumerate(np.linspace(0.0, 0.3, n_configs)):
        if q == 0:
            configs[f"cfg_q{q:.2f}"] = rets.values
        else:
            thresh = vol.quantile(1 - q)
            mask = (vol <= thresh).values
            configs[f"cfg_q{q:.2f}"] = rets.where(mask, 0.0).values
    return configs


def strategy_level_pbo(equity, n_splits=None, max_combinations=None):
    n_splits = n_splits or STRATEGY_PBO["n_splits"]
    max_combinations = max_combinations or STRATEGY_PBO["max_combinations"]
    min_bars = STRATEGY_PBO["min_equity_bars"]
    rets = equity.pct_change().dropna()
    if len(rets) < min_bars:
        return {"pbo": 1.0, "error": "样本不足", "passed": False}
    configs = _make_pseudo_configs(rets)
    if len(configs) < 3:
        return {"pbo": 1.0, "error": "配置不足", "passed": False}
    rets_df = pd.DataFrame(configs)
    result = cscv_pbo(rets_df, n_splits=n_splits,
                      max_combinations=max_combinations)
    result["passed"] = result.get("pbo", 1.0) < STRATEGY_PBO["pbo_threshold"]
    return result


def filter_strategies_by_pbo(strategies, min_pbo_pass=2):
    print("\n========== 策略级 PBO 过滤 ==========")
    pbo_results, filtered = {}, {}
    for name, s in strategies.items():
        r = strategy_level_pbo(s["equity"]["equity"])
        pbo_results[name] = r
        flag = "✅" if r.get("passed") else "❌"
        print(f"  {name:35s} PBO={r.get('pbo', 1):.4f} {flag}")
        if r.get("passed"):
            filtered[name] = s
    if len(filtered) < min_pbo_pass and strategies:
        ranked = sorted(pbo_results.items(),
                        key=lambda x: x[1].get("pbo", 1.0))
        for name, _ in ranked[:min_pbo_pass]:
            filtered[name] = strategies[name]
    print(f"  过滤后: {len(filtered)}/{len(strategies)}")
    return {"filtered": filtered, "pbo_results": pbo_results}