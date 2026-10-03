# -*- coding: utf-8 -*-
"""P0-1 追加量（09-27）：名单独立闸的刀口深度，在**真正掏钱的 5 席下单层**值多少钱。

来由：`dd_scale_0926.py` 第一次把 5 席层的账量出来 —— 这道闸在 50 只观察名单层是
+0.76pp/年（㊱），搬到 5 席层却变成年均 **−8.78pp**、12 年里 10 年为负。当时没量的是
**深度**：这笔亏是「这道闸本身不该要」还是「0.80 这一刀切得太狠」？

判据口径（一句解释）：刀口 q = 「池内分位 ≥ q 的票挡在名单外」。q 越大挡得越少：
0.80 挡两成、0.95 只挡二十分之一 ⇒ 换血更少、税更低，但信号也用得更少。

四条腿 + 一条锚点腿，全部走**同一个** `screen_on_date` 与**同一份**回放实现
（`dd_scale_0926.replay_picks`，本脚本不另写时序，免得两表口径分叉）：

  A  全局前 50·无闸     —— 只当反证式自检用：max_drawdown 必须是 −0.535676
  B  配额名单·无闸      —— 这道闸的对照基准（所有「差」都减它）
  q=0.80/0.85/0.90/0.95 —— 配额名单 + 独立闸，刀口逐档

⚠️ 09-26 那两场（㊱ 名单层、㉟ 池内命中）都没量过 5 席层，所以 0.85/0.90/0.95 在
这一层的账单是全新的；㊲ 那句「税严格单调下降」是**名单层**的结论，能不能搬到 5 席层
正是本轮要答的。

边界照旧（同 P0-1）：570 个调仓日回放不跑那三道执行层闸（真成交/贴涨停/ST），
行业映射用今天这份表回溯 2015 年 ⇒ 量的是信号层形状，不是实盘账户。

09-27 拆分：`prepare()` / `build_picks(P, legs)` 单独成函数，让追加的 2023 归因驱动
（`dd_gate_2023_0927.py`）能拿到**同一批篮子**而不抄第三份时序；拆完重跑本脚本，
两张 CSV 与拆前**逐字节相同**才算数（对表见 `tmp_dd_gate_depth_0927_pre_split/`）。
"""

import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "stock/v1/src"))
import _bootstrap  # noqa: F402,E402

import dd_scale_0926 as D                                    # noqa: E402  复用同一份回放
from config import (ASHARE_BUY_MIN_HITS, ASHARE_BUY_TOP_N,   # noqa: E402
                    ASHARE_ORDER_MAX_PER_BOARD, ASHARE_ORDER_MAX_PER_INDUSTRY,
                    ASHARE_ORDER_TOP_N,
                    ASHARE_PORT_COST_ONE_WAY, ASHARE_PORT_HOLD,
                    ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED, ASHARE_PORT_START,
                    ASHARE_SCREEN_QUANTILE)
from ashare_screen import (BUY_EXPR, BUY_EXTRA_EXPRS, RULE_EXPRS, board_of,   # noqa: E402
                           build_matrices, factor_matrices, load_industry_map,
                           load_panel, order_candidates, screen_on_date, tradable_mask)
import run_ashare_portfolio_eval as R                        # noqa: E402

OUT = os.path.join(HERE, "tmp_dd_gate_depth_0927")
os.makedirs(OUT, exist_ok=True)
QUANTS = [0.80, 0.85, 0.90, 0.95]
OFF = 2.0                       # 分位取值域是 (0,1] ⇒ 永不命中 = 这道闸关掉
TRADING_DAYS = 252
# 腿标签 → 刀口深度（B 用惰值 2.0 = 关掉这道闸）。拆成常量是给追加归因的驱动复用
QUANT_BY_LABEL = {"B": OFF, **{f"q{q:g}": q for q in QUANTS}}


