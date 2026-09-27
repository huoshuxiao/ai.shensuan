# -*- coding: utf-8 -*-
"""一次性体检：重建 ETF 主线池缓存，看放宽到 max_count=100 之后进得来几只。

用法（工作区根目录）：
    /usr/bin/python3.10 shell/build_universe_check_0924.py
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "etf", "v1", "src")
sys.path.insert(0, _SRC)
import _bootstrap  # noqa: F401  挂好本线与 common 的裸模块导入路径

import pandas as pd
from etf_universe import ETFUniverse
from config import ETF_FILTER, UNIVERSE_CACHE

u = ETFUniverse()
df = u.build(use_cache=False)
print(f"\n=== 落盘 {UNIVERSE_CACHE}：{len(df)} 只 ===")
print(df[["code", "name", "index_group", "list_date", "median_amount",
          "ann_vol"]].head(30).to_string())
print("\n指数组覆盖：", df["index_group"].nunique(), "个不同指数")
print("上市日期分布（按年）：\n",
      pd.to_datetime(df["list_date"]).dt.year.value_counts().sort_index())
print("年化波动分布：\n", df["ann_vol"].describe())
print("中位成交额分布（亿元）：\n",
      (df["median_amount"] / 1e8).describe())
print("max_count =", ETF_FILTER["max_count"],
      "min_avg_amount =", ETF_FILTER["min_avg_amount"],
      "min_ann_vol =", ETF_FILTER["min_ann_vol"])
