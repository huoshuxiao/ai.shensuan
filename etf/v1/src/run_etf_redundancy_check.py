# -*- coding: utf-8 -*-
"""ETF 因子判重预检入口 —— 准入链第三环。

一句话：一条新因子在进库之前先问两句 ——
    (a) 它和**同批候选**是不是同一个东西？（同批里留 |RankICIR| 高的那条）
    (b) 它和**已在库的 active 因子**是不是同一个东西？（是就没必要再收一条）
两问用的都是同一个量：逐日截面相关的时间平均，判决看它的**绝对值**。

判重口径（本线自己定，写清楚为什么）
------------------------------------
    corr_t(F_a, F_b) = corr_i(F_{a,t,i}, F_{b,t,i})     i ∈ 当日全部候选齐备的标的
    redundancy(a, b) = | mean_t corr_t |
    判据用 Spearman（秩）版，因为本线现行跨源判重就是
    `common/src/core/multi_source_mining.py` 里 `dedup_factors` 的
    `abs(spearman) > 0.85`；Pearson 版并列输出（取 Spearman 挑中的那一对），
    两者差得多说明有一侧被离群值撑高。

为什么判决看 |corr| 而不是带号的 corr
----------------------------------
两条因子逐日截面排序完全相反 = 同一个信息乘了个 -1，那还是重复。09-28 之前这一环
用**带符号最大值**挑近亲，于是 -0.98 在这一格里读成"和谁都不像、可提名"。实测（账单
`etf/v1/temp/ring3_absfix_account_0928.py`）：31 条族候选对旧库侧 21 条可译因子有
<=-0.85 反号近亲的 **9 条**、候选内部另有 6 条，换成绝对值口径后 **8 条判决被翻掉**。
两条最刺眼的：`波动·STD20` 对库内 `volatility_20` 恰好 **-1.0000**（旧读数报"危险区
0.70~0.85"），`价量·Amihud20` 对 `量能·成交额MA20` = **-0.9622**（经济学上必然：Amihud =
|收益|/成交额，分母就是成交额；旧读数的内部判决是"可提名（<0.70）"）。
带符号的原值不丢，另存 `cand_signed` / `library_signed` 两列 —— 判重看绝对值，
"这条和谁反号"本身是信息。

为什么对侧要按名字剔掉"它自己"
--------------------------
带 `ETF_SPEC_JSON` 跑时，候选清单常常就是从库里挑出来的几条（拔管对照、近亲复核）。
那种条目在库侧有一个同名同表达式的自己，而"自己和自己的截面相关"恰好是 1.0000 ⇒
`max_vs_library` 恒为 1、近亲列写着它自己的名字、判决永远是"必被丢"。这条读数不含
任何信息，还会把同一行里真正该看的近亲挤掉，故对侧按**名字**剔掉自身，并如实记一列
`self_in_library` 说明"这条本来就在库里"。⚠️ 只按名字剔：**表达式相同、名字不同**的
对侧一律不剔（如 `波动·STD5` vs 库内 `gp_0`），因为"你以为挖到一条新的、其实库里
已经有了"正是这一环最该喊出来的一件事。

为什么还要补一道「在库 × 在库」（09-28 丁方案）
--------------------------------------------
上面两问把库当成**对手池**，从不问"池子里有没有同一条东西被数了两次"。09-28 量了一次：
`factor_library.csv` 35 条 active 里有 **4 对逐字同式**（`mogp_4`/`mogp_6`、`mogp_5`/`mogp_7`、
`hybrid_0`/`hybrid_5`、`volatility_20`/`vol_20`）⇒ 唯一表达式只有 31 条。根因在写入侧：
`factor_library.upsert` 按**名字**认条目，而挖掘引擎的名字是位置式的（`mogp_6` 这种），
同一表达式换一轮排到别的下标就又进一次。这道对角线本身**只加读数、不改判据**，但它量完
的第二天（09-29 00:24）就被拿去动了库：按 `|corr| >= RDAGENT_BAR(0.99)` 并簇、每簇留库内
`|ic|` 最高者，9 条标成 `inactive`（是谁替谁留的写在每条的 `status_reason` 里）⇒ 库从
36 条 active 降到 27（其中 1 条 `ma_ratio_10_30` 无 expr，判重仍看不见它）。关键读数是
**0.85 档的唯一簇清前清后都是 23 条** —— 那 9 条没带走任何信息，只是让"在库 N 条"这个
数不再虚高。写入侧按名字认条目这件事**没改**，所以同一表达式还会再长回来。
「建议留」用的是库内自带的 `|ic|`（这批条目没进过环 1 归档 ⇒ 没有截面 RankICIR），
只用于在同一对里挑一条，**不能**拿去和候选的 |RankICIR| 横比。

为什么不能"把两列拉平后整体求相关"
---------------------------------
本池 2013 年中位 28 只、2026 年中位 871 只。拉平后那个数把「不同日期」当成同一个
截面，后段样本天然占优，任何两条时序因子都能算出虚高的正相关 —— 那是池子在长，
不是因子像。逐日算再取均值才是"这两个因子在同一天排序像不像"。

三条线（都在 etf_admission 常量里，可覆盖；比较对象是 |redundancy|）
--------------------------------------------------------------
    |redundancy| >= 0.99   进 RD-Agent 循环必被 deduplicate_new_factors 丢
    |redundancy| >= 0.85   本线跨源判重线，入库即触发剔除
    0.70 ~ 0.85            危险区：不触发判重但已是同一簇，人工复核
    < 0.70                 可提名

只读：不写 `data/library/`（判重输入用它，产物只落 results/），不触发 git 提交。
产物  data/results/etf_redundancy_check.csv（逐条判决）
      data/results/etf_redundancy_detail.csv（逐对明细，含近亲是谁）
      data/results/etf_redundancy_library_internal.csv（在库×在库对角线，09-28 新增）
带 `ETF_SPEC_JSON` 跑时全部产物加 `.cand` 中缀，不覆盖权威口径（理由见 `route`）。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import json
import os
import sys
import time

import numpy as np
import pandas as pd

import etf_admission as EA

PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))
SPEC_JSON = os.environ.get("ETF_SPEC_JSON", "")


def route(path):
    """与环 1 同一条规矩：带外部候选清单（`ETF_SPEC_JSON`）跑时，本环产物一律在扩展名前
    插 `.cand` 中缀，权威口径的 csv 一个字都不动。判据见
    `run_etf_factor_eval.py::route`（`ETF_RESULTS_DIR` 空转那个坑）。
    两份矩阵的基名里本来就带 `_cand`（那是"候选 × 候选"矩阵的意思，与外部候选清单无关），
    所以候选模式下会读作 `etf_redundancy_matrix_cand.cand.csv` —— 啰嗦但可预测：
    中缀位置全链一致，`ls *.cand.csv` 一次列全。"""
    stem, ext = os.path.splitext(path)
    return stem + (".cand" if SPEC_JSON else "") + ext


# 外部候选跑时优先配同源的 .cand 因子表（ICIR 只用来决定"重复对里留谁"，缺表不致命）
CAND_EVAL = route(EA.FACTOR_EVAL_OUT)
EVAL_CSV = CAND_EVAL if SPEC_JSON and os.path.exists(CAND_EVAL) else EA.FACTOR_EVAL_OUT
OUT_CSV = route(EA.REDUNDANCY_OUT)
DETAIL_CSV = route(EA.REDUNDANCY_DETAIL_OUT)
LIB_INT_CSV = route(os.path.join(EA.RESULTS_DIR, "etf_redundancy_library_internal.csv"))


def library_counts(n_evaluated):
    """库的三个口径数一次数清：(active 总数, 无 expr 的, 求值失败的)。

    为什么单独立一个函数：`load_active_library()` 的返回长度**不等于**库里的 active 数
    —— 它在内部就把 `expr` 为空的条目滤掉了（只在自己打印时提一句）。早先那行汇总直接写
    `len(lib) - len(facs_l)` 还标成"不可译/无 expr"，于是库里明明躺着 1 条无 expr 的
    `ma_ratio_10_30`，汇总却读出"另有 0 条"，把盲区读成了零。这里直接读 csv 本体数。
    """
    if not os.path.exists(EA.FACTOR_LIBRARY_CSV):
        return n_evaluated, 0, 0
    raw = pd.read_csv(EA.FACTOR_LIBRARY_CSV, encoding="utf-8-sig",
                      keep_default_na=False)
    live = raw["status"].astype(str).str.strip().str.lower() == "active"
    blank = live & (raw["expr"].astype(str).str.strip() == "")
    n_active = int(live.sum())
    n_blank = int(blank.sum())
    return n_active, n_blank, max(0, (n_active - n_blank) - n_evaluated)


def verdict(x):
    """把 redundancy 数值翻译成本线会怎么处理（与 RED_BAR/RDAGENT_BAR/NEAR_DUP 对齐）。"""
    if not np.isfinite(x):
        return "无法判定（对侧不可求值）"
    if x >= EA.RDAGENT_BAR:
        return "必被丢（>=%.2f，RD-Agent dedup 同值）" % EA.RDAGENT_BAR
    if x >= EA.RED_BAR:
        return "会被判重复（>=%.2f，本线跨源判重线）" % EA.RED_BAR
    if x >= EA.NEAR_DUP:
        return "危险区 %.2f~%.2f（同簇，人工复核）" % (EA.NEAR_DUP, EA.RED_BAR)
    return "可提名（<%.2f）" % EA.NEAR_DUP


def abs_worst(row, exclude=()):
    """按 |corr| 挑近亲 —— 反号的两条是同一个信息的两个符号，判重必须认（见模块 docstring）。

    返回 (近亲名, 带符号原值, 绝对值)；这一行没有任何可对侧时 (None, nan, nan)。
    `exclude` **只按名字**剔掉"它自己"（外部候选清单里那些本来就在库里的条目，
    自己和自己的截面相关恰好 1.0000，那条读数不含信息）；表达式相同而名字不同的对侧
    照算 —— 那正是"以为挖到新的、其实库里有"。
    """
    s = row[~row.index.isin(list(exclude))].dropna()
    if s.empty:
        return None, np.nan, np.nan
    nm = s.abs().idxmax()
    return nm, float(s.loc[nm]), float(abs(s.loc[nm]))


def library_internal(LS, LP, names, ic_of):
    """在库 × 在库的上三角读数（09-28 丁方案，理由见模块 docstring）。

    只列 `|corr| >= NEAR_DUP` 的对，对角（自己和自己的截面相关恰好 1.0000）不读。
    `ic_of` = {库内名: |库自带 ic|}，用来在同一对里挑一条留下 —— 那批条目没进过环 1
    归档、没有截面 RankICIR，所以这一列**不能**拿去和候选的 |RankICIR| 横比。
    返回 (逐对明细 DataFrame, 唯一簇数)。唯一簇数把 `|corr| >= RED_BAR` 的对并成簇后
    数簇，"35 条 active 里只有 31 条唯一"那句汇总就来自它（危险区的对不并簇：本线
    从没说 0.70~0.85 算同一条，那条线只喊人工复核）。
    ⚠️ 表里每行的 `abs_rank_corr` 是**这一对**的读数。要拿这张表去动库（09-29 丁2 就是这么干的），
    被剔那条配的数必须是它与替身**之间**那条边的值，**不是簇内最大值** —— 一个簇里
    `mogp_5`↔`mogp_7` 可以是 1.0000，而 `mogp_5`↔替身 `mom_20` 只有 0.9968，写串了就是假账。
    """
    rows, parent = [], {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            v = float(LS.loc[a, b])
            if not np.isfinite(v) or abs(v) < EA.NEAR_DUP:
                continue
            keep = a if ic_of.get(a, 0.0) >= ic_of.get(b, 0.0) else b
            rows.append({"侧1": a, "侧2": b, "范围": "在库×在库",
                         "rank_corr": v, "abs_rank_corr": abs(v),
                         "pearson_corr": float(LP.loc[a, b]),
                         "判决": verdict(abs(v)),
                         "建议留": keep,
                         "留的|ic|": ic_of.get(keep, np.nan),
                         "剔的|ic|": ic_of.get(b if keep == a else a, np.nan)})
            if abs(v) >= EA.RED_BAR:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
    det = pd.DataFrame(rows).sort_values("abs_rank_corr", ascending=False) \
        if rows else pd.DataFrame(columns=["侧1", "侧2", "范围", "rank_corr",
                                           "abs_rank_corr", "pearson_corr", "判决",
                                           "建议留", "留的|ic|", "剔的|ic|"])
    return det, len({find(n) for n in names})


def load_specs():
    specs = list(EA.FAMILY_SPECS)
    if SPEC_JSON:
        with open(SPEC_JSON, encoding="utf-8") as f:
            specs += EA.specs_from_rows(json.load(f), family="外部")
    return specs


def icir_of():
    """尽量读第一环产物拿 |RankICIR|，用于"重复对里留谁"。缺表就返回空表。"""
    if not os.path.exists(EVAL_CSV):
        return {}
    res = pd.read_csv(EVAL_CSV)
    key = f"rank_icir_h{PRIMARY_H}"
    if key not in res.columns:
        return {}
    return {r["name"]: r[key] for _, r in res.iterrows()
            if r.get("status") == "ok" and pd.notna(r[key])}


def keeper(a, b, stats):
    """重复对里建议留的那条：|RankICIR| 高者留（无 IC 数据时按在库优先）。"""
    if not stats:
        return f"{a}|{b}(缺IC，未判)"
    return a if abs(stats.get(a, 0.0)) >= abs(stats.get(b, 0.0)) else b


def main():
    t0 = time.time()
    print("=" * 78)
    print("ETF 因子判重预检（准入链第三环）· 运行参数")
    print("=" * 78)
    for k, v in EA.run_params().items():
        print(f"  {k:14s} = {v}")

    cand = load_specs()
    lib = EA.load_active_library()          # 只读
    lib_specs = EA.specs_from_rows(lib, family="在库")
    # 库侧出现的名字集合：外部候选若就是库里这条，对侧要先剔掉它自己（见模块 docstring）
    lib_names = {EA.spec_name(s) for s in lib_specs}
    print(f"[输入] 候选 {len(cand)} 条 × 在库可译 {len(lib_specs)} 条")

    pool = EA.load_pool()
    facs_c = {k: EA.slice_to_start(v, EA.EVAL_START)
              for k, v in EA.evaluate_factors(pool, cand).items()}
    facs_l = {k: EA.slice_to_start(v, EA.EVAL_START)
              for k, v in EA.evaluate_factors(pool, lib_specs).items()}
    if not facs_c:
        raise SystemExit("[候选] 全部不可求值")
    stats = icir_of()
    any_one = next(iter(facs_c.values()))
    print(f"[求值] 候选成表 {len(facs_c)}/{len(cand)} 张、在库成表 {len(facs_l)} 张；"
          f"评估窗 {any_one.index[0].date()} ~ {any_one.index[-1].date()}")

    # (a) 候选内部两两
    PC, SC = EA.cs_corr_mean(facs_c, min_cs=EA.MIN_CS)
    # (b) 候选 × 在库
    PL, SL = (EA.cs_corr_mean(facs_c, facs_l, min_cs=EA.MIN_CS)
              if facs_l else (pd.DataFrame(), pd.DataFrame()))
    # (c) 在库 × 在库对角线 —— 只加读数：不改 (a)(b) 任何判决，也不剔任何条目
    lib_ic = {(r.get("name") or (r.get("expr") or "")[:24]):
              abs(float(r.get("ic") or 0.0)) for r in lib}
    if len(facs_l) > 1:
        LP_L, LS_L = EA.cs_corr_mean(facs_l, min_cs=EA.MIN_CS)
        lib_det, n_unique = library_internal(LS_L, LP_L, list(facs_l), lib_ic)
    else:
        lib_det, n_unique = pd.DataFrame(
            columns=["侧1", "侧2", "范围", "rank_corr", "abs_rank_corr",
                     "pearson_corr", "判决", "建议留", "留的|ic|", "剔的|ic|"]), \
            len(facs_l)

    rows, pairs = [], []
    for nm in facs_c:
        # 与同批候选的最大重复度（|corr| 口径，对侧去掉自身）
        best_c, best_cs, best_cv = abs_worst(SC.loc[nm], exclude=[nm])
        best_l, best_ls, best_lv = (abs_worst(SL.loc[nm], exclude=[nm])
                                    if nm in SL.index else (None, np.nan, np.nan))
        rows.append({"name": nm,
                     "family": next((s["family"] for s in cand
                                     if EA.spec_name(s) == nm), ""),
                     "expr": next((s["expr"] for s in cand if EA.spec_name(s) == nm), ""),
                     "rank_icir": stats.get(nm, np.nan),
                     "max_vs_cand": best_cv, "cand_twin": best_c or "",
                     "cand_signed": best_cs,
                     "max_vs_library": best_lv, "library_twin": best_l or "",
                     "library_signed": best_ls,
                     "self_in_library": nm in set(lib_names),
                     "cand_verdict": verdict(best_cv),
                     "library_verdict": verdict(best_lv),
                     "pearson_vs_cand": float(PC.loc[nm, best_c]) if best_c else np.nan,
                     "pearson_vs_library": float(PL.loc[nm, best_l]) if best_l else np.nan})
        if best_c and np.isfinite(best_cv) and best_cv >= EA.NEAR_DUP:
            pairs.append({"侧1": nm, "侧2": best_c, "范围": "候选内部",
                          "rank_corr": best_cs, "abs_rank_corr": best_cv,
                          "pearson_corr": float(PC.loc[nm, best_c]),
                          "判决": verdict(best_cv),
                          "建议留": keeper(nm, best_c, stats)})
        if best_l and np.isfinite(best_lv) and best_lv >= EA.NEAR_DUP:
            pairs.append({"侧1": nm, "侧2": best_l, "范围": "候选×在库",
                          "rank_corr": best_ls, "abs_rank_corr": best_lv,
                          "pearson_corr": float(PL.loc[nm, best_l]),
                          "判决": verdict(best_lv),
                          "建议留": nm if best_lv < EA.RED_BAR else best_l})

    res = pd.DataFrame(rows).sort_values(["max_vs_library", "max_vs_cand"],
                                         ascending=False)
    res.to_csv(OUT_CSV, index=False)
    det = pd.DataFrame(pairs).sort_values("abs_rank_corr", ascending=False) \
        if pairs else pd.DataFrame(columns=["侧1", "侧2", "范围", "rank_corr",
                                            "abs_rank_corr", "pearson_corr",
                                            "判决", "建议留"])
    det.to_csv(DETAIL_CSV, index=False)
    # 候选内部完整矩阵也留一份（矩阵形态，交叉表方便眼看整簇）
    SC.to_csv(route(os.path.join(EA.RESULTS_DIR, "etf_redundancy_matrix_cand.csv")))
    if len(SL.columns):
        SL.to_csv(route(os.path.join(EA.RESULTS_DIR,
                                     "etf_redundancy_matrix_vs_library.csv")))
    lib_det.to_csv(LIB_INT_CSV, index=False)

    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 40)
    print(f"\n===== 逐条判决（按「与在库的最大重复度」降序）=====")
    print(res.round(4).to_string(index=False))
    print(f"\n===== 高重复对明细（|rank_corr| >= {EA.NEAR_DUP} 才列，共 {len(det)} 对）=====")
    print(det.round(4).to_string(index=False) if len(det) else "（无）")
    print(f"\n===== 在库×在库对角线（池子自己的近亲，共 {len(lib_det)} 对）=====")
    print(lib_det.round(4).to_string(index=False) if len(lib_det) else "（无）")
    n_red = int((res["library_verdict"].str.startswith("会被判重复")).sum())
    n_dead = int((res["library_verdict"].str.startswith("必被丢")).sum())
    n_warn = int((res["library_verdict"].str.startswith("危险区")).sum())
    print(f"\n[汇总] 候选 {len(res)} 条：撞在库红线 {n_red} 条、必被 rdagent 丢 {n_dead} 条、"
          f"危险区 {n_warn} 条、其余可提名 "
          f"{len(res) - n_red - n_dead - n_warn} 条")
    n_lib_active, n_lib_blank, n_lib_broken = library_counts(len(facs_l))
    print(f"[汇总] 在库 active {n_lib_active} 条（进得了判重 {len(facs_l)} 条、"
          f"无 expr 本环看不见 {n_lib_blank} 条、求值失败 {n_lib_broken} 条）"
          f"⇒ 可译里唯一簇 {n_unique} 条"
          f"（按 |corr| >= {EA.RED_BAR} 并掉 {len(facs_l) - n_unique} 条）")
    print(f"[输出] {OUT_CSV}\n[输出] {DETAIL_CSV}\n[输出] {LIB_INT_CSV}\n"
          f"[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
