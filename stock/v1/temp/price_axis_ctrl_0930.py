# -*- coding: utf-8 -*-
"""甲（09-30）：价格族搬到**下单层 5 席 + 配额**那把秤上，值多少钱

背景（全是已落档或本批实测的读数，本脚本只补没量过的那一层）：
  09-30 丙那三场回放里，篮子层（50 只整篮）价格族四把尺子 **9 个格子全胜**现轴：
  @50 只 hold=5 时 VWAP(Price,5) +3.82%/年（IR 0.36）、MAX(Price,5) +3.21%、
  MA(Price,5) +3.19%，而现轴 ts_mean(volume,20) 只有 +0.20%（IR 0.019）；
  年换手还只有现轴的一半（5.5× vs 10.9×）。**但篮子层赢不等于下单层赢** ——
  09-28 那次旧轴 ts_std(volume,20) 就是篮子层三项全赢、搬到 5 席反号成 −3.34pp/年。
  所以「换轴」这条路今天的状态跟当时一样：**只有篮子层证据**。本脚本补的就是这一格。

  ⚠️ 一根必须先分清的价：`build_matrices` 里 DSL 绑的 close 是**复权价**（茅台面板
  304.93 / factor 0.243208 = 1253.80 元才是券商行情）。所以 ma(df,5) 排出来的「低价端」
  ≠  literature 里的低价股（现价几块钱的票），它更像「首日基准价低 + 累计除权多」的老票。
  ⇒ 本脚本同时跑**盘面真实报价**那一臂（mtx["raw_price"] 的 5 日均价），
  两臂一比就知道这 3pp 是真·低价股效应，还是复权锚点给的形状。

一次跑齐五套组合（每套都走完整链路，判据一字未改，只有排序键不同）：
    臂 0 现轴 ts_mean(volume,20)                    —— 复现归档 +0.130379 = 锚点
    臂 1 复权 5 日均价 ma(df,5)                     —— 只换排序键（闸门仍吃现轴）
    臂 2 复权 5 日 VWAP  ts_sum(volume*close,5)/ts_sum(volume,5)
    臂 3 复权 5 日最高收盘 max(df,5)
    臂 4 **盘面**真实报价 5 日均价（非 DSL，走 raw_price）

    候选 = tradable_mask(四道闸) ∧ 量能并集命中 < ASHARE_BUY_MIN_HITS(=3)   ← 与生产同一份实现
    名单 = 全局前 50 / 板块配额 20·10·10·10（段内按轴升序，凑不满回填）
    下单 = ashare_screen.order_candidates(名单, 行业表, 槽位, 行业≤1, 板块≤3)  ← 判据没动
    回放 = 信号日收盘定名单 → 次日开盘建仓 → 持有 5 日 → 开盘平仓，双边 15bp 记在建仓日，
           每席 1/实际只数（满仓，09-25 选项A 的生产口径）

边界照旧（与归档 M4 同口径才可比）：历史复现没有当日收盘快照，生产名单额外的三道执行性闸
（当日真成交、收盘已贴涨停不追、ST）未跑；行业映射用今天这份表回溯 2015 年；
全样本挑轴 = 样本内，五臂横比本身就是五次比较（冠军诅咒由 乙 那把样本外刀再判）。

**样本末钉在 2026-09-24（不是随手取的）**：M4 归档与 09-28 那场都停在 2852 个 live 交易日，
面板 09-29 14:28 那次日更多了一天（2026-09-28，现 2853）⇒ 同一段代码、同一份口径，
5 席超额从 0.130378915 变成 0.131250438（+8.7e-4，A1 也同步 +9e-4）。这正是「跨面板版本
只能报过期度、逐位对表只在同版本内成立」那条老规矩。所以这里**裁样本对齐**，而不是把
1e-6 放宽成模糊带：一来 A2 的逐位闸重新成立（能证明驱动没改口径），二来这五臂才跟
09-28 那条「旧轴 5 席 +9.70%」坐在同 570 场上，两张表可以并排读。

八道自检，任何一道红就 exit=1（不许「报了问题还退 0」）：
  A0 样本末必须真裁到归档那一版（2852 个 live 交易日，末日 2026-09-24）
  A1 现轴·五十只全买 复现 +0.0155（容差 1.5e-3）
  A2 现轴·配额·5 席满仓 逐位复现归档 +0.130379（容差 1e-6；偏了=驱动改到口径，全表作废）
  A3 正对照：每条价格臂的 5 席超额必须与现轴**不等**（>1e-6）——相等=那臂的注入没生效
  A4 结构对照：每臂前 200 名与现轴的平均重合 <199（否则两臂是同一张表）
  A5 五套组合的调仓日数必须一致（少一天=被 NaN 覆盖度卡掉，要如实报）
  A6 现轴与价格臂都须 570 天全复现
  A7 覆盖度读数（只报不判）：各臂候选池中位数只数 —— 复权价有值但量能 NaN 的票有多少
负对照：PRICE_NEGCTL=1 把所有价格臂钉成现轴 ⇒ A3/A4 必须红、**A0/A1/A2 仍须绿**
        （2026-09-30 10:14 真跑验过：九条红全在 A3/A4 那八格，注入一拔就抓到）
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
HEAD = 200                     # 每天只留最靠前 200 只：配额最多 14，回填也用不到更深
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
OUT_DIR = os.path.join(ROOT, "stock/v1/temp/tmp_price_axis_ctrl_0930"
                       + ("_negctl" if os.environ.get("PRICE_NEGCTL") == "1" else ""))
OUT = os.path.join(OUT_DIR, "price_axis_0930.csv")
OUT_Y = os.path.join(OUT_DIR, "price_axis_0930_yearly.csv")
OUT_DAY = os.path.join(OUT_DIR, "price_axis_0930_seats.csv")
ARCHIVED_M4 = os.path.join(ROOT, "stock/v1/data/results/quota_watchlist_m4_0925.csv")

CUR_AXIS = "ts_mean(volume,20)"
VWAP_AXIS = "ts_sum(volume*close,5)/ts_sum(volume,5)"
RAW_KEY = "__raw_price_ma5"     # 非 DSL：盘面真实报价，后面单独造矩阵
NEGCTL = os.environ.get("PRICE_NEGCTL") == "1"
# 样本末对齐 M4 归档 / 09-28 那场那一版；面板只会往后长，改这个数等于换样本、A2 必红
SAMPLE_END = os.environ.get("AXIS_SAMPLE_END", "2026-09-24")
EXPECT_LIVE_DAYS = 2852        # 归档那一版的 live 交易日数（A0 钉这个）

# (标签, 排序轴取哪张矩阵, 闸门因子)
ARMS = [("臂0 现轴 SMA(vol,20)", CUR_AXIS, CUR_AXIS),
        ("臂1 复权均价 ma(df,5)", "ma(df,5)", CUR_AXIS),
        ("臂2 复权 VWAP(Price,5)", VWAP_AXIS, CUR_AXIS),
        ("臂3 复权最高收盘 max(df,5)", "max(df,5)", CUR_AXIS),
        ("臂4 盘面真实报价 5 日均价", RAW_KEY, CUR_AXIS)]
if NEGCTL:                       # 负对照：价格臂全钉成现轴，A3 必须红
    ARMS = [(ARMS[0][0], CUR_AXIS, CUR_AXIS)] + \
           [(lbl, CUR_AXIS, CUR_AXIS) for lbl, _a, _g in ARMS[1:]]

QUOTA = {"主板": 20, "创业板": 10, "科创板": 10, "北交所": 10}
SCHEMES = [("现状·全局前50", None), ("E-a 20/10/10/10 回填", QUOTA)]
SLOTS = (5, 10)
BOARDS = ("主板", "创业板", "科创板", "北交所")
SELF_CHECK_REF = 0.0155
ANCHOR_REF = 0.130379


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


def build_heads(days, mtx, allow_prod, axis, gate_axis, hold=ASHARE_PORT_HOLD):
    """一套（排序轴, 闸门因子）下的每日候选前 HEAD 只；闸门一律吃现轴=只换排序键

    `hold` 09-30 起提出来当参数（默认仍是生产那一档 ⇒ 本脚本自己的账单一字不变）：
    `temp/q_hold_grid_0930.py` 要扫持有窗，若它另抄一份调仓网格，两层就不是同一把尺了。
    """
    heads = {}
    for i in range(0, len(days) - hold - 1, hold):
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
          f"｜负对照 PRICE_NEGCTL={int(NEGCTL)}")
    assert BUY_EXPR == CUR_AXIS, f"生产排序轴已不是 {CUR_AXIS}，这张对照表的参照行得重定义"
    t0 = time.time()
    wide, _ = load_panel()
    mtx = build_matrices(wide)
    exprs = sorted(set(RULE_EXPRS) | {CUR_AXIS, VWAP_AXIS, "ma(df,5)", "max(df,5)"})
    fms = factor_matrices(exprs, mtx)
    # 盘面真实报价那一臂：raw_price 已在 build_matrices 里折好（= 复权价 / factor）
    fms[RAW_KEY] = (mtx["raw_price"].astype("float64").rolling(5).mean()
                    .astype("float32"))
    full_idx = mtx["ret_open"].index
    from_start = full_idx >= pd.Timestamp(ASHARE_PORT_START)
    n_full = int(from_start.sum())
    live = from_start & (full_idx <= pd.Timestamp(SAMPLE_END))
    n_used = int(live.sum())
    print(f"[样本] 起点 {ASHARE_PORT_START} 起共 {n_full} 个交易日，钉到 {SAMPLE_END} 用 {n_used} 个"
          f"（面板比归档那一版多的 {n_full - n_used} 天已裁掉——不裁就没法逐位对表）")
    mtx = {k: v.loc[full_idx[live]] for k, v in mtx.items()}
    fms = {e: m.loc[m.index[live]] for e, m in fms.items()}
    days = mtx["ret_open"].index
    universe = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
                & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    bench = mtx["ret_open"].where(universe).mean(axis=1)
    print(f"[准备] {time.time() - t0:.0f}s｜{len(days)} 个交易日｜求值 {len(exprs) + 1} 张因子矩阵")

    pool_df, hit_df = R.exclusion_hits(mtx, days, fms, ASHARE_PORT_HOLD,
                                       ASHARE_SCREEN_QUANTILE)
    all_r = tuple(range(len(R.VOLUME_RULES)))
    allow_prod = R.exclusion_mask(("生产", "并集", all_r, ASHARE_BUY_MIN_HITS),
                                  pool_df, hit_df)
    ind, ind_meta = load_industry_map()
    ind_map = ind.to_dict() if ind_meta.get("loaded") else {}
    print(f"[行业表] loaded={ind_meta.get('loaded')} 条数={ind_meta.get('n')}")

    heads_by_cfg, t1 = {}, time.time()
    for label, axis_expr, gate_expr in ARMS:
        heads_by_cfg[label] = build_heads(days, mtx, allow_prod,
                                          fms[axis_expr], fms[gate_expr])
        print(f"[名单] {label}：复现 {len(heads_by_cfg[label])} 个调仓日的前 {HEAD} 只")
    print(f"[名单] 五套合计 {time.time() - t1:.0f}s")

    # ---------- A5：各臂的调仓日必须同一天数，否则整张表不是同一批样本 ----------
    n_days = {k: len(v) for k, v in heads_by_cfg.items()}
    same_days = len(set(n_days.values())) == 1
    base_label = ARMS[0][0]
    hs_cur = heads_by_cfg[base_label]

    rows, yearly, seat_rows, picks_prod = [], {}, [], {}
    for label, axis_expr, gate_expr in ARMS:
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
                    "arm": label, "sort_expr": axis_expr, "gate_expr": gate_expr,
                    "sample_end": SAMPLE_END, "live_days": n_used,
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
                        seat_rows.append({"date": s, "arm": label,
                                          "seats": "、".join(sorted(v))})

    full = pd.DataFrame(rows)

    # ---------- A1：现轴的「五十只全买」应当复现 +0.0155 ----------
    base = {s: list(h["code"][:ASHARE_BUY_TOP_N]) for s, h in hs_cur.items()}
    port50 = replay(days, mtx, base, ASHARE_BUY_TOP_N, ASHARE_PORT_HOLD,
                    ASHARE_PORT_COST_ONE_WAY)
    j50 = pd.concat([port50, bench], axis=1, join="inner").fillna(0.0)
    v50 = float((j50.iloc[:, 0] - j50.iloc[:, 1]).dropna().mean() * TRADING_DAYS)

    def five_row(label):
        r = full[(full["arm"] == label) & full["scheme"].str.startswith("E-a")
                 & (full["slots"] == 5)]
        return float(r["excess_univ_ew_ann"].iloc[0])

    # ---------- A2：现轴·配额·5 席 应当逐字复现归档那一行 ----------
    v_cur = five_row(base_label)
    ref_m4 = None
    if os.path.exists(ARCHIVED_M4):
        a = pd.read_csv(ARCHIVED_M4)
        r = a[a["scheme"].str.startswith("E-a") & (a["slots"] == 5)
              & a["cash_mode"].str.startswith("满仓")]
        ref_m4 = float(r["excess_univ_ew_ann"].iloc[0])

    # ---------- 自检表：A0/A1/A2 是口径闸，A3/A4 逐臂（下面追加）----------
    checks = [("A0 样本末已裁到归档那一版（同一样本才许逐位对表）",
               n_used == EXPECT_LIVE_DAYS and days[-1] == pd.Timestamp(SAMPLE_END),
               f"用 {n_used} 天（应 {EXPECT_LIVE_DAYS}）｜末日 {days[-1].date()}"
               f"｜面板现有 {n_full} 天"),
              ("A1 现轴·五十只全买复现参照", abs(v50 - SELF_CHECK_REF) < 0.0015,
               f"{v50:+.4f} vs 参照 {SELF_CHECK_REF:+.4f}（差 {abs(v50 - SELF_CHECK_REF):.4f}）"),
              ("A2 现轴·配额·5 席复现归档 +%.6f" % ANCHOR_REF,
               ref_m4 is not None and abs(v_cur - ref_m4) < 1e-6,
               f"本跑 {v_cur:.9f}｜归档 {('%.9f' % ref_m4) if ref_m4 is not None else '归档缺失'}"
               f"｜样本末 {SAMPLE_END}")]
    for label, axis_expr, _g in ARMS[1:]:
        v_arm = five_row(label)
        checks.append((f"A3 正对照：{label} 必须≠现轴那一行", abs(v_arm - v_cur) > 1e-6,
                       f"现轴 {v_cur:.6f}｜该臂 {v_arm:.6f}｜差 {v_arm - v_cur:+.6f}"))
        common = sorted(set(hs_cur) & set(heads_by_cfg[label]))
        ov = np.array([len(set(hs_cur[s]["code"]) & set(heads_by_cfg[label][s]["code"]))
                       for s in common])
        checks.append((f"A4 结构对照：{label} 前 200 名必须真换人",
                       float(ov.mean()) < HEAD - 1,
                       f"平均重合 {ov.mean():.1f}/{HEAD} 只｜重合=200 的天数 {int((ov == HEAD).sum())}"))
    checks.append(("A5 五套组合的调仓日数一致", same_days, f"{n_days}"))
    checks.append(("A6 570 天全复现（一天没落）",
                   all(v == 570 for v in n_days.values()),
                   "｜".join(f"{k.split()[0]} {v}" for k, v in n_days.items())))
    # A7 只报不判：候选池只数的中位数 —— 覆盖度差异的读数
    pool_med = {label: int(np.median([len(v) for v in heads.values()]))
                for label, heads in heads_by_cfg.items()}
    print("\n[A7 覆盖度读数·不判红] 每臂调仓日候选池中位数（只）：")
    for label, m in pool_med.items():
        print(f"    {label}  {m} 只"
              + (f"（比现轴 {'+' if m >= pool_med[base_label] else ''}{m - pool_med[base_label]}）"
                 if label != base_label else ""))

    print("\n===== 自检（任何一条红 → exit=1，读数全部作废）=====")
    bad = []
    for name, ok, detail in checks:
        print(f"  {'✅' if ok else '❌'} {name}：{detail}")
        if not ok:
            bad.append(name)

    # ---------- 跨臂：生产那一格（配额·5 席）相对现轴换了几张面孔 ----------
    pc = picks_prod[base_label]
    for label in picks_prod:
        if label == base_label:
            continue
        po = picks_prod[label]
        dd = sorted(set(pc) & set(po))
        ch = np.array([len(set(po[s]) - set(pc[s])) for s in dd])
        print(f"\n[跨臂·配额5席] {label}：每场平均换 {ch.mean():.2f} 只"
              f"｜中位 {np.median(ch):.0f} 只｜一场没换占 {(ch == 0).mean():.1%}"
              f"｜换 ≥3 只占 {(ch >= 3).mean():.1%}")

    pd.set_option("display.width", 320)
    pd.set_option("display.max_columns", 45)
    print("\n===== 甲：价格族搬到下单层（满仓，样本内 570 场；配额·5 席就是掏钱那一格）=====")
    show = ["arm", "scheme", "slots", "excess_univ_ew_ann", "excess_ir", "max_drawdown",
            "ann_return", "sharpe", "凑满槽位天数占比", "单板块占比_中位", "每场换几席",
            "avg_amount_20d", "neg_years", "pos_years_2021", "avg_excess_2021", "ex_drop_best1"]
    print(full[show].to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    prod = full[full["scheme"].str.startswith("E-a") & (full["slots"] == 5)]
    print("\n===== 只看掏钱那一格（配额 20/10/10/10 · 5 席 · 满仓）相对现轴的差 =====")
    for r in prod.itertuples():
        d = r.excess_univ_ew_ann - v_cur
        print(f"  {r.arm:<28} {r.excess_univ_ew_ann:+.4f}｜相对现轴 {d:+.4f}"
              f"｜凑满 {r.凑满槽位天数占比:.1%}｜均额 {r.avg_amount_20d / 1e8:.2f} 亿"
              f"｜回撤 {r.max_drawdown:.4f}｜扔最好一年后 {r.ex_drop_best1:+.4f}")
    yr_df = pd.DataFrame(yearly)
    yr_df.index.name = "year"
    print("\n===== 逐年超额（相对过闸池等权；小数=当年多赚的比例）=====")
    print(yr_df.to_string(float_format=lambda v: f"{v:+.3f}"))

    full.to_csv(OUT, index=False)
    yr_df.to_csv(OUT_Y, index=True)
    pd.DataFrame(seat_rows).to_csv(OUT_DAY, index=False)
    print(f"\n[输出] {OUT}\n[输出] {OUT_Y}\n[输出] {OUT_DAY}")

    if bad:
        print(f"\n[判据] {len(bad)} 条红：{'、'.join(bad)} ⇒ 退出码 1，上面的数一个都别引")
        sys.exit(1)
    print(f"\n[判据] {len(checks)} 条全绿")


if __name__ == "__main__":
    main()
