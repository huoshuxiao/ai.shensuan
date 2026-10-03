# -*- coding: utf-8 -*-
"""50 只收成 5 只之前先量三件事：换血率、单票风险、彼此相关（09-24）

组合层入口的档位是 `STOCK_PORT_TOP_N`（默认 50,100,200），**从没跑过 top5**，
所以「5 只行不行」不能靠外推。这一跑补的是回测给不了的三个数：

 1. **名单换血率**：相邻两个收盘日（09-22 / 09-23）各自取 top5/top10/top50，
    看隔一天还剩几只。5 只的名单若天天换，就要天天付双边费、也天天被打脸；
    这个数回测里的 `one_way_turnover` 是月/周频调仓口径，看不见日频换血。
 2. **单票近 60 日波动**：等权 5 只 ⇒ 单票占 20%，它自己的日波动直接乘 0.2 进组合。
 3. **两两相关**：5 只的平均两两相关 vs 50 只的平均两两相关。相关越接近 1，
    「分散」越是一句空话；这是集中度之外真正决定尾部风险的那一项。

排序键与生产同源：保留池（当日 signal_*.csv 的 keep）内 `ts_std($volume,20)` 升序，
用面板裸 pandas 复算（shell/audit_buylist_0924.py 已证它与 CSV 逐票一致、顺序相同）。
行业/板块这里**判不了**：面板没有行业字段，5 只会不会全挤在同一板块属于已知盲区。
"""
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, SRC)

import glob
import os

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401

from config import ASHARE_SIGNAL_DIR
from ashare_screen import BUY_EXPR, build_matrices, load_panel

SIG_DIR = ASHARE_SIGNAL_DIR if os.path.isabs(ASHARE_SIGNAL_DIR) else os.path.join(
    "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1", ASHARE_SIGNAL_DIR)
sig_files = sorted(glob.glob(os.path.join(SIG_DIR, "signal_*.csv")))
assert len(sig_files) >= 2, f"要两天的名单才量得出换血，现在只有 {sig_files}"
d_prev, d_last = [os.path.basename(f)[7:15] for f in sig_files[-2:]]
tp, tl = [pd.Timestamp(f"{x[:4]}-{x[4:6]}-{x[6:]}").normalize() for x in (d_prev, d_last)]

wide, _b = load_panel()
mtx = build_matrices(wide)
vol, ret = mtx["volume"], mtx["ret_open"]
q = vol.rolling(20).std()                     # 与 BUY_EXPR=ts_std(volume,20) 同一条轴

def pick(day, sig_file, n):
    """当日保留池内的前 n 名：signal_*.csv 本身就是闸门后的可投池（5225 行），
    keep 列就是量能剔除后的保留池（09-23 是 2928），不在此重算判据"""
    s = pd.read_csv(sig_file, dtype={"code": str}).set_index("code")
    keep = set(s.index[s["keep"].astype(bool)])
    v = q.loc[day].astype("float64")
    v = v[v.index.isin(keep)]
    v = v[vol.loc[day].reindex(v.index).gt(0)]          # 当日得真有成交
    return list(v.sort_values().index[:n])

print(f"\n===== 1 换血率：{d_prev} vs {d_last}（保留池内 {BUY_EXPR} 升序）=====")
pools = {}
for n in (5, 10, 20, 50):
    a, b = pick(tp, sig_files[-2], n), pick(tl, sig_files[-1], n)
    inter = set(a) & set(b)
    pools[n] = b
    print(f"  top{n:<3d}：隔天仍在名单 {len(inter)}/{n}（留 {len(inter)/n:.0%}、换 {n-len(inter)} 只）")

# ===== 2/3 单票风险与相关：用最新那份名单 =====
last = pd.read_csv(sig_files[-1], dtype={"code": str}).set_index("code")
buy = pd.read_csv(os.path.join(SIG_DIR, f"buy_{d_last}.csv"), dtype={"code": str}).set_index("code")
r60 = ret.loc[:tl].tail(60)
codes50 = list(buy.index)
codes5 = codes50[:5]

def risk(codes, tag):
    sub = r60[codes].astype("float64")
    sd = sub.std() * np.sqrt(252)
    cm = sub.corr()
    iu = np.triu_indices_from(cm.values, 1)
    pair = float(cm.values[iu].mean())
    print(f"\n{tag}（{len(codes)} 只）：单票年化波动 中位 {sd.median():.1%}、最大 {sd.max():.1%}"
          f"（{sd.idxmax()}）；等权组合年化波动 {sub.mean(axis=1).std()*np.sqrt(252):.1%}；"
          f"两两相关均值 {pair:.3f}")
    nav = (1.0 + sub.mean(axis=1)).cumprod()
    dd = float((nav / nav.cummax() - 1).min())
    print(f"    近 60 日等权：最大回撤 {dd:.1%}、最差单日 {float(sub.mean(axis=1).min()):.2%}")

risk(codes5, "===== 2/3 top5 名单")
risk(codes50, "top50 名单")
print(f"\ntop5 是 top50 的前 5 名：{codes5}")
