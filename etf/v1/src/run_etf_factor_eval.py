# -*- coding: utf-8 -*-
"""ETF 全市场池（871 只）因子层截面评估入口 —— 准入链第一环。

一句话：把一条因子表达式放到**当天所有 ETF 之间**比排序，看它对之后 5/10/20 天的
涨跌有没有稳定的预测力。回答的是「这个信号在 ETF 截面上到底有没有信息」。

被评对象：`etf_admission.FAMILY_SPECS` 的 8 族候选（价格水平/动量/反转/趋势位置/
波动/量能/价量交互/日内隔夜），外加 `ETF_SPEC_JSON=<路径>` 指定的外部表达式清单
（RD-Agent 回收的 factors.json 就从这里进来）。

判据为什么是 **RankIC 而不是 IC**（本线自己量出来的，不是抄股票线）
-------------------------------------------------------------------
    IC_t      = corr_i(F_{t,i}, r_{i,t→t+h})            原始值，qlib 报表口径
    RankIC_t  = corr_i(rank(F_{t,i}), rank(r_{i,t→t+h}))  秩，抗离群值
    ICIR      = mean_t(RankIC_t) / std_t(RankIC_t)
本池价格量级横跨 0.3~100 元、成交额横跨三个数量级，原始值 Pearson 极易被少数
高价/大成交标的独揽 —— 实测同一批因子 `ic_h10` 与 `rank_ic_h10` 的符号都不一定一致。
故**准入判据用 RankIC**，IC 只作对照列出（两列都在产物里，谁也不藏）。

池子口径：为什么必须是 871 只全市场
-----------------------------------
主线 20 只代表池日均只有约 7.7 只当日有行情，横截面统计量在那个样本量上是噪声
（本仓库已实测过的结论）。本入口固定用 `data/universe_all/` 全市场池，并且
**逐日报告截面厚度**：`days_thin` 列 = 当日有效标的数 < MIN_CS 而被丢弃的天数。
MIN_CS=30 的理由见 etf_admission 的常量注释（与本线 factor_dsl.compute_ic 的
「n<30 无统计意义」同口径），评估窗默认从 2019-01-01 起 —— 池子在那之前还没长起来。

只读：不写因子库、不触发 git 提交；产物只落
    data/results/etf_factor_eval.csv     因子 × 期限 的截面指标
    data/results/etf_factor_layers.csv   因子 × 分层 的单调性与换手
带 `ETF_SPEC_JSON` 跑时这两份改成 `*.cand.csv`（外部候选不配占用后续两环的输入口径）。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import json
import os
import sys
import time

import numpy as np
import pandas as pd

import etf_admission as EA

# 口径常量一律取自 etf_admission（ETF_* 环境变量可覆盖），本脚本不定义第二份
SPEC_JSON = os.environ.get("ETF_SPEC_JSON", "")
PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))    # 排序/合成用的主期限


def route(path):
    """带外部候选清单（`ETF_SPEC_JSON`）跑时，产物一律改名落盘，**绝不覆盖权威口径那三份 csv**。

        判据：本入口的默认输出是准入链后续两环的输入（环 2 读它选因子、环 3 读它取 ICIR），
        一次候选评估写进去就把"31 条族候选"的口径换成了"候选 + 外部"的口径。
        踩过：`ETF_RESULTS_DIR` 这个环境变量是空转的（RESULTS_DIR 由 DATA_DIR 推导），
        09-24 两批文法候选评估因此直接污染了权威产物，只能整环重跑复原。
    """
    stem, ext = os.path.splitext(path)
    return stem + (".cand" if SPEC_JSON else "") + ext


OUT_CSV = route(EA.FACTOR_EVAL_OUT)
LAYER_CSV = route(EA.FACTOR_LAYER_OUT)


def load_specs():
    """族候选 + 外部表达式清单（json: [{name, expr}]），合并成一次评估的输入。"""
    specs = list(EA.FAMILY_SPECS)
    if SPEC_JSON:
        with open(SPEC_JSON, encoding="utf-8") as f:
            rows = json.load(f)
        extra = EA.specs_from_rows(rows, family="外部")
        print(f"[候选] 外部 {SPEC_JSON}：{len(rows)} 条 → 可翻译 {len(extra)} 条"
              f"（本次产物落 *.cand.csv，不动权威口径那两份）")
        specs += extra
    return specs


def eval_one(fac, m, horizons):
    """一张因子宽表 × 多个期限 → 指标 dict（RankIC 为主、IC 作对照）。

        RankIC_t = corr_i(rank(F_{t,i}), rank(r_{i,t→t+h}))，当日两侧齐备的标的 i
        ICIR     = mean/std，胜率 = 占比_t sign(IC_t)=sign(mean)，t = mean/(std/√T)
    """
    out = {}
    for h in horizons:
        ic, ric, n = EA.daily_cs_ic(fac, m[f"fwd{h}"], min_cs=EA.MIN_CS)
        r = EA.ic_summary(ric, n, EA.MIN_CS)
        p = EA.ic_summary(ic, n, EA.MIN_CS)
        out[f"rank_ic_h{h}"] = r["mean"]
        out[f"rank_icir_h{h}"] = r["icir"]
        out[f"rank_ic_win_h{h}"] = r["win"]
        out[f"rank_ic_t_h{h}"] = r["t"]
        out[f"ic_h{h}"] = p["mean"]
        out[f"icir_h{h}"] = p["icir"]
        out[f"days_h{h}"] = r["days"]
        out[f"cs_med_h{h}"] = r["cs_median"]
        out[f"days_thin_h{h}"] = r["days_thin"]
    return out


def main():
    t0 = time.time()
    print("=" * 78)
    print("ETF 因子层截面评估（准入链第一环）· 运行参数")
    print("=" * 78)
    for k, v in EA.run_params().items():
        print(f"  {k:14s} = {v}")
    print(f"  primary_h      = {PRIMARY_H}（排序与合成打分用的主期限）")

    specs = load_specs()
    print(f"\n[候选] {len(specs)} 条表达式，族："
          f"{sorted({s['family'] for s in specs})}")

    # 全历史载入 → 因子有暖机段（rolling 窗未满的头几根只是不算，不污染后面）
    pool = EA.load_pool()
    if len(pool) < 300:
        raise SystemExit(f"[数据] 全市场池只剩 {len(pool)} 只，口径异常，停止评估")
    m_all = EA.build_matrices(pool)
    m = EA.take_window(m_all)
    print(f"[截面厚度] {EA.thickness_report(m)}")
    tb = EA.thickness_by_year(m, EA.MIN_CS)
    print("[逐年厚度]（池子在长个子：早年的 IC 抖动多半是截面薄，不是因子失效）")
    print(tb.T.to_string())

    # 秩一律按 (因子, 期限) 现算，不跨因子缓存：mask 是"两侧都有值"的交集，
    # 换一条因子就换一张 mask，缓存标签的秩等于偷换定义（daily_cs_ic 里记着这条）
    rows, layer_rows = [], []
    facs = {}
    t_eval = time.time()
    dead = []
    for i in range(0, len(specs), 8):      # 分批求值 + 立刻切窗口，峰值内存控制在 8 张表
        chunk = specs[i:i + 8]
        # verbose=False：evaluate_factors 只报当批 8 条，日志读起来像"只评了 8 条"
        # （09-24 真被这行骗过一次）。总数在循环后统一报。
        got = EA.evaluate_factors(pool, chunk, verbose=False)
        got = {k: EA.slice_to_start(v, EA.EVAL_START) for k, v in got.items()}
        for sp in chunk:
            nm = EA.spec_name(sp)
            base = {"name": nm, "family": sp["family"], "expr": sp["expr"],
                    "note": sp.get("note", "")}
            if nm not in got:
                rows.append({**base, "status": "不可求值/全NaN"})
                dead.append(nm)
                continue
            fac = got[nm]
            facs[nm] = fac
            q = EA.layer_summary(EA.quintile_layers(fac, m["ret"], q=EA.QUINTILES,
                                                    min_cs=EA.MIN_CS))
            layer_rows.append({"name": nm, "family": sp["family"],
                               **{f"q{b + 1}_ann": v for b, v in enumerate(q["q_ann"])},
                               "q_spread_ann": q["q_spread_ann"],
                               "q_top_ann": q["q_top_ann"],
                               "q_top_excess_ann": q["q_top_excess_ann"],
                               "mono": q["mono"], "ls_ann": q["ls_ann"],
                               "ls_win": q["ls_win"],
                               "top_turnover_ann": q["top_turnover_ann"],
                               "top_avg_n": q["top_avg_n"]})
            # coverage：窗口内"有因子值"的格子占比 —— 按**日内占比的年中位数**读，
            # 不能对整个矩阵直接求均值：池子是逐年在长的（2013 年 28 只、2026 年 871 只），
            # 全矩阵均值会被"晚上市"这一件事主导，读不出因子本身的有效范围。
            # 低覆盖的因子只在少数几只上说话，IC 再高也不能算全市场结论，故并列呈报。
            dc = fac.notna().mean(axis=1)
            yc = dc.groupby(dc.index.year).median()
            rows.append({**base, "status": "ok",
                         "coverage_med_year": float(yc.median()),
                         "coverage_last_year": float(yc.iloc[-1]),
                         **eval_one(fac, m, EA.HORIZONS)})
        del got

    print(f"[求值] {len(specs)} 条表达式（每批 8 条）× {len(pool)} 只 → "
          f"成表 {len(facs)} 列"
          + (f"，不可求值 {len(dead)} 条：" + "、".join(dead) if dead else "")
          + f"，耗时 {time.time() - t_eval:.0f}s")

    res = pd.DataFrame(rows)
    lay = pd.DataFrame(layer_rows)
    key = f"rank_icir_h{PRIMARY_H}"
    if key not in res.columns:            # 一条都没求值成功时也要出表，而不是抛 KeyError
        res[key] = np.nan
    res["abs_rank_icir"] = res[key].abs()
    res = res.sort_values("abs_rank_icir", ascending=False)
    res.to_csv(OUT_CSV, index=False)
    # 分层表跟着主表名次走；不可求值的因子没有分层行，跳过而不是补一行 NaN
    if len(lay):
        lay = lay.set_index("name").reindex(
            [n for n in res["name"] if n in set(lay["name"])]).reset_index()
    lay.to_csv(LAYER_CSV, index=False)

    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 80)
    show = (["name", "family", "rank_ic_h5", "rank_ic_h10", "rank_ic_h20",
             "rank_icir_h5", "rank_icir_h10", "rank_icir_h20",
             "rank_ic_win_h10", "ic_h10", "rank_ic_t_h10",
             "days_h10", "cs_med_h10", "days_thin_h10",
             "coverage_med_year", "status"])
    print("\n===== 截面 IC 排行（按 |RankICIR| @h=%d）=====" % PRIMARY_H)
    print(res[[c for c in show if c in res.columns]].head(18).to_string(index=False))
    print("\n===== 分层（Q1 低分 → Q5 高分，逐日再平衡等权，不扣费）=====")
    print(lay[["name", "family", "q1_ann", "q3_ann", "q5_ann", "mono",
               "ls_ann", "top_turnover_ann"]].head(18).to_string(index=False))

    def _best_name(s):
        return res.loc[s.idxmax(), "name"] if s.notna().any() else ""

    def _best_val(s):
        return float(s.iloc[np.nanargmax(s.abs())]) if s.notna().any() else np.nan

    fam = (res[res["status"] == "ok"]
           .groupby("family", as_index=False)
           .agg(n=("name", "size"),
                best_abs_rank_icir=("abs_rank_icir", "max"),
                best_rank_ic=(f"rank_ic_h{PRIMARY_H}", _best_val),
                best_name=("abs_rank_icir", _best_name)))
    fam["mono_of_best"] = fam["best_name"].map(lay.set_index("name")["mono"]) \
        if len(lay) else np.nan
    print("\n===== 族级判决（一族取 |RankICIR| 最高者当天花板）=====")
    print(fam.sort_values("best_abs_rank_icir", ascending=False).round(4)
          .to_string(index=False))
    print(f"\n[输出] {OUT_CSV}\n[输出] {LAYER_CSV}\n[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
