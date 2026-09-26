# -*- coding: utf-8 -*-
"""名单层独立闸的判据探针（合成数据，不读面板）：六道断言，正反对照都齐。

为什么要单独探针：这道闸的失效模式全是**静默**的 —— 表达式没进 factor_matrices、
当日整列缺值、开关被关掉，每一种都会让它「一条都不挡」，而名单照旧产出 50 只，
看不出来。所以每一条都要有正对照（该挡的确实挡住了）+ 反对照（不该挡的没挡）。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "stock", "v1", "src")
sys.path.insert(0, SRC)
os.chdir(SRC)

import numpy as np                                          # noqa: E402
import pandas as pd                                         # noqa: E402
import strategy.ashare_screen as S                              # noqa: E402

EXPR = S.BUY_EXTRA_RULES[0][2]
assert EXPR == "(-1.0*((low / close)))", EXPR
Q = S.ASHARE_SCREEN_QUANTILE

dates = pd.DatetimeIndex(["2026-09-22", "2026-09-23", "2026-09-24"])
codes = [f"SZ30{i:04d}" for i in range(100)]
s = dates[-1]
# 造一个单调面：第 i 只票 low/close = 0.50 + i/1000 ⇒ 越大 = 下影越浅 = 越该留
ratio = pd.Series(0.50 + np.arange(100) / 1000.0, index=codes, dtype="float32")
rm = {EXPR: pd.DataFrame({c: [-ratio[c]] * 3 for c in codes},
                         index=dates, dtype="float32")}
pool = pd.Series(True, index=codes)

# ①正对照：分位判据真的在咬人 —— 踢掉的恰是下影最深的那一批
#   边界含等号（`pct >= 0.8`，与 volume_exclusion 同一行代码的立场）：100 只里
#   第 80~100 名 = **21** 只，不是 20。这个 +1 在池子小时会放大，探针要钉住它
blocked, fired = S.buy_extra_block(rm, s, pool)
exp = [c for c in codes if ratio[c] <= sorted(ratio)[20] + 1e-9]
assert int(blocked.sum()) == 21, int(blocked.sum())
assert set(blocked.index[blocked.to_numpy()]) == set(exp), "踢错人了"
assert fired == {"下影深度": 21}, fired

# ②「判不了就不挡」：NaN 格子不进命中，但**不占名额**（仍按有效票的 20% 踢）
rm2 = dict(rm)
m = rm[EXPR].copy()
m.loc[s, codes[0]] = np.nan                          # 本来是第一个被踢的
rm2[EXPR] = m
b2, f2 = S.buy_extra_block(rm2, s, pool)
assert not b2[codes[0]], "NaN 被当成命中了"
assert int(b2.sum()) == 20, int(b2.sum())            # 有效票 99 只 ⇒ 第 80~99 名
assert set(b2.index[b2.to_numpy()]) == set(codes[1:21]), "缺值后顺延的不是下一批"

# ③分位只在**池内**算：池外的高分端不挡（也说明它不改「不该买」那张域）
pool3 = pool.copy()
pool3[codes[10:]] = False                            # 只留最下影的 10 只在池里
b3, f3 = S.buy_extra_block(rm, s, pool3)
assert int(b3.sum()) == 3, int(b3.sum())             # 池内 10 只：第 8/9/10 名
assert not b3.loc[codes[10:]].any(), "池外的票被越权挡了"

# ④开关关掉 = 整道闸不存在，且**不要求**矩阵里有那条表达式（老场次复算靠这条）
b4, f4 = S.buy_extra_block({}, s, pool, names="")
assert int(b4.sum()) == 0 and f4 == {}, (int(b4.sum()), f4)

# ⑤拒绝型判据的反面：开着闸却忘了送表达式 ⇒ 必须报错，不许静默放过
try:
    S.buy_extra_block({}, s, pool)
    raise AssertionError("该报错却没报错：这道闸会悄悄失效")
except SystemExit as e:
    assert "factor_matrices" in str(e), str(e)

# ⑥未知键也报错（否则 STOCK_BUY_EXTRA_RULES 打错字 = 关掉一道闸还以为是开着）
try:
    S.buy_extra_block(rm, s, pool, names="low")
    raise AssertionError("该报错却没报错：错键被当成关闸")
except SystemExit as e:
    assert "未知构造" in str(e), str(e)

print(f"[探针] 六道断言全过：池内 100 只命中 21（边界含等号）、缺值不占名额（99 只→20）、"
      f"分位只在池内算（池 10 只→3）、关掉=不存在、缺矩阵报错、错键报错（阈值 ≥{Q:.0%}）")
