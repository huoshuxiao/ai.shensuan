# -*- coding: utf-8 -*-
"""M4（选项E）：把「分散」从下单层挪到观察层 —— 观察名单按板块配额生成，值不值

M3 留下的结论是这条的出发点：观察名单**本身就是一张单板块名单**（单一板块占名单比例
中位 94%、75.6% 的调仓日 ≥90%、最差 100%），所以下单层「同板块 ≤3」能给的席位天花板
≈ 3 × (名单里有几个板块) ⇒ 10 席在 0/570 天填得满。要真想要 10 个席位，只能在**名单生成
那一步**就把板块铺开，而不是在下单那一步放宽上限。

本脚本一次量四套配额方案（外加现状作对照），每套都走完整链路：

    候选 = tradable_mask(三道闸) ∧ allow_prod(量能并集命中 < ASHARE_BUY_MIN_HITS)
           按轴（安静度 = SMA($volume,20)）升序                ← 与生产同一份实现
    名单 = 按板块从各自最安静的往取配额只数（取不满时分「回填」「不回填」两版）
    下单 = ashare_screen.order_candidates(名单, 行业表, 槽位, 1, 3)  ← 判据一字未改
    回放 = 开盘买、持有 5 日、双边 15bp；**两种资金口径成对出**（1/槽位留现金、1/实际只数满仓）

(方案) × (5 席 / 10 席) × (留现金 / 满仓) 一张表读齐，重点看四列：
    单板块占比中位   —— 铺开没有（现状 0.94）
    凑满槽位天数占比 —— 10 席到底填不填得满（现状 0/570）
    excess_univ_ew   —— 铺开的代价或收益
    ex_drop_best1    —— 扔掉最好一年还剩多少（防"全凭某一年"）

刻意**不**改任何生产配置：`ASHARE_BUY_TOP_N`/`ASHARE_ORDER_TOP_N` 全靠本脚本传参覆写，
落盘只写自己那一张 `_m4_0925.csv`。
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
OUT = os.path.join(ROOT, "data/results/quota_watchlist_m4_0925.csv")
OUT_Y = os.path.join(ROOT, "data/results/quota_watchlist_m4_0925_yearly.csv")
SELF_CHECK_REF = 0.0155          # M3 的「五十只全买」超额年化（本脚本应复现）
HEAD = 200                       # 每天只保留最安静的前 200 只：配额最多 14，回填也用不到更深
BOARDS = ("主板", "创业板", "科创板", "北交所")
N_SLOT = (5, 10)               # 现状槽位数与用户想要的槽位数，一次跑齐好对照

# (标签, 配额方案)。None = 现状（不分板块，直接全局最安静前 50）
SCHEMES = [
    ("现状·全局前50", None),
    ("E-a 20/10/10/10 回填", {"主板": 20, "创业板": 10, "科创板": 10, "北交所": 10}),
    ("E-b 每段12·余2归主板 回填", {"主板": 14, "创业板": 12, "科创板": 12, "北交所": 12}),
    ("E-c 池内等比·每段≥3 回填", "equal"),
    ("E-d 20/10/10/10 不回填", {"主板": 20, "创业板": 10, "科创板": 10, "北交所": 10}),
]
FILL = {"E-d 20/10/10/10 不回填": False}


def quota_equal(counts, total):
    """池内等比 + 每段下限 3，凑成恰好 total（多退少补都动最大那段，保证确定性）"""
    q = {b: max(3, int(np.floor(total * counts.get(b, 0) / max(sum(counts.values()), 1))))
         for b in BOARDS}
    while sum(q.values()) > total:
        b = max(BOARDS, key=lambda x: (q[x], counts.get(x, 0)))
        if q[b] <= 3:
            break
        q[b] -= 1
    if sum(q.values()) < total:
        q[max(BOARDS, key=lambda x: counts.get(x, 0))] += total - sum(q.values())
    return q


def make_list(head_df, quota, counts, total, fill=True):
    """按配额从「已按安静度升序的前 HEAD 只」里生成观察名单（名次一律按轴，不按取用顺序）

    head_df: 列 [code, 板块]，已按轴升序。配额取不满时按全局顺序回填（fill=True），
    回填正是"名单仍可能单板块"的来源，所以另跑一版不回填的（E-d）看差在哪。
    """
    if quota is None:
        sel = head_df.head(total)
        return list(sel["code"]), {}
    q = quota_equal(counts, total) if quota == "equal" else quota
    chosen, left = set(), total
    order = []
    for b in BOARDS:
        n = min(q.get(b, 0), left)
        got = head_df[head_df["板块"] == b].head(n)
        order.extend(got.index.tolist())
        chosen |= set(got["code"])
        left -= len(got)
    if fill and left > 0:
        rest = head_df[~head_df["code"].isin(chosen)]
        order.extend(rest.head(left).index.tolist())
    # 名次按轴重排（order 里存的是 head_df 的行位置，天然就是名次序，但要按轴再排一次
    # 因为分板块取的时候跳过了别段）
    picked = head_df.loc[order].sort_index()
    return list(picked["code"]), q


def replay(days, mtx, picks, n_slot, hold, cost, renorm=False):
    """与 M3 同一套时序：信号日 i → 建仓日 j=i+1 → 持仓段 [j+1, j+1+hold)，成本记在 j"""
    ret = mtx["ret_open0"].to_numpy(dtype="float64")
    col_of = {c: i for i, c in enumerate(mtx["close"].columns)}
    w = np.zeros((len(days), mtx["close"].shape[1]))
    c = np.zeros(len(days))
    prev = None
    for i in range(0, len(days) - hold - 1, hold):
        s = days[i]
        if s not in picks:
            continue
        names, j = picks[s], i + 1
        lo, hi = j + 1, min(j + 1 + hold, len(days))
        w[lo:hi] = 0.0
        wt = 1.0 / (len(names) if renorm else n_slot)
        for nm in names:
            w[lo:hi, col_of[nm]] = wt
        phi = 1.0 if prev is None else 1.0 - len(set(prev) & set(names)) / n_slot
        c[j] += 2 * cost * phi
        prev = names
    return pd.Series((w * ret).sum(axis=1), index=days) - c


def main():
    print(f"[口径] 涨停闸 = {ASHARE_TRADABLE_GATE}（{gate_desc()}）｜名单 {ASHARE_BUY_TOP_N} 只"
          f"｜下单同行业 ≤{ASHARE_ORDER_MAX_PER_INDUSTRY}、同板块 ≤{ASHARE_ORDER_MAX_PER_BOARD}"
          f"｜持有 {ASHARE_PORT_HOLD} 日｜单边费率 {ASHARE_PORT_COST_ONE_WAY}")
    t0 = time.time()
    wide, _ = load_panel()
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

    # 每天的「最安静前 HEAD 只」只算一次，五套配额共用（省掉 5 次面板级排序）
    t1 = time.time()
    heads, pools = {}, {}
    for i in range(0, len(days) - ASHARE_PORT_HOLD - 1, ASHARE_PORT_HOLD):
        s, d1 = days[i], days[i + 1]
        ok, _ = tradable_mask(s, d1, axis, mtx)
        cand = axis.loc[s].where(ok & allow_prod.loc[s]).dropna().sort_values()
        if len(cand) < ASHARE_BUY_TOP_N:
            continue
        h = pd.DataFrame({"code": list(cand.index[:HEAD])})
        h["板块"] = [board_of(c) for c in h["code"]]
        heads[s] = h
        pools[s] = pd.Series([board_of(c) for c in cand.index]).value_counts().to_dict()
    print(f"[名单] 复现 {len(heads)} 个调仓日的前 {HEAD} 只候选 {time.time() - t1:.0f}s")

    rows = []
    yearly = {}
    for label, quota in SCHEMES:
        lists, quotas = {}, []
        for s, h in heads.items():
            names, q_used = make_list(h, quota, pools[s], ASHARE_BUY_TOP_N,
                                      FILL.get(label, True))
            lists[s] = names
            quotas.append(q_used)
        lenn = np.array([len(v) for v in lists.values()])
        # 名单自己铺不铺开，与下单层填不填得满是两件事，分开记
        share = np.array([max(pd.Series([board_of(c) for c in v]).value_counts())
                          for v in lists.values()]) / lenn
        # 配额是真兑现了，还是「给科创配 10 只、但最安静 200 名里一只都没有」⇒ 缺口
        # 全数回补给主板。这一组列决定 E 是「结构性分散」还是「某几年的运气」
        got_cnt = pd.DataFrame([{b: sum(1 for c in v if board_of(c) == b) for b in BOARDS}
                                for v in lists.values()])
        want_cnt = pd.DataFrame(quotas).reindex(columns=list(BOARDS)).fillna(0.0)
        fill_stat = {**{f"名单{b}只_均": float(got_cnt[b].mean()) for b in BOARDS},
                     **{f"配额{b}只_均": float(want_cnt[b].mean()) for b in BOARDS},
                     **{f"配额{b}未满天数": int((got_cnt[b] < want_cnt[b]).sum())
                        for b in BOARDS}}
        for n_slot in N_SLOT:
            picks, stats = {}, []
            for s, names in lists.items():
                buy = pd.DataFrame({"rank": range(1, len(names) + 1),
                                    "板块": [board_of(c) for c in names]}, index=names)
                od, st = order_candidates(buy, ind_map, n_slot,
                                          ASHARE_ORDER_MAX_PER_INDUSTRY,
                                          ASHARE_ORDER_MAX_PER_BOARD)
                picks[s] = list(od.index)
                stats.append(st)
            got = np.array([x["n_picked"] for x in stats])
            depth = np.array([x["scan_depth"] for x in stats])
            memb = [set(v) for v in picks.values()]
            for renorm in (False, True):
                port = replay(days, mtx, picks, n_slot, ASHARE_PORT_HOLD,
                              ASHARE_PORT_COST_ONE_WAY, renorm=renorm)
                j = pd.concat([port, bench], axis=1, join="inner").fillna(0.0)
                ex = (j.iloc[:, 0] - j.iloc[:, 1]).dropna()
                sd = float(ex.std() * np.sqrt(TRADING_DAYS))
                p = port.dropna()
                ann = float(p.mean() * TRADING_DAYS)
                vol = float(p.std() * np.sqrt(TRADING_DAYS))
                yr = R._yearly(port, bench)["excess"]
                if renorm:                    # 分年度只存满仓那一档：A 之后这就是生产口径
                    yearly[f"{label}｜{n_slot}席"] = yr
                rows.append({
                    "scheme": label, "slots": n_slot,
                    "cash_mode": "满仓(不留现金)" if renorm else "按槽位(余量现金)",
                    **fill_stat,
                    "avg_list_len": float(lenn.mean()),
                    "list_lt_target_dates": int((lenn < ASHARE_BUY_TOP_N).sum()),
                    "单板块占比_中位": float(np.median(share)),
                    "单板块占比_最差": float(share.max()),
                    "avg_picked": float(got.mean()),
                    "凑满槽位天数占比": float((got >= n_slot).mean()),
                    "avg_cash_left": float((n_slot - got).mean() / n_slot),
                    "avg_scan_depth": float(depth.mean()),
                    "list_one_way_turnover": float(np.mean(
                        [1.0] + [1.0 - len(memb[i - 1] & memb[i]) / n_slot
                                 for i in range(1, len(memb))])),
                    "ann_return": ann, "sharpe": ann / vol if vol else np.nan,
                    "excess_univ_ew_ann": float(ex.mean() * TRADING_DAYS),
                    "excess_ir": float(ex.mean() * TRADING_DAYS / sd) if sd else np.nan,
                    "max_drawdown": float(((1 + p).cumprod()
                                           / (1 + p).cumprod().cummax() - 1).min()),
                    "neg_years": int((yr < 0).sum()), "years": int(len(yr)),
                    "ex_drop_best1": float(yr.drop(yr.idxmax()).sum())})

    # 自检①：现状·全局前50 + 50 席全买，应当复现 M3 的 +0.0155
    base = {s: list(h["code"][:ASHARE_BUY_TOP_N]) for s, h in heads.items()}
    port50 = replay(days, mtx, base, ASHARE_BUY_TOP_N, ASHARE_PORT_HOLD,
                    ASHARE_PORT_COST_ONE_WAY)
    j50 = pd.concat([port50, bench], axis=1, join="inner").fillna(0.0)
    v50 = float((j50.iloc[:, 0] - j50.iloc[:, 1]).dropna().mean() * TRADING_DAYS)
    print(f"\n[自检] 五十只全买超额 = {v50:+.4f}｜对照 M3 参照行 {SELF_CHECK_REF:+.4f}"
          f"｜差 {abs(v50 - SELF_CHECK_REF):.4f}"
          f" {'✅ 同一套时序，配额方案的差可比' if abs(v50 - SELF_CHECK_REF) < 0.0015 else '❌ 回放写错了，别看下面的数'}")
    # 自检②：现状那一行的 avg_picked 必须与 M3 的 5 席/10 席逐字相同（挑票逻辑没动）
    cur = pd.DataFrame(rows)
    for slot, ref in ((5, 4.670175438596491), (10, 5.535087719298246)):
        v = float(cur[(cur["scheme"] == "现状·全局前50") & (cur["slots"] == slot)]
                  ["avg_picked"].iloc[0])
        print(f"[自检] 现状·{slot} 席 avg_picked = {v:.9f}｜M3 = {ref:.9f}"
              f" {'✅' if abs(v - ref) < 1e-9 else '❌ 名单复现偏了'}")

    pd.set_option("display.width", 280)
    pd.set_option("display.max_columns", 40)
    print("\n===== M4 观察名单按板块配额生成 × 下单层 5/10 席 × 两种资金口径 =====")
    print(cur.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print("""
