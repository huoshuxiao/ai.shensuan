# -*- coding: utf-8 -*-
"""M3 补测：满仓口径重跑 + 名单板块构成随时间的分布（09-25 b 轮）

用户两点要求，一次装载里跑完，保证两张表吃的是**同一批 570 个观察名单**（口径分叉
正是上一轮被指出来的问题）：

  M3-1  「等权买入、不留现金」再验证
        上一轮 6 档里只有 1 档是满仓，其余档位都带着「买不满 ⇒ 钱趴现金」的拖累，
        于是「约束让你买不满」和「这些票本身不行」在表里混在一起。本轮把
        同一批档位各跑两遍（按槽位给权 / 强行归一满仓），成对读。
        注：M1（全市场取前 N）与 M2（50 只里二次筛 10 只）那两张表本来每档都恰好
        挑满 N 只、没有缺席席位，所以补满仓只影响本张下单层表。

  M3-2  名单的板块构成随时间怎么变（决定「放宽板块上限」值不值得做）
        今天那天是 主板 48 / 创业 2，于是板块 ≤3 时理论上限只有 3+2=5 席。要判这是
        偶发还是常态，得看 570 天的分布。分三段量：
          (a) 逐年各板块平均几只          —— 48/2 是常态还是今天特有
          (b) 按名单名次分段（1-10/11-25/26-50）—— 「创业/科创是被这根轴排到后排的」
              这条判断成立与否，看的是名单**内部**的顺序，不是总量
          (c) 过闸候选池（取前 50 之前）的板块占比 —— 分开「池子里本来就没有创业」
              与「池子里有、但轴不选它」。只有 (c) 显示池子占比明显高于名单占比，
              才能说集中是排序轴造成的

判据一律调生产实现（`order_candidates`），不在此抄第二份。回放时序与上一轮同一个
`replay()`：信号日收盘定名单 → 次日开盘建仓 → 持有 5 日 → 开盘平仓，双边 15bp 记在
建仓日；每席 1/n_slot（满仓档为 1/实际只数）。

边界照旧：历史复现没有当日收盘快照，故生产名单额外的三道执行性闸（当日真成交、
收盘已贴涨停不追、ST）未跑；行业映射用今天这份表回溯 2015 年。
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
from ashare_screen import (BUY_EXPR, INDUSTRY_UNKNOWN, RULE_EXPRS, board_of,
                           build_matrices, factor_matrices, gate_desc,
                           load_industry_map, load_panel, order_candidates,
                           tradable_mask)
import run_ashare_portfolio_eval as R

TRADING_DAYS = 252
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1"
DIR = os.path.join(ROOT, "data/results")
OUT_FULL = os.path.join(DIR, "order_layer_fullinvest_0925b.csv")
OUT_MIX_YEAR = os.path.join(DIR, "list_board_mix_yearly_0925b.csv")
OUT_MIX_BAND = os.path.join(DIR, "list_board_mix_rankband_0925b.csv")
OUT_CEIL = os.path.join(DIR, "list_board_ceiling_0925b.csv")
OUT_MIX_DAY = os.path.join(DIR, "list_board_mix_daily_0925b.csv")
SELF_CHECK_REF = 0.0155        # 上一轮「五十只等权」超额年化（本脚本应复现它）
BOARDS = ("主板", "创业板", "科创板", "北交所")
# (槽位, 同行业上限, 同板块上限)。50 那一行是「不买 5 也不买 10，直接把 50 只全买」
# 的基准，它天然没有缺席席位，两种权重下应当完全相同 —— 这本身就是一个自检
COMBOS = [(5, ASHARE_ORDER_MAX_PER_INDUSTRY, ASHARE_ORDER_MAX_PER_BOARD),
          (10, 1, 3), (10, 2, 3), (10, 2, 5), (10, 3, 5),
          (ASHARE_BUY_TOP_N, 99, 99)]
NO_CAP = 99                    # 远大于名单长度 ⇒ 该条约束实质失效，用来单独量另一条


def replay(days, mtx, picks, n_slot, hold, cost, renorm=False):
    """按下单层口径回放。`renorm=True` = 用户要的「不留现金」：每席 1/实际只数"""
    ret = mtx["ret_open0"].to_numpy(dtype="float64")
    col_of = {c: i for i, c in enumerate(mtx["close"].columns)}
    w = np.zeros((len(days), mtx["close"].shape[1]))
    c = np.zeros(len(days))
    prev = None
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
        prev = names
    gross = pd.Series((w * ret).sum(axis=1), index=days)
    return gross - c


def bench_row(port, bench):
    j = pd.concat([port, bench], axis=1, join="inner").fillna(0.0)
    ex = (j.iloc[:, 0] - j.iloc[:, 1]).dropna()
    sd = float(ex.std() * np.sqrt(TRADING_DAYS))
    return {"ann_return": float(port.dropna().mean() * TRADING_DAYS),
            "excess_univ_ew_ann": float(ex.mean() * TRADING_DAYS),
            "excess_ir": float(ex.mean() * TRADING_DAYS / sd) if sd else np.nan,
            "max_drawdown": float(((1 + port.dropna()).cumprod()
                                   / (1 + port.dropna()).cumprod().cummax() - 1).min())}


def main():
    print(f"[口径] 涨停闸 = {ASHARE_TRADABLE_GATE}（{gate_desc()}）｜名单 {ASHARE_BUY_TOP_N} 只"
          f"｜生产同行业 ≤{ASHARE_ORDER_MAX_PER_INDUSTRY}、同板块 ≤{ASHARE_ORDER_MAX_PER_BOARD}"
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

    # ---------- 复现 570 个调仓日的名单；顺手记下名次与池子板块构成（M3-2 的输入）----------
    t1 = time.time()
    lists, mix = {}, []
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
        pool_boards = pd.Series([board_of(c) for c in cand.index])
        row = {"date": s, "pool_n": int(len(cand))}
        for b in BOARDS:
            row[f"池_{b}"] = int((pool_boards == b).sum())
            row[f"池占比_{b}"] = float((pool_boards == b).mean())
            row[f"名单_{b}"] = int((buy["板块"] == b).sum())
        mix.append(row)
    mix = pd.DataFrame(mix).set_index("date")
    print(f"[名单] 复现 {len(lists)} 个调仓日的 {ASHARE_BUY_TOP_N} 只观察名单"
          f" {time.time() - t1:.0f}s")

    # ---------- M3-2c：名单内部名次分段（前 10 / 11-25 / 26-50 各自的板块构成）----------
    band_rows = []
    edges = [("第 1-10 名", 1, 10), ("第 11-25 名", 11, 25), ("第 26-50 名", 26, 50)]
    for label, a, b in edges:
        cnt = {x: 0 for x in BOARDS}
        n_dates = 0
        for s, buy in lists.items():
            seg = buy[(buy["rank"] >= a) & (buy["rank"] <= b)]["板块"]
            if not len(seg):
                continue
            n_dates += 1
            for x in BOARDS:
                cnt[x] += int((seg == x).sum())
        tot = sum(cnt.values())
        band_rows.append({"band": label, "n_dates": n_dates,
                          **{f"n_{x}": cnt[x] / n_dates for x in BOARDS},
                          **{f"占比_{x}": cnt[x] / tot for x in BOARDS}})
    band = pd.DataFrame(band_rows)

    # ---------- M3-2b：逐年板块构成 + 池子占比（判「集中是轴的还是池子的」）----------
    mix["year"] = mix.index.year
    g = mix.groupby("year")
    yearly = pd.DataFrame({
        "名单_主板": g["名单_主板"].mean(), "名单_创业板": g["名单_创业板"].mean(),
        "名单_科创板": g["名单_科创板"].mean(), "名单_北交所": g["名单_北交所"].mean(),
        "池_创业板只数": g["池_创业板"].mean(), "池_科创板只数": g["池_科创板"].mean(),
        "池_北交所只数": g["池_北交所"].mean(), "池_总只数": g["pool_n"].mean(),
        "池占比_主板": g["池占比_主板"].mean(), "池占比_创业板": g["池占比_创业板"].mean(),
        "池占比_科创板": g["池占比_科创板"].mean(), "池占比_北交所": g["池占比_北交所"].mean(),
    }).reset_index()
    # 「轴选了池子里几成的创业」：>1 是轴偏爱该段，<1 是轴回避。分开 (b) 与 (c) 就看这列
    yearly["名单占池_创业"] = yearly["名单_创业板"] / (yearly["池_创业板只数"] / yearly["池_总只数"] * ASHARE_BUY_TOP_N)
    yearly["名单占池_科创"] = yearly["名单_科创板"] / (yearly["池_科创板只数"] / yearly["池_总只数"] * ASHARE_BUY_TOP_N)
    yearly["名单占池_北交"] = yearly["名单_北交所"] / (yearly["池_北交所只数"] / yearly["池_总只数"] * ASHARE_BUY_TOP_N)

    # ---------- M3-2 天花板：把两条约束分别摘掉，看 10 席是被谁卡住的 ----------
    ceil_rows = []
    for s, buy in lists.items():
        _o, st_board = order_candidates(buy, ind_map, NO_CAP, NO_CAP, 3)
        _o, st_b5 = order_candidates(buy, ind_map, NO_CAP, NO_CAP, 5)
        _o, st_ind = order_candidates(buy, ind_map, NO_CAP, 1, NO_CAP)
        _o, st_both = order_candidates(buy, ind_map, NO_CAP,
                                       ASHARE_ORDER_MAX_PER_INDUSTRY,
                                       ASHARE_ORDER_MAX_PER_BOARD)
        ceil_rows.append({"date": s,
                          "只板块3上限": st_board["n_picked"],
                          "只板块5上限": st_b5["n_picked"],
                          "只行业1上限": st_ind["n_picked"],
                          "生产两条一起": st_both["n_picked"]})
    ceil = pd.DataFrame(ceil_rows).set_index("date")
    ceil_sum = pd.DataFrame({
        "平均可下单只数": ceil.mean(),
        "最少": ceil.min(), "中位": ceil.median(), "最多": ceil.max(),
        "能凑满10只的次数占比": (ceil >= 10).mean(),
        "能凑满5只的次数占比": (ceil >= 5).mean()}).rename_axis("constraint")

    # ---------- M3-1：每档跑两遍（按槽位给权 / 满仓不留现金）----------
    rows = []
    for n_slot, cap_ind, cap_brd in COMBOS:
        picks, stats = {}, []
        for s, buy in lists.items():
            od, st = order_candidates(buy, ind_map, n_slot, cap_ind, cap_brd)
            picks[s] = list(od.index)
            stats.append(st)
        got = np.array([s["n_picked"] for s in stats])
        depth = np.array([s["scan_depth"] for s in stats])
        memb = [set(v) for v in picks.values()]
        for renorm in (False, True):
            port = replay(days, mtx, picks, n_slot, ASHARE_PORT_HOLD,
                          ASHARE_PORT_COST_ONE_WAY, renorm=renorm)
            r = {"slots": n_slot, "cap_industry": cap_ind, "cap_board": cap_brd,
                 "cash_mode": "满仓(不留现金)" if renorm else "按槽位(余量现金)",
                 "avg_picked": float(got.mean()),
                 "shortfall_pct": float((got < n_slot).mean()),
                 "avg_cash_left": float((n_slot - got).mean() / n_slot),
                 "avg_scan_depth": float(depth.mean()),
                 "list_one_way_turnover": float(np.mean(
                     [1.0] + [1.0 - len(memb[i - 1] & memb[i]) / n_slot
                              for i in range(1, len(memb))])),
                 **bench_row(port, bench)}
            yr = R._yearly(port, bench)["excess"]
            r.update({"neg_years": int((yr < 0).sum()), "years": int(len(yr)),
                      "ex_drop_best1": float(yr.drop(yr.idxmax()).sum())})
            rows.append(r)

    # ---------- 自检：五十只全买应当在两种权重下逐字相同，且等于上一轮的 0.0155 ----------
    full = pd.DataFrame(rows)
    ref = full[(full["slots"] == ASHARE_BUY_TOP_N)]
    d = float(abs(ref["excess_univ_ew_ann"].max() - ref["excess_univ_ew_ann"].min()))
    print(f"\n[自检1] 五十只全买：两档权重差 {d:.6f}"
          f" {'✅ 无缺席席位，权重模式本就无关' if d < 1e-9 else '❌ 满仓实现有问题'}")
    v50 = float(ref["excess_univ_ew_ann"].iloc[0])
    print(f"[自检2] 五十只全买超额 = {v50:+.4f}｜对照上一轮参照行 {SELF_CHECK_REF:+.4f}"
          f"｜差 {abs(v50 - SELF_CHECK_REF):.4f}"
          f" {'✅ 同一套时序' if abs(v50 - SELF_CHECK_REF) < 0.0015 else '❌ 回放写错了，别看下面的数'}")

    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 40)
    print("\n===== M3-1 下单层各档 × 两种资金口径（等权买入不留现金 / 按槽位余量现金）=====")
    print(full.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print("\n===== M3-2a 观察名单的板块构成：逐年平均（名单 50 只里的只数）=====")
    print(yearly.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print("\n===== M3-2b 名单内部按名次分段的板块构成 =====")
    print(band.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print("\n===== M3-2c 拆掉约束后的「最多能下几只」天花板（570 天分布）=====")
    print(ceil_sum.to_string(float_format=lambda v: f"{v:.3f}"))
    print("""
