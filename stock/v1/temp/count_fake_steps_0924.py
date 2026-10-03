# -*- coding: utf-8 -*-
"""对账：假台阶格子的生产口径计数到底是 92 还是 326

09-23 记在 CHANGELOG / docstring / 看板 / 记忆里的数都是 **326 个 (票,日)**，
而 shell/probe_factor_anchor_0924.py 直接读 h5 手算 pct_change 得到 **92**。
两个数不可能都对，而「326」现在被引用了五处 —— 先定住哪个是对的。

差别只可能来自三处：数据类型（float32 直接算 vs 先 astype float64）、
日期切片（生产走 ASHARE_PORT_START - 暖机窗，探针走全 h5）、
以及判据本身（是否要求「盘面侧正常」）。这里把四种组合同时报出来。
"""
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402,F401  (裸模块名导入的 sys.path 引导，必须先于 config)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from config import ASHARE_DAILY_H5  # noqa: E402
from strategy.ashare_screen import RET_LIMIT, build_matrices, load_panel  # noqa: E402

wide, _bench = load_panel()
mtx = build_matrices(wide)
op64, fc = wide["open"].astype("float64"), wide["factor"].astype("float64")
adj = op64.pct_change(fill_method=None)
raw = (op64 / fc).pct_change(fill_method=None)

fake = adj.abs().gt(RET_LIMIT) & raw.abs().le(RET_LIMIT)
over = adj.abs().gt(RET_LIMIT)
print(f"\n[生产切片] 面板 {op64.shape[0]} 日 × {op64.shape[1]} 票，"
      f"{op64.index[0].date()} ~ {op64.index[-1].date()}")
print(f"  |复权开盘收益|>{RET_LIMIT:.0%}                ：{int(over.values.sum())}")
print(f"  且盘面同幅正常（= guard_ret 裁剪集，生产口径）：{int(fake.values.sum())}")
print(f"  且盘面侧缺失/停牌（raw 为 NaN）              ："
      f"{int((over & raw.isna()).values.sum())}")
chg = (mtx["ret_open"].astype("float64") - adj).abs().gt(1e-5)
print(f"  build_matrices 输出里被实际改动的格子：{int((chg & adj.notna()).values.sum())}")
# 2023-10-16 那一天的分布，与旧记录「一天 64 只」对看
day = pd.Timestamp("2023-10-16")
if day in fake.index:
    print(f"  其中 {day.date()} 当天：{int(fake.loc[day].sum())} 只")
by_board = pd.Series([c[:2] for c in fake.columns])
print("  按板块：" + "  ".join(
    f"{b} {int(fake.loc[:, (fake.columns.str[:2] == b)].values.sum())}"
    for b in by_board.unique()))

# 全 h5（不切日期）那一版，探针就是这么算的
p = pd.read_hdf(ASHARE_DAILY_H5)
p = p[[c for c in p.columns if c.startswith("$")]]
p.columns = [c.lstrip("$") for c in p.columns]
inst = np.array(sorted(p.index.get_level_values("instrument").unique()))
is_idx = np.array([(s[:2] == "SH" and s[2:].startswith("000"))
                   or (s[:2] == "SZ" and s[2:].startswith("399")) for s in inst])
pool = [s for s in inst[~is_idx]]
o = p["open"].unstack("instrument").reindex(columns=pool)
f = p["factor"].unstack("instrument").reindex(columns=pool)
a2, r2 = o.pct_change(fill_method=None), (o / f).pct_change(fill_method=None)
n_all = int((a2.abs().gt(.30) & r2.abs().le(.30)).values.sum())
print(f"\n[全 h5 不切片] 同一判据 = {n_all} 个；dtype={o['open' if False else pool[0]].dtype}")
print("  ⇒ 若两数相同，则 326 与 92 的差不在切片，而在判据或当日数据状态")
