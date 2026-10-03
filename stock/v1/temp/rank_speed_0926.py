# -*- coding: utf-8 -*-
"""把「新 rank 写法到底多快」量成**当轮自己的数**，不再从探针表继承

为什么要单独量：`factor_dsl.py:33` 那句注释写着「全市场实测 402 → 37 ms/只」（= 2282s → 211s），
但那个 37 ms/只 是从 **argsort 版**（与原版在停牌窗口不等价、已弃用）的探针继承下来的。
现在落地的是 NaN 感知闭式版 ⇒ 不能拿别人的数给自己的注释背书。

量法：同一批标的（stride=19 ⇒ 299 只，避开 `STOCK_SAMPLE=300` 取 `[:300]` 全是北交所短
历史新股的低估陷阱），**只计表达式求值那一段**（不含逐日截面 IC，那段两种写法完全一样），
两个实现各跑一遍：
  旧 = 改动前的模块快照 `factor_dsl_before_0926.py`（`rolling.apply(raw=False)` + pandas.rank）
  新 = 现 `factor_dsl.FACTOR_DSL["rank"]`（NaN 感知闭式解）
再按 299→5677 只线性外推，与 b08/b09 那两批的批次秒数交叉核对。
"""
import importlib.util
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "..", "stock", "v1", "src"))
sys.path.insert(0, SRC)
os.chdir(SRC)

from run_ashare_factor_eval import load_panel        # noqa: E402
from factor_dsl import FACTOR_DSL, safe_eval          # noqa: E402

_spec = importlib.util.spec_from_file_location("dsl_before", os.path.join(HERE, "factor_dsl_before_0926.py"))
old_dsl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(old_dsl)

EXPR = "rank(close, 20)"
pool = load_panel()
codes = list(pool)[::19]
print(f"[样本] {len(codes)} 只（stride=19）")


def bench(fn, tag):
    t0 = time.time()
    n_val = 0
    for c in codes:
        df = pool[c]
        env = {"df": df, "np": np, "pd": __import__("pandas"), "close": df["close"],
               "open": df["open"], "high": df["high"], "low": df["low"],
               "volume": df["volume"], "returns": df["close"].pct_change(fill_method=None),
               "rank": fn}
        try:
            r = eval(EXPR, {"__builtins__": {}}, env)
            n_val += int(r.notna().sum())
        except Exception as e:
            print(f"  ❌ {tag}: {e}")
            return None
    s = time.time() - t0
    per = s / len(codes) * 1000
    print(f"  [{tag:<28}] {s:6.1f}s／{len(codes)} 只 ⇒ {per:5.1f} ms/只　"
          f"外推全市场 {per*5677/1000:.0f}s　非缺值格子 {n_val}")
    return per


print("\n===== 单条 rank(close,20) 的求值代价（只算表达式，不含 IC）=====")
p_old = bench(old_dsl.FACTOR_DSL["rank"], "旧写法 apply(raw=False)")
p_new = bench(FACTOR_DSL["rank"], "新写法 NaN 感知闭式")
assert p_old and p_new
print(f"\n  ⇒ 新比旧快 {p_old/p_new:.1f}×")

print("\n===== 交叉核对：两者在同一只票上是否逐位相同 =====")
df = pool[codes[0]]
env_common = {"df": df, "np": np, "close": df["close"]}
a = old_dsl.FACTOR_DSL["rank"](df["close"], 20)
b = FACTOR_DSL["rank"](df["close"], 20)
d = (a - b).abs()
print(f"  {codes[0]}：最大差 {d.max():.3e}、不等格子 {int((d > 0).sum())}／{int(a.notna().sum())}"
      f"（这只票 close 缺值 {int(df['close'].isna().sum())} 个）")
assert int((d > 0).sum()) == 0, "新写法与旧写法不逐位相等 ⇒ 注释里「等价」那句是假的"
print("  ✅ 逐位相等（含停牌窗口）")
print(f"\n[注] b08（6 条 quantile + 2 条 rank）实测 634.8s、b09（3 条 rank + 5 条 RSV）747.2s；"
      f"若把「一批 8 条便宜表达式」的基线取 ~300s，反推单条 rank ≈ "
      f"{(634.8-300+2*38)/2:.0f}s／{(747.2-300+5*45)/3:.0f}s，与上面外推应同量级")
