# -*- coding: utf-8 -*-
"""只读探针：折溢价"可读只数 851"到底是全池还是 QDII 段？

看板 caption 写的是"基本是 513 跨境段"，而 metrics 打印 851 只 —— 两者只能有一个对。
统计每只标的**自己最近可读日**的收盘/净值，并按代码段与日期分组看清楚覆盖结构。
"""
import os
import sys

os.chdir(os.path.join(os.path.dirname(__file__), "..", "..", "etf", "v1", "src"))
sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("data"))

import _bootstrap  # noqa: F401,E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import etf_admission as EA  # noqa: E402
import config  # noqa: E402

pd.set_option("display.width", 200)

pool = EA.load_pool(verbose=False)
m = EA.build_matrices(pool)
prem = EA.premium_matrix(m)
import fetch_etf_risk_panel as RP  # noqa: E402
raw = RP.read_long(RP.NAV_THS_OUT)
print(f"[长表] 净值 {len(raw)} 行，采集日 {sorted(map(str, raw['date'].unique()))[-5:]}")
print(f"[长表] 净值所属日 {sorted(str(x)[:10] for x in raw['nav_date'].dropna().unique())[-5:]}")
print(f"[矩阵] prem shape={prem.shape} 非空格子 {int(prem.notna().sum().sum())}")

per_date = prem.notna().sum()
print("\n[按日期] 可算折溢价的只数（最近 8 个有数据的日期）")
print(per_date[per_date > 0].tail(8).to_string())

last = prem.apply(lambda s: s.last_valid_index())
cnt = last.value_counts().head(5)
print("\n[按每只最近可读日] 分组只数")
print(cnt.to_string())

d = cnt.index[0]
codes = [c for c in prem.columns if last.get(c) is not None and last[c] == d]
print(f"\n[最近可读日 {d}] 共 {len(codes)} 只；代码段分布：")
seg = pd.Series([str(c)[:3] for c in codes]).value_counts().head(10)
print(seg.to_string())
pv = prem.loc[d, codes].dropna()
print(f"\n该日截面：n={pv.size} 中位 {pv.median() * 100:+.3f}%  "
      f"P5 {pv.quantile(.05) * 100:+.3f}%  P95 {pv.quantile(.95) * 100:+.3f}%")
q = pv[[c for c in codes if str(c).startswith("513")]]
o = pv.drop(index=q.index, errors="ignore")
print(f"  513 段 n={q.size} 中位 {q.median() * 100:+.3f}% | "
      f"非 513 n={o.size} 中位 {o.median() * 100:+.3f}% 最大绝对 {o.abs().max() * 100:.2f}%")

# 看板用的口径：每只自己最近可读日拼起来的截面
pv_all = prem.apply(lambda s: s.dropna().iloc[-1] if s.notna().any() else np.nan).dropna()
print(f"\n[看板口径] 逐只最近可读日：n={pv_all.size} 中位 {pv_all.median() * 100:+.3f}% "
      f"最大绝对 {pv_all.abs().max() * 100:.2f}%")

print("\n[极端读数 top12（按绝对值）]")
ex = pv.abs().sort_values(ascending=False).head(12)
for c in ex.index:
    print(f"  {c}  {pv[c] * 100:+7.2f}%  收盘={float(m['close'].loc['2026-09-23', c]):.3f} "
          f"净值={float(EA.premium_matrix(m).index[-1]) if False else ''}")
navm = RP.load_nav_matrix()
navd = navm.loc[navm.index.max()]
for c in ex.index:
    print(f"  {c} 收盘={float(m['close'].loc['2026-09-23', c]):.4f} "
          f"净值={float(navd.get(c)):.4f} 前收={float(m['close'].loc['2026-09-22', c]):.4f} "
          f"当日涨跌={(m['close'].loc['2026-09-23', c] / m['close'].loc['2026-09-22', c] - 1) * 100:+.2f}%")
print("\n[按采集日] prem 可算只数")
print(prem.notna().sum(axis=1)[lambda s: s > 0].to_string())
