# -*- coding: utf-8 -*-
"""Q2 实测（09-25）：50 只名单之上再做一次「只留 10 只」的二次筛选，值不值

背景：Q1 已经量完「直接把名单砍短」的代价 —— 按安静度升序取前 10 只，11 年平均
比整个池子平均只多赚 0.19%（前 50 只 = +1.39%），而且那点账面好处全来自 2023、
2025 两年。本脚本回答另一半问题：**如果 10 只不是"砍"出来的而是"配"出来的
（互相不像、波动可控、买得进去），能不能把 +1.39% 守住？**

判据全部从生产搬，一处都不新造 alpha：
    候选池 = tradable_mask(容量 / 次新 / 涨停) ∧ 名单入口闸 n_hit < ASHARE_BUY_MIN_HITS
    排队轴 = BUY_EXPR = ts_mean(volume,20)（安静度），升序，安静者在前
    50 只名单 = 池内排队取前 50              ← 生产现状，本脚本的参照行

回放不自己写：把每个变体选出的 10 只做成一张「当天可用」布尔矩阵，喂回
run_ashare_portfolio_eval.run_signal 的 allow 参数。因为该函数是在**已过闸门的
候选里按轴取前 top_n**，allow 里恰好只有这 10 只 ⇒ 篮子必然就是这 10 只。
于是扣费口径（开盘买、持有 5 日、双边 15bp）、调仓网格、两道基准全部与归档表同源。

七个筛法（每个都出恰好 10 只）：
    V0 直接前10        l50[:10]                        = Q1 已知最差的那把砍法
    V1 相关<0.7        按序扫，与已选任一只近 60 日收益 |ρ| < 0.7 才收，收不满回填
    V2 相关<0.5        同上，阈值收紧（更分散，代价是回填更多）
    V3 低波动10        l50 里近 60 日日收益标准差最小的 10 只
    V4 高流动性10      l50 里 20 日均成交额最大的 10 只
    V5 相关<0.7+低波动 先在 l50 里砍掉波动高于中位数的那一半，再做 V1
    V6 行业去重        同一行业只留 1 只（按安静度取最先出现的）；行业表缺席则整列跳过
    参照·50只          生产现状，回归自检行：这一行应当 ≈ 归档 top50 = +1.39%

「收不满 10 只就回填」是刻意的：run_signal 在候选数 < top_n 时**整天跳过**，
不同变体的调仓日集合就会不一样，数字便不可比。故一律补满，只把补了多少天如实记下。

相关系数只用信号日 s 及之前的 60 个交易日，不看未来；ρ 走成对完整观测
（停牌缺口不进分子分母），不用 0 填充 —— 否则停过的票会被算成「波动小、跟谁都不像」。

本脚本不改任何生产配置，产物只落到 data/results/second_pass_diversify_0925.csv。
"""
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, SRC)

import os
import time

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401  (裸模块名导入的 sys.path 引导)

from config import (ASHARE_BUY_MIN_HITS, ASHARE_BUY_TOP_N, ASHARE_PORT_COST_ONE_WAY,
                    ASHARE_PORT_HOLD, ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED,
                    ASHARE_PORT_OUT, ASHARE_PORT_START, ASHARE_SCREEN_QUANTILE,
                    ASHARE_TRADABLE_GATE)
from ashare_screen import (BUY_EXPR, RULE_EXPRS, build_matrices, factor_matrices,
                           gate_desc, load_industry_map, load_panel, tradable_mask)
import run_ashare_portfolio_eval as R

TRADING_DAYS = 252
CORR_WIN = 60          # 近 60 个交易日（约一个季度）判两只票像不像
TARGET_N = 10          # 二次筛选后要留下的只数
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1"
OUT = os.path.join(ROOT, "data/results/second_pass_diversify_0925.csv")


def mean_abs_corr(corr, names):
    """名单内两两 |ρ| 的均值 = 这张名单有多「不像」。corr 可能缺列（长停牌/次新），先对齐"""
    k = [c for c in names if c in corr.index and c in corr.columns]
    if len(k) < 2:
        return np.nan
    sub = corr.loc[k, k].to_numpy(dtype="float64")
    iu = np.triu_indices(len(k), 1)
    v = np.abs(sub[iu])
    v = v[~np.isnan(v)]
    return float(v.mean()) if len(v) else np.nan


