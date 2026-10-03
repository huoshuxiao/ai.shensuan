# -*- coding: utf-8 -*-
"""环2/环3 刷新后的对表：新归档 vs 备份，差在哪、有没有翻符号、有没有排名翻转。

用法：/usr/bin/python3.10 stock/v1/temp/ring23_refresh_diff_0926.py
备份目录默认 09-26 那次刷新的现场，要比别的一天就带环境变量指过去：
    STOCK_RING23_BAK=stock/v1/temp/tmp_ring23_refresh_0927/backup \\
        /usr/bin/python3.10 stock/v1/temp/ring23_refresh_diff_0926.py
默认比 ring-2 四张（eval / eval_yearly / exclusion / buylist）+ ring-3 两张
（判重表 + 候选×在库全矩阵）——09-27 复跑归档刷新时环3 也在覆写面上，只比环2 等于
漏掉两张表没人看。axis_col 留空 = 那张表没有「会翻符号」的收益轴（判重表只有相关系数），
只报逐指标最大差与没动的列。判据不是「必须为 0」—— 面板与代码都可能不同版 ——
而是**把差异摆出来 + 说清落在哪些行**，所以每张表都出：逐指标最大绝对差、
超额符号翻转的行、按超额排名变化的行。
"""

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
# 备份目录可以指到任意一天的现场（判据本身不变：新归档 vs 那一天留的底）
BAK = os.environ.get("STOCK_RING23_BAK") or os.path.join(HERE, "tmp_ring23_refresh_0926", "backup")
LIVE = os.path.join(ROOT, "stock/v1/data/results")

SPECS = [
    ("ashare_portfolio_eval.csv", ["signal", "top_n"], "excess_univ_ew_ann"),
    ("ashare_portfolio_exclusion.csv", ["variant"], "gain_ann"),
    ("ashare_portfolio_buylist.csv", ["variant"], "excess_univ_ew_ann"),
    ("ashare_portfolio_eval_yearly.csv", ["form", "year"], "excess"),
    ("ashare_redundancy_check.csv", ["name"], ""),
    ("ashare_redundancy_detail.csv", ["candidate", "in_library"], "pearson"),
]

pd.set_option("display.width", 200)
pd.set_option("display.unicode.east_asian_width", True)
print(f"[对表面] {len(SPECS)} 张表　备份目录 = {BAK}")

for fname, keys, axis_col in SPECS:
    old_p, new_p = os.path.join(BAK, fname), os.path.join(LIVE, fname)
    idx = None if fname == "ashare_portfolio_eval_yearly.csv" else keys
    if fname == "ashare_portfolio_eval_yearly.csv":
        idx = [0, 1]
    old = pd.read_csv(old_p, index_col=idx)
    new = pd.read_csv(new_p, index_col=idx)
    print(f"\n===== {fname}　旧 {len(old)} 行 / 新 {len(new)} 行 =====")
    only_new, only_old = new.index.difference(old.index), old.index.difference(new.index)
    if len(only_new) or len(only_old):
        print(f"[行数] 只在新表 {len(only_new)} 行：{list(only_new)[:6]}")
        print(f"[行数] 只在旧表 {len(only_old)} 行：{list(only_old)[:6]}")
    both = new.index.intersection(old.index)
    num = [c for c in new.columns if c in old.columns
           and pd.api.types.is_numeric_dtype(new[c])
           and pd.api.types.is_numeric_dtype(old[c])]
    o, n = old.loc[both, num].astype("float64"), new.loc[both, num].astype("float64")
    d = (n - o).abs()
    big = d.max().sort_values(ascending=False)
    print("[逐指标最大绝对差] " + "　".join(
        f"{c}={v:.3e}" for c, v in big.head(6).items()))
    print(f"[完全没动的列] {sum(v == 0 for v in big)}/{len(big)} 列差为 0")
    if axis_col in num:
        a = o[axis_col]
        b = n[axis_col]
        flip = both[(a * b < 0) & (a.abs() > 1e-6) & (b.abs() > 1e-6)]
        print(f"[{axis_col}] 符号翻转 {len(flip)} 行：{list(flip)[:6]}")
        mv = (b - a).sort_values()
        print(f"  位移最大 3 负：{ {k: f'{v:+.4f}' for k, v in mv.head(3).items()} }")
        print(f"  位移最大 3 正：{ {k: f'{v:+.4f}' for k, v in mv.tail(3).items()} }")
        if fname == "ashare_portfolio_eval.csv":
            g_old = old.groupby("top_n")[axis_col].rank(ascending=False)
            g_new = new.groupby("top_n")[axis_col].rank(ascending=False)
            ch = (g_old - g_new).abs().loc[both].dropna()
            print(f"[排名] 同档内名次变化的行 {(ch > 0).sum()}/{len(both)}，"
                  f"最大位移 {int(ch.max()) if len(ch) else 0}")
