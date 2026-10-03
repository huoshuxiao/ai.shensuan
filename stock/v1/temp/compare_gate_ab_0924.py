"""涨停闸 flat / board / dated 的账单对比（09-24）。

各份产物来自**同一次代码、同一份面板、同一调仓网格**，只有 `STOCK_TRADABLE_GATE`
不同 ⇒ 差异全部归因给那道闸。

    flat   全线 0.095 —— 已入库那批组合层结论的口径
    board  板块限幅 × ASHARE_LIMIT_NEAR（主板 9.5% 与 flat **逐字相同**，科创/创业 19%、
           北交 28.5%）—— 今天的真实规则
    dated  同 board，但按**买入日当天生效**的限幅取档（创业 2020-08-24 前 ±10%、
           科创 2019-07-22 前 ±10%、北交继承精选层 ±30%）—— 跑历史唯一不自相矛盾的档

三档之差只可能来自三段尾板块，且 `dated` 的作用面比 `board` 更窄：面板里科创板
最早 start 就是它的生效日、北交所 596 只只有 1 只早于开市日，所以 dated 与 board 的
差异**全部来自创业板 2020-08-24 之前那 837 只票**。

判据看三条：`ann_return`（扣费后年化）、`excess_univ_ew_ann`（对可投资域等权的超额）、
`one_way_turnover`（单程换手 —— 换闸会改建仓票，换手是成本通道），
外加机制项 `avg_blocked_limit_up`（每个调仓日被这道闸挡掉的只数）。

用法：/usr/bin/python3.10 shell/compare_gate_ab_0924.py [前缀] [档...]
前缀默认 full（全窗口 2015~），冒烟轮给 smoke；档默认拿存在的（flat 为基线）。
基线可用 GATE_BASE 换（`GATE_BASE=board` 就出 dated − board 那一对照，看 dated 的
增量到底落在哪儿）。文件名为 <前缀>_<闸>.csv、<前缀>_excl_<闸>.csv、
<前缀>_bl_<闸>.csv，都在 /tmp/gate_ab_0924/。
"""
import os
import sys

import pandas as pd

PRE = sys.argv[1] if len(sys.argv) > 1 else "full"
GATES = sys.argv[2:] or [g for g in ("flat", "board", "dated")
                         if os.path.exists(f"/tmp/gate_ab_0924/{PRE}_{g}.csv")]
BASE = os.environ.get("GATE_BASE", "flat" if "flat" in GATES else GATES[0])
D = "/tmp/gate_ab_0924"
METRICS = ["ann_return", "ann_return_gross", "excess_univ_ew_ann",
           "excess_univ_ew_ir", "excess_sh000300_ann", "one_way_turnover",
           "avg_blocked_limit_up", "universe", "max_drawdown"]


def pair(leg, keys, tag):
    """leg='main' → full_flat.csv / full_dated.csv；否则 full_excl_<闸>.csv 等"""
    stem = f"{PRE}" if leg == "main" else f"{PRE}_{leg}"
    frames = {}
    for g in GATES:
        path = f"{D}/{stem}_{g}.csv"
        if not os.path.exists(path):
            print(f"[跳过] 没有 {path}")
            continue
        df = pd.read_csv(path)
        got = sorted(set(df["gate"].astype(str)))
        assert got == [g], f"{stem} 的 {g} 文件里 gate 列是 {got}，产物串了"
        frames[g] = df
    if BASE not in frames or len(frames) < 2:
        print(f"\n===== {tag}：不足两份可比，跳过 =====")
        return
    base = frames[BASE]
    k = [c for c in keys if c in base.columns]
    m = [c for c in METRICS if c in base.columns]
    for g, df in frames.items():
        if g == BASE:
            continue
        x = base.merge(df, on=k, suffixes=("_a", "_b"), validate="one_to_one")
        out = pd.DataFrame({c: x[f"{c}_b"] - x[f"{c}_a"] for c in m})
        out.insert(0, "行", [" · ".join(str(r[c]) for c in k) for _, r in x.iterrows()])
        print(f"\n===== {tag}：{g} − {BASE}（正=换成 {g} 档更有利）=====")
        with pd.option_context("display.width", 250, "display.max_columns", 40,
                               "display.unicode.east_asian_width", True):
            print(out.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))
        # 机制项两档**必然**不同（阈值放宽了就是少挡票），所以判「建仓票一只没换」只看
        # 结果列；否则这一栏恒为 0，是个假诊断
        outcome = [c for c in m if c != "avg_blocked_limit_up"]
        still = int(out[outcome].abs().lt(5e-7).all(axis=1).sum()) if len(out) else 0
        print(f"两档结果逐位相同的行：{still}/{len(out)}（这些行的建仓票一只都没被闸换掉）")
        if "avg_blocked_limit_up" in out.columns:
            col = out["avg_blocked_limit_up"]
            print(f"机制项 avg_blocked_limit_up（{g} − {BASE}，只/调仓日）："
                  f"均值 {col.mean():+.2f}，范围 [{col.min():+.2f}, {col.max():+.2f}]")
        flip = ((x[f"excess_univ_ew_ann_a"] * x[f"excess_univ_ew_ann_b"]) < 0).sum() \
            if "excess_univ_ew_ann" in m else 0
        if "excess_univ_ew_ann" in m:
            print(f"超额符号翻转的行：{flip}/{len(x)}")


print(f"前缀 {PRE} · 参与对比的档 {GATES} · 基线 {BASE}")
pair("main", ["signal", "kind", "top_n"], "组合层主表（多头回放 + 五分位/剔除腿）")
pair("excl", ["variant", "kind", "min_hits"], "减法腿（并集/命中≥k/留一）")
pair("bl", ["variant", "kind", "top_n"], "短名单腿（安静度前 50，扣费）")
