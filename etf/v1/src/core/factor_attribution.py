# -*- coding: utf-8 -*-
"""因子归因（Shapley / LOO / Marginal）"""

import numpy as np
import pandas as pd
from config import FACTOR_ATTRIBUTION, RISK_BUDGET
from factor_orthogonal import orthogonalize_factors
from risk_budget import compute_factor_weights
from strategy import IntradayRotationStrategy
from backtest import MinuteBacktester


class AttributionEvaluator:
    def __init__(self, pool, universe, all_ts, risk_params,
                 ortho_params=None):
        self.pool = pool
        self.universe = universe
        self.all_ts = all_ts
        self.risk_params = risk_params
        self.ortho_params = ortho_params or {
            "threshold": 0.7, "method": "gram_schmidt"}

    def evaluate(self, factors):
        if not factors:
            return 0.0
        try:
            ortho = orthogonalize_factors(
                factors, threshold=self.ortho_params["threshold"],
                method=self.ortho_params["method"]) or factors
            weights = None
            if RISK_BUDGET["enabled"]:
                weights = compute_factor_weights(
                    ortho, self.pool,
                    method=RISK_BUDGET["weight_method"])
            s = IntradayRotationStrategy(
                ortho, self.pool, self.universe,
                factor_weights=weights)
            sig = s.generate_signals(self.all_ts)
            bt = MinuteBacktester(self.pool, self.universe,
                                  self.risk_params)
            r = bt.run(sig)
            return float(r["stats"].get("总收益率", "0%").strip("%"))
        except Exception:
            return 0.0


def shapley_attribution(evaluator, factors, n_samples=100,
                        random_state=42):
    names = [f["name"] for f in factors]
    n = len(names)
    if n == 0:
        return pd.DataFrame()
    rng = np.random.default_rng(random_state)
    shapley = {name: 0.0 for name in names}
    for _ in range(n_samples):
        perm = rng.permutation(n)
        prev = 0.0
        for i in perm:
            current_set = [factors[j] for j in perm[:
                list(perm).index(i) + 1]]
            current = evaluator.evaluate(current_set)
            shapley[names[i]] += current - prev
            prev = current
    for k in shapley:
        shapley[k] /= n_samples
    df = pd.DataFrame([{"factor": k, "shapley": v}
                       for k, v in shapley.items()])
    df = df.sort_values("shapley", ascending=False).reset_index(drop=True)
    df["contribution_pct"] = (df["shapley"] /
                              (df["shapley"].abs().sum() + 1e-9) * 100)
    return df


def loo_attribution(evaluator, factors):
    names = [f["name"] for f in factors]
    n = len(names)
    if n == 0:
        return pd.DataFrame()
    full = evaluator.evaluate(factors)
    rows = []
    for i, name in enumerate(names):
        subset = [factors[j] for j in range(n) if j != i]
        v = evaluator.evaluate(subset)
        rows.append({"factor": name, "without_factor": v,
                     "full": full, "contribution": full - v})
    df = pd.DataFrame(rows).sort_values("contribution",
                                         ascending=False).reset_index(drop=True)
    df["contribution_pct"] = (df["contribution"] /
                              (df["contribution"].abs().sum() + 1e-9) * 100)
    return df


def marginal_attribution(factors, factor_weights):
    rows = []
    for f in factors:
        name = f["name"]
        w = factor_weights.get(name, 0.0)
        ic = f.get("mean_ic", 0.0)
        rows.append({"factor": name, "weight": w, "ic": ic,
                     "contribution": w * abs(ic)})
    df = pd.DataFrame(rows)
    df["contribution_pct"] = (df["contribution"] /
                              (df["contribution"].sum() + 1e-9) * 100)
    return df.sort_values("contribution", ascending=False).reset_index(drop=True)


def run_attribution(factors, pool, universe, all_ts,
                    risk_params, factor_weights=None):
    cfg = FACTOR_ATTRIBUTION
    print("\n========== 因子归因 ==========")
    if not cfg["enabled"] or len(factors) < 2:
        return {}
    evaluator = AttributionEvaluator(pool, universe, all_ts, risk_params)
    method = cfg["method"]
    if method == "shapley":
        df = shapley_attribution(evaluator, factors, cfg["n_samples"])
    elif method == "leave_one_out":
        df = loo_attribution(evaluator, factors)
    else:
        if factor_weights is None:
            factor_weights = compute_factor_weights(
                factors, pool, method=RISK_BUDGET["weight_method"])
        df = marginal_attribution(factors, factor_weights)
    if df.empty:
        return {}
    top = df.head(cfg["top_n"])["factor"].tolist()
    zero = df[df["contribution"].abs() < cfg["min_contribution"]][
        "factor"].tolist()
    print(f"  Top 贡献: {top}")
    if zero:
        print(f"  ⚠️ 零贡献: {zero}")
    return {"method": method, "attribution": df,
            "top_contributors": top, "zero_contributors": zero}


def save_attribution(result, out_dir="."):
    if not result or "attribution" not in result:
        return
    result["attribution"].to_csv(f"{out_dir}/factor_attribution.csv",
                                  index=False, encoding="utf-8-sig")