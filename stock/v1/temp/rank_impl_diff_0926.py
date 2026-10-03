# -*- coding: utf-8 -*-
"""C+B 第一步：`rank` 的两种写法在全市场上到底差多少（决定「新扫进来的 RANK* 用哪种」）

背景（已实测，不是假设）：**在库因子一条都没用 `rank`** —— 股票在库 21 条、ETF 的 8 条、
ETF 注册库 35 条，表达式里 `rank(` 出现 **0 次** ⇒ 换写法对**已有读数**的影响是空集。
剩下的问题只有一个：选项C 会新带进 5 条 `RANK5~60 = Rank($close, n)`，这 5 条用哪种写法。

两种写法的分叉点只有一个 —— **窗口里有没有缺值（停牌）**：
- 本线现写法：`rolling.apply(raw=False)` 里 `pd.Series(x).rank(pct=True)`，**先剔 NaN**
  再按「非缺值个数」算百分位（`factor_dsl.py:42-44`）；
- 快写法：`raw=True` + 双重 `argsort`，NaN 被排到**最大**那一边，分母**固定 n**。
无缺值时两者逐位相等；有缺值时同一个数最多差 (窗口内缺值数)/n。

三段量：① 格子上有多大一片不同（小样本，秒级）② 全市场逐日截面 IC 差多少（分钟级）
③ 四窗口「稳不稳」读数会不会因此变结论 —— 用的是产品里那一份 `stability_stats`，不另写尺子。

只读：不写任何权威产物。
"""
import os
import resource
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "..", "stock", "v1", "src"))
sys.path.insert(0, SRC)
os.chdir(SRC)

from run_ashare_factor_eval import load_panel, evaluate      # noqa: E402  同一把尺子
from factor_dsl import FACTOR_DSL                            # noqa: E402
import run_ashare_rolling_ic as R                            # noqa: E402  稳不稳读数用产品那一份

SLOW = FACTOR_DSL["rank"]


def fast_rank(s, n):
    """窗口百分位的快写法：NaN 当最大值、分母固定 n（与慢写法只在含缺值窗口分叉）。"""
    return s.rolling(int(n)).apply(lambda w: (np.argsort(np.argsort(w))[-1] + 1) / float(n),
                                   raw=True)


def rss_gb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0 / 1024.0


W = 20                                   # 量 n=20 这一条（qlib RANK20 = Rank($close, 20)）
EXPR = f"rank(close, {W})"
task = [{"name": "RANK20", "expr": EXPR}]

t0 = time.time()
pool = load_panel()
print(f"[面板] {len(pool)} 只，load_panel {time.time()-t0:.0f}s　RSS {rss_gb():.2f}GB")

# ===== ① 格子层面：多大一片窗口含缺值（分叉的全部来源）=====
print("\n===== ① 小样本格子差（stride=95 ⇒ 60 只；缺值进窗才会分叉）=====")
items = list(pool.items())
tiny = {k: v for i, (k, v) in enumerate(items) if i % 95 == 0}
for w in (5, 20, 60):
    nd = nt = nc = 0
    worst = 0.0
    for k, d in tiny.items():
        c = d["close"]
        a = SLOW(c, w)
        b = fast_rank(c, w)
        m = (a - b).abs()
        nt += int(m.notna().sum())
        nd += int((m > 1e-12).sum())
        nc += int(c.isna().sum())
        worst = max(worst, float(m.max(skipna=True) or 0.0))
    print(f"  n={w:<3} 值不同的格子 {nd}/{nt} = {nd/nt:6.2%}　close 整列缺值占比 "
          f"{nc/nt:5.2%}　单格最大差 {worst:.4f}（= 窗口内缺值数/{w}）")

# ===== ② 全市场逐日截面 IC：先慢后快（慢的那一遍就是 38~46 分钟的大头）=====
sink_slow, sink_fast = {}, {}
t = time.time()
row_s = evaluate(pool, task, series_sink=sink_slow)[0]
ts = time.time() - t
print(f"\n===== ② 全市场 {len(pool)} 只、{EXPR} =====")
print(f"  [慢写=本线现写法] {ts:.0f}s　status={row_s.get('status')}　"
      f"cs_rank_ic_mean={row_s.get('cs_rank_ic_mean'):.6f}")

FACTOR_DSL["rank"] = fast_rank
t = time.time()
row_f = evaluate(pool, task, series_sink=sink_fast)[0]
tf = time.time() - t
FACTOR_DSL["rank"] = SLOW                      # 立刻复原，不把补丁留给后面的步骤
print(f"  [快写=argsort]    {tf:.0f}s　status={row_f.get('status')}　"
      f"cs_rank_ic_mean={row_f.get('cs_rank_ic_mean'):.6f}")
print(f"  ⇒ 单条快 {ts/tf:.1f}×；按本条单价外推 5 条 RANK*：慢 ≈{ts*5/3600:.2f}h、快 ≈{tf*5/3600:.2f}h")

# ===== ③ 四窗口「稳不稳」：产品那一份 stability_stats，两条序列各量一遍 =====
srow = R.stability_stats("RANK20", sink_slow["RANK20"]["rank"], sink_slow["RANK20"]["pearson"])
frow = R.stability_stats("RANK20", sink_fast["RANK20"]["rank"], sink_fast["RANK20"]["pearson"])
print("\n===== ③ 四窗口读数对照（同一个因子、两种 rank 写法）=====")
show = ["status", "rank_ic_full", "roll_same_pct", "year_same_pct", "lo_ic_year", "lo_ic",
        "hi_ic_year", "hi_ic", "fold_same_pct", "fold_last", "recent_ic",
        "recent_sign_ok", "drift_ratio", "n_days"]
print(f"  {'字段':<17}{'慢写（现）':>18}{'快写':>18}{'差':>13}")
for k in show:
    a, b = srow.get(k), frow.get(k)
    if isinstance(a, (int, float)) and not isinstance(a, bool) and \
       isinstance(b, (int, float)) and not isinstance(b, bool):
        d = float(b) - float(a)
        tol = 0.0 if ("year" in k or k == "n_days") else 1e-6
        print(f"  {k:<17}{a:>18.6g}{b:>18.6g}{d:>13.3g}{'　←变了' if abs(d) > tol else ''}")
    else:
        print(f"  {k:<17}{str(a):>18}{str(b):>18}{'':>13}{'同' if a == b else '　←变了'}")

ds = (sink_slow["RANK20"]["rank"] - sink_fast["RANK20"]["rank"]).dropna()
print(f"\n  逐日截面 IC 之差：均值 {ds.mean():+.3e}、最大 {ds.abs().max():.3e}"
      f"　不为零的日子 {int((ds.abs() > 1e-15).sum())}/{len(ds)}")
print("  对照量级：环 1 归档「落后一天面板」= 6.4e-05。若这里的差远小于它 ⇒ 换写法比『面板过"
      "了一天』还轻，不构成重跑历史归档的理由。")
print(f"\n[峰值 RSS] {rss_gb():.2f} GB（本机 15GB）")
