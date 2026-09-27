# -*- coding: utf-8 -*-
"""一次性探针：实测 etf/v1/data/universe_all 的形态与质量，为评估模块定口径提供依据。

要回答的问题：
1. 列名/编码是否统一（有 BOM？）
2. 有多少只是「平值/近零方差」的坏序列（159003 实测 open=100.0, high=100.001）
3. 每日截面厚度分布（多少天 <30 只）
4. 上市年份分布（2022 后上市占比 → 早年截面薄的程度）
5. 重复日期 / 非单调索引
6. close 最小值（log/除法类表达式的安全性）
"""
import glob
import os
import collections

import numpy as np
import pandas as pd

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1"
D = os.path.join(SRC, "data", "universe_all")
files = sorted(glob.glob(os.path.join(D, "*_daily.csv")))
print("files:", len(files))

colsets = collections.Counter()
bad_flat, bad_dup, bad_notmono, rows_listed = [], [], [], {}
nuniq = {}
close_min = {}
year_first = collections.Counter()
per_day = collections.defaultdict(int)
returns = {}

for i, f in enumerate(files):
    code = os.path.basename(f).split("_")[0]
    try:
        df = pd.read_csv(f, encoding="utf-8-sig")
    except Exception as e:
        print("READFAIL", code, e)
        continue
    colsets[tuple(df.columns)] += 1
    d = pd.to_datetime(df["date"], errors="coerce")
    if d.duplicated().any():
        bad_dup.append(code)
    if not d.is_monotonic_increasing:
        bad_notmono.append(code)
    cl = pd.to_numeric(df["close"], errors="coerce")
    r = cl.pct_change(fill_method=None).dropna()
    if len(cl.dropna()):
        close_min[code] = float(cl.min())
    # 「平值」判据：整段唯一收盘数 <= 5 或日收益 std 极小
    if cl.notna().sum() >= 30 and (cl.nunique() <= 5 or r.std() < 1e-5):
        bad_flat.append((code, int(cl.nunique()), float(r.std()), len(cl)))
    rows_listed[code] = len(df)
    year_first[int(d.min().year)] += 1
    returns[code] = r
    for x in d.dt.date:
        per_day[x] += 1

print("\ncolsets:", dict(colsets))
print("\n唯一收盘数<=5 或 日收益std<1e-5 的坏序列:", len(bad_flat))
for t in bad_flat[:15]:
    print("   ", t)
print("重复日期:", len(bad_dup), bad_dup[:10])
print("非单调索引:", len(bad_notmono), bad_notmono[:10])
print("\n上市首行年份分布:", dict(sorted(year_first.items())))
print("\n每文件行数: min %d med %d max %d" % (
    min(rows_listed.values()), int(np.median(list(rows_listed.values()))),
    max(rows_listed.values())))
print("close 最小值分布: min %.4f p1 %.4f med %.4f" % (
    min(close_min.values()),
    float(np.percentile(list(close_min.values()), 1)),
    float(np.median(list(close_min.values())))))

ks = sorted(per_day)
print("\n总交易日:", len(ks), " 区间", ks[0], "~", ks[-1])
cnts = np.array([per_day[k] for k in ks])
for th in (10, 30, 50, 100, 200, 300, 400, 500):
    print("  截面<%4d 只的天数: %4d (%.1f%%)" % (
        th, int((cnts < th).sum()), 100 * (cnts < th).mean()))
print("  截面中位数:", int(np.median(cnts)), " 最大:", int(cnts.max()))
# 最近 500 天的截面厚度
print("  最近 500 天截面中位数:", int(np.median(cnts[-500:])))
print("  2020-01-01 起天数:", sum(1 for k in ks if k >= __import__('datetime').date(2020, 1, 1)))
for y in range(2013, 2027):
    sub = np.array([per_day[k] for k in ks if k.year == y])
    if len(sub):
        print("   %d: 天数 %3d  截面 med %4d  min %4d  max %4d" % (
            y, len(sub), int(np.median(sub)), sub.min(), sub.max()))

allr = pd.concat(returns, names=["code", "dt"]).swaplevel().sort_index()
print("\n日收益全景: n=%d mean %.5f std %.4f |p99.9| %.3f max %.3f min %.3f" % (
    len(allr), allr.mean(), allr.std(), allr.abs().quantile(0.999),
    allr.max(), allr.min()))
print("  |r|>0.11 的极端样本数:", int((allr.abs() > 0.11).sum()))
