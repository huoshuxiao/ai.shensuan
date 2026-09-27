# -*- coding: utf-8 -*-
"""ETF 线 · #15 剩余两条路径的代价探针（09-25，只算不写产物）

要回答的两个问题分开对待，因为一个能精算、一个不能：

路径 4「试验账本清零」= **纯数学**。合并样本外那一行把 DSR 的全部输入都持久化了
（`walk_forward_oos_daily.csv`：sr_observed / sr_variance / skew / kurt / n_samples /
n_trials），而 `expected_max_sharpe` 与 `deflated_sharpe_ratio` 里 N 只进极值系数、
不进 sr_variance ⇒ 换个 N 重算就是**精确值**，不需要重跑任何回测。

路径 3「回测起点后移」= **只能给代理读数**。起点后移改变的首先是每段自己的 T
（门槛随 1/sqrt(T) 抬），但**实测夏普、偏度、峰度都会随区间换掉而变**，那三个量
不重跑就无从得知。本脚本因此把偏度/峰度钉在 09-25 实测值上做代理，并明确标注：
这条读数是"几何效应"的下界估计，不含"策略在近段更强/更弱"的信息。
真实读数要另跑一次 walk-forward（折内引擎换回 registry，不拉容器）。

公式一律走仓库自己的实现（`from dsr import ...`），不在这里另写一遍——
判据的口径只有一处出处。
"""

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "etf", "v1", "src")
os.chdir(SRC)
sys.path.insert(0, SRC)
import _bootstrap  # noqa: F401
from config import RESULTS_DIR, FREQ, DSR  # noqa: E402
from dsr import expected_max_sharpe, TrialCounter  # noqa: E402

oos = pd.read_csv(f"{RESULTS_DIR}/walk_forward_oos_{FREQ}.csv").iloc[0]

sr = float(oos["sr_observed"])
skew = float(oos["skew"])
kurt = float(oos["kurt"])
T0 = int(oos["n_samples"])
n_now = int(oos["n_trials"])
sv_now = float(oos["sr_variance"])
# 年化系数不写死、也不去配置里猜：产物同时存了逐 bar 门槛与年化门槛，两者之比
# 就是当轮 walk_forward 实际用的 sqrt(bars_per_year)，反解回来必与代码一致。
BYP = round((float(oos["sr0_annual"]) / float(oos["sr0_expected_max"])) ** 2)



def dsr_at(sr_, T, n_trials, skew_=skew, kurt_=kurt):
    """按仓库公式重算一条 DSR（sr_variance 随 T 自算，与 deflated_sharpe_ratio 一致）。"""
    sv = (1 + 0.5 * sr_ ** 2) / T
    sr0 = expected_max_sharpe(n_trials, sv)
    denom = np.sqrt(max(1e-6, 1 - skew_ * sr_ + (kurt_ - 1) / 4 * sr_ ** 2))
    z = (sr_ - sr0) * np.sqrt(T - 1) / denom
    from scipy.stats import norm
    return {"dsr": float(norm.cdf(z)), "sr0_ann": round(sr0 * np.sqrt(BYP), 4),
            "sr_ann": round(sr_ * np.sqrt(BYP), 4), "passed": float(norm.cdf(z)) > 0.95}


# ---- 0. 先自证：重算必须复现产物里那一行，否则后面全是假数 ----
chk = dsr_at(sr, T0, n_now)
print("=== 自证（拿持久化输入重放仓库公式，必须对上产物）===")
print(f"  产物: DSR={float(oos['dsr']):.6f} 门槛年化={oos['sr0_annual']} "
      f"sr_variance={sv_now:.9g} N={n_now} T={T0}")
print(f"  重放: DSR={chk['dsr']:.6f} 门槛年化={chk['sr0_ann']}")
assert abs(chk["dsr"] - float(oos["dsr"])) < 1e-6, "公式重放对不上，探针作废"

tc = TrialCounter()
print(f"\n  账本当前真值: trial_counter.json count={tc.get()} "
      f"| DSR['n_trials'] 覆盖值={DSR.get('n_trials')}（非空即优先于账本）")

# ---- 1. 路径 4：N 取不同值，门槛与 DSR 怎么动（实测夏普钉住不动）----
print(f"\n=== 路径 4：账本清零能买到多少（实测年化夏普钉在 {chk['sr_ann']}）===")
print(f"  {'N':>6} | {'门槛年化':>9} | {'DSR':>7} | 过 0.95？")
for n in [1, 2, 5, 10, 20, 43, 68, 92, 120, n_now, 200, 400]:
    r = dsr_at(sr, T0, n)
    print(f"  {n:>6} | {r['sr0_ann']:>9} | {r['dsr']:>7.4f} | {r['passed']}")


# ---- 2. 反过来问：要过线，实测年化夏普得到多少 ----
def required_sr(T, n_trials, target=0.95):
    """二分反解：给定 T 与 N，过 DSR=target 所需的最小实测年化夏普。"""
    lo, hi = 1e-4, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if dsr_at(mid / np.sqrt(BYP), T, n_trials)["dsr"] < target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 3)


print("\n=== 反解：过 DSR=0.95 所需年化夏普（N=现状 vs N=1）===")
for T, tag in [(T0, "现状合并段 2014 bar"), (int(T0 * 0.5), "起点后移⇒合并段减半"),
               (int(T0 * 0.72), "起点 2019 前后"), (3000, "起点前移到更长样本")]:
    print(f"  T={T:>5} ({tag:<22}) N={n_now:>3} → {required_sr(T, n_now):>5} | "
          f"N=1 → {required_sr(T, 1):>5} | 本轮实测 {chk['sr_ann']}")

# ---- 3. 路径 3 的几何代理：起点后移只改 T，偏度/峰度钉住 ----
print("\n=== 路径 3（代理读数，只含几何效应，不含近段策略强弱）===")
wf = pd.read_csv(f"{RESULTS_DIR}/walk_forward_{FREQ}.csv")
print("  现状三折（产物原值）：")
for _, row in wf.iterrows():
    print(f"    折{int(row['fold'])} 测试段 {int(row['测试段bar数']):>4} bar "
          f"夏普 {row['夏普比率']:>6} DSR {row['DSR']:>6} "
          f"门槛 {row['运气门槛年化']:>6} 因子 {int(row['折内因子数'])} 只 "
          f"来源 {row['折内因子来源']}")
for start, bars in [("2010-01-01（现状）", T0), ("2015-01-01", int(T0 * 0.72)),
                    ("2019-01-01", int(T0 * 0.5)), ("2021-01-01", int(T0 * 0.34))]:
    r_now, r_n1 = dsr_at(sr, bars, n_now), dsr_at(sr, bars, 1)
    print(f"  起点 {start:<18} 合并段≈{bars:>5} bar → 门槛年化 N={n_now}: "
          f"{r_now['sr0_ann']:>7}｜N=1: {r_n1['sr0_ann']:>7}｜"
          f"实测 {chk['sr_ann']} 的 DSR: {r_now['dsr']:.4f} / {r_n1['dsr']:.4f}")
print("\n  ⚠️ 上表的合并段 bar 是按「起点后移 ⇒ 样本按比例缩短」推的代理值，"
      "且沿用了 09-25 全段的偏度/峰度。")
print("     起点后移真正的收益在**截面厚度**（早期折可用标的数），那要重跑才拿得到；"
      "而近段偏度/峰度大概率比全样本轻（2015/2020 的厚尾在窗外），那会让门槛略降。")
