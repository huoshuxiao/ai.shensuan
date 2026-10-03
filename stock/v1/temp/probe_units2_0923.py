# -*- coding: utf-8 -*-
"""探针②：用大盘蓝筹钉死 $volume 单位（手 vs 股），并定位 $factor 异常段。

蓝筹的真实日成交额量级是公开常识（工行/中石化 10~30 亿，茅台 40~80 亿），
且这些票上市十年以上、复权因子不会离谱，raw=close/factor 与盘面价应当吻合。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "stock", "v1", "src"))
import _bootstrap  # noqa: F401,E402
import numpy as np
import pandas as pd

from config import ASHARE_DAILY_H5

# 代码: (名称, 真实盘面价量级, 真实单日成交额量级/亿)
CHECKS = {
    "SH600519": ("贵州茅台", 1250, 50),
    "SH601398": ("工商银行", 7.5, 25),
    "SH600028": ("中石化", 6, 12),
    "SH600000": ("浦发银行", 9, 8),
    "SZ300750": ("宁德时代", 300, 90),
    "SH688981": ("中芯国际", 120, 60),
    "SZ000001": ("平安银行", 12, 15),
    "SH600570": ("恒生电子", 30, 12),
    "SH688256": ("寒武纪", 1100, 90),
}

raw = pd.read_hdf(ASHARE_DAILY_H5, key="data")
dt = raw.index.get_level_values("datetime")
last = dt.max()
d = raw.loc[last]
if d.index.nlevels == 2:
    d = d.droplevel("datetime")

print(f"末日 {last}")
print(f"{'代码':10} {'名称':6} {'adj':>9} {'factor':>9} {'raw':>9} "
      f"{'真实价':>8} {'$volume':>13} {'额×100/亿':>10} {'额×1/亿':>9} "
      f"{'真实额/亿':>9} {'单位判':>6}")
for code, (cn, px, amt) in CHECKS.items():
    if code not in d.index:
        print(f"{code:10} {cn:6} 面板无此代码")
        continue
    r = d.loc[code]
    cl, vol, fac = float(r["$close"]), float(r["$volume"]), float(r["$factor"])
    rawp = cl / fac
    a100, a1 = rawp * vol * 100 / 1e8, rawp * vol / 1e8
    # 单位判据：与真实成交额同量级（0.3x~3x）的那一档
    g100 = abs(np.log10(a100 / amt)) if a100 > 0 else 9
    g1 = abs(np.log10(a1 / amt)) if a1 > 0 else 9
    print(f"{code:10} {cn:6} {cl:9.3f} {fac:9.5f} {rawp:9.2f} {px:8.1f} "
          f"{vol:13.0f} {a100:10.1f} {a1:9.1f} {amt:9.1f} "
          f"{'手 x100' if g100 < g1 else '股 x1':>8}")

# factor 异常段的代码前缀分布：定位是哪些板块被复权因子污染
cl_all = d["$close"]
fac_all = d["$factor"]
ok = cl_all.notna() & fac_all.notna() & (fac_all > 0)
ratio = (cl_all[ok] / fac_all[ok]) / cl_all[ok]
pref = pd.Series([i[:4] for i in ratio.index], index=ratio.index)
tab = pd.DataFrame({"raw/adj": ratio, "pref": pref})
print("\nraw/adj 中位数 按代码段（>50 即复权因子异常）:")
print(tab.groupby("pref")["raw/adj"].agg(["count", "median", lambda x: (x > 50).sum()])
        .rename(columns={"<lambda_0>": "n>50"}).sort_values("median", ascending=False).to_string())

# 异常票按成交额贡献排序：闸门只看绝对额，这些票在池内分位会被推到顶
bad = ratio[ratio > 50]
amt_bad = (cl_all[bad.index] / fac_all[bad.index]) * d.loc[bad.index, "$volume"] * 100
print(f"\nraw/adj>50 的票: {len(bad)} 只，其 ×100 成交额合计 "
      f"{amt_bad.sum() / 1e12:.2f} 万亿（占全市场 {amt_bad.sum() / ((cl_all / fac_all) * d['$volume'] * 100).sum() * 100:.0f}%）")
print("其中成交额中位:", f"{amt_bad.median() / 1e8:.1f} 亿", " 全体中位:",
      f"{((cl_all / fac_all) * d['$volume'] * 100).median() / 1e8:.1f} 亿")