列的白话含义：
  scheme                名单怎么生成。配额=每段最多取几只（都在各段最安静的里取）
                        回填=某段凑不满配额时，用全局次安静的补满 50 只；不回填=宁可少几只
  avg_list_len          名单平均几只（不回填的那版会小于 50）
  list_lt_target_dates  名单没到 50 只的天数 —— 生产入口有个「不足 50 只就不出名单」的
                        守卫，这一列 >0 就意味着那些天会整天空转，必须先解决它再谈收益
  单板块占比_中位/最差  名单里最大的那个板块占多少（现状中位 0.94 = 几乎是单板块名单）
  avg_picked            下单层平均真挑到几只
  凑满槽位天数占比       这一档最关键的可行性列（现状 10 席 = 0/570）
  avg_cash_left         空着的资金比例（满仓档读作「若按槽位给权会空多少」，实际为 0）
  avg_scan_depth        要往名单第几名扫才停手
  excess_univ_ew_ann    相对「过闸池等权」每年多赚/少赚多少
  excess_ir             年化超额 ÷ 超额年化波动，>0.3 算能用
  ex_drop_best1         扔掉最好那一年后，剩下年份的超额逐年相加
""")
    if os.path.exists(OUT):
        print(f"[警告] 产物已存在，本次覆盖：{OUT}")
    cur.to_csv(OUT, index=False)
    # 分年度：E 若只在某一两年赢，就不配叫"结构性改善"，这张表是专门防这个的
    yr_df = pd.DataFrame(yearly)
    yr_df.index.name = "year"
    yr_df.to_csv(OUT_Y, index=True)
    print("\n===== M4b 逐年超额（满仓口径；小数 = 当年相对过闸池等权多赚的比例）=====")
    print(yr_df.to_string(float_format=lambda v: f"{v:+.3f}"))
    print(f"[输出] {OUT}\n[输出] {OUT_Y}")


if __name__ == "__main__":
    main()
