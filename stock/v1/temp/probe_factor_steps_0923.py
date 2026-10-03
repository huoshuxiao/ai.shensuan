# -*- coding: utf-8 -*-
"""探针④：$factor 的「台阶」质量。

level 错不影响收益（pct_change 把它约掉了），只有 step 错才凭空造跳空。
判据：除息日一年没几天，单日复权因子跳变 > 2% 就等价于「当天派了 2% 以上的息」，
超过 5% 在 A 股几乎不存在（除零碎赠送外），若成片出现说明因子是合成的。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "stock", "v1", "src"))
import _bootstrap  # noqa: F401,E402
import numpy as np
import pandas as pd

from config import ASHARE_DAILY_H5

raw = pd.read_hdf(ASHARE_DAILY_H5, key="data")[["$factor", "$close"]]
f = raw["$factor"].unstack("instrument").sort_index()
yr = f.index >= (f.index.max() - pd.Timedelta(days=365))
step = f.pct_change(fill_method=None)[yr]

print(f"区间 {f.index[yr].min().date()} ~ {f.index.max().date()}，"
      f"交易日 {int(yr.sum())}，个股 {f.shape[1]}")
n = step.notna()
print(f"factor 有跳变的 (票,日) 组合: {int(n.values.sum()):,} 个"
      f"　占全部样本 {n.values.sum() / n.size:.2%}")
a = step.abs()
for th in (0.0001, 0.005, 0.02, 0.05, 0.10, 0.30):
    k = int((a.values >= th).sum())
    print(f"  |step| >= {th:>6.1%}: {k:>7,} 个")

# 同一天同一只票的「价格跳空」应与 factor 跳变互相抵消：
# 复权收益 ≈ 盘面收益 + factor 收益。若 factor 是合成的，两者相加不再等于真实涨跌。
cl = raw["$close"].unstack("instrument").sort_index()[yr]
ret_adj = cl.pct_change(fill_method=None)
rawp = (cl / f[yr])
ret_raw = rawp.pct_change(fill_method=None)
gap = (ret_adj - ret_raw).abs()          # 除息日应非 0，其他日应≈0
print(f"\n复权收益 - 盘面收益 的逐日 |差|（正常只在除息日非 0）:")
for th in (0.001, 0.01, 0.05, 0.10):
    print(f"  > {th:>5.1%}: {int((gap.values > th).sum()):>7,} 个"
          f"（{(gap.values > th).sum() / gap.size:.2%}）")
big = gap[gap > 0.05].stack().sort_values(ascending=False)
print(f"\n|差|>5% 的前 10 个（这些日子的收益里混进了非交易性跳变）:")
for (d, code), v in big.head(10).items():
    print(f"  {d.date()} {code}  复权 {ret_adj.loc[d, code]:+.3f}  "
          f"盘面 {ret_raw.loc[d, code]:+.3f}  |差| {v:.3f}  "
          f"factor {f.loc[d, code]:.6f} 价 {rawp.loc[d, code]:.2f} 元")
