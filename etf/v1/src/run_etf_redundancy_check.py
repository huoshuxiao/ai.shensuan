# -*- coding: utf-8 -*-
"""ETF 因子判重预检入口 —— 准入链第三环。

一句话：一条新因子在进库之前先问两句 ——
    (a) 它和**同批候选**是不是同一个东西？（同批里留 |RankICIR| 高的那条）
    (b) 它和**已在库的 26 条 active 因子**是不是同一个东西？（是就没必要再收一条）
两问用的都是同一个量：逐日截面相关的时间平均。

判重口径（本线自己定，写清楚为什么）
------------------------------------
    corr_t(F_a, F_b) = corr_i(F_{a,t,i}, F_{b,t,i})     i ∈ 当日全部候选齐备的标的
    redundancy(a, b) = mean_t corr_t
    判据用 Spearman（秩）版，因为本线现行判重就是
    `etf/v1/src/data/multi_source_mining.py` 里的 `spearman_corr_matrix > 0.85`；
    Pearson 版并列输出，两者差得多说明有一侧被离群值撑高。

为什么不能"把两列拉平后整体求相关"
---------------------------------
本池 2013 年中位 28 只、2026 年中位 871 只。拉平后那个数把「不同日期」当成同一个
截面，后段样本天然占优，任何两条时序因子都能算出虚高的正相关 —— 那是池子在长，
不是因子像。逐日算再取均值才是"这两个因子在同一天排序像不像"。

三条线（都在 etf_admission 常量里，可覆盖）
------------------------------------------
    redundancy >= 0.99   进 RD-Agent 循环必被 deduplicate_new_factors 丢
    redundancy >= 0.85   本线跨源判重线，入库即触发剔除
    0.70 ~ 0.85          危险区：不触发判重但已是同一簇，人工复核
    < 0.70               可提名

只读：不写 `data/library/`（判重输入用它，产物只落 results/），不触发 git 提交。
产物  data/results/etf_redundancy_check.csv（逐条判决）
      data/results/etf_redundancy_detail.csv（逐对明细，含近亲是谁）
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

    rows, pairs = [], []
    for nm in facs_c:
        # 与同批候选的最大重复度（去掉自身）
        sc = SC.loc[nm].drop(index=[nm])
        best_c = sc.idxmax() if len(sc) else None
        best_cv = float(sc.max()) if len(sc) else np.nan
        sl = SL.loc[nm] if nm in SL.index else pd.Series(dtype="float64")
        best_l = sl.idxmax() if len(sl) else None
        best_lv = float(sl.max()) if len(sl) else np.nan
        rows.append({"name": nm,
                     "family": next((s["family"] for s in cand
                                     if EA.spec_name(s) == nm), ""),
                     "expr": next((s["expr"] for s in cand if EA.spec_name(s) == nm), ""),
                     "rank_icir": stats.get(nm, np.nan),
                     "max_vs_cand": best_cv, "cand_twin": best_c or "",
                     "cand_verdict": verdict(best_cv),
                     "max_vs_library": best_lv, "library_twin": best_l or "",
                     "library_verdict": verdict(best_lv),
                     "pearson_vs_cand": float(PC.loc[nm].drop(index=[nm]).max())
                     if len(PC.loc[nm]) > 1 else np.nan,
                     "pearson_vs_library": float(PL.loc[nm].max())
                     if nm in PL.index and len(PL.columns) else np.nan})
        if best_c and np.isfinite(best_cv) and best_cv >= EA.NEAR_DUP:
            pairs.append({"侧1": nm, "侧2": best_c, "范围": "候选内部",
                          "rank_corr": best_cv,
                          "pearson_corr": float(PC.loc[nm, best_c]),
                          "判决": verdict(best_cv),
                          "建议留": keeper(nm, best_c, stats)})
        if best_l and np.isfinite(best_lv) and best_lv >= EA.NEAR_DUP:
            pairs.append({"侧1": nm, "侧2": best_l, "范围": "候选×在库",
                          "rank_corr": best_lv,
                          "pearson_corr": float(PL.loc[nm, best_l]),
                          "判决": verdict(best_lv),
                          "建议留": nm if best_lv < EA.RED_BAR else best_l})

    res = pd.DataFrame(rows).sort_values(["max_vs_library", "max_vs_cand"],
                                         ascending=False)
    res.to_csv(OUT_CSV, index=False)
    det = pd.DataFrame(pairs).sort_values("rank_corr", ascending=False) \
        if pairs else pd.DataFrame(columns=["侧1", "侧2", "范围", "rank_corr",
                                            "pearson_corr", "判决", "建议留"])
    det.to_csv(DETAIL_CSV, index=False)
    # 候选内部完整矩阵也留一份（矩阵形态，交叉表方便眼看整簇）
    SC.to_csv(route(os.path.join(EA.RESULTS_DIR, "etf_redundancy_matrix_cand.csv")))
    if len(SL.columns):
        SL.to_csv(route(os.path.join(EA.RESULTS_DIR,
                                     "etf_redundancy_matrix_vs_library.csv")))

    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 40)
    print(f"\n===== 逐条判决（按「与在库的最大重复度」降序）=====")
    print(res.round(4).to_string(index=False))
    print(f"\n===== 高重复对明细（>= {EA.NEAR_DUP} 才列，共 {len(det)} 对）=====")
    print(det.round(4).to_string(index=False) if len(det) else "（无）")
    n_red = int((res["library_verdict"].str.startswith("会被判重复")).sum())
    n_dead = int((res["library_verdict"].str.startswith("必被丢")).sum())
    n_warn = int((res["library_verdict"].str.startswith("危险区")).sum())
    print(f"\n[汇总] 候选 {len(res)} 条：撞在库红线 {n_red} 条、必被 rdagent 丢 {n_dead} 条、"
          f"危险区 {n_warn} 条、其余可提名 "
          f"{len(res) - n_red - n_dead - n_warn} 条")
    print(f"[输出] {OUT_CSV}\n[输出] {DETAIL_CSV}\n[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