def year_excess(port, bench):
    p = port.dropna()
    j = pd.concat([p, bench.reindex(p.index).fillna(0.0)], axis=1)
    return (j.iloc[:, 0] - j.iloc[:, 1]).groupby(j.index.year).mean() * TRADING_DAYS


def prepare():
    """读面板、建矩阵、复现共识闸掩码。口径只在这里出现一次，供各归因驱动复用"""
    t0 = time.time()
    wide, _ = load_panel()
    mtx = build_matrices(wide)
    fms = factor_matrices(sorted(set(RULE_EXPRS) | {BUY_EXPR} | set(BUY_EXTRA_EXPRS)), mtx)
    cut = np.searchsorted(mtx["ret_open"].index, pd.Timestamp(ASHARE_PORT_START))
    start = max(cut - ASHARE_PORT_HOLD, 1)
    off = cut - start                       # 前 off 天只当「昨收」用，不出名单
    all_days = mtx["ret_open"].index
    mtx = {k: v.loc[all_days[start:]] for k, v in mtx.items()}
    fms = {e: m.loc[m.index[start:]] for e, m in fms.items()}
    days = mtx["ret_open"].index
    universe = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
                & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    bench = mtx["ret_open"].where(universe).mean(axis=1)
    bench = bench[bench.index >= pd.Timestamp(ASHARE_PORT_START)]
    gate = fms[RULE_EXPRS[0]]
    ind, ind_meta = load_industry_map()
    ind_map = ind.to_dict() if ind_meta.get("loaded") else {}
    pool_df, hit_df = R.exclusion_hits(mtx, days[off:], fms, ASHARE_PORT_HOLD,
                                       ASHARE_SCREEN_QUANTILE)
    allow = R.exclusion_mask(("并集", "x", tuple(range(len(R.VOLUME_RULES))),
                              ASHARE_BUY_MIN_HITS), pool_df, hit_df)
    print(f"[准备] {time.time() - t0:.0f}s｜{len(days)} 交易日｜行业表 {ind_meta.get('loaded')}")
    # universe 也返回出去：仓位层驱动要按可投域算市场宽度，别在那里再抄一遍口径
    return dict(mtx=mtx, fms=fms, days=days, off=off, bench=bench, gate=gate,
                ind_map=ind_map, allow=allow, universe=universe)


def build_picks(P, legs):
    """按标签逐调仓日复现「真正下单的那 5 席」

    A = 全局前 50·无闸（只当反证式自检的锚点腿），其余走生产同一个 `screen_on_date`，
    只换 `buy_extra_quantile` 这一根旋钮。归因驱动要 B 与某一档，直接传标签即可 ——
    时序与判据不在别处再写第二份。
    """
    mtx, fms, days, off = P["mtx"], P["fms"], P["days"], P["off"]
    allow, gate, ind_map = P["allow"], P["gate"], P["ind_map"]
    n = ASHARE_ORDER_TOP_N
    picks = {k: {} for k in legs}
    t1 = time.time()
    for i in range(off, len(days) - ASHARE_PORT_HOLD - 1, ASHARE_PORT_HOLD):
        s, d1 = days[i], days[i + 1]
        ok, _ = tradable_mask(s, d1, fms[BUY_EXPR], mtx)
        if "A" in legs:
            cand = fms[BUY_EXPR].loc[s].where(ok & allow.loc[s]).dropna().sort_values()
            if len(cand) >= ASHARE_BUY_TOP_N:
                l50 = list(cand.index[:ASHARE_BUY_TOP_N])
                buy = pd.DataFrame({"rank": range(1, len(l50) + 1),
                                    "板块": [board_of(c) for c in l50]}, index=l50)
                od, _ = order_candidates(buy, ind_map, n, ASHARE_ORDER_MAX_PER_INDUSTRY,
                                         ASHARE_ORDER_MAX_PER_BOARD)
                picks["A"][s] = list(od.index)
        base = dict(top_n=ASHARE_BUY_TOP_N, st_codes=None,
                    buy_min_hits=ASHARE_BUY_MIN_HITS)
        for lab in [k for k in legs if k != "A"]:
            r = screen_on_date(s, d1, mtx, fms, gate, ASHARE_SCREEN_QUANTILE,
                               buy_extra_quantile=QUANT_BY_LABEL[lab], **base)
            if len(r["buy"]) >= n:
                od, _ = order_candidates(r["buy"], ind_map, n,
                                         ASHARE_ORDER_MAX_PER_INDUSTRY,
                                         ASHARE_ORDER_MAX_PER_BOARD)
                picks[lab][s] = list(od.index)
    print(f"[名单] {time.time() - t1:.0f}s｜" +
          "、".join(f"{k}:{len(v)}" for k, v in picks.items()))
    for k, v in picks.items():
        if k != "A" and v.keys() != picks["B"].keys():
            raise SystemExit(f"⇒ 腿 {k} 的调仓日集合与 B 不齐（{len(v)} vs {len(picks['B'])}）"
                             f"⇒ 对照不是同窗口之差，别看")
    return picks


