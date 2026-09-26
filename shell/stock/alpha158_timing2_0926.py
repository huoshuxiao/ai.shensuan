# -*- coding: utf-8 -*-
"""选项I 账单第二半·补测：第一轮没测到的两类东西

第一轮（`alpha158_timing_0926.py`）15 条探针覆盖了甲/甲′/乙/丙的大部分，但留了两个洞：
1. **回归族 15 列**（BETA=Slope/$close、RSQR、RESI）压根没探 —— 而 qlib 的 `Slope` 是
   rolling OLS，写法不同代价差一个量级（`polyfit` 逐窗拟合 vs 协方差闭式解）；
2. 第一轮里唯一爆价的 P07_RANK20（402 ms/只，其余全 15~18 ms）是本线 `rank` 的
   `rolling.apply(raw=False)` 实现造成的（`factor_dsl.py:42-44`），不是 Alpha158 本身贵
   —— 不证这一条，「107 条 ≈ 7 小时」这个数就会被读成「这类因子天生贵」。

所以这里探 7 条：`Slope` 的三种写法（polyfit / 点积 / 协方差闭式解）+ 由闭式解派生的
RESI、RSQR + `IdxMax-IdxMin` + **rank 的快写法**。

`协方差闭式解` 依赖一个新算子 `slope(s,n)`（本线 DSL 现在没有），为了能在 `safe_eval` 里
按真实形态跑，这里在**进程内**临时把它塞进 `FACTOR_DSL`（不落盘、不改仓库文件）。

**顺手测出来的 DSL 硬约束**（第一版探针就死在这上面）：`safe_eval` 用
`eval(expr, {"__builtins__": {}}, env)`（`factor_dsl.py:64`），globals 与 locals 分开传 ⇒
表达式里**内联 lambda 看不到 np / pd / 任何列名**（lambda 的自由变量按 globals 解析），
`close.rolling(20).apply(lambda w: np.argmax(w), raw=True)` 直接 `NameError: np`。
所以扩算子必须**注册成函数**（函数对象自带真 `__globals__`），内联写法在这套 DSL 里不通
——这一条会影响「7 个新算子怎么写」的方案形状。

只读：不写任何权威产物。
"""
import json
import os
import resource
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "stock", "v1", "src"))
sys.path.insert(0, SRC)
os.chdir(SRC)

from run_ashare_factor_eval import load_panel, evaluate   # noqa: E402  同一把尺子（它最先 import _bootstrap 铺 sys.path）
from factor_dsl import FACTOR_DSL                         # noqa: E402  裸模块名，靠 _bootstrap 铺出来的 common/src/core


def _slope(s, n):
    """窗口内 y 对「窗口内第几天」的一元线性回归斜率（闭式解，全程向量算）。

    slope = Σ(t-t̄)(y-ȳ)/Σ(t-t̄)² ，t 取绝对序号不影响斜率；
    Σ(t-t̄)² = n(n²-1)/12 ⇒ 窗口内 cov 除以 (n²-1)/12 即为 slope。
    """
    t = pd.Series(np.arange(1, len(s) + 1, dtype=float), index=s.index)
    cov = (t * s).rolling(int(n)).mean() - s.rolling(int(n)).mean() * t.rolling(int(n)).mean()
    return cov / ((n * n - 1) / 12.0)


def _poly_slope(s, n):
    """同一件事的「逐窗 polyfit」写法（qlib 的 Slope 就是这个形态），代价对比用。"""
    return s.rolling(int(n)).apply(lambda w: np.polyfit(np.arange(len(w)), w, 1)[0],
                                   raw=True)


def _dot_slope(s, n):
    """中间形态：逐窗点积（一次 numpy dot，不做最小二乘分解）。"""
    tt = np.arange(float(n)) - (n - 1) / 2.0
    denom = float((tt ** 2).sum())
    return s.rolling(int(n)).apply(lambda w: float(np.dot(w, tt)) / denom, raw=True)


