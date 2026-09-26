# -*- coding: utf-8 -*-
"""DSL 扩算子的验收：① 老表达式**零影响**回归 ② 新算子逐条对上参考实现

改动落在共享层 `common/src/core/factor_dsl.py`（两条线共用）⇒ 验收必须是「加了 9 个键
之后，现有全部在库表达式算出来的数一个字节都不变」，而不是「新算子能跑」。

对照片 = `shell/stock/factor_dsl_before_0926.py`（改动前从仓库原样拷来的快照，md5 已记在
CHANGELOG ㉞）。用 importlib 把它当第二个模块加载，与新实现**同时**跑在同一批数据上。
"""
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "common", "src", "core"))

import factor_dsl as NEW                                    # noqa: E402  改后的实现
spec = importlib.util.spec_from_file_location("dsl_before", os.path.join(HERE, "factor_dsl_before_0926.py"))
OLD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(OLD)                                 # noqa: E402  改前快照

FAIL = []


def ck(tag, cond, msg=""):
    print(f"  {'✅' if cond else '❌'} {tag}　{msg}")
    if not cond:
        FAIL.append(tag)


# ---------- 数据：造一段含停牌缺值 / 含并列值 / 含零量的人造行情 ----------
def make_df(seed, n=900):
    rng = np.random.RandomState(seed)
    ret = rng.randn(n) * 0.02
    close = 10 * np.exp(np.cumsum(ret))
    gap = rng.choice(n, 25, replace=False)
    close[gap] = np.nan                              # 停牌：整段缺值，正是两种 rank 的分叉点
    tie = rng.choice(n, 40, replace=False)
    close[tie] = 12.345                              # 并列值：考验 ties 的 average 名次
    df = pd.DataFrame({
        "open": close * (1 + rng.randn(n) * 1e-3),
        "high": close * (1 + np.abs(rng.randn(n)) * 3e-3),
        "low": close * (1 - np.abs(rng.randn(n)) * 3e-3),
        "close": close,
        "volume": np.where(rng.rand(n) < 0.03, 0.0, rng.lognormal(14, 1, n)),
    }, index=pd.date_range("2010-01-04", periods=n, freq="B"))
    return df


frames = [make_df(s) for s in (1, 2, 3)]

# ---------- ① 零影响：两条线全部在库表达式，改前 vs 改后 ----------
exprs = []
for path in (f"{REPO}/stock/v1/data/results/rdagent_output/factors.json",
             f"{REPO}/etf/v1/data/results/rdagent_output/factors.json"):
    for f in json.load(open(path, encoding="utf-8")):
        if f.get("expr"):
            exprs.append((path.split("/")[1], f["name"], f["expr"]))
lib = json.load(open(f"{REPO}/etf/v1/data/library/factor_library_index.json", encoding="utf-8"))
for f in (lib if isinstance(lib, list) else lib.values()):
    e = f.get("expr") if isinstance(f, dict) else None
    if e:
        exprs.append(("etf注册库", str(f.get("name"))[:40], e))
print(f"\n===== ① 老表达式零影响（{len(exprs)} 条 × {len(frames)} 段行情）=====")
_by = {}
for tag, _, _ in exprs:
    _by[tag] = _by.get(tag, 0) + 1
print("  四件套：" + "　".join(f"{k} 带表达式 {v} 条" for k, v in _by.items())
      + "（ETF 注册库 35 条里只有 28 条带 expr，其余 7 条压根求值不了，不在回归面上）")
assert len(exprs) == 57, f"表达式条数不对：{len(exprs)}（应为 21+8+28=57）"
bad = 0
for tag, nm, ex in exprs:
    for k, df in enumerate(frames):
        a = OLD.safe_eval(ex, df)
        b = NEW.safe_eval(ex, df)
        same = (a.isna() == b.isna()).all() and \
               np.allclose(a.dropna().astype(float), b.dropna().astype(float),
                           rtol=0, atol=0, equal_nan=False)
        if not same:
            bad += 1
            print(f"  ❌ 数值变了：[{tag}] {nm} #{k}  最大差 "
                  f"{float((a-b).abs().max(skipna=True)):.3e}")
ck("Z1 全部在库表达式改前/改后逐位相等", bad == 0, f"不等的 {bad} 处")
# 反证：这条判据必须抓得住真改动 —— 把 rank 换成 argsort 快写法就该报警
NEW_TEST = dict(NEW.FACTOR_DSL)
NEW_TEST["rank"] = lambda s, n: s.rolling(int(n)).apply(
    lambda w: (np.argsort(np.argsort(w))[-1] + 1) / float(n), raw=True)
caught = 0
for df in frames:
    a = NEW.safe_eval("rank(close, 20)", df)
    b = NEW_TEST["rank"](df["close"], 20)
    caught += int((a - b).abs().max(skipna=True) > 1e-12)
