# -*- coding: utf-8 -*-
"""PBO (CSCV)"""

import itertools
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from config import PBO as PBO_CFG


def _sharpe(returns):
    r = returns[~np.isnan(returns)]
    if len(r) < 5:
        return -np.inf
    s = r.std(ddof=1)
    if s < 1e-12:
        return -np.inf
    return r.mean() / s


def build_returns_matrix(equity_dict, common_index=None):
    rets = {name: eq.pct_change() for name, eq in equity_dict.items()}
    df = pd.DataFrame(rets)
    if common_index is not None:
        df = df.reindex(common_index)
    df = df.dropna(how="all").fillna(0.0)
    return df


def cscv_pbo(returns_df, n_splits=None, max_combinations=None):
    n_splits = n_splits or PBO_CFG["n_splits"]
    max_combinations = max_combinations or PBO_CFG["max_combinations"]

    if n_splits % 2 == 1:
        n_splits -= 1
    half = n_splits // 2
    T, N = returns_df.shape
    if T < n_splits * 5 or N < 3:
        return {"pbo": 1.0, "error": "样本或策略不足", "passed": False}

    segments = np.array_split(np.arange(T), n_splits)
    all_combos = list(itertools.combinations(range(n_splits), half))
    if len(all_combos) > max_combinations:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(all_combos), max_combinations, replace=False)
        all_combos = [all_combos[i] for i in idx]

    logits, oos_ranks = [], []
    values = returns_df.values

    for combo in all_combos:
        is_idx = np.concatenate([segments[i] for i in combo])
        oos_combo = [i for i in range(n_splits) if i not in combo]
        oos_idx = np.concatenate([segments[i] for i in oos_combo])

        is_sharpe = np.array([_sharpe(values[is_idx, j]) for j in range(N)])
        n_star = int(np.argmax(is_sharpe))

        oos_sharpe = np.array([_sharpe(values[oos_idx, j]) for j in range(N)])
        if np.all(np.isinf(oos_sharpe)):
            continue

        ranks = rankdata(oos_sharpe)
        rank_n_star = ranks[n_star] / (N + 1)
        oos_ranks.append(rank_n_star)
        eps = 1e-6
        r = min(max(rank_n_star, eps), 1 - eps)
        logits.append(np.log(r / (1 - r)))

    if not logits:
        return {"pbo": 1.0, "error": "无有效切分", "passed": False}

    logits = np.array(logits)
    pbo = float((logits <= 0).mean())
    return {"pbo": pbo, "logits_mean": float(logits.mean()),
            "logits_std": float(logits.std()),
            "n_combinations": len(logits),
            "oos_rank_median": float(np.median(oos_ranks)),
            "passed": pbo < 0.5, "logits": logits.tolist()}


def collect_config_equities(pool, universe, factor_sets, risk_param_sets,
                            strategy_cls, backtester_cls, max_configs=None):
    max_configs = max_configs or PBO_CFG["n_configs"]
    equity_dict = {}
    ref_code = next(iter(pool))
    all_ts = pool[ref_code].index
    count = 0
    for fi, factors in enumerate(factor_sets):
        if not factors:
            continue
        strategy = strategy_cls(factors, pool, universe)
        signals = strategy.generate_signals(all_ts)
        for ri, risk_params in enumerate(risk_param_sets):
            if count >= max_configs:
                break
            try:
                bt = backtester_cls(pool, universe, risk_params)
                res = bt.run(signals)
                equity_dict[f"F{fi}_R{ri}"] = res["equity"]["equity"]
                count += 1
            except Exception:
                continue
    return equity_dict