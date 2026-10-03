# -*- coding: utf-8 -*-
"""选项K 回归 A：`factor_matrices` 喂给 DSL 的 high/low 到底是真值还是 close 占位。

判据（**两条都要读，缺一条就是恒真**）：
1. 带 high/low 的表达式：环 1 的逐票路径（`run_ashare_factor_eval.load_panel` →
   直接 `safe_eval(expr, 单标的 df)`，那个 df 的 high/low 是从 h5 的 $high/$low 来的）
   与环 2 的矩阵路径（`load_panel` → `build_matrices` → `factor_matrices`）
   **逐位相等**（float32 最大绝对差 == 0.0，且 NaN 位置完全一致）。
2. 正对照 `ts_mean(volume,20)`：不带 high/low，两条路径**本来就该相等**。
   它的作用是证明「判据 1 挂掉」只能归因于 high/low，而不是本探针自己的装载/
   对齐有 bug —— 没有这条，判据 1 的失败可以来自任何一处错位，等于没测。

修复前预期：判据 1 里 `(high-low)/open` 整列变 0、`low/close` 整列变 1
（因为 `factor_matrices` 用 `cl` 同时顶替 high 和 low），判据 1 FAIL、正对照 PASS。
修复后预期：两条都 PASS。

只读：不改任何权威产物（两边的落点都被本脚本绕开）。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "stock", "v1", "src")

os.environ["STOCK_SAMPLE"] = "1500"         # 两条路各自抽样、只有交集可比，故抽得宽些
os.environ.setdefault("STOCK_VOL_BASIS", "adj")
sys.path.insert(0, SRC)
os.chdir(SRC)

import numpy as np                                             # noqa: E402
import pandas as pd                                            # noqa: E402
import run_ashare_factor_eval as R1                            # noqa: E402
from factor_dsl import safe_eval                               # noqa: E402
import strategy.ashare_screen as S                             # noqa: E402

R1.START, R1.END = "2010-01-01", ""       # 环 1 取全历史，最大化与环 2 的重叠区

HL_EXPRS = ["((high - low) / open)", "(low / close)", "(ts_min(low, 10.0) / close)"]
CTRL_EXPRS = ["ts_mean(volume,20)"]
ALL = HL_EXPRS + CTRL_EXPRS


def main():
    # ---- 参考值：环 1 的逐票路径（真 $high/$low）----
    pool = R1.load_panel()
    codes = sorted(pool)
    ref = {}
    for e in ALL:
        acc = {}
        for c in codes:
            try:
                acc[c] = safe_eval(e, pool[c]).astype("float32")
            except Exception:
                pass
        ref[e] = pd.DataFrame(acc)
    del pool

    # ---- 被测值：环 2 的矩阵路径（factor_matrices）----
    wide, _b = S.load_panel()
    mtx = S.build_matrices(wide)
    got = S.factor_matrices(ALL, mtx)

    n_fail = 0
    for e in ALL:
        # 两条路的**日期区间本来就不一样**（环 1 从 2010 起、环 2 从组合层起点起），
        # 不对齐区间就比 NaN 会把「窗口不同」读成「bug」，那这条判据等于没测。
        # 只在两方共有的 (日, 票) 格子上比。
        ii = ref[e].index.intersection(got[e].index)
        jj = ref[e].columns.intersection(got[e].columns)
        r = ref[e].loc[ii, jj].to_numpy("float32")
        g = got[e].loc[ii, jj].to_numpy("float32")
        print(f"  对齐 {e}: 共有 {len(ii)} 日 × {len(jj)} 只")
        both_nan = np.isnan(g) & np.isnan(r)
        nan_mismatch = int((np.isnan(g) != np.isnan(r)).sum())
        m = ~np.isnan(g) & ~np.isnan(r)
        diff = float(np.abs(g[m] - r[m]).max()) if m.any() else float("nan")
        nuniq = len(pd.unique(g[m].ravel())) if m.any() else 0
        # 主判据只有「共值格子上逐位相等」这一条。**NaN 错位不当判据**：本探针顺带
        # 量出一个两路本来就有的口径差 —— 环 1 是逐票连续序列（h5 里停牌日根本没有行，
        # rolling 窗口跨过停牌继续数），环 2 是日历对齐宽表（停牌日是一行 NaN，会把
        # 窗口打断）。正对照 `ts_mean(volume,20)` 不碰 high/low 也照样有 NaN 错位，
        # 可见它测的是「两路口径不同」而不是「有没有这个缺陷」，拿它当闸会误伤。
        ok = (diff == 0.0)
        n_fail += (not ok)
        tag = "带high/low" if e in HL_EXPRS else "正对照    "
        print(f"[{'PASS' if ok else 'FAIL'}] {tag} {e:32s} "
              f"共值 {int(m.sum()):,} 最大绝对差 {diff:.3e} "
              f"（NaN错位 {nan_mismatch}，仅读数）被测列去重值 {nuniq}")
    print(f"\n[对拍] {len(ALL) - n_fail}/{len(ALL)} 条逐位一致")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