def main():
    t0 = time.time()
    P = prepare()
    legs = ["A", "B"] + [f"q{q:g}" for q in QUANTS]
    picks = build_picks(P, legs)
    mtx, days, off, bench = P["mtx"], P["days"], P["off"], P["bench"]
    n = ASHARE_ORDER_TOP_N

    rows, ex = [], {}
    for k in picks:
        port, _hold = D.replay_picks(days, mtx, picks[k], ASHARE_PORT_HOLD,
                                     ASHARE_PORT_COST_ONE_WAY, off)
        port = port.iloc[off:]
        dd = D.dd_of(D.nav_of(port))
        p = port.dropna()
        memb = [set(v) for v in picks[k].values()]
        rows.append({"腿": k,
                     "年化净收益": float(p.mean() * TRADING_DAYS),
                     "超额(vs 域等权)": float((p - bench.reindex(p.index).fillna(0)).mean()
                                             * TRADING_DAYS),
                     "波动": float(p.std() * np.sqrt(TRADING_DAYS)),
                     "最深回撤": float(dd.min()),
                     "单程换手": float(np.mean([1.0] + [1.0 - len(memb[i - 1] & memb[i]) / n
                                                        for i in range(1, len(memb))])),
                     "与B不同的篮子场次": int(sum(1 for x in picks[k]
                                                   if set(picks[k][x]) != set(picks["B"][x])))})
        ex[k] = year_excess(port, bench)
    cmp = pd.DataFrame(rows).set_index("腿")
    yrx = pd.DataFrame(ex).fillna(0.0)
    for q in QUANTS:
        yrx[f"q{q:g}−B"] = yrx[f"q{q:g}"] - yrx["B"]
    yrx.loc["逐年平均"] = yrx.mean()

    anchor = float(cmp.loc["A", "最深回撤"])
    print(f"\n[自检·锚点腿 A] 最深回撤 {anchor:.6f}｜归档 {D.ANCHOR_REF_DD:.6f}")
    if abs(anchor - D.ANCHOR_REF_DD) > D.ANCHOR_TOL_DD:
        raise SystemExit("⇒ 锚点腿没复现归档 ⇒ 本脚本的回放与 ㉗ 不同形，五档读数一律作废")
    print("⇒ 通过（与 P0-1 同一份回放实现）")

    cmp.to_csv(os.path.join(OUT, "depth_compare.csv"), index_label="腿")
    yrx.to_csv(os.path.join(OUT, "depth_yearly.csv"), index_label="年")
    with pd.option_context("display.width", 220):
        print("\n===== 5 席下单层：独立闸刀口逐档（差一律减「同一张配额名单·无闸」）=====")
        print(cmp.to_string(float_format=lambda v: f"{v:+.4f}"))
        print("\n===== 逐年超额（含各档 −B）=====")
        print(yrx.to_string(float_format=lambda v: f"{v:+.4f}"))
    print(f"\n[输出] {OUT}/depth_compare.csv｜depth_yearly.csv　总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
