# -*- coding: utf-8 -*-
"""探针③：按代码段核对单位一致性。

判据（与真实行情无关，纯内部自洽）：**单票日成交额不应由股价决定**。
若某板块的成交额与股价同向放大 ~100 倍，说明该板块 $volume 记的是股而非手，
成交额被多算了一个股价因子。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "stock", "v1", "src"))
import _bootstrap  # noqa: F401,E402
import numpy as np
import pandas as pd

from config import ASHARE_DAILY_H5

raw = pd.read_hdf(ASHARE_DAILY_H5, key="data")
dt = raw.index.get_level_values("datetime")
d = raw.loc[dt.max()]
if d.index.nlevels == 2:
    d = d.droplevel("datetime")
cl, vol, fac = d["$close"], d["$volume"], d["$factor"]
ok = cl.notna() & vol.notna() & fac.notna() & (fac > 0) & (cl > 0) & (vol > 0)
r = pd.DataFrame({
    "raw": (cl / fac)[ok], "vol": vol[ok],
    "amt100": ((cl / fac) * vol * 100)[ok],
}).assign(pref=lambda x: [i[:4] for i in x.index])
r["board"] = r["pref"].map(lambda p: {
    "SH60": "沪主板", "SZ00": "深主板", "SZ30": "创业板",
    "SH68": "科创板", "BJ8": "北交所", "BJ4": "北交所"}.get(p, p))

g = r.groupby("board").agg(
    n=("raw", "size"), 中位价=("raw", "median"), 中位量=("vol", "median"),
    中位额亿=("amt100", lambda x: x.median() / 1e8),
    p95额亿=("amt100", lambda x: x.quantile(.95) / 1e8),
    合计万亿=("amt100", lambda x: x.sum() / 1e12))
print("=== 分板块（$volume ×100 口径）===")
print(g.round(2).to_string())

# 每亿元成交额需要多少手：单位一致时该比值应与股价无关
r["amt_per_price"] = r["amt100"] / r["raw"]          # = vol*100，纯股数
print("\n=== 关键判据：成交额/股价 = 成交股数（应与板块股价水平无关）===")
print(r.groupby("board").agg(
    中位成交股数万股=("amt_per_price", lambda x: x.median() / 1e4),
    中位股价=("raw", "median")).round(1).to_string())

# 反证：若创业板/科创板 volume 其实是「股」，除以 100 后各板块成交额是否同量级
adj_amt = r["amt100"].copy()
adj_amt[r["board"].isin(["创业板", "科创板"])] /= 100.0
print("\n=== 假设：30/68 的 $volume 单位是「股」（即再 /100）===")
print(pd.DataFrame({"中位额亿": adj_amt / 1e8, "board": r["board"]})
      .groupby("board").agg(中位额亿=("中位额亿", "median"),
                            合计万亿=("中位额亿", lambda x: x.sum() / 1e4)).round(3).to_string())
print(f"全市场合计（混合单位修正后）: {adj_amt.sum() / 1e12:.2f} 万亿元")

# 股价分位 × 成交额分位：单位一致时相关系数应接近 0
print("\n=== corr(log 股价, log 成交额) 按板块（单位污染会给出接近 +1 的假相关）===")
for b, sub in r.groupby("board"):
    if len(sub) > 30:
        print(f"  {b}: {np.corrcoef(np.log(sub['raw']), np.log(sub['amt100']))[0, 1]:+.3f}"
              f"   （修正后 "
              f"{np.corrcoef(np.log(sub['raw']), np.log(adj_amt[sub.index]))[0, 1]:+.3f}）")
