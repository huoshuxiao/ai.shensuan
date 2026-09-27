# -*- coding: utf-8 -*-
"""连续低量闸门的三种读法：同一份数据上量"谁挡得住僵尸基"，纯只读。

背景：#14 探源之后要在组合层加一道"买进去出不来"的闸门。候选判据三条，
用文字争论不出结果，因为它们的差别是**尾部形态**而不是阈值高低：

    弱读法（峰值）   max(amount_{t-N+1..t}) >= min_amount   —— 近 N 日有一天够量就放行
    强读法（谷值）   min(amount_{t-N+1..t}) >= min_amount   —— 近 N 日天天够量才放行
    分位读法         P10(amount_{t-19..t}) >= min_amount     —— 允许偶发缩量日

本脚本在全市场池（871 只，2019 起）上把三者逐 N 跑一遍，报四个数：
  1) 末日本池有行情只数 / 过均值闸只数 / 再过本读法只数；
  2) 全期日均可投域只数（读法相对均值闸又砍掉多少）；
  3) 全历史**至少被剔过一次**的只数（这决定它是不是只在尾部起作用）；
  4) 被剔掉的 date×code 格子占"均值闸已放行格子"的比例。

跑法：仓库根 `/usr/bin/python3.10 -u shell/probe_lowvol_gate_0924.py`
输出归档为同名 .log，`etf/v1/src/etf_admission.py` 的 AMT_STREAK 注释直接引它。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "etf", "v1", "src")
sys.path.insert(0, SRC)

import etf_admission as EA  # noqa: E402


def report(name, keep, base, m):
    """keep/base 为 bool DataFrame（date × code）；base 是均值闸已放行的域。"""
    last = keep.index[-1]
    n_live = int(m["close"].loc[last].notna().sum())
    n_base = int(base.loc[last].sum())
    n_keep = int(keep.loc[last].sum())
    daily = keep.sum(axis=1)
    ever = keep.any(axis=0)
    hit = (base & ~keep)
    cells = int(hit.values.sum())
    tot = int(base.values.sum())
    print(f"  {name:<34s} 末日 有行情{n_live:4d} → 均值闸{n_base:4d} → 本读法{n_keep:4d}"
          f" ｜全期日均 {daily.mean():6.1f} ｜被剔过 {(~ever).sum():4d} 只"
          f" ｜格子剔 {cells / max(1, tot):6.1%}")
    return daily.mean(), n_keep


def main():
    t0 = time.time()
    print("=" * 100)
    print("连续低量闸门：峰值 / 谷值 / 分位 三种读法的实测代价")
    print("=" * 100)
    for k, v in EA.run_params().items():
        print(f"  {k:14s} = {v}")
    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    del pool
    amt, a20 = m["amount"], m["amount20"]
    min_amt = EA.MIN_AMOUNT

    # 基线：有行情 + 满 120 根 K 线 + 20 日均额够量（即 #13 的全部容量口径）
    base = (m["close"].notna() & (m["listed_days"] >= EA.MIN_LISTED)
            & (a20 >= min_amt))
    print(f"\n[基线·均值闸] 末日 {int(base.sum(axis=1).iloc[-1])} 只过闸，"
          f"全期日均 {base.sum(axis=1).mean():.1f} 只，"
          f"窗口 {base.index[0].date()}~{base.index[-1].date()}（{len(base)} 个交易日）")

    print("\n--- 弱读法：近 N 日单日成交额**峰值** >= 3000 万（有一天够量即放行）---")
    for n in (5, 10, 20, 40):
        r = amt.rolling(n, min_periods=n).max()
        report(f"N={n:<3d} 峰值", base & (r.isna() | (r >= min_amt)), base, m)

    print("\n--- 强读法：近 N 日单日成交额**谷值** >= 3000 万（天天够量才放行）---")
    for n in (5, 10, 20, 40):
        r = amt.rolling(n, min_periods=n).min()
        report(f"N={n:<3d} 谷值", base & (r.isna() | (r >= min_amt)), base, m)

    print("\n--- 分位读法：20 日成交额 P10 >= 阈值（容许偶发缩量日）---")
    r = amt.rolling(20, min_periods=20).quantile(0.10)
    for thr in (3e7, 1e7):
        report(f"20 日 P10 >= {thr / 1e7:.0f} 千万",
               base & (r.isna() | (r >= thr)), base, m)

    print(f"\n[耗时] {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
