# -*- coding: utf-8 -*-
"""#15 折数/验证段的实测探针（只读，不落任何结果文件）

目的：把「切成几折、从哪年起切」从拍脑袋变成可比较的读数。每档配置报
  训练段 bar / 测试段 bar / 训练段可用标的（>240 根 bar，walk_forward 的门槛）
  / 测试段日均截面厚度（该折测试段里每天有几只有 bar）
最后一项与 HOLD=10 一起才是统计功效的来源：截面按 ρ̄≈0.327 折成有效独立
观测 k_eff，时间维按 hold 折成不重叠的持仓周期数；两者相乘≈该折样本外
「数得过来的独立赌注」。
两张表都再带一列「过 DSR 所需年化夏普」（正态近似，N 取本轮实测账本终值
23）：它是折几何的**判决代价**——门槛高过任何可实现的夏普，该档几何就只是
仪式，不产生证据。

用法：/usr/bin/python3.10 shell/i15_fold_probe_0924.py
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                    "etf", "v1", "src")
sys.path.insert(0, _SRC)
import _bootstrap  # noqa: E402,F401

import config  # noqa: E402
config.RDAGENT_USE_OFFICIAL_FALLBACK = False

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from data_loader import DataLoader  # noqa: E402
from etf_universe import get_universe  # noqa: E402

HOLD = 10          # 裁判链的持仓天数（etf_admission.HOLD），不重叠周期数用它折
RHO_BAR = 0.327    # 2021+ 截面两两相关均值，见 CHANGELOG「ρ̄/k_eff」一节
N_TRIALS = 23      # 本轮实测账本终值（主链 6 + 三折 6/6/5），拿它当门槛基准
EULER_GAMMA = 0.5772156649


def k_eff(n):
    """n 只等权、两两相关 ρ̄ 时的有效独立观测数：n/(1+(n-1)ρ̄)"""
    return n / (1 + (n - 1) * RHO_BAR)


def needed_sharpe(T, n_trials=N_TRIALS, bars_per_year=252, z=1.645):
    """T 根 bar 的样本外要跑出多高的**年化**夏普才可能过 DSR(0.95)。

    DSR = Φ((SR̂-SR*)·sqrt(T-1)/denom)，SR* = sqrt((1+0.5SR̂²)/T)·极值系数
    （dsr.py 修复后的口径）；取 denom=1（正态）、SR̂² 项忽略（千分位），
    门槛随 T 缩短而抬：这就是"折切得越碎、DSR 越不可能过"的量化形态。"""
    from scipy.stats import norm
    coef = ((1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_trials)
            + EULER_GAMMA * norm.ppf(1 - 1 / (n_trials * np.e)))
    se = 1 / np.sqrt(T)
    sr_star = coef * se
    return (sr_star + z * se) * np.sqrt(bars_per_year)


def geometry(ts, n_splits, train_ratio, embargo, start):
    """复刻 walk_forward.make_splits 的切法，返回每折
    (训练起, 训练止, 测试起, 测试止)。"""
    ts = ts[ts >= pd.Timestamp(start)]
    n = len(ts)
    window = n // n_splits
    folds = []
    for i in range(n_splits):
        s = i * window
        e = s + window if i < n_splits - 1 else n
        train_end = s + int((e - s) * train_ratio)
        test_start = min(train_end + embargo, e - 1)
        if test_start >= e - 1:
            continue
        folds.append((ts[s], ts[train_end - 1], ts[test_start], ts[e - 1]))
    return folds


def main():
    uni = get_universe()
    pool = DataLoader(freq="daily").load_pool(uni.universe["code"].tolist())
    ref = max(pool, key=lambda c: len(pool[c]))
    ts = pool[ref].index
    embargo = config.LOOKBACK_BARS // 4
    close = pd.DataFrame({c: df["close"] for c, df in pool.items()})
    # 统一到主线那条时间轴（ref 标的的 index），否则各标的日期并集会让段长虚高
    close = close.reindex(ts)
    print(f"池 {len(pool)} 只 | 时间轴 {ref} {ts[0]:%Y-%m-%d}~{ts[-1]:%Y-%m-%d}"
          f" ({len(ts)} bar) | embargo={embargo} bar | HOLD={HOLD}")

    print("\n===== 折几何（起点 × 折数，train_ratio=0.7）=====")
    print(f"{'起点':>5} {'折':>2} {'训练bar/折':>22} {'测试bar/折':>18} "
          f"{'测试合计':>7} {'可用标的/折':>16} {'截面厚度/折':>16} "
          f"{'过DSR年化门槛/折':>18}  合并OOS门槛")
    for start in ("2010-01-01", "2017-01-01", "2019-01-01", "2021-01-01"):
        for n_splits in (2, 3, 4, 5, 6):
            folds = geometry(ts, n_splits, 0.7, embargo, start)
            if not folds:
                continue
            train, test, usable, thick, need = [], [], [], [], []
            for tr_s, tr_e, te_s, te_e in folds:
                train.append(len(close.loc[tr_s:tr_e]))
                seg = close.loc[te_s:te_e]
                test.append(len(seg))
                usable.append(sum(1 for c, df in pool.items()
                                  if len(df.loc[tr_s:tr_e]) > 240))
                n_thick = float(seg.notna().sum(axis=1).mean())
                thick.append(n_thick)
                # 该折样本外要多少年化夏普才够得着 DSR=0.95（正态近似）
                need.append(round(needed_sharpe(len(seg)), 2))
            fmt = lambda xs: "/".join(str(x) for x in xs)
            print(f"{start[:4]:>5} {n_splits:>2} {fmt(train):>22} "
                  f"{fmt(test):>18} {sum(test):>7} {fmt(usable):>16} "
                  f"{fmt([round(x) for x in thick]):>16}  {fmt(need):>18}"
                  f"   拼接 {needed_sharpe(sum(test)):.2f}"
                  f"（周期数 {sum(test) // HOLD}×k_eff"
                  f"≈{sum(test) / HOLD * k_eff(min(thick)):.0f} 注）")

    # 第二张表：把 train_ratio 也当旋钮（调低它 = 验证段变长 = 门槛变低，
    # 代价是每折训练段变短，能挖出的因子更少）
    print("\n===== 加长验证段（train_ratio 往下调）=====")
    print(f"{'起点':>5} {'折':>2} {'ratio':>5} {'训练bar/折':>18} "
          f"{'测试bar/折':>18} {'可用标的/折':>14} {'过DSR年化门槛/折':>18} "
          f"{'拼接门槛':>8}")
    for start in ("2010-01-01", "2019-01-01"):
        for n_splits in (2, 3):
            for ratio in (0.5, 0.6, 0.7):
                folds = geometry(ts, n_splits, ratio, embargo, start)
                if not folds:
                    continue
                train, test, usable, need = [], [], [], []
                for tr_s, tr_e, te_s, te_e in folds:
                    seg = close.loc[te_s:te_e]
                    train.append(len(close.loc[tr_s:tr_e]))
                    test.append(len(seg))
                    usable.append(sum(1 for c, df in pool.items()
                                      if len(df.loc[tr_s:tr_e]) > 240))
                    need.append(round(needed_sharpe(len(seg)), 2))
                fmt = lambda xs: "/".join(str(x) for x in xs)
                print(f"{start[:4]:>5} {n_splits:>2} {ratio:>5} "
                      f"{fmt(train):>18} {fmt(test):>18} "
                      f"{fmt(usable):>14} {fmt(need):>18}"
                      f"   {needed_sharpe(sum(test)):.2f}")


if __name__ == "__main__":
    main()
