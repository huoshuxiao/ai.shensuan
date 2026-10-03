# -*- coding: utf-8 -*-
"""只读探针：#15 五档定档路径的门槛，在 09-23 数据 + 当前账本 N=43 下重算。

两种门槛别混：
  1. **运气门槛 SR\\***（`walk_forward_daily.csv` 的 `运气门槛年化` 列）= N 次试验
     纯运气能挣到的年化夏普，只随 T 与 N 变。
  2. **过线所需年化夏普** = 把 DSR=0.95 反解出来的 SR̂，除 T、N 之外还吃这条净值
     自己的偏度/峰度（日线厚尾把夏普估计的方差抬起来 ⇒ 要求高得多）。#15 原表里
     "2.21~7.10 / 门槛 2.86→1.65" 说的是这一个。

自检：先复算已落盘的三折 `运气门槛年化`（残差 ~2e-3 来自 CSV 把年化夏普圆到三位）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "etf", "v1", "src"))
import _bootstrap  # noqa: E401,F401

import numpy as np  # noqa: E402
from scipy.stats import norm  # noqa: E402
from dsr import expected_max_sharpe  # noqa: E402

BARS = 252
# 本轮全样本 DSR 落盘的高阶矩（dsr_daily.csv）：反解口径用它代表这条策略净值
SKEW, KURT = 7.7032, 264.7417


def sr0_annual(t_bars, n_trials, sr_hat_annual):
    """口径 1：运气门槛 SR*（与 SR̂ 通过 Lo2002 方差耦合，量纲逐 bar → 年化）。"""
    sr_bar = sr_hat_annual / np.sqrt(BARS)
    return expected_max_sharpe(n_trials,
                               sr_variance=(1 + 0.5 * sr_bar ** 2) / t_bars
                               ) * np.sqrt(BARS)


def need_annual(t_bars, n_trials, skew=SKEW, kurt=KURT, target=0.95):
    """口径 2：反解"SR̂ 年化要多大才让 DSR = target"（二分；SR* 随 SR̂ 微增）。"""
    z = norm.ppf(target)

    def dsr_of(sr_bar):
        sr_star = expected_max_sharpe(
            n_trials, sr_variance=(1 + 0.5 * sr_bar ** 2) / t_bars)
        denom = np.sqrt(max(1e-12, 1 - skew * sr_bar + (kurt - 1) / 4 * sr_bar ** 2))
        return norm.cdf((sr_bar - sr_star) * np.sqrt(t_bars - 1) / denom)

    lo, hi = 1e-6, 20.0                      # 年化上限 20 足够包住任何现实档
    for _ in range(200):
        mid = (lo + hi) / 2
        if dsr_of(mid / np.sqrt(BARS)) > target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


print("自检 · 复算三折的运气门槛（对照 walk_forward_daily.csv 的 运气门槛年化）：")
for fold, (t, n, sr, got) in {1: (402, 32, 0.826, 1.6659),
                              2: (402, 38, 1.137, 1.7217),
                              3: (402, 43, 1.472, 1.7616)}.items():
    print(f"  折 {fold}: 复算 {sr0_annual(t, n, sr):.4f} vs 落盘 {got}"
          f"  Δ={sr0_annual(t, n, sr) - got:+.4f}")

MEAN_SR = 1.1450          # 本轮三折年化夏普均值（最好的折 1.472）
print(f"\n五档路径的门槛（账本 N=43；SR̂ 一律按折均 {MEAN_SR} 代入 SR*）")
print(f"{'路径':<40}{'T':>6}{'SR* 年化':>10}{'过线所需年化':>14}   判定")
rows = [
    ("现状：单折（3 折各自算）", 402),
    ("路径 1：合并样本外", 1206),
    ("路径 2：train_ratio 0.5 → 单折", 672),
    ("路径 2+1：合并", 2016),
    ("路径 3：起点 2019 → 单折", 183),
    ("路径 3+1：合并", 549),
    ("路径 3+2：2019 & 0.5 → 单折", 308),
    ("路径 3+2+1：合并", 924),
]
for label, t in rows:
    s, need = sr0_annual(t, 43, MEAN_SR), need_annual(t, 43)
    print(f"{label:<40}{t:>6}{s:>10.3f}{need:>14.2f}   "
          f"{'折均可过线' if MEAN_SR >= need else ('最好折可过线' if 1.472 >= need else '够不着')}")

print(f"\n路径 4（账本）单独看：同一 T 下 N 从 43 → 61 的门槛变化")
for t, name in [(1206, "合并 T=1206"), (402, "单折 T=402")]:
    print(f"  {name}: SR* {sr0_annual(t, 43, MEAN_SR):.3f} → {sr0_annual(t, 61, MEAN_SR):.3f}"
          f"｜过线所需 {need_annual(t, 43):.2f} → {need_annual(t, 61):.2f}")
