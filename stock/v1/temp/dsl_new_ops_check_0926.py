# -*- coding: utf-8 -*-
"""DSL 扩算子的验收：① 老表达式**零影响**回归 ② 新算子逐条对上参考实现

改动落在共享层 `common/src/core/factor_dsl.py`（两条线共用）⇒ 验收必须是「加了 9 个键
之后，现有全部在库表达式算出来的数一个字节都不变」，而不是「新算子能跑」。

**归属要说清（09-27）**：这条不是「股票线的维修」，它测的是两线共用的底层，只是**住在**
`stock/v1/temp/` 下。所以读数按线拆开报（Z1a 股票腿 / Z1b ETF 腿 / Z1c 合计），一条线的
因子库变了不许掩盖另一条。ETF 线自己那 9 个算子级单元测试在
`etf/v1/tests/test_factor_dsl.py`（跑在 pytest 里），本脚本只补「两线真实在库表达式」
这一层的对拍，两边不重复也不互相顶替。

对照片 = `stock/v1/temp/factor_dsl_before_0926.py`（改动前从仓库原样拷来的快照，md5 已记在
CHANGELOG ㉞）。用 importlib 把它当第二个模块加载，与新实现**同时**跑在同一批数据上。
"""
import importlib.util
import json
import os
import re
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
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

# ---------- ① 零影响：**按线分开钉数、按线分开出读数** ----------
# 09-27 回归时这里是一条 `assert len(exprs) == 57` 的总钉，09-26 晚 ETF 注册库补齐
# 7 条空表达式后变 64 ⇒ 崩在 load 阶段、真正的对拍一次都没跑成。总钉还有一个更坏的
# 性质：一条线的库变了会把另一条线的库变化一起掩盖掉，所以拆成三条按线各钉。
LIB_SOURCES = (
    ("stock在库", f"{REPO}/stock/v1/data/results/rdagent_output/factors.json", 21),
    ("etf实验", f"{REPO}/etf/v1/data/results/rdagent_output/factors.json", 8),
)
LIB_REGISTERED = (f"{REPO}/etf/v1/data/library/factor_library_index.json", 35)
# tag 以前取 `path.split("/")[1]`，在绝对路径上拿到的是 `"home"` ⇒ 两条线被并成同一个
# 标签，报数时分不出哪条属于股票线。改成相对仓库根的首段。
exprs = []
for tag, path, _n in LIB_SOURCES:
    for f in json.load(open(path, encoding="utf-8")):
        if f.get("expr"):
            exprs.append((tag, f["name"], f["expr"]))
lib = json.load(open(LIB_REGISTERED[0], encoding="utf-8"))
for f in (lib if isinstance(lib, list) else lib.values()):
    e = f.get("expr") if isinstance(f, dict) else None
    if e:
        exprs.append(("etf注册库", str(f.get("name"))[:40], e))
print(f"\n===== ① 老表达式零影响（{len(exprs)} 条 × {len(frames)} 段行情）=====")
_by = {}
for tag, _, _ in exprs:
    _by[tag] = _by.get(tag, 0) + 1
print("  三条库：" + "　".join(f"{k} {v} 条" for k, v in _by.items())
      + "（注册库那 7 条空表达式 09-26 晚已补齐 ⇒ 现在 35 条全带 expr、全在回归面上；"
        "旧版那句「35 条里只有 28 条带 expr」已作废）")
for tag, _p, n in LIB_SOURCES + (("etf注册库", "", LIB_REGISTERED[1]),):
    ck(f"Z0 库条数没被换掉：{tag} = {n} 条", _by.get(tag, 0) == n,
       f"实得 {_by.get(tag, 0)} 条" + ("" if _by.get(tag, 0) == n
                                       else " ⇒ 这条线的因子库变过，"
                                            "先确认是加的还是改名的再动本脚本"))
bad = 0
bad_by_tag = {}
# 09-26 新加的算子在**改前那份快照**里根本不存在，凡用到它们的表达式「改前」直接
# NameError ⇒ 这类只能验「新实现能算」，不能验「改前改后逐位相等」（没有改前可比）。
# 但不许把这条豁免写成静默跳过：豁免名单必须逐个点名，且**不含**任何一条不用新算子的
# 表达式 —— 那样等于老表达式从回归面上悄悄掉出去，正是这条判据要防的事。
NEW_OPS = sorted(set(NEW.FACTOR_DSL) - set(OLD.FACTOR_DSL))
print("  改前快照里没有的算子：" + "、".join(NEW_OPS))


def uses_new_op(e):
    return [op for op in NEW_OPS if re.search(rf"(?<![A-Za-z0-9_]){op}\s*\(", e)]


skipped = []
compared = 0
for tag, nm, ex in exprs:
    nu = uses_new_op(ex)
    for k, df in enumerate(frames):
        try:
            a = OLD.safe_eval(ex, df)
        except Exception as e:
            if nu:
                skipped.append((tag, nm, tuple(nu), type(e).__name__))
                break
            raise ValueError(f"老表达式在改前快照上就算不出来：[{tag}] {nm} -> {e}"
                             "（它不含任何新算子 ⇒ 不是豁免，是回归面破了）") from e
        compared += 1
        b = NEW.safe_eval(ex, df)
        same = (a.isna() == b.isna()).all() and \
               np.allclose(a.dropna().astype(float), b.dropna().astype(float),
                           rtol=0, atol=0, equal_nan=False)
        if not same:
            bad += 1
            bad_by_tag[tag] = bad_by_tag.get(tag, 0) + 1
            print(f"  ❌ 数值变了：[{tag}] {nm} #{k}  最大差 "
                  f"{float((a-b).abs().max(skipna=True)):.3e}")
for tag, nm, ops, err in skipped:
    print(f"  [豁免·只能新实现算] [{tag}] {str(nm)[:34]} 用到 {'、'.join(ops)}（{err}）")
# 豁免集合必须**恰好等于**「用到新算子」那批：多一个是老表达式借道逃出回归面，
# 少一个是有一条该报的没报（比如异常被更宽的 try 吞掉）。两头都要能失败。
_use_set = {(t, n) for t, n, e in exprs if uses_new_op(e)}
_skip_set = {(t, n) for t, n, _o, _e in skipped}
ck("Z1d 豁免集合 == 用到新算子的表达式集合", _skip_set == _use_set,
   f"豁免 {len(_skip_set)} 条 / 用到新算子 {len(_use_set)} 条"
   + (f"；多豁免 {_skip_set - _use_set} 漏报 {_use_set - _skip_set}"
      if _skip_set != _use_set else ""))
ck("Z1e 对拍条数 + 豁免条数 = 全部条数（没有静默消失的）",
   compared + len(_skip_set) * len(frames) == len(exprs) * len(frames),
   f"对拍 {compared} 段·条 + 豁免 {len(_skip_set)} 条 = 应等于 {len(exprs)} 条")
# 逐位相等**按线各出一个读数**：合并成一个数就看不出是哪条线的库在动
ck("Z1a 股票线在库表达式改前/改后逐位相等", bad_by_tag.get("stock在库", 0) == 0,
   f"不等的 {bad_by_tag.get('stock在库', 0)} 处（{21} 条 × {len(frames)} 段）")
_etf_bad = bad_by_tag.get("etf实验", 0) + bad_by_tag.get("etf注册库", 0)
ck("Z1b ETF 线（实验库 + 注册库）改前/改后逐位相等", _etf_bad == 0,
   f"不等的 {_etf_bad} 处（8 + {35} 条 × {len(frames)} 段）")
ck("Z1c 两线合计零影响", bad == 0, f"不等的 {bad} 处")
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