列的白话含义：
  cash_mode           按槽位 = 每只固定 1/槽位数，买不满就把差额留在现金上（生产口径）
                      满仓 = 把当天真买到的几只强行放大到 100%（用户这轮要的口径）
  avg_picked          平均每次真挑到几只
  shortfall_pct       没凑满槽位的次数占比
  avg_cash_left       「槽位数 − 实际只数」换算的资金缺口，与 cash_mode 无关：满仓档它
                      读作「若按槽位给权会空多少」，那一档实际空着的钱是 0（差额摊进票里）
  avg_scan_depth      要往名单第几名扫才停手（50 = 扫到底了还没凑满）
  list_one_way_turnover 每次调仓有多少席位换人（单边）
  excess_univ_ew_ann  相对「过闸池等权」每年多赚/少赚多少（这里等权池 = 几千只冷门票
                      打包一起买，是打分用的地板，不是一个真能买的组合）
  excess_ir           年化超额 ÷ 超额的年化波动，衡量这份超额稳不稳，>0.3 算能用
  ex_drop_best1       把最好那一年整段扔掉后，剩下 11 年的超额逐年相加还剩多少
  名单_主板/创业/科创/北交  当年那 50 只里各板块平均几只
  池_总只数           过完三道闸、再剔掉「命中≥3 条量能构造」之后，当天能挑的总只数
  池占比_x            x 段在这个候选池里的占比（轴如果与板块无关，名单里就该出现 50×这个数）
  名单占池_x          名单实际只数 ÷ 上面那个期望值。<1 = 这根轴在回避该段，>1 = 偏爱
  只板块3上限         只留「同板块≤3」这一条时最多能下几只（行业约束摘掉）
  只行业1上限         只留「同行业≤1」这一条时最多能下几只（板块约束摘掉）
  生产两条一起        两条都留 = 生产真实天花板""")
    for p, df, keep_index in ((OUT_FULL, full, False), (OUT_MIX_YEAR, yearly, False),
                              (OUT_MIX_BAND, band, False), (OUT_CEIL, ceil_sum, True),
                              # 逐日那张是本轮跑完才发现要看的：北交所在名单里
                              # 2025 年 15.4 只 → 2026 年 0 只，年度均值把这个摆动抹平了
                              (OUT_MIX_DAY, mix, True)):
        if os.path.exists(p):
            print(f"[警告] 产物已存在，本次覆盖：{p}")
        df.to_csv(p, index=keep_index)
    print(f"[输出] {OUT_FULL}\n[输出] {OUT_MIX_YEAR}\n[输出] {OUT_MIX_BAND}"
          f"\n[输出] {OUT_CEIL}\n[输出] {OUT_MIX_DAY}")


if __name__ == "__main__":
    main()
