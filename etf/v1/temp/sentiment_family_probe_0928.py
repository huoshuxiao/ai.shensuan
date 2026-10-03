# -*- coding: utf-8 -*-
"""「情绪族」该不该进 ETF 候选位 —— 只量代价，一个配置都不改（09-28）。

背景：`live2etf/v2/AGENT.md:437` 把"回答贪婪还是恐惧"的那一类叫**情绪**（举例 RSI、乖离率、
波动率），但本线 31 个候选位里没有这一族（族=价格水平/动量/反转/趋势位置/波动/量能/
价量交互/日内隔夜）。所以要量的不是"情绪有没有道理"，而是三条**代价**：

1. **撞线代价**（决定性那道闸）：这批候选进得来吗？判重口径与生产单点复用
   `run_etf_redundancy_check.abs_worst` / `verdict`（|corr| 版，昨日刚修），
   对手池 = 在库可译 35 条 + 本线现有 31 条候选 = 66 条。
2. **信号代价**：环 1 口径的 RankIC/RankICIR@h10，与现有各族最强的一条同表横向比
   （比值大于 1 才谈得上"它带来了新信息"）。
3. **稀释代价**（代数，不用重跑）：现行 `ETF_COMPOSITE_LEVEL=family` 是"族内全员等权
   → 族间一票"（`run_etf_portfolio_eval.py:88-90`）。加第 9 族 ⇒ 现有 8 族每一族拿到的
   组合权重从 1/8 掉到 1/9 = **−11.1%**，与被加进来的条数无关。

两条**反证式对照**（防止探针自己算错，不是防止判据）：
- 乖离率 = `close/ma(df,20)-1`，与本线 `位置·C/MA20` **逐字同式**、只是换了名字 ⇒
  它与那条的 |corr| 必须读到 1.0000。读不到就是求值/对表环节坏了，本次所有读数作废。
- RSI 的量纲必须 ∈[0,1]、波动分位 ∈(0,1]，越界说明表达式写错。

只读：不写 `data/results` 任何 csv、不碰因子库、不触发 git。产物只有本脚本的 stdout。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(ROOT, "etf", "v1", "src")
sys.path.insert(0, SRC)
os.chdir(SRC)

import numpy as np                                            # noqa: E402
import pandas as pd                                           # noqa: E402
import etf_admission as EA                                    # noqa: E402
from run_etf_redundancy_check import abs_worst, verdict       # noqa: E402  判据单点复用

PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))

# ---------- 草稿：按 v2 蓝图那一类的口径翻成 10 条 DSL 表达式（不新增算子） ----------
UP = "(delta(close,1)+abs(delta(close,1)))/2"     # max(Δclose, 0)
DN = "(abs(delta(close,1))-delta(close,1))/2"     # max(-Δclose, 0)
NEW = [
    {"family": "情绪?", "name": "情绪·RSI14",
     "expr": f"ts_mean({UP},14)/(ts_mean({UP},14)+ts_mean({DN},14)+1e-9)",
     "note": "标准 RSI(14)，用 (x+|x|)/2 表达 max(x,0)，DSL 无需新增算子"},
    {"family": "情绪?", "name": "情绪·RSI6",
     "expr": f"ts_mean({UP},6)/(ts_mean({UP},6)+ts_mean({DN},6)+1e-9)",
     "note": "短线 RSI"},
    {"family": "情绪?", "name": "情绪·乖离MA20(换名对照)", "expr": "close/ma(df,20)-1",
     "note": "⚠️ 与 位置·C/MA20 逐字同式，专门用来验探针自己（必须读到 1.0000）"},
    {"family": "情绪?", "name": "情绪·乖离MA5", "expr": "close/ma(df,5)-1", "note": "周尺度乖离"},
    {"family": "情绪?", "name": "情绪·波动分位",
     "expr": "rank(ts_std(returns,20),120)",
     "note": "当前波动在过去半年里的百分位＝『恐惧到了历史什么位置』"},
    {"family": "情绪?", "name": "情绪·波动比60",
     "expr": "ts_std(returns,20)/ts_mean(ts_std(returns,20),60)",
     "note": "波动相对自身常态的放大倍数"},
    {"family": "情绪?", "name": "情绪·下行占比",
     "expr": "ts_mean((abs(returns)-returns)/2,20)/(ts_std(returns,20)+1e-9)",
     "note": "下行波动占总波动的比例"},
    {"family": "情绪?", "name": "情绪·偏度20",
     "expr": "ts_mean(((returns-ts_mean(returns,20))/(ts_std(returns,20)+1e-9))**3,20)",
     "note": "收益分布偏斜：暴跌尾部 vs 慢涨"},
    {"family": "情绪?", "name": "情绪·振幅比",
     "expr": "((high-low)/close)/(ts_mean((high-low)/close,20)+1e-9)",
     "note": "今日振幅 / 20 日均振幅（异动）"},
    {"family": "情绪?", "name": "情绪·恐慌抛压",
     "expr": "ts_mean(volume*(returns<0),20)/(ts_mean(volume,20)+1e-9)",
     "note": "下跌日均量 / 全日均量：放量下跌＝恐慌，缩量下跌＝惜售"},
]

print("=" * 100)
print(f"[输入] 情绪类草稿 {len(NEW)} 条 | 对手池 = 本线候选 {len(EA.FAMILY_SPECS)} 条 "
      f"+ 在库可译 active | 判重口径 |corr|（单点复用环 3）")
for k, v in EA.run_params().items():
    print(f"  {k:14s} = {v}")

pool = EA.load_pool()
lib_specs = EA.specs_from_rows(EA.load_active_library(), family="在库")
opp_specs = list(EA.FAMILY_SPECS) + lib_specs
print(f"[输入] 对手池 {len(opp_specs)} 条（在库可译 {len(lib_specs)} 条）")

facs_new = {k: EA.slice_to_start(v, EA.EVAL_START)
            for k, v in EA.evaluate_factors(pool, NEW).items()}
facs_opp = {k: EA.slice_to_start(v, EA.EVAL_START)
            for k, v in EA.evaluate_factors(pool, opp_specs).items()}
dead = [EA.spec_name(s) for s in NEW if EA.spec_name(s) not in facs_new]
if dead:
    print(f"[求值] ⚠️ 全标的不可求值 {dead}")
if len(facs_new) < len(NEW):
    print(f"[求值] ⚠️ 成表只有 {len(facs_new)}/{len(NEW)} 张，缺的不进读数")

# ---------- 反证 ①：量纲护栏（表达式写错会在这里红；只验天然 ∈[0,1] 的那三条） ----------
for nm in ("情绪·RSI14", "情绪·RSI6", "情绪·波动分位"):
    v = facs_new[nm].values
    v = v[np.isfinite(v)]
    lo, hi = float(v.min()), float(v.max())
    assert -1e-9 <= lo and hi <= 1.0 + 1e-9, f"{nm} 越出 [0,1]：[{lo:.3f},{hi:.3f}]"
    print(f"[量纲] {nm:18s} ∈ [{lo:.4f}, {hi:.4f}]  ✓")

# ---------- 代价 ①：撞线（与 66 条对手池的 |corr| 最大值 + 判决） ----------
_, M = EA.cs_corr_mean(facs_new, facs_opp, min_cs=EA.MIN_CS)
rows = []
for nm in facs_new:
    b, bs, bv = abs_worst(M.loc[nm], exclude=[nm])
    rows.append({"候选": nm, "最像谁": b, "带符号": bs, "|corr|": bv,
                 "判决": verdict(bv)})
res = pd.DataFrame(rows).sort_values("|corr|", ascending=False)

# 反证 ②：换名必须被读成 1.0000（读不到＝探针坏，本次读数全废）
tw = res[res["候选"] == "情绪·乖离MA20(换名对照)"]
got = float(tw["|corr|"].iloc[0]) if len(tw) else np.nan
print(f"[反证] 乖离率(换名) 对侧 = {tw['最像谁'].iloc[0] if len(tw) else '?'}，"
      f"|corr| = {got:.4f}（必须 1.0000）")
assert got > 0.999, f"换名对照没读到 1.0000（{got}）：求值/对表环节有问题"

pd.set_option("display.width", 240)
pd.set_option("display.max_colwidth", 34)
print("\n===== 代价① 撞线读数（|corr| 降序；>=0.85 挡在门外，>=0.99 连 rdagent 那道也挡）=====")
print(res.round(4).to_string(index=False))
n_block = int((res["|corr|"] >= EA.RED_BAR).sum())
n_ok = int((res["|corr|"] < EA.NEAR_DUP).sum())
print(f"[小结] {len(res)} 条里 {n_block} 条会被判重复、{n_ok} 条干净（<0.70）、"
      f"其余 {len(res) - n_block - n_ok} 条落危险区")

# ---------- 代价 ②：信号强度，与现有各族最强一条同表比 ----------
m = EA.take_window(EA.build_matrices(pool))
sig = []
for nm, w in facs_new.items():
    ic, ric, n = EA.daily_cs_ic(w, m[f"fwd{PRIMARY_H}"], min_cs=EA.MIN_CS)
    s = EA.ic_summary(ric, n, EA.MIN_CS)
    sig.append({"候选": nm, f"rank_ic_h{PRIMARY_H}": s["mean"],
                "rank_icir": s["icir"], "胜率": s.get("win"), "t": s.get("t")})
sig = pd.DataFrame(sig)
ref = pd.read_csv(os.path.join(ROOT, "etf/v1/data/results/etf_factor_eval.csv"))
ref = ref[ref["status"] == "ok"]
key = f"rank_icir_h{PRIMARY_H}"
best = ref.loc[ref.groupby("family")[key].apply(lambda s: s.abs().idxmax())].copy()
best["|rank_icir|"] = best[key].abs()
best = best.rename(columns={"name": "该族最强(环1归档)"})[["family", "该族最强(环1归档)", "|rank_icir|"]]
print(f"\n===== 代价② 信号读数（环 1 口径 @h{PRIMARY_H}）=====")
print(sig.round(4).to_string(index=False))
print("\n[参照] 现有 8 族各自最强的一条（环 1 权威归档）")
print(best.round(4).to_string(index=False))
lib_best = float(best["|rank_icir|"].max())
print(f"[小结] 现库最强 |rank_icir| = {lib_best:.4f}；新候选里过这条线的 "
      f"{int((sig['rank_icir'].abs() > lib_best).sum())}/{len(sig)} 条")

# ---------- 代价 ③：族间一票的稀释（纯代数） ----------
fam_now = sorted({s["family"] for s in EA.FAMILY_SPECS})
n_fam = len(fam_now)
print(f"\n===== 代价③ 稀释（现行族间一票，{n_fam} 族：{'、'.join(fam_now)}）=====")
print(f"[代数] 现有每族：1/{n_fam} = {1 / n_fam:.4f} → 1/{n_fam + 1} = {1 / (n_fam + 1):.4f}"
      f"，即 **{(n_fam / (n_fam + 1) - 1) * 100:.1f}%**；这条与被加进来的条数无关")
print(f"[代数] 若情绪族放 {len(NEW)} 条，则每条权重 = 1/{n_fam + 1}/{len(NEW)} = "
      f"{1 / (n_fam + 1) / len(NEW):.4f}，对比现有波动族每条 1/{n_fam}/5 = "
      f"{1 / n_fam / 5:.4f}")
print("\n[只读声明] 本脚本未写任何 data/results 归档、未碰因子库、未改 .env")
