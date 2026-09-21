# -*- coding: utf-8 -*-
"""因子归因（Shapley / LOO / Marginal）"""

import numpy as np
import pandas as pd
from config import FACTOR_ATTRIBUTION, RISK_BUDGET, RESULTS_DIR
from factor_orthogonal import orthogonalize_factors
from risk_budget import compute_factor_weights
from strategy import IntradayRotationStrategy
from backtest import get_backtester


class AttributionEvaluator:
    """Shapley/LOO 评估器：对任意因子子集重跑"正交化→加权→信号→回测"，
    返回总收益率(%)。子集结果按 frozenset 缓存——随机置换间大量前缀
    子集重复，缓存可将回测次数从 n×n_samples 降到不同子集数。"""

    def __init__(self, pool, universe, all_ts, risk_params,
                 ortho_params=None):
        self.pool = pool
        self.universe = universe
        # 归因只评估最近 eval_window_bars 根 bar，避免全历史重复回测
        window = FACTOR_ATTRIBUTION.get("eval_window_bars")
        self.all_ts = all_ts if not window else all_ts[-window:]
        self.risk_params = risk_params
        self.ortho_params = ortho_params or {
            "threshold": 0.7, "method": "gram_schmidt"}
        self._cache = {}
        self._err_reported = False

    def evaluate(self, factors):
        if not factors:
            return 0.0
        key = frozenset(f["name"] for f in factors)
        if key in self._cache:
            return self._cache[key]
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
            bt = get_backtester(self.pool, self.universe,
                                self.risk_params)
            r = bt.run(sig)
            value = float(r["stats"].get("总收益率", "0%").strip("%"))
        except Exception as e:
            # 首个异常打印一次，防止子集 bug 全部静默为 0
            if not self._err_reported:
                self._err_reported = True
                print(f"  ⚠️ 归因评估异常（后续同类不再打印）: "
                      f"{type(e).__name__}: {e}")
            value = 0.0
        self._cache[key] = value
        return value


def shapley_attribution(evaluator, factors, n_samples=100,
                        random_state=42):
    """蒙特卡洛置换采样近似 Shapley 值：
    每个随机置换 π 中，因子 i 的边际贡献 = v(π 中 i 及其前缀) - v(前缀)"""
    names = [f["name"] for f in factors]
    n = len(names)
    if n == 0:
        return pd.DataFrame()
    rng = np.random.default_rng(random_state)
    shapley = {name: 0.0 for name in names}
    import time
    t0 = time.time()
    for m in range(n_samples):
        perm = rng.permutation(n)
        prev = 0.0
        cum = []
        for i in perm:
            cum.append(factors[i])
            current = evaluator.evaluate(list(cum))
            shapley[names[i]] += current - prev
            prev = current
        # 进度可观测：每 25% 报一次，子集数走缓存去重后的真实回测次数
        if (m + 1) % max(1, n_samples // 4) == 0:
            print(f"  置换采样 {m + 1}/{n_samples}  "
                  f"已评估子集 {len(evaluator._cache)}  "
                  f"用时 {time.time() - t0:.0f}s", flush=True)
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
    # 因子数上限：子集枚举开销 ~2^n，只归因 |IC| 前 max_factors 个，
    # 其余因子不参与 Shapley（贡献视为 0 并如实标注）
    cap = cfg.get("max_factors") or len(factors)
    if len(factors) > cap:
        dropped = sorted(factors, key=lambda f: -abs(f.get("mean_ic", 0)))[cap:]
        factors = sorted(factors, key=lambda f: -abs(f.get("mean_ic", 0)))[:cap]
        print(f"  因子数 {len(factors) + len(dropped)} > 上限 {cap}，"
              f"按 |IC| 取前 {cap} 归因，未归因: "
              f"{[f['name'] for f in dropped]}")
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
    if "contribution" not in df.columns and "shapley" in df.columns:
        df["contribution"] = df["shapley"]
    top = df.head(cfg["top_n"])["factor"].tolist()
    zero = df[df["contribution"].abs() < cfg["min_contribution"]][
        "factor"].tolist()
    print(f"  Top 贡献: {top}")
    if zero:
        print(f"  ⚠️ 零贡献: {zero}")
    return {"method": method, "attribution": df,
            "top_contributors": top, "zero_contributors": zero}


def save_attribution(result, out_dir=RESULTS_DIR):
    if not result or "attribution" not in result:
        return
    result["attribution"].to_csv(f"{out_dir}/factor_attribution.csv",
                                  index=False, encoding="utf-8-sig")