# -*- coding: utf-8 -*-
"""Deflated Sharpe Ratio"""

import os
import json
import numpy as np
from scipy.stats import norm
from config import TRIAL_COUNTER_FILE

EULER_GAMMA = 0.5772156649


def expected_max_sharpe(n_trials, sr_variance=1.0):
    if n_trials < 2:
        return 0.0
    z1 = norm.ppf(1 - 1.0 / n_trials)
    z2 = norm.ppf(1 - 1.0 / (n_trials * np.e))
    return np.sqrt(sr_variance) * ((1 - EULER_GAMMA) * z1 +
                                    EULER_GAMMA * z2)


def deflated_sharpe_ratio(returns, n_trials=1,
                          sr_variance=1.0, benchmark_sr=0.0):
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    T = len(r)
    if T < 30:
        return {"error": "样本不足", "dsr": 0.0, "passed": False}
    mu, sigma = r.mean(), r.std(ddof=1)
    if sigma < 1e-12:
        return {"error": "波动为零", "dsr": 0.0, "passed": False}

    sr = mu / sigma
    skew = float(((r - mu) ** 3).mean() / (sigma ** 3 + 1e-12))
    kurt = float(((r - mu) ** 4).mean() / (sigma ** 4 + 1e-12))
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    denom = np.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    z = (sr - sr0 - benchmark_sr) * np.sqrt(T - 1) / denom
    dsr = float(norm.cdf(z))

    return {"sr_observed": float(sr), "sr0_expected_max": float(sr0),
            "dsr": dsr, "skew": skew, "kurt": kurt,
            "n_trials": n_trials, "n_samples": T,
            "passed": dsr > 0.95}


def annualize_sharpe(sr_bar, bars_per_year=240 * 252):
    return sr_bar * np.sqrt(bars_per_year)


class TrialCounter:
    def __init__(self, path=TRIAL_COUNTER_FILE):
        self.path = path
        self.count = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    return int(json.load(f).get("count", 0))
            except Exception:
                return 0
        return 0

    def add(self, n=1):
        self.count += n
        with open(self.path, "w") as f:
            json.dump({"count": self.count}, f)

    def reset(self):
        self.count = 0
        with open(self.path, "w") as f:
            json.dump({"count": 0}, f)

    def get(self):
        return max(1, self.count)