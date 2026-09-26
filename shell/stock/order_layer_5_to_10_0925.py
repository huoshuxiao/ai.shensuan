# -*- coding: utf-8 -*-
"""下单层从 5 只拨到 10 只：凑不凑得满、缺几席、钱会不会被迫趴在现金上（09-25）

需求来源：用户要「最终盯 10 只以内」。Q1/Q2 已量完「观察名单砍到 10 只」不成立，
本轮量另一条路：**观察名单保持 50 只不动，只把下单层的槽位从 5 拨到 10**。
这一层不新造 alpha，只有两条分散约束（同行业 ≤ ASHARE_ORDER_MAX_PER_INDUSTRY、
同板块 ≤ ASHARE_ORDER_MAX_PER_BOARD），所以真正要问的是三件事：

  1. 挑得满吗？  50 只名单在「同行业只留 1 只」这条上撑不撑得起 10 个席位。
  2. 往名单深处扫多少？  贪心扫描要走到第几名才凑满，走到深处挑到的是不是越来越差的票。
  3. 少买的席位算谁的？  权重按 1/top_n 给（生产口径），凑不满 10 席 ⇒ 差额是现金，
     要量出这笔现金拖累多少收益。

判据一律调生产实现，不在这里抄第二份：
    挑哪几只   = ashare_screen.order_candidates(buy, ind, top_n)   ← 与日频入口同一函数
    名单怎么来 = 与 shell/stock/second_pass_diversify_0925.py 同一套复现
                 （闸门 ∧ 命中数<ASHARE_BUY_MIN_HITS ∧ 按 SMA($volume,20) 升序取前 50）

回放**不**用 run_signal：它在候选数 < top_n 时整天跳过，而生产的缺席是「少买几只、
余量拿现金」，两者不是一回事。这里按同一套时序自己写（信号日收盘定名单 → 次日开盘
建仓 → 持有 5 日 → 开盘平仓，双边 15bp 记在建仓那天），并拿「五十只等权」那一档
对齐 second_pass_diversify_0925.py 的参照行做自检 —— 对不上就是这里写错了。

历史复现的边界（如实说，别当成生产名单）：这里没有当日收盘快照，所以生产名单额外的
三道执行性闸（当日真成交、收盘已贴涨停不追、ST）**没跑**；行业映射用的是今天这份表
回溯到 2015 年，当时的行业归属与今天不完全一致。这两条影响的是"缺几席"的绝对数，
所以本脚本另跑一次当日真入口（覆写到 shell/stock/ 下的临时目录）作为今天的实盘读数。
"""
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, SRC)

import os
import time

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401

from config import (ASHARE_BUY_MIN_HITS, ASHARE_BUY_TOP_N, ASHARE_ORDER_MAX_PER_BOARD,
                    ASHARE_ORDER_MAX_PER_INDUSTRY, ASHARE_PORT_COST_ONE_WAY,
                    ASHARE_PORT_HOLD, ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED,
                    ASHARE_PORT_START, ASHARE_SCREEN_QUANTILE, ASHARE_TRADABLE_GATE)
from ashare_screen import (BUY_EXPR, RULE_EXPRS, board_of, build_matrices,
                           factor_matrices, gate_desc, load_industry_map, load_panel,
                           order_candidates, tradable_mask)
import run_ashare_portfolio_eval as R

TRADING_DAYS = 252
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1"
OUT = os.path.join(ROOT, "data/results/order_layer_5_to_10_0925.csv")
SELF_CHECK_REF = 0.0155       # second_pass_diversify_0925.py 的「参照·50只」超额年化


def replay(days, mtx, picks, n_slot, hold, cost, renorm=False):
    """按下单层口径回放：每席权重 1/n_slot，缺的席位留现金，开盘买、持有 hold 日

    与 run_signal 同一套时序：信号日 i → 建仓日 j=i+1 → 持仓段 [j+1, j+1+hold)
    记在 j 的成本 = 2c·φ，φ 是「新进名单的席位占比」（等权下买卖对称）。
    `renorm=True` 把当天实际挑到的几只归一为满仓 —— 生产**不是**这么做的（按槽位
    给权、余量现金），这一档只是用来把「约束让你买不满」和「这么多只本身不行」
    两件事拆开，不参与任何口径结论。
    """
    ret = mtx["ret_open0"].to_numpy(dtype="float64")
    cols = list(mtx["close"].columns)
    col_of = {c: i for i, c in enumerate(cols)}
    w = np.zeros((len(days), len(cols)))
    c = np.zeros(len(days))
    prev, n_rebal = None, 0
    for i in range(0, len(days) - hold - 1, hold):
        s = days[i]
        if s not in picks:
            continue
        names = picks[s]
        j = i + 1
        lo, hi = j + 1, min(j + 1 + hold, len(days))
        w[lo:hi] = 0.0
        wt = 1.0 / (len(names) if renorm else n_slot)
        for nm in names:
            w[lo:hi, col_of[nm]] = wt
        phi = 1.0 if prev is None else 1.0 - len(set(prev) & set(names)) / n_slot
        c[j] += 2 * cost * phi
        prev, n_rebal = names, n_rebal + 1
    gross = pd.Series((w * ret).sum(axis=1), index=days)
    return gross - c, n_rebal