def greedy_corr(order, corr, thr, n):
    """按 order 顺序扫，与已选任一只 |ρ| >= thr 就跳过，收满 n 只

    返回 (名单, 回填只数)。近 60 日无足够收益序列的票（corr 里没它那一行）不设门槛，
    直接收 —— 它不是「分散」，是「不知道像不像」，但把这类票整批挡掉会让名单长期
    偏向老票，那是另一个判据，不在这次实测里混进来。
    """
    chosen = []
    for c in order:
        if len(chosen) >= n:
            break
        if c not in corr.index:
            chosen.append(c)
            continue
        have = [x for x in chosen if x in corr.columns]
        if have:
            rho = np.abs(corr.loc[c, have].to_numpy(dtype="float64"))
            rho = rho[~np.isnan(rho)]
            if len(rho) and float(rho.max()) >= thr:
                continue
        chosen.append(c)
    back = max(0, n - len(chosen))
    if back:
        chosen = chosen + [c for c in order if c not in set(chosen)][:back]
    return chosen[:n], back


def greedy_unique(order, ind, n):
    """每个行业只留 1 只，按 order 取最先出现的；行业未知的票不设限"""
    seen, chosen = set(), []
    for c in order:
        g = ind.get(c)
        if not g or str(g) in ("", "nan", "未知"):
            chosen.append(c)
            continue
        if g in seen:
            continue
        seen.add(g)
        chosen.append(c)
    back = max(0, n - len(chosen))
    if back:
        chosen = chosen + [c for c in order if c not in set(chosen)][:back]
    return chosen[:n], back