def _rank_fast(s, n):
    """窗口内百分位，`raw=True` + argsort 写法（对照本线现用 raw=False 的慢版）。"""
    return s.rolling(int(n)).apply(lambda w: (np.argsort(np.argsort(w))[-1] + 1) / n,
                                   raw=True)


def _argmax_pos(s, n):
    """窗口内最大值出现在第几天（qlib 的 IdxMax，1..n）。"""
    return s.rolling(int(n)).apply(lambda w: float(np.argmax(w) + 1), raw=True)


def _argmin_pos(s, n):
    return s.rolling(int(n)).apply(lambda w: float(np.argmin(w) + 1), raw=True)


# 仅本进程内注入，用来量代价；不落盘、不改仓库
FACTOR_DSL.update({"slope": _slope, "poly_slope": _poly_slope,
                              "dot_slope": _dot_slope, "rank_fast": _rank_fast,
                              "argmax_pos": _argmax_pos, "argmin_pos": _argmin_pos})
print("[注入] 进程内临时算子 slope/poly_slope/dot_slope/rank_fast/argmax_pos/argmin_pos")

tasks = json.load(open(os.path.join(HERE, "tmp_alpha158", "probe_exprs2_0926.json"),
                       encoding="utf-8"))
print(f"[探测] {len(tasks)} 条（回归族三种写法 + rank 快写法 + IMXD）")

t0 = time.time()
pool_all = load_panel()
n_all = len(pool_all)
load_s = time.time() - t0
print(f"[面板] {n_all} 只，load_panel 用时 {load_s:.0f}s")

STRIDE = 19
lens_all = pd.Series([len(d) for d in pool_all.values()])
items = list(pool_all.items())
sub = {k: v for i, (k, v) in enumerate(items) if i % STRIDE == 0}
del pool_all, items
lens_sub = pd.Series([len(d) for d in sub.values()])
print(f"[样本] stride={STRIDE} ⇒ {len(sub)} 只"
      f"　样本中位 {lens_sub.median():.0f} 行 vs 全表中位 {lens_all.median():.0f} 行"
      f" ⇒ 比值 {lens_sub.median()/lens_all.median():.2f}")


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# ===== 先对表「闭式解 == 逐窗 polyfit」，再谈代价（否则省的是算错的时间）=====
k, d = next(iter(sub.items()))
c = d["close"]
a, b = _slope(c, 20), _poly_slope(c, 20)
diff = float((a - b).abs().max())
print(f"[对表] 窗口 20：闭式解 slope vs 逐窗 polyfit 最大差 = {diff:.3e}"
      f"（{k}，{int(c.notna().sum())} 行）⇒ {'两种写法算的是同一个数' if diff < 1e-8 else '不一致，闭式解写错了'}")
q = _rank_fast(c, 20)
r = FACTOR_DSL["rank"](c, 20)
print(f"[对表] rank 快写法 vs 本线现用 rank：最大差 = {float((q-r).abs().max()):.3e}"
      f"（无并列值时应当完全相等）")

print("\n===== 逐条：单只·单次秒数 ⇒ 外推全市场 5677 只 =====")
for t in tasks:
    tt = time.time()
    try:
        r = evaluate(sub, [t])
        st, extra = r[0].get("status"), ""
        nm = r[0].get("cs_rank_ic_mean")
        extra = f"　ic={nm:.5f}" if isinstance(nm, float) else ""
    except Exception as e:      # 探不出来也要报，不许静默跳过
        dt = time.time() - tt
        print(f"  {t['name']:<14} {dt:6.2f}s  status=raise  {type(e).__name__}: {e}")
        continue
    dt = time.time() - tt
    print(f"  {t['name']:<14} {dt:6.2f}s / {len(sub)} 只 ⇒ {dt/len(sub)*1000:6.1f} ms/只"
          f"　外推 5677 只 = {dt/len(sub)*n_all:7.0f}s = {dt/len(sub)*n_all/60:5.1f} 分"
          f"　status={st}{extra}")
print(f"\n[峰值 RSS] {rss_mb()/1024:.2f} GB（本机上限 15 GB）")
