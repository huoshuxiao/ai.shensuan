# -*- coding: utf-8 -*-
"""Deflated Sharpe Ratio（缩水夏普比率, Bailey & López de Prado 2014）

多重检验校正：挖掘的因子/策略越多，样本内最优夏普的"运气成分"越大。
DSR 回答"在尝试了 N 个变体的前提下，真实夏普 > 0 的概率有多大"。
"""

import os
import json
import numpy as np
from scipy.stats import norm
from config import TRIAL_COUNTER_FILE

EULER_GAMMA = 0.5772156649


def expected_max_sharpe(n_trials, sr_variance=1.0):
    """N 次独立试验下"纯运气"能得到的最大期望夏普（逐 bar 单位）：
    SR* ≈ sqrt(var) · [(1-γ)·z(1-1/N) + γ·z(1-1/(N·e))]，
    γ=Euler-Mascheroni 常数，z 为标准正态分位数（极值分布一阶近似）。

    sr_variance 与 SR̂ 必须同量纲（**逐 bar** 夏普估计量的方差），默认
    1.0 只是把极值系数归一，实际调用方（deflated_sharpe_ratio）要按数据
    频率给出，否则门槛与样本夏普不在一个尺度上。"""
    if n_trials < 2:
        return 0.0
    z1 = norm.ppf(1 - 1.0 / n_trials)
    z2 = norm.ppf(1 - 1.0 / (n_trials * np.e))
    return np.sqrt(sr_variance) * ((1 - EULER_GAMMA) * z1 +
                                    EULER_GAMMA * z2)


def deflated_sharpe_ratio(returns, n_trials=1,
                          sr_variance=None, benchmark_sr=0.0):
    """DSR = Φ( (SR̂ - SR* - SR_bench) · sqrt(T-1) /
               sqrt(1 - γ₁·SR̂ + (γ₂-1)/4·SR̂²) )

    SR̂=样本夏普(逐bar)；γ₁ 偏度、γ₂ 峰度进入分母做非正态修正
    （厚尾/偏态会放大夏普估计的方差）；Φ 为标准正态 CDF。
    passed: DSR > 0.95 才认为扣除多重检验后仍显著。

    sr_variance=运气门槛的量纲来源（SR* = sqrt(sr_variance)·极值系数），
    缺省时按 Lo(2002) 的夏普估计量抽样方差 Var[SR̂] ≈ (1 + 0.5·SR̂²)/T
    自算：一次试验在 T 根 bar 上能"纯运气"挣到的夏普尺度就是它的标准差。
    写死 1.0 等于假定每次试验的夏普自带 ±1（逐 bar）噪声——日线 SR̂ 只有
    千分之几，门槛 0.85 永远跨不过，DSR 恒为 0（#15 前该链正是这样失效的）。"""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    T = len(r)
    if T < 30:
        return {"error": "样本不足", "dsr": 0.0, "passed": False}
    mu, sigma = r.mean(), r.std(ddof=1)
    if sigma < 1e-12:
        return {"error": "波动为零", "dsr": 0.0, "passed": False}

    sr = mu / sigma                                        # 逐 bar 样本夏普
    skew = float(((r - mu) ** 3).mean() / (sigma ** 3 + 1e-12))   # γ₁
    kurt = float(((r - mu) ** 4).mean() / (sigma ** 4 + 1e-12))   # γ₂
    if sr_variance is None:
        sr_variance = float((1 + 0.5 * sr ** 2) / T)
        sr_variance_source = "Lo2002 抽样方差 (1+0.5·SR̂²)/T"
    else:
        sr_variance_source = "caller"
    sr0 = expected_max_sharpe(n_trials, sr_variance)       # 期望最大夏普门槛 SR*
    # 夏普估计量的渐近标准差（非正态修正项）
    denom = np.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    z = (sr - sr0 - benchmark_sr) * np.sqrt(T - 1) / denom
    dsr = float(norm.cdf(z))

    return {"sr_observed": float(sr), "sr0_expected_max": float(sr0),
            "sr_variance": float(sr_variance),
            "sr_variance_source": sr_variance_source,
            "dsr": dsr, "skew": skew, "kurt": kurt,
            "n_trials": n_trials, "n_samples": T,
            "passed": dsr > 0.95}


def annualize_sharpe(sr_bar, bars_per_year=240 * 252):
    """逐 bar 夏普 → 年化：SR_ann = SR_bar · sqrt(bars_per_year)
    （假定各 bar 收益独立同分布）"""
    return sr_bar * np.sqrt(bars_per_year)


class TrialCounter:
    """多重检验计数器：累计因子/策略挖掘的试验次数 n_trials，
    落盘 JSON 持久化，供 DSR 计算运气门槛 SR* 使用。"""
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