def main():
    print(f"[口径] 涨停闸 = {ASHARE_TRADABLE_GATE}（{gate_desc()}）｜排队轴 = {BUY_EXPR}"
          f"｜名单入口闸 n_hit < {ASHARE_BUY_MIN_HITS}｜持有 {ASHARE_PORT_HOLD} 日"
          f"｜单边费率 {ASHARE_PORT_COST_ONE_WAY}｜目标只数 {TARGET_N}")
    t0 = time.time()
    wide, bench_open = load_panel()
    mtx = build_matrices(wide)
    exprs = sorted(set(RULE_EXPRS) | {BUY_EXPR})
    fms = factor_matrices(exprs, mtx)
    print(f"[准备] 面板 + {len(exprs)} 条表达式 {time.time() - t0:.0f}s")

    live = mtx["ret_open"].index >= pd.Timestamp(ASHARE_PORT_START)
    mtx = {k: v.loc[mtx["ret_open"].index[live]] for k, v in mtx.items()}
    fms = {e: m.loc[m.index[live]] for e, m in fms.items()}
    days = mtx["ret_open"].index
    col_of = {c: i for i, c in enumerate(mtx["close"].columns)}
    universe = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
                & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    bench = {"univ_ew": mtx["ret_open"].where(universe).mean(axis=1)}
    if len(bench_open):
        bench["sh000300"] = bench_open.reindex(days).astype("float64") \
            .pct_change(fill_method=None)

    axis = fms[BUY_EXPR]
    grid = [i for i in range(0, len(days) - ASHARE_PORT_HOLD - 1, ASHARE_PORT_HOLD)]
    print(f"[网格] {len(grid)} 个调仓日 {days[grid[0]].date()} → {days[grid[-1]].date()}")

    # 生产的那四条剔除构造 + 名单入口强度，与归档表同一份实现
    pool_df, hit_df = R.exclusion_hits(mtx, days, fms, ASHARE_PORT_HOLD,
                                      ASHARE_SCREEN_QUANTILE)
    all_r = tuple(range(len(R.VOLUME_RULES)))
    allow_prod = R.exclusion_mask(("生产", "并集", all_r, ASHARE_BUY_MIN_HITS),
                                  pool_df, hit_df)

    ind, ind_meta = load_industry_map()
    print(f"[行业表] loaded={ind_meta.get('loaded')} 条数={ind_meta.get('n')}"
          f"{' ⇒ V6 行业去重整列跳过（不假装过滤过）' if not ind_meta.get('loaded') else ''}")

    variants = ["参照·50只", "V0 直接前10", "V1 相关<0.7", "V2 相关<0.5",
                "V3 低波动10", "V4 高流动性10", "V5 相关<0.7+低波动"]
    if ind_meta.get("loaded"):
        variants.append("V6 行业去重")
    sel = {v: {} for v in variants}
    rho = {v: [] for v in variants}
    backfill = {v: [] for v in variants}
    ret_hist = mtx["ret_open"]
    ind_map = ind.to_dict() if ind_meta.get("loaded") else {}
    t1 = time.time()
    n_skip = 0
    for i in grid:
        s, d1 = days[i], days[i + 1]
        ok, _ = tradable_mask(s, d1, axis, mtx)
        ok = ok & allow_prod.loc[s]
        cand = axis.loc[s].where(ok).dropna().sort_values()
        if len(cand) < ASHARE_BUY_TOP_N:
            n_skip += 1                      # 候选凑不满 50 只 ⇒ 生产当天也不出名单
            continue
        l50 = list(cand.index[:ASHARE_BUY_TOP_N])
        w = ret_hist.iloc[max(0, i - CORR_WIN + 1):i + 1][l50]
        corr = w.corr()
        vol60 = w.std()
        amt = mtx["amount20"].loc[s, l50]

        picked = {
            "参照·50只": (l50, 0),
            "V0 直接前10": (l50[:TARGET_N], 0),
            "V3 低波动10": (list(vol60.sort_values().index[:TARGET_N]), 0),
            "V4 高流动性10": (list(amt.sort_values(ascending=False).index[:TARGET_N]), 0),
        }
        for lab, thr in (("V1 相关<0.7", 0.7), ("V2 相关<0.5", 0.5)):
            picked[lab] = greedy_corr(l50, corr, thr, TARGET_N)
        quiet = [c for c in l50 if c in vol60.index and vol60[c] <= vol60.median()]
        picked["V5 相关<0.7+低波动"] = greedy_corr(quiet, corr, 0.7, TARGET_N)
        if "V6 行业去重" in variants:
            picked["V6 行业去重"] = greedy_unique(l50, ind_map, TARGET_N)

        for lab, (names, back) in picked.items():
            sel[lab][s] = names
            backfill[lab].append(back)
            rho[lab].append(mean_abs_corr(corr, names))
    print(f"[选股] {len(grid) - n_skip}/{len(grid)} 日复现成功（候选不足 50 只而跳过 {n_skip} 日）"
          f" {time.time() - t1:.0f}s")

    cols = mtx["close"].columns
    rows = []
    for lab in variants:
        chosen = sel[lab]
        n_target = ASHARE_BUY_TOP_N if lab == "参照·50只" else TARGET_N
        m = pd.DataFrame(False, index=pd.DatetimeIndex(sorted(chosen)), columns=cols)
        for d, k in chosen.items():
            m.loc[d, list(k)] = True
        port, st, _ = R.run_signal(axis, col_of, days, mtx, n_target,
                                   hold=ASHARE_PORT_HOLD, universe=universe,
                                   quintiles=0, allow=m)
        row = {"variant": lab, "gate": ASHARE_TRADABLE_GATE, "sort_axis": BUY_EXPR,
               "top_n": n_target, "n_dates": len(chosen),
               "avg_abs_corr": float(np.nanmean(rho[lab])),
               "backfill_dates": int(np.count_nonzero(backfill[lab])),
               "backfill_names": int(np.sum(backfill[lab])), **st}
        for bn, bs in bench.items():
            row.update(R._excess(port, bs, bn))
        ex = R._yearly(port, bench["univ_ew"])["excess"]
        row["years"] = int(len(ex))
        row["neg_years"] = int((ex < 0).sum())
        row["ex_2015_2020"] = float(ex[ex.index <= 2020].sum())
        row["ex_2021_2026"] = float(ex[ex.index >= 2021].sum())
        # 把贡献最大的那一年整段扔掉之后还剩多少：Q1 的教训是账面好处可能全住在一两年里
        row["ex_drop_best1"] = float(ex.drop(ex.idxmax()).sum())
        rows.append(row)

    res = pd.DataFrame(rows).sort_values("excess_univ_ew_ann", ascending=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    show = [c for c in ["variant", "top_n", "n_dates", "avg_abs_corr",
                        "backfill_dates", "ann_return", "excess_univ_ew_ann",
                        "sharpe", "max_drawdown", "one_way_turnover",
                        "avg_amount_20d", "neg_years", "ex_2015_2020",
                        "ex_2021_2026", "ex_drop_best1"] if c in res.columns]
    print("\n===== 50 只之上再筛到 10 只：七种筛法 vs 生产 50 只 =====")
    print(res[show].to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print("""
列的白话含义：
  ann_return        扣完手续费之后的年化收益（持有 5 天换一次手）
  excess_univ_ew_ann 比「当天所有能买的票的平均水平」多赚的年化 —— 本表主判据
  avg_abs_corr      名单里两两涨跌幅的平均相似度，越小代表这 10 只越不像同一件事
  backfill_dates    有多少天「按规则凑不满 10 只、只好往后补」
  neg_years         12 年里有几年跑输那个平均水平
  ex_2015_2020/2021_2026 前后两段各自的逐年超额之和（正负各算，不平均）
  ex_drop_best1    把表现最好的那一年整段扔掉之后，剩下的年份加起来还剩多少""")
    if os.path.exists(OUT):
        print(f"[警告] 产物已存在，本次覆盖：{OUT}")
    res.to_csv(OUT, index=False)
    print(f"[输出] {OUT}")
    print(f"[自检] 参照·50只 这一行应当贴近归档主表 top50"
          f"（{os.path.basename(ASHARE_PORT_OUT)} 的 +1.39%/年）；对不上就是本脚本有 bug")


if __name__ == "__main__":
    main()
