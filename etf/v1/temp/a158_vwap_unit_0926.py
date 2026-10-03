# -*- coding: utf-8 -*-
"""探针：ETF 池里 `amount/volume`（当日均价）与 `$close`（前复权）的比例稳不稳

为什么先量这个：Alpha158 第 158 条 `VWAP0 = $vwap/$close` 在股票线因为面板没有 vwap
被跳过，本线 CSV 有 `amount`+`volume` 看着能补上。但这两列**不在同一个价格基准上**：
`close` 是前复权（折算日会被整体缩放），`amount/volume` 是当天真实成交均价。如果比例
在一只基金的历史上稳定 ⇒ 缩放两边同乘、比值不变，VWAP0 可算；如果逐年漂 ⇒ 这条因子
读到的主要是"复权台阶"而不是价格形态，进池就是掺假。

只读，不写任何权威产物。
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

UNIV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "..", "etf", "v1", "data", "universe_all")
UNIV = os.path.abspath(UNIV)

rows = []
files = sorted(glob.glob(os.path.join(UNIV, "*_daily.csv")))
for p in files:
    try:
        df = pd.read_csv(p, encoding="utf-8-sig", parse_dates=["date"])
    except Exception as e:                      # 坏文件本身也是要报的读数
        print(f"  ! 读不了 {os.path.basename(p)}: {type(e).__name__}: {e}")
        continue
    if len(df) < 120 or df["volume"].le(0).any():
        continue
    vwap = df["amount"] / df["volume"]
    ratio = vwap / df["close"]
    ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()
    if len(ratio) < 120:
        continue
    yr = df.loc[ratio.index, "date"].dt.year
    med_by_year = ratio.groupby(yr).median()
    rows.append({"code": os.path.basename(p)[:6],
                 "med_ratio": float(ratio.median()),
                 "year_min": float(med_by_year.min()),
                 "year_max": float(med_by_year.max()),
                 "spread": float(med_by_year.max() - med_by_year.min()),
                 "n_years": int(med_by_year.size)})

r = pd.DataFrame(rows)
print(f"可算 {len(r)} / {len(files)} 只\n")
print("单日 ratio（vwap/close）分布：")
print(r["med_ratio"].describe(percentiles=[.01, .05, .5, .95, .99]).to_string())
print("\n同一只基金「逐年中位数」的极差 spread（=复权基准漂移的直接读数）：")
print(r["spread"].describe(percentiles=[.5, .9, .95, .99]).to_string())
print(f"\nspread > 0.05（比例漂 5% 以上 ⇒ VWAP0 读成复权台阶）: "
      f"{(r['spread'] > 0.05).sum()} 只 / {len(r)} 只 "
      f"= {(r['spread'] > 0.05).mean()*100:.1f}%")
print("\n漂得最狠的 10 只：")
print(r.sort_values("spread", ascending=False).head(10).to_string(index=False))