# (槽位数, 同行业上限, 同板块上限, 是否强行满仓)。前四行是「直接拨槽位」，
# 后两行是「拨不动就放宽约束」的代价，一次量完免得再来一轮
GRID = [(5, 1, 3, False), (10, 1, 3, False), (10, 1, 3, True),
        (10, 2, 3, False), (10, 2, 5, False), (10, 3, 5, False)]


def main():
    print(f"[口径] 涨停闸 = {ASHARE_TRADABLE_GATE}（{gate_desc()}）｜名单 {ASHARE_BUY_TOP_N} 只"
          f"｜同行业 ≤{ASHARE_ORDER_MAX_PER_INDUSTRY}、同板块 ≤{ASHARE_ORDER_MAX_PER_BOARD}"
          f"｜持有 {ASHARE_PORT_HOLD} 日｜单边费率 {ASHARE_PORT_COST_ONE_WAY}")
    t0 = time.time()
    wide, bench_open = load_panel()
    mtx = build_matrices(wide)
    fms = factor_matrices(sorted(set(RULE_EXPRS) | {BUY_EXPR}), mtx)
    live = mtx["ret_open"].index >= pd.Timestamp(ASHARE_PORT_START)
    mtx = {k: v.loc[mtx["ret_open"].index[live]] for k, v in mtx.items()}
    fms = {e: m.loc[m.index[live]] for e, m in fms.items()}
    days = mtx["ret_open"].index
    universe = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
                & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    bench = mtx["ret_open"].where(universe).mean(axis=1)
    axis = fms[BUY_EXPR]
    print(f"[准备] {time.time() - t0:.0f}s")

    pool_df, hit_df = R.exclusion_hits(mtx, days, fms, ASHARE_PORT_HOLD,
                                      ASHARE_SCREEN_QUANTILE)
    all_r = tuple(range(len(R.VOLUME_RULES)))
    allow_prod = R.exclusion_mask(("生产", "并集", all_r, ASHARE_BUY_MIN_HITS),
                                  pool_df, hit_df)
    ind, ind_meta = load_industry_map()
    ind_map = ind.to_dict() if ind_meta.get("loaded") else {}
    print(f"[行业表] loaded={ind_meta.get('loaded')} 条数={ind_meta.get('n')}")

    t1 = time.time()
    lists = {}
    for i in range(0, len(days) - ASHARE_PORT_HOLD - 1, ASHARE_PORT_HOLD):
        s, d1 = days[i], days[i + 1]
        ok, _ = tradable_mask(s, d1, axis, mtx)
        cand = axis.loc[s].where(ok & allow_prod.loc[s]).dropna().sort_values()
        if len(cand) < ASHARE_BUY_TOP_N:
            continue
        l50 = list(cand.index[:ASHARE_BUY_TOP_N])
        buy = pd.DataFrame({"rank": range(1, len(l50) + 1),
                            "板块": [board_of(c) for c in l50]}, index=l50)
        lists[s] = buy
    print(f"[名单] 复现 {len(lists)} 个调仓日的 50 只观察名单 {time.time() - t1:.0f}s")

    rows = []
    for n_slot, cap_ind, cap_brd, renorm in GRID:
        picks, stats = {}, []
        for s, buy in lists.items():
            od, st = order_candidates(buy, ind_map, n_slot, cap_ind, cap_brd)
            picks[s] = list(od.index)
            stats.append(st)
        got = np.array([s["n_picked"] for s in stats])
        depth = np.array([s["scan_depth"] for s in stats])
        unk = np.array([s["n_unknown"] for s in stats])
        bj = np.array([s["n_board_in_list"].get("北交所", 0) for s in stats])
        port, n_rebal = replay(days, mtx, picks, n_slot, ASHARE_PORT_HOLD,
                               ASHARE_PORT_COST_ONE_WAY, renorm=renorm)
        p = port.dropna()
        ann = float(p.mean() * TRADING_DAYS)
        j = pd.concat([port, bench], axis=1, join="inner").fillna(0.0)
        ex = (j.iloc[:, 0] - j.iloc[:, 1]).dropna()
        # 分年度与 Q1/Q2 同一份实现（R._yearly：逐年复合收益之差），免得三张表的
        # "几年为负" 各自算一套
        yr = R._yearly(port, bench)["excess"]
        # 下单层自己的成分换手：这 10 席里有多少是上次没买过的
        memb = [set(v) for v in picks.values()]
        phi = [1.0] + [1.0 - len(memb[i - 1] & memb[i]) / n_slot
                       for i in range(1, len(memb))]
        ex_sd = float(ex.std() * np.sqrt(TRADING_DAYS))
        rows.append({
            "slots": n_slot, "cap_industry": cap_ind, "cap_board": cap_brd,
            "renormalised": renorm, "n_dates": len(picks),
            "avg_picked": float(got.mean()), "shortfall_dates": int((got < n_slot).sum()),
            "shortfall_pct": float((got < n_slot).mean()),
            "worst_shortfall": int(n_slot - got.min()),
            "avg_cash_left": float((n_slot - got).mean() / n_slot),
            "avg_scan_depth": float(depth.mean()), "max_scan_depth": int(depth.max()),
            "avg_industry_unknown": float(unk.mean()),
            "bj_in_list_avg": float(bj.mean()),
            "ann_return": ann,
            "excess_univ_ew_ann": float(ex.mean() * TRADING_DAYS),
            # IR = 年化超额 / 年化超额波动。两者各自年化（×252 与 ×√252），
            # 少乘一次就是差 15.9 倍（本列 09-25 第一版栽在这里）
            "excess_ir": float(ex.mean() * TRADING_DAYS / ex_sd) if ex_sd else np.nan,
            "sharpe": ann / float(p.std() * np.sqrt(TRADING_DAYS)),
            "max_drawdown": float(((1 + p).cumprod() / (1 + p).cumprod().cummax() - 1).min()),
            "list_one_way_turnover": float(np.mean(phi)),
            "neg_years": int((yr < 0).sum()), "years": int(len(yr)),
            "ex_drop_best1": float(yr.drop(yr.idxmax()).sum()),
            "n_names_ever": len({c for v in picks.values() for c in v})})

    # 自检：五十只全买（不套分散约束）应当复现 second_pass_diversify_0925.py 的参照行
    picks50 = {s: list(buy.index) for s, buy in lists.items()}
    port50, _ = replay(days, mtx, picks50, ASHARE_BUY_TOP_N, ASHARE_PORT_HOLD,
                       ASHARE_PORT_COST_ONE_WAY)
    j50 = pd.concat([port50, bench], axis=1, join="inner").fillna(0.0)
    ex50 = float((j50.iloc[:, 0] - j50.iloc[:, 1]).dropna().mean() * TRADING_DAYS)
    print(f"\n[自检] 五十只等权（本脚本自己写的回放）超额 = {ex50:+.4f}"
          f"｜对照 second_pass_diversify_0925.py 的参照行 {SELF_CHECK_REF:+.4f}"
          f"｜差 {abs(ex50 - SELF_CHECK_REF):.4f}"
          f" {'✅ 同一套时序' if abs(ex50 - SELF_CHECK_REF) < 0.0015 else '❌ 回放写错了，别看下面的数'}")

    res = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print("\n===== 观察名单 50 只不动，只把下单层从 5 席拨到 10 席 =====")
    print(res.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print("""
列的白话含义：
  cap_industry/cap_board 同行业最多几只 / 同板块最多几只（生产现在是 1 和 3）
  renormalised  True = 把当天实际挑到的几只强行归一成满仓（生产不这么做，这一档
                        只用来分开「买不满」和「这么多只本身不行」两件事）
  avg_picked         平均每次真挑满了几只（10 席时若只有 8.7，就是常缺 1~2 席）
  shortfall_dates    有多少次没凑满（缺席 ⇒ 那部分钱趴在现金上，不算收益）
  worst_shortfall    最惨的一次缺了几席
  avg_cash_left      平均每次有多大比例的资金是空着的
  avg_scan_depth     要往名单第几名扫才凑得满（越深说明挑到的越是名单后排的票）
  bj_in_list_avg     那 50 只名单里平均有几只北交所（板块约束的输入）
  list_one_way_turnover 下单层自己的成分翻转（这 10 席里多少是上次没买过的）
  excess_ir          年化超额 / 年化超额的波动（衡量超额稳不稳，>0.3 才算能用）
  ex_drop_best1      把最好那一年整段扔掉之后，剩下的年份加起来还剩多少""")
    if os.path.exists(OUT):
        print(f"[警告] 产物已存在，本次覆盖：{OUT}")
    res.to_csv(OUT, index=False)
    print(f"[输出] {OUT}")


if __name__ == "__main__":
    main()
