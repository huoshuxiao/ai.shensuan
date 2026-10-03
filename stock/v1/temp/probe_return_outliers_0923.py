# -*- coding: utf-8 -*-
"""探针⑤：复权收益里的「不可能跳变」有多少、集中在那儿、值几个百分点。

判据：A 股单日涨跌停最宽是北交所 ±30%（科创/创业 ±20%，主板 ±10%），
|日收益| > 30% 只能是数据问题（新券首日不算，因为它没进过前一日截面）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "stock", "v1", "src"))
import _bootstrap  # noqa: F401,E402
import numpy as np
import pandas as pd

from config import ASHARE_DAILY_H5

raw = pd.read_hdf(ASHARE_DAILY_H5, key="data")[["$factor", "$close", "$open"]]
f = raw["$factor"].unstack("instrument").sort_index()
cl = raw["$close"].unstack("instrument").sort_index()
ret = cl.pct_change(fill_method=None)          # 研究线实际用的就是这条（复权）

bad = ret.abs() > 0.30
print(f"全样本 {int(ret.notna().values.sum()):,} 个 (票,日) 收益，"
      f"|复权日收益| > 30%: {int(bad.values.sum()):,} 个"
      f"（{bad.values.sum() / ret.notna().values.sum():.4%}）")
for th in (0.11, 0.21, 0.30, 0.50, 1.00):
    print(f"  > {th:>5.0%}: {int((ret.abs() > th).values.sum()):>6,} 个"
          f"   同口径盘面价: {int(((cl / f).pct_change(fill_method=None).abs() > th).values.sum()):>6,} 个")

yr = bad.groupby(bad.index.year).sum().sum(axis=1)
print("\n按年份：")
print(yr[yr > 0].to_string())
m = bad.sum(axis=1)
print("\n最集中的 12 个交易日：")
print(m[m > 0].sort_values(ascending=False).head(12).to_string())

# 这些跳变在等权组合里值多少：逐日均值收益含/剔除异常
ew_all = ret.mean(axis=1)
ew_clean = ret.where(~bad).mean(axis=1)
d = (ew_all - ew_clean).dropna()
print(f"\n全市场等权日收益：含异常 {ew_all.mean():+.6f} vs 剔除异常 {ew_clean.mean():+.6f}"
      f"　差 {d.sum():+.4f}（区间累计）")
print(f"受影响的交易日 {int((d.abs() > 1e-9).sum())} 个，"
      f"单日被拉高最大 {d.max():+.4f}、拉低最小 {d.min():+.4f}")
print("被拉得最狠的 8 天：")
print(d.reindex(d.abs().sort_values(ascending=False).index).head(8).to_string())

# 同一批票在跳变日的盘面收益：若 factor 是真除权，盘面价应同向下跌
r0 = (cl / f).pct_change(fill_method=None)
tab = pd.DataFrame({"ret_adj": ret.stack()[bad.stack()],
                    "ret_raw": r0.stack()[bad.stack()]}).reset_index()
tab.columns = ["day", "code", "ret_adj", "ret_raw"]
tab = tab[np.isfinite(tab["ret_raw"])]
print(f"\n异常跳变当日盘面收益分布（n={len(tab)}）：真除权应给出负值")
print(tab["ret_raw"].describe().round(4).to_string())
print(f"其中 |盘面|<=10% 占 {(tab['ret_raw'].abs() <= 0.10).mean():.1%}"
      f"　盘面同向下跌占 {(tab['ret_raw'] < -0.02).mean():.1%}")
