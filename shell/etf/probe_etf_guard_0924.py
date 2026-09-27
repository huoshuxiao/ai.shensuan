# -*- coding: utf-8 -*-
"""一次性探针 2：定「收益护栏」「准入门槛」的实测依据。

1. 极端收益（|r|>0.11）长在哪些代码、是否孤立单日 → 判定是份额折算还是真行情
2. 近零波动（货基型）标的有多少只 → 是否需要波动率下限
3. 成交额分布 → 组合层容量闸门取值
4. NaN 情况（volume/amount 缺失比例）
"""
import glob
import os

import numpy as np
import pandas as pd

D = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/data/universe_all"
pool = {}
for f in sorted(glob.glob(os.path.join(D, "*_daily.csv"))):
    code = os.path.basename(f).split("_")[0]
    df = pd.read_csv(f, encoding="utf-8-sig")
    df["date"] = pd.to_datetime(df["date"])
    pool[code] = df.set_index("date").sort_index()

print("池:", len(pool))

# ---- 1. 极端收益 ----
rows = []
for code, df in pool.items():
    r = df["close"].pct_change(fill_method=None)
    m = r.abs() > 0.11
    if m.any():
        rows.append((code, int(m.sum()), len(df), float(r[m].abs().max()),
                     str(r[m].index[0].date())))
rows.sort(key=lambda t: -t[1])
print("\n|r|>0.11 的标的数:", len(rows), " 总样本:", sum(t[1] for t in rows))
for t in rows[:12]:
    print("   code=%s 次数=%d/%d 最大|r|=%.3f 首次=%s" % t)
print("  只出现 1 次的标的:", sum(1 for t in rows if t[1] == 1))
print("  占比>5% K线的标的:", sum(1 for t in rows if t[1] / max(t[2], 1) > 0.05))

# 份额折算特征：价格数量级跳变（>3x 或 <1/3），成交额不一定跳
jumps = []
for code, df in pool.items():
    c = df["close"]
    q = c / c.shift(1)
    m = (q > 3) | (q < 1 / 3)
    if m.any():
        jumps.append((code, int(m.sum()), float(q[m].max()), float(q[m].min()),
                      str(c[m].index[0].date())))
print("\n价格数量级跳变(>3x 或 <1/3) 的标的数:", len(jumps))
for t in sorted(jumps, key=lambda x: -x[1])[:8]:
    print("   code=%s 次数=%d 最大比=%.2f 最小比=%.3f 首次=%s" % t)

# ---- 2. 近零波动 ----
vols = {}
for code, df in pool.items():
    r = df["close"].pct_change(fill_method=None).dropna()
    if len(r) >= 60:
        vols[code] = float(r.tail(250).std())
vs = pd.Series(vols)
print("\n250 日年化波动分位:",
      {q: round(float(vs.quantile(q) * np.sqrt(252)), 4)
       for q in (0.005, 0.01, 0.02, 0.05, 0.1, 0.5)})
for th in (0.01, 0.02, 0.03, 0.05):
    print("   年化波动<%.2f 的标的数: %d" % (th, int((vs * np.sqrt(252) < th).sum())))

# ---- 3. 成交额 ----
last_amt = {}
for code, df in pool.items():
    a = pd.to_numeric(df["amount"], errors="coerce").tail(20)
    if a.notna().sum() >= 5:
        last_amt[code] = float(a.mean())
aseries = pd.Series(last_amt)
print("\n近 20 日成交额(元)分位:",
      {q: round(float(aseries.quantile(q)) / 1e6, 2)
       for q in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9)})
for th in (1e6, 3e6, 1e7, 3e7):
    print("   成交额<%.0e 的标的数: %d" % (th, int((aseries < th).sum())))

# ---- 4. NaN ----
tot = n_nan = 0
nanvol = []
for code, df in pool.items():
    for c in ("open", "high", "low", "close", "volume", "amount"):
        if c in df:
            tot += len(df)
            n_nan += int(df[c].isna().sum())
    nanvol.append(float(df["volume"].isna().mean()))
print("\nOHLCV 整体 NaN 比例: %.4f" % (n_nan / tot))
print("volume 全 NaN 的标的数:", sum(1 for v in nanvol if v == 1.0))
print("volume NaN 比例>10% 的标的数:", sum(1 for v in nanvol if v > 0.1))

# ---- 5. 因子求值耗时（单标的 26 条库内表达式）----
import time
import sys
sys.path.insert(0, "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/src")
os.environ.setdefault("ETF_DATA_DIR", "/tmp/etfprobe_data")
from factor_dsl import safe_eval  # noqa: E402
import csv  # noqa: E402
lib = list(csv.DictReader(open(
    "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/data/library/factor_library.csv",
    encoding="utf-8-sig")))
exprs = [r["expr"] for r in lib if r.get("expr")]
one = pool[sorted(pool)[0]].rename(columns=str.lower)
t0 = time.time()
ok = 0
for e in exprs:
    try:
        s = safe_eval(e, one)
        ok += int(s.notna().sum() > 0)
    except Exception:
        pass
dt = time.time() - t0
print("\n库内 %d 表达式：单标的可求值 %d 条，耗时 %.2fs → 871 只估计 %.0fs"
      % (len(exprs), ok, dt, dt * 871))
print("表达式样例:", exprs[:3])
