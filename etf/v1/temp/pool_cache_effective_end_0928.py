# -*- coding: utf-8 -*-
"""只读数：日更之后，回测实际拿到的末日是哪一天（09-28 #53 顺带）

为什么要有：链路 ① 今天只把**深市**镜像推到了 09-28（沪市 483 只 `uptodate`、池缓存
20 只 `uptodate`），而 `DataLoader.load()` 的取数顺序是 缓存 → 镜像 → 网络源，
两级都有闸（`_cache_is_stale` 比的是 `_mirror_batch_end()` = 镜像目录的**最大**末日；
`_load_mirror` 的容忍是 `MIRROR_MAX_LAG_DAYS=7` 个**日历日**）。
⇒ "盘里的文件止于哪天" 与 "回测吃到的末日" 不是一个数，必须按代码真走一遍判断分支。

本脚本**一行都不写**：不调 `load()`（它会把镜像自愈回写进 `data/cache/`），只复用
`data_loader` 的判据函数读文件末日。跑法：
    /usr/bin/python3.10 etf/v1/temp/pool_cache_effective_end_0928.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "etf", "v1", "src")
sys.path.insert(0, SRC)
os.chdir(SRC)

import _bootstrap  # noqa: E402,F401  必须先于项目模块导入
import pandas as pd  # noqa: E402
from data_loader import (DataLoader, MIRROR_MAX_LAG_DAYS,  # noqa: E402
                         _last_date)
from config import CACHE_DIR, UNIVERSE_ALL_DIR  # noqa: E402

d = DataLoader(freq="daily")
batch = d._mirror_batch_end()
rows = []
for fn in sorted(f for f in os.listdir(CACHE_DIR) if f.endswith("_daily.csv")):
    code = fn.split("_")[0]
    cache_last = _last_date(os.path.join(CACHE_DIR, fn))
    if not d._cache_is_stale(pd.DataFrame({"x": [cache_last]},
                                          index=[cache_last])):
        rows.append((code, cache_last, "缓存未过期", cache_last))
        continue
    mpath = os.path.join(UNIVERSE_ALL_DIR, f"{code}_daily.csv")
    mlast = _last_date(mpath) if os.path.exists(mpath) else ""
    if not mlast:
        rows.append((code, cache_last, "镜像无此代码 → 回源", ""))
        continue
    lag = (pd.Timestamp(batch) - pd.Timestamp(mlast)).days
    if lag > MIRROR_MAX_LAG_DAYS:
        rows.append((code, cache_last, f"镜像落后 {lag} 天 > {MIRROR_MAX_LAG_DAYS} → 回源", ""))
    else:
        rows.append((code, cache_last, f"用镜像（落后 {lag} 天）", mlast))

out = pd.DataFrame(rows, columns=["代码", "缓存末日", "分支", "回测吃到的末日"])
print(f"镜像批次末尾（`_mirror_batch_end()`）= {batch}　"
      f"镜像滞后容忍 MIRROR_MAX_LAG_DAYS = {MIRROR_MAX_LAG_DAYS} 个日历日")
print(out.to_string(index=False))
eff = pd.to_datetime(out["回测吃到的末日"].replace("", pd.NA)).dropna()
print(f"\n[读数] {len(out)} 只主线池缓存：回测末日中位 {eff.median():%Y-%m-%d}，"
      f"最早 {eff.min():%Y-%m-%d}，最晚 {eff.max():%Y-%m-%d}")
for mkt, lab in (("5", "沪(5 开头)"), ("1", "深(1 开头)")):
    sub = out[out["代码"].str.startswith(mkt)]
    if len(sub):
        print(f"  {lab} {len(sub)} 只 → 末日集合 {sorted(set(sub['回测吃到的末日']))}")
print("[判据] 上面两行的末日集合**不相等**，就意味着截面回测里有一侧多/少一根 bar。")