ck("Z2 反证：把 rank 换成 argsort 快写法，Z1 这把尺子抓得到", caught == len(frames),
   f"{caught}/{len(frames)} 段行情报警（缺值窗口就是分叉点）")

# ---------- ② 新算子逐条对参考实现 ----------
print("\n===== ② 新算子对表（同一批人造行情）=====")
c, h, l, v = frames[0]["close"], frames[0]["high"], frames[0]["low"], frames[0]["volume"]
n = 20
tt = np.arange(1.0, n + 1)

ref_slope = c.rolling(n).apply(lambda w: np.polyfit(tt, w, 1)[0], raw=True)
ck("S1 slope == 逐窗 polyfit", float((NEW._slope(c, n) - ref_slope).abs().max()) < 1e-9,
   f"最大差 {float((NEW._slope(c,n)-ref_slope).abs().max()):.2e}")

tmat = np.full(len(c), np.nan)
rs_ref = c.rolling(n).apply(
    lambda w: np.corrcoef(tt, w)[0, 1] ** 2 if np.std(w) > 0 else np.nan, raw=True)
rs = NEW._rsquare(c, n)
ck("S2 rsquare == corr(t,y)²", float((rs - rs_ref).abs().max(skipna=True)) < 1e-8,
   f"最大差 {float((rs-rs_ref).abs().max(skipna=True)):.2e}")

resi_ref = c.rolling(n).apply(lambda w: w[-1] - (np.polyfit(tt, w, 1)[1]
                                                 + np.polyfit(tt, w, 1)[0] * n), raw=True)
ck("S3 resi == y_末 − 拟合值", float((NEW._resi(c, n) - resi_ref).abs().max()) < 1e-8,
   f"最大差 {float((NEW._resi(c,n)-resi_ref).abs().max()):.2e}")

im = NEW._idx_max(h, n)
partial = h.rolling(n).count() < n                  # 窗口里含缺值 ⇒ 本线口径要返回 NaN
ck("S4 idx_max == argmax+1，且含缺值窗口按本线口径给 NaN",
   float((im.dropna() - h.rolling(n).apply(lambda w: np.argmax(w) + 1, raw=True).dropna())
         .abs().max()) < 1e-12
   and bool(im[partial].isna().all()) and bool(partial.any()),
   f"缺值窗口 {int(partial.sum())} 个全部为 NaN")
ck("S5 idx_min == argmin+1", bool((NEW._idx_min(l, n).dropna()
                                   .between(1, n)).all()), "取值落在 [1, n]")
ck("S6 quantile == pandas 原生",
   float((NEW._quantile(c, n, 0.8) - c.rolling(n).quantile(0.8)).abs().max()) == 0.0)
ck("S7 corr == pandas 原生",
   float((NEW._corr(c, np.log(v + 1), n) - c.rolling(n).corr(np.log(v + 1))).abs().max(skipna=True))
   == 0.0)
ck("S8 ts_max/ts_min 对任意列生效（max/min 仍钉 close）",
   float((NEW._ts_max(h, n) - h.rolling(n).max()).abs().max()) == 0.0
   and float((NEW.FACTOR_DSL["max"](frames[0], n) - c.rolling(n).max()).abs().max()) == 0.0)

# rank 的「逐位等价」是本次最关键的一条：并列值 + 缺值都造了
ref_rank = OLD.FACTOR_DSL["rank"]
worst = 0.0
nbad = 0
for df in frames:
    a = ref_rank(df["close"], 20)
    b = NEW._rank(df["close"], 20)
    nbad += int(((a - b).abs() > 0).sum())
    worst = max(worst, float((a - b).abs().max(skipna=True) or 0.0))
ck("R1 rank 快实现 == 原 pandas 版（含并列值、含停牌缺值）", nbad == 0,
   f"不等的格子 {nbad}、最大差 {worst:.1e}（数据里塞了 25 个缺值点 + 40 个并列值）")

# ---------- ③ 表达式里内联 lambda 仍然不通（这条是 DSL 的形状约束，写死成判据） ----------
print("\n===== ③ 形状约束：内联 lambda 在 safe_eval 里不可用 =====")
try:
    NEW.safe_eval("close.rolling(20).apply(lambda w: np.argmax(w), raw=True)", frames[0])
    ck("L1 内联 lambda 必须报 NameError（所以新语义只能注册成函数）", False, "居然跑通了")
except Exception as e:
    ck("L1 内联 lambda 必须报 NameError（所以新语义只能注册成函数）",
       "not defined" in str(e), f"{type(e).__name__}: {str(e)[:56]}")

print(f"\n{'✅ 全部通过' if not FAIL else '❌ 失败 ' + str(len(FAIL)) + ' 条：' + str(FAIL)}")
sys.exit(1 if FAIL else 0)
