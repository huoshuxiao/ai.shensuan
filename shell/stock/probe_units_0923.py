# -*- coding: utf-8 -*-
"""一次性探针:核对 qlib cn_data 的 $volume 单位与盘面价 reconstruction。

判据不是「茅台看起来对」这种单点印象,而是同一天的全市场合计成交额
必须落在真实量级(万亿 CNY),以及个股成交额 Top 榜不能出现 4 位数的亿元。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "stock", "v1", "src"))
import _bootstrap  # noqa: F401,E402
import numpy as np
import pandas as pd

from config import ASHARE_DAILY_H5

raw = pd.read_hdf(ASHARE_DAILY_H5, key="data")
print("列:", list(raw.columns))

dt = raw.index.get_level_values("datetime")
last = dt.max()
d = raw.loc[last]
inst = np.asarray(d.index.get_level_values("instrument"))
is_idx = np.array([(s[:2] == "SH" and s[2:].startswith("000"))
                   or (s[:2] == "SZ" and s[2:].startswith("399")) for s in inst])
d = d.loc[~is_idx]
inst = inst[~is_idx]

cl, vol, fac = d["$close"], d["$volume"], d["$factor"]
ok = cl.notna() & vol.notna() & fac.notna() & (fac > 0) & (cl > 0) & (vol > 0)
cl, vol, fac = cl[ok], vol[ok], fac[ok]
r = cl / fac

def q(x, name, div=1.0):
    print(f"  {name}: " + "  ".join(
        f"p{p}={x.quantile(p / 100) / div:.4g}" for p in (5, 25, 50, 75, 95)) +
        f"  max={x.max() / div:.4g}")

print(f"\n最后一天 {last}，有值个股 {int(ok.sum())} 只")
print("价格（元）:")
q(cl, "  $close(复权)")
q(r, "  raw=close/factor")
q(r / cl, "  raw/adj 比值")
print("量:")
q(vol, "  $volume")

a100 = r * vol * 100
a1 = r * vol
for name, a in ("成交额 = raw*vol*100", a100), ("成交额 = raw*vol", a1):
    print(f"\n{name}:")
    q(a / 1e8, "  单票(亿元)")
    print(f"  全市场合计: {a.sum() / 1e12:.4f} 万亿元")

# 成交额榜：真实市场前 10 名单票在 100~500 亿量级，不该出现千亿元
for name, a in ("×100", a100), ("×1", a1):
    top = a.sort_values(ascending=False).head(12)
    print(f"\n成交额 Top12（{name}）:")
    for code, v in top.items():
        i = list(inst).index(code) if code in list(inst) else -1
        print(f"  {code}  raw={r[code]:>9.2f}  adj={cl[code]:>9.2f}  "
              f"fac={fac[code]:.5f}  vol={vol[code]:>12.0f}  额={v / 1e8:>8.1f}亿")

# 换手率视角：成交额/流通市值 无法算（无市值列），改用「高价股是否被 factor 抬爆」
print("\nraw/adj 最大的 10 只（若 factor 归一化异常会在这里暴露）:")
ratio = (r / cl).sort_values(ascending=False).head(10)
for code, v in ratio.items():
    print(f"  {code}  raw/adj={v:>7.2f}  adj={cl[code]:>9.2f}  raw={r[code]:>9.2f}  "
          f"vol={vol[code]:>12.0f}  额x100={r[code] * vol[code] * 100 / 1e8:>8.1f}亿")
print("\n$factor 分布:")
q(fac, "  factor")
print(f"  末两日 factor 是否一致（抽 5 只）:")
prev = sorted(set(dt))[-2]
p = raw.loc[prev].droplevel("instrument") if raw.loc[prev].index.nlevels == 2 else raw.loc[prev]
for code in list(ratio.index[:5]):
    try:
        f1 = raw.loc[(prev, code), "$factor"]
        f2 = raw.loc[(last, code), "$factor"]
        c1 = raw.loc[(prev, code), "$close"]
        c2 = raw.loc[(last, code), "$close"]
        print(f"    {code}  factor {f1:.6f} -> {f2:.6f}   close {c1:.4f} -> {c2:.4f}")
    except Exception as e:
        print(f"    {code}  取不到: {type(e).__name__} {e}")
