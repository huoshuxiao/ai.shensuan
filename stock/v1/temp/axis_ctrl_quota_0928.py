# -*- coding: utf-8 -*-
"""B 路（09-28）：观察名单的排序轴换回旧轴，到底值多少钱 —— 一张对照表读齐

背景（全是已落档的读数，本脚本只补没量过的那一半）：
  排序轴 09-24 从 `ts_std(volume,20)`（量能波动 = 20 日成交量的标准差）换成
  `ts_mean(volume,20)`（量能水平 = 20 日成交量的均值，中文名「安静度」，**升序买低分端**
  = 买没人交易的那一角）。换轴当时的三条依据里，**篮子层那条 09-25 换名单形状后翻了**：
  配额档下三档全部旧轴更高（+1.00%/+1.73%/+3.69% vs +0.11%/+1.25%/+1.54%，
  `ashare_portfolio_eval.csv`），而分年度那条没翻（现轴 2021 起 4/6 为正 +2.06%/年，
  旧轴 2/6、-1.32%/年，`ashare_portfolio_eval_yearly.csv` 只记 top100 那一档）。
  **从没量过的是真正掏钱那一层**：下单层 5 席 + 配额 + 旧轴 = ? 归档里 +13.04%/年
  是现轴跑出来的（`quota_watchlist_m4_0925.csv`），所以「换回旧轴」这个决定一直缺一条腿。

一次跑齐三种组合（每组合都走完整链路，判据一字未改）：
    轴 A 现轴 SMA($volume,20)   —— 闸门因子=现轴（生产现状，用来复现归档 = 锚点）
    轴 B 旧轴 STD($volume,20) + 闸门因子仍=现轴  —— **只换排序键**，回答「轴值多少钱」
    轴 B 旧轴 STD($volume,20) + 闸门因子也跟轴    —— 真换轴时长这样，因为
      `tradable_mask` 第 1 道闸判的就是「传入的那根因子当日有值」，换轴会连带换闸门覆盖面

    候选 = tradable_mask(四道闸) ∧ 量能并集命中 < ASHARE_BUY_MIN_HITS(=3)   ← 与生产同一份实现
    名单 = 全局前 50 / 板块配额 20·10·10·10（段内按轴升序，凑不满回填）
    下单 = ashare_screen.order_candidates(名单, 行业表, 槽位, 行业≤1, 板块≤3)  ← 判据没动
    回放 = 信号日收盘定名单 → 次日开盘建仓 → 持有 5 日 → 开盘平仓，双边 15bp 记在建仓日，
           每席 1/实际只数（满仓，09-25 选项A 的生产口径）

边界照旧（与归档 M4 同一个口径，所以可比）：历史复现没有当日收盘快照，生产名单额外的
三道执行性闸（当日真成交、收盘已贴涨停不追、ST）未跑；行业映射用今天这份表回溯 2015 年。

四道自检，任何一道红就 exit=1（不许「报了问题还退 0」）：
  A1 现轴·全局前50·五十只全买 应当复现 M3/M4 的 +0.0155（容差 1.5e-3）
  A2 现轴·配额·5 席满仓 应当复现归档 +0.130379（容差 1e-6；偏了说明驱动改到口径，全表作废）
  A3 正对照：旧轴那一行的超额必须与 A2 **不等**（>1e-6）。相等=换轴注入根本没生效
  A4 结构对照：两轴各自的前 200 名候选，平均重合只数必须 <200（否则两次排序是同一张表）
  A5 570 个调仓日：三套组合的名单天数必须一致（少一天就是被 NaN 覆盖度卡掉了，要如实报）
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
HEAD = 200                     # 每天只留最安静的前 200 只：配额最多 14，回填也用不到更深
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
OUT_DIR = os.path.join(ROOT, "stock/v1/temp/tmp_axis_ctrl_0928")
OUT = os.path.join(OUT_DIR, "axis_ctrl_0928.csv")
OUT_Y = os.path.join(OUT_DIR, "axis_ctrl_0928_yearly.csv")
OUT_DAY = os.path.join(OUT_DIR, "axis_ctrl_0928_seats.csv")
ARCHIVED_M4 = os.path.join(ROOT, "stock/v1/data/results/quota_watchlist_m4_0925.csv")

CUR_AXIS = "ts_mean(volume,20)"
OLD_AXIS = "ts_std(volume,20)"
SELF_CHECK_REF = 0.0155        # M3/M4 的「五十只全买」超额年化
QUOTA = {"主板": 20, "创业板": 10, "科创板": 10, "北交所": 10}
SCHEMES = [("现状·全局前50", None), ("E-a 20/10/10/10 回填", QUOTA)]
SLOTS = (5, 10)
BOARDS = ("主板", "创业板", "科创板", "北交所")
NEGCTL = os.environ.get("AXIS_NEGCTL") == "1"   # 负对照：两轴都钉成现轴，A3 必须红

# (标签, 排序轴表达式, 闸门因子是否跟轴)
CONFIGS = [(f"现轴 {CUR_AXIS}", CUR_AXIS, CUR_AXIS),
           (f"旧轴 {OLD_AXIS}·只换排序键", OLD_AXIS, CUR_AXIS),
           (f"旧轴 {OLD_AXIS}·闸门也跟轴", OLD_AXIS, OLD_AXIS)]
if NEGCTL:
    CONFIGS = [(f"现轴 {CUR_AXIS}", CUR_AXIS, CUR_AXIS),
               (f"负对照·假称旧轴 {CUR_AXIS}", CUR_AXIS, CUR_AXIS),
               ("负对照·跳过", CUR_AXIS, CUR_AXIS)]


def make_list(head_df, quota, total, fill=True):
    """按配额从「已按轴升序的前 HEAD 只」里生成观察名单（名次一律按轴，不按取用顺序）"""
    if quota is None:
        return list(head_df.head(total)["code"]), {}
    chosen, left, order = set(), total, []
    for b in BOARDS:
        got = head_df[head_df["板块"] == b].head(min(quota.get(b, 0), left))
        order.extend(got.index.tolist())
        chosen |= set(got["code"])
        left -= len(got)
    if fill and left > 0:
        order.extend(head_df[~head_df["code"].isin(chosen)].head(left).index.tolist())
    picked = head_df.loc[order].sort_index()
    return list(picked["code"]), quota


def replay(days, mtx, picks, n_slot, hold, cost):
    """与 M3/M4 同一套时序：信号日 i → 建仓日 j=i+1 → 持仓段 [j+1, j+1+hold)，成本记在 j"""
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
        for nm in names:
            w[lo:hi, col_of[nm]] = 1.0 / len(names)
        phi = 1.0 if prev is None else 1.0 - len(set(prev) & set(names)) / n_slot
        c[j] += 2 * cost * phi
        prev = names
    return pd.Series((w * ret).sum(axis=1), index=days) - c


def build_heads(days, mtx, allow_prod, axis, gate_axis):
    """一套（排序轴, 闸门因子）下的每日候选前 HEAD 只；闸门跟不跟轴就差 gate_axis 一根"""
    heads = {}
    for i in range(0, len(days) - ASHARE_PORT_HOLD - 1, ASHARE_PORT_HOLD):
        s, d1 = days[i], days[i + 1]
        ok, _ = tradable_mask(s, d1, gate_axis, mtx)
        cand = axis.loc[s].where(ok & allow_prod.loc[s]).dropna().sort_values()
        if len(cand) < ASHARE_BUY_TOP_N:
            continue
        h = pd.DataFrame({"code": list(cand.index[:HEAD])})
        h["板块"] = [board_of(c) for c in h["code"]]
        heads[s] = h
    return heads


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"[口径] 涨停闸 = {ASHARE_TRADABLE_GATE}（{gate_desc()}）｜名单 {ASHARE_BUY_TOP_N} 只"
          f"｜下单同行业 ≤{ASHARE_ORDER_MAX_PER_INDUSTRY}、同板块 ≤{ASHARE_ORDER_MAX_PER_BOARD}"
          f"｜持有 {ASHARE_PORT_HOLD} 日｜单边费率 {ASHARE_PORT_COST_ONE_WAY}"
          f"｜独立闸 {os.environ.get('STOCK_BUY_EXTRA_RULES', '(默认关)')}")
    assert BUY_EXPR == CUR_AXIS, f"生产排序轴已不是 {CUR_AXIS}，这张对照表的参照行得重定义"
    t0 = time.time()
    wide, _ = load_panel()
    mtx = build_matrices(wide)
    fms = factor_matrices(sorted(set(RULE_EXPRS) | {CUR_AXIS, OLD_AXIS}), mtx)
    live = mtx["ret_open"].index >= pd.Timestamp(ASHARE_PORT_START)
    mtx = {k: v.loc[mtx["ret_open"].index[live]] for k, v in mtx.items()}
    fms = {e: m.loc[m.index[live]] for e, m in fms.items()}
    days = mtx["ret_open"].index
    universe = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
                & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    bench = mtx["ret_open"].where(universe).mean(axis=1)
    print(f"[准备] {time.time() - t0:.0f}s｜{len(days)} 个交易日")

    pool_df, hit_df = R.exclusion_hits(mtx, days, fms, ASHARE_PORT_HOLD,
                                       ASHARE_SCREEN_QUANTILE)
    all_r = tuple(range(len(R.VOLUME_RULES)))
    allow_prod = R.exclusion_mask(("生产", "并集", all_r, ASHARE_BUY_MIN_HITS),
                                  pool_df, hit_df)
    ind, ind_meta = load_industry_map()
    ind_map = ind.to_dict() if ind_meta.get("loaded") else {}
    print(f"[行业表] loaded={ind_meta.get('loaded')} 条数={ind_meta.get('n')}")

    heads_by_cfg, t1 = {}, time.time()
    for label, axis_expr, gate_expr in CONFIGS:
        heads_by_cfg[label] = build_heads(days, mtx, allow_prod,
                                          fms[axis_expr], fms[gate_expr])
        print(f"[名单] {label}：复现 {len(heads_by_cfg[label])} 个调仓日的前 {HEAD} 只")
    print(f"[名单] 三套合计 {time.time() - t1:.0f}s")

    # ---------- A5：三套的调仓日必须同一天数，否则整张表不是同一批样本 ----------
    day_sets = {k: set(v) for k, v in heads_by_cfg.items()}
    n_days = {k: len(v) for k, v in day_sets.items()}
    same_days = len(set(np.unique(list(n_days.values())))) == 1

    rows, yearly, seat_rows = [], {}, []
    picks_prod = {}          # 生产那一格（配额·5 席·满仓）的逐场下单票，跨轴对比用
    for label, axis_expr, gate_expr in CONFIGS:
        heads = heads_by_cfg[label]
        for sname, quota in SCHEMES:
            lists = {}
            for s, h in heads.items():
                names, _q = make_list(h, quota, ASHARE_BUY_TOP_N)
                lists[s] = names
            lenn = np.array([len(v) for v in lists.values()])
            share = np.array([max(pd.Series([board_of(c) for c in v]).value_counts())
                              for v in lists.values()]) / lenn
            got_cnt = pd.DataFrame([{b: sum(1 for c in v if board_of(c) == b) for b in BOARDS}
                                    for v in lists.values()])
            want_cnt = pd.DataFrame([quota or {b: 0 for b in BOARDS}] * len(lists),
                                    columns=list(BOARDS))
            for n_slot in SLOTS:
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
                amt = np.array([float(mtx["amount20"].loc[s].reindex(v).mean())
                                if len(v) else np.nan for s, v in picks.items()])
                port = replay(days, mtx, picks, n_slot, ASHARE_PORT_HOLD,
                              ASHARE_PORT_COST_ONE_WAY)
                j = pd.concat([port, bench], axis=1, join="inner").fillna(0.0)
                ex = (j.iloc[:, 0] - j.iloc[:, 1]).dropna()
                sd = float(ex.std() * np.sqrt(TRADING_DAYS))
                p = port.dropna()
                ann = float(p.mean() * TRADING_DAYS)
                vol = float(p.std() * np.sqrt(TRADING_DAYS))
                yr = R._yearly(port, bench)["excess"]
                rows.append({
                    "axis_cfg": label, "sort_expr": axis_expr, "gate_expr": gate_expr,
                    "scheme": sname, "slots": n_slot, "cash_mode": "满仓(不留现金)",
                    **{f"名单{b}只_均": float(got_cnt[b].mean()) for b in BOARDS},
                    **{f"配额{b}未满天数": int((got_cnt[b] < want_cnt[b]).sum())
                       for b in BOARDS},
                    "avg_list_len": float(lenn.mean()),
                    "单板块占比_中位": float(np.median(share)),
                    "单板块占比_最差": float(share.max()),
                    "avg_picked": float(got.mean()),
                    "凑满槽位天数占比": float((got >= n_slot).mean()),
                    "avg_scan_depth": float(depth.mean()),
                    "每场换几席": float(np.mean([0.0] + [len(memb[i] - memb[i - 1])
                                                     for i in range(1, len(memb))])),
                    "list_one_way_turnover": float(np.mean(
                        [1.0] + [1.0 - len(memb[i - 1] & memb[i]) / n_slot
                                 for i in range(1, len(memb))])),
                    "avg_amount_20d": float(np.nanmean(amt)),
                    "ann_return": ann, "sharpe": ann / vol if vol else np.nan,
                    "excess_univ_ew_ann": float(ex.mean() * TRADING_DAYS),
                    "excess_ir": float(ex.mean() * TRADING_DAYS / sd) if sd else np.nan,
                    "max_drawdown": float(((1 + p).cumprod()
                                           / (1 + p).cumprod().cummax() - 1).min()),
                    "neg_years": int((yr < 0).sum()), "years": int(len(yr)),
                    "pos_years_2021": int((yr[yr.index >= 2021] > 0).sum()),
                    "avg_excess_2021": float(yr[yr.index >= 2021].mean()),
                    "ex_drop_best1": float(yr.drop(yr.idxmax()).sum())})
                yearly[f"{label}｜{sname}｜{n_slot}席"] = yr
                if sname.startswith("E-a") and n_slot == 5:
                    picks_prod[label] = picks
                    for s, v in picks.items():
                        seat_rows.append({"date": s, "cfg": label,
                                          "seats": "、".join(sorted(v))})

    full = pd.DataFrame(rows)

    # ---------- A1：现轴的「五十只全买」应当复现 +0.0155 ----------
    base = {s: list(h["code"][:ASHARE_BUY_TOP_N]) for s, h in heads_by_cfg[CONFIGS[0][0]].items()}
    port50 = replay(days, mtx, base, ASHARE_BUY_TOP_N, ASHARE_PORT_HOLD,
                    ASHARE_PORT_COST_ONE_WAY)
    j50 = pd.concat([port50, bench], axis=1, join="inner").fillna(0.0)
    v50 = float((j50.iloc[:, 0] - j50.iloc[:, 1]).dropna().mean() * TRADING_DAYS)

    # ---------- A2：现轴·配额·5 席 应当逐字复现归档那一行 ----------
    cur_row = full[(full["axis_cfg"] == CONFIGS[0][0]) & full["scheme"].str.startswith("E-a")
                   & (full["slots"] == 5)]
    v_cur = float(cur_row["excess_univ_ew_ann"].iloc[0])
    ref_m4 = None
    if os.path.exists(ARCHIVED_M4):
        a = pd.read_csv(ARCHIVED_M4)
        r = a[a["scheme"].str.startswith("E-a") & (a["slots"] == 5)
              & a["cash_mode"].str.startswith("满仓")]
        ref_m4 = float(r["excess_univ_ew_ann"].iloc[0])

    # ---------- A3 正对照 / A4 结构对照 ----------
    old_row = full[(full["axis_cfg"] == CONFIGS[1][0]) & full["scheme"].str.startswith("E-a")
                   & (full["slots"] == 5)]
    v_old = float(old_row["excess_univ_ew_ann"].iloc[0])
    hs_cur = heads_by_cfg[CONFIGS[0][0]]
    hs_old = heads_by_cfg[CONFIGS[1][0]]
    common = sorted(set(hs_cur) & set(hs_old))
    overlap = np.array([len(set(hs_cur[s]["code"]) & set(hs_old[s]["code"])) for s in common])

    # ---------- 跨轴：生产那一格（配额·5 席）到底换几张面孔 ----------
    cross = None
    if CONFIGS[0][0] in picks_prod and CONFIGS[1][0] in picks_prod:
        pc, po = picks_prod[CONFIGS[0][0]], picks_prod[CONFIGS[1][0]]
        dd = sorted(set(pc) & set(po))
        cross = pd.DataFrame({"date": dd,
                              "现轴5席": ["、".join(sorted(pc[s])) for s in dd],
                              "旧轴5席": ["、".join(sorted(po[s])) for s in dd],
                              "换了几席": [len(set(po[s]) - set(pc[s])) for s in dd]})
        print(f"\n[跨轴·配额5席] 每场平均换 {cross['换了几席'].mean():.2f} 只"
              f"｜中位 {cross['换了几席'].median():.0f} 只"
              f"｜一场没换占 {(cross['换了几席'] == 0).mean():.1%}"
              f"｜换 ≥3 只占 {(cross['换了几席'] >= 3).mean():.1%}")

    checks = [("A1 现轴·五十只全买复现参照", abs(v50 - SELF_CHECK_REF) < 0.0015,
               f"{v50:+.4f} vs 参照 {SELF_CHECK_REF:+.4f}（差 {abs(v50 - SELF_CHECK_REF):.4f}）"),
              ("A2 现轴·配额·5 席复现归档 +0.130379",
               ref_m4 is not None and abs(v_cur - ref_m4) < 1e-6,
               f"本跑 {v_cur:.9f}｜归档 {('%.9f' % ref_m4) if ref_m4 is not None else '归档缺失'}"),
              ("A3 正对照：旧轴那一行必须≠现轴那一行", abs(v_cur - v_old) > 1e-6,
               f"现轴 {v_cur:.6f}｜旧轴 {v_old:.6f}｜差 {v_old - v_cur:+.6f}"),
              ("A4 结构对照：两轴前 200 名必须真换人", float(overlap.mean()) < HEAD - 1,
               f"平均重合 {overlap.mean():.1f}/{HEAD} 只（最少 {int(overlap.min())}、"
               f"重合=200 的天数 {int((overlap == HEAD).sum())}）"),
              ("A5 三套组合的调仓日数一致", same_days,
               f"{n_days}｜共同日 {len(common)}"),
              ("A6 570 天全复现（一天没落）", len(hs_cur) == 570 and len(hs_old) == 570,
               f"现轴 {len(hs_cur)} 天｜旧轴 {len(hs_old)} 天")]

    print("\n===== 自检（任何一条红 → exit=1，读数全部作废）=====")
    bad = []
    for name, ok, detail in checks:
        print(f"  {'✅' if ok else '❌'} {name}：{detail}")
        if not ok:
            bad.append(name)

    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 45)
    print("\n===== B 路：排序轴 × 名单形状 × 下单槽位（满仓，样本内 570 场）=====")
    show = ["axis_cfg", "gate_expr", "scheme", "slots", "excess_univ_ew_ann", "excess_ir",
            "max_drawdown", "ann_return", "sharpe", "凑满槽位天数占比", "单板块占比_中位",
            "list_one_way_turnover", "每场换几席", "avg_amount_20d", "neg_years",
            "pos_years_2021", "avg_excess_2021", "ex_drop_best1"]
    print(full[show].to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    yr_df = pd.DataFrame(yearly)
    yr_df.index.name = "year"
    print("\n===== 逐年超额（相对过闸池等权；小数=当年多赚的比例）=====")
    print(yr_df.to_string(float_format=lambda v: f"{v:+.3f}"))

    full.to_csv(OUT, index=False)
    yr_df.to_csv(OUT_Y, index=True)
    pd.DataFrame(seat_rows).to_csv(OUT_DAY, index=False)
    if cross is not None:
        cross.to_csv(os.path.join(OUT_DIR, "axis_ctrl_0928_prod_seats.csv"), index=False)
    print(f"\n[输出] {OUT}\n[输出] {OUT_Y}\n[输出] {OUT_DAY}")
    print("\n列的白话含义：")
    print("  excess_univ_ew_ann  相对「过闸池等权」每年多赚/少赚（打分用的地板，不是真能买的组合）")
    print("  excess_ir           年化超额÷超额年化波动，>0.3 算能用")
    print("  每场换几席          每次调仓平均有几只票是新面孔（5 席满仓那档看这列判「改得动吗」）")
    print("  avg_amount_20d      这 5 只票的 20 日均额平均，量「买不买得到」的另一条腿")
    print("  pos_years_2021      2021 年起六年里有几年为正；avg_excess_2021 = 那六年均值")
    print("  ex_drop_best1       扔掉最好那一年后，剩下 11 年超额逐年相加")

    if bad:
        print(f"\n[判据] {len(bad)} 条红：{'、'.join(bad)} ⇒ 退出码 1，上面的数一个都别引")
        sys.exit(1)
    print("\n[判据] 六条全绿")


if __name__ == "__main__":
    main()
