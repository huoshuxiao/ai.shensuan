# -*- coding: utf-8 -*-
"""选项I 账单第三半：**窗长**这一维。前两轮探针全钉在 n=20，而 Alpha158 每条算子有 5/10/20/30/60 五个窗长

为什么必须补这一轮：`pandas` 的 `rolling().mean()/max()/corr()` 是**增量**算法（滑窗进出一条
改一次），换窗长几乎不涨；但 `rolling().apply()` 是**逐窗回调**，每窗算 O(n) ⇒ 60 天窗比
20 天窗贵约 3 倍。第二轮测出来的两个爆价货（`rank` 慢版、`Slope` 逐窗 polyfit）恰好全是
`apply` 形态，而 Alpha158 里这类列每个都要跑 5 个窗长。不量这一维，「157 条 ≈ 4.5 小时」
那个数就是拿 20 天窗的价格给 60 天窗开的发票。

这里把同一批算子在 **n=60** 上重测（对照组沿用第二轮 n=20 的数），并先对表「闭式解 ==
逐窗 polyfit」在 60 天窗上依然成立（否则比的是两个不同的数）。

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
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "..", "stock", "v1", "src"))
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

tasks = json.load(open(os.path.join(HERE, "tmp_alpha158", "probe_exprs3_0926.json"),
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


# ===== 先对表「闭式解 == 逐窗 polyfit」在 60 天窗上仍成立，再谈代价 =====
k, d = next(iter(sub.items()))
c = d["close"]
a, b = _slope(c, 60), _poly_slope(c, 60)
diff = float((a - b).abs().max())
print(f"[对表] 窗口 60：闭式解 slope vs 逐窗 polyfit 最大差 = {diff:.3e}"
      f"（{k}，{int(c.notna().sum())} 行）⇒ {'两种写法算的是同一个数' if diff < 1e-8 else '不一致，闭式解写错了'}")
q = _rank_fast(c, 60)
r = FACTOR_DSL["rank"](c, 60)
nbad = int(((q - r).abs() > 1e-12).sum())
print(f"[对表] rank 快写法 vs 本线现用 rank（60 天窗）：不相等 {nbad} 个窗口"
      f"、最大差 {float((q-r).abs().max()):.3f} ⇒ 停牌缺值处两者语义就分叉（argsort 把 NaN 当最大值、"
      f"分母固定 n；pandas rank 剔掉 NaN 再按比例）")

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
