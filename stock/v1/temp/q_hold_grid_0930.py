# -*- coding: utf-8 -*-
"""戊 + C：装载一次面板，把「刀口在哪」和「多久结一次账」两根旋钮各自扫一遍。

两件事共用同一份机制，所以合在一场里跑（`exclusion_hits(mtx, days, fms, hold, quantile)`
本来就同时吃这两个参数 ⇒ 分开跑等于把 0.8GB 的面板读两遍、把同一个锚点对两次）：

  戊（B）：`ASHARE_SCREEN_QUANTILE` 0.60~0.95 八档，**三层**各给一行账单
      L1 减法腿（域等权、毛收益、不扣费）＝归档那 +6.46%/年 用的口径
      L2 名单腿（50 只整篮、配额、扣双边 15bp）＝归档那 +0.28% 用的口径
      L3 下单层（5 席 + 配额 + 满仓、扣费）＝真正掏钱那一层，甲量了轴、没量刀口
  C：`ASHARE_PORT_HOLD` 1/2/5/10 四档（刀口钉在生产 0.80），同样三层
      ⇒ 回答「并集很值钱」那句是否只在 5 日窗成立。准入那侧（环1）用的可是
      **次日 1 天**的收益（`run_ashare_factor_eval.py:151` shift(-1)），两层不同钟。
      ⚠️ 本表的 1 日档是 open→open，与环1 那条 close→close 标签**同长不同口径**，
      只用来问「窗长一挪结论翻不翻」，不许冒充环1 复现。

省时的关键一步（也是最需要自证的一步）：刀口只是**池内截面分位上的一个切点**，
所以「四条构造的分位矩阵」与刀口无关 ⇒ 一次遍历算完分位，八档只是比大小。
这不是我新造的口径：`pct_grid()` 的表达式逐字照 `R.exclusion_hits` 抄，并由
**判据 P0** 拿生产那一档（q=0.80、hold=5）与 `R.exclusion_hits` 的真实返回值
**逐格对表**（bool 矩阵完全相等）——不等就整场作废，宁可不出数。

覆写面：只写本目录 `stock/v1/temp/tmp_q_hold_grid_0930/`（负对照 `_negctl`、
断线对照 `_noallow`）。三张对表基准 `ashare_portfolio_exclusion.csv` /
`ashare_portfolio_buylist.csv` / `quota_watchlist_m4_0925.csv` **只读**，
由外层驱动 `run_q_hold_grid_0930.sh` 卡批次前后 md5。
两张表都**一行一落盘**（不是等整场算完再写）：最贵的那一档排在后面，
崩在它上面时刀口那 8 行与已完成的持有窗行都还在盘上。

判据 = 11 行（只扫刀口的两场负对照）/ 12 行（正常场多一行 P7）。任何一行红 ⇒ exit=1：
  P0     快路子的分位必须逐格复现生产 `exclusion_hits` 的命中矩阵（口径闸）
  P1     样本末已裁到归档那一版（2852 个 live 交易日、末日 2026-09-24）
  P2a/b/c 锚点·减法腿：生产档三行（参照 / 并集≥1 / 并集≥3）对归档 |Δ|<1e-9
  P3a/b  锚点·名单腿：生产档两行（a=参照 / b=并集≥3）对归档 |Δ|<1e-6
  P4     锚点·下单层：生产档 5 席那一行对归档 |Δ|<1e-6
  P5     刀口有牙：保留占比必须随刀口**严格单调上升**（相邻档差 >1e-6）
  P6     刀口接进了名单腿：首末两档的 L2 年化超额必须不等（>1e-9）
  P7     周期有牙：每档 n_rebal 与 gain_days 必须等于网格算术（只扫刀口的那两场没有这行）
  P8     接线自证（破坏性）：在生产那一格把「当日轴值最低那一只」从允许池里挖掉，
         5 席名单必须真换人。这一条**不依赖闸的真实强度**，是它把「L3 读了 allow」钉住的。

只报不判的两条（它们是答案，不是尺子坏了）：
  P6b 刀口接进下单层 ⇒ 首末两档 5 席账单差多少、八档里有几档与生产档不同。
      09-30 探针实测：2015~2017 那一段里 L3 对刀口**完全不动**（八档逐字同值），
      同格「摘掉整道剔除闸」也一动不动 ⇒ 拿「L3 必须变」当判据会把一个真结论判成尺子坏。
      接线那一问交给 P8，强度这一问交给落盘的 `L3_席位差_摘闸_场数` 列。
  席位重合 ⇒ 首末两档 5 席逐日重合几只（真实名单层面，不靠年化反推）。

负对照（都要真跑，不是设想；两档都只扫刀口 ⇒ 每档约 4 分钟）：
  GH_NEGCTL=1  八档刀口全钉成 0.80 ⇒ 红集必须恰好 {P5, P6}，其余（含 P0/P2/P3/P4/P8）全绿
  GH_NOALLOW=1 把 L2/L3 吃的保留池换成「参照·不剔除」那一档（同一张矩阵、同一条代码路径，
               只是剔除不再咬）⇒ 红集**必须含** {P3b, P6}，且 P4 红不红必须与正常场落盘的
               `L3_席位差_摘闸_场数` 一致（>0 ⇒ P4 红；=0 ⇒ 5 席层对这道闸免疫、P4 必须绿）。
               这一条由外层驱动 `run_q_hold_grid_0930.sh` 拿两场的产物对表，不在此自证。
"""
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, f"{ROOT}/stock/v1/src")
sys.path.insert(0, f"{ROOT}/stock/v1/temp")

import os
import time

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401,E402

from config import (ASHARE_BUY_MIN_HITS, ASHARE_BUY_TOP_N, ASHARE_ORDER_MAX_PER_BOARD,
                    ASHARE_ORDER_MAX_PER_INDUSTRY, ASHARE_PORT_COST_ONE_WAY,
                    ASHARE_PORT_HOLD, ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED,
                    ASHARE_PORT_START, ASHARE_SCREEN_QUANTILE)
from ashare_screen import (BUY_EXPR, RULE_EXPRS, VOLUME_RULES, board_of, build_matrices,
                           factor_matrices, gate_desc, load_industry_map, load_panel,
                           order_candidates)
import run_ashare_portfolio_eval as R
# 下单层的时序单点（build_heads / make_list / replay / HEAD / QUOTA / BOARDS）：
# 甲用它逐位复现过归档，这里复用同一份，绝不在本脚本另写第二套调仓逻辑
import price_axis_ctrl_0930 as A

TRADING_DAYS = 252
NEGCTL = os.environ.get("GH_NEGCTL") == "1"
NOALLOW = os.environ.get("GH_NOALLOW") == "1"
Q_ONLY = NEGCTL or NOALLOW or os.environ.get("GH_Q_ONLY") == "1"
SUF = "_negctl" if NEGCTL else ("_noallow" if NOALLOW else "")
OUT_DIR = f"{ROOT}/stock/v1/temp/tmp_q_hold_grid_0930{SUF}"

Q_PROD, H_PROD = ASHARE_SCREEN_QUANTILE, ASHARE_PORT_HOLD
Q_LADDER = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
H_LADDER = (1, 2, 5, 10)
if NEGCTL:
    Q_LADDER = tuple([Q_PROD] * len(Q_LADDER))
SAMPLE_END = os.environ.get("GH_SAMPLE_END", "2026-09-24")
EXPECT_LIVE_DAYS = 2852
REF_LAB, GE1, GE3 = "参照·不剔除", "并集(命中>=1)", "并集(命中>=3)"
# 对表基准默认指生产那三张。外层驱动会把它们**逐字节快照**进 temp 并用 env 改指快照：
# 这场要跑 20~30 分钟，中途若有别的会话推日更链覆写归档，「样本对不上」会被读成「尺子坏了」
ANCHOR_DIR = os.environ.get("GH_ANCHOR_DIR") or f"{ROOT}/stock/v1/data/results"
ARCH_EX = f"{ANCHOR_DIR}/ashare_portfolio_exclusion.csv"
ARCH_BL = f"{ANCHOR_DIR}/ashare_portfolio_buylist.csv"
ARCH_M4 = f"{ANCHOR_DIR}/quota_watchlist_m4_0925.csv"
RULE_NAMES = R.RULE_NAMES
CHECKS = []
EXTRA = {"done": False}   # 生产那一格的两次加算（摘闸 / 挖轴）只做一次，别在八档上重复付费
# 生产档必须在梯子上，否则「锚点」根本不存在，P2~P4 会拿别档冒充生产
assert Q_PROD in Q_LADDER and (Q_ONLY or H_PROD in H_LADDER), \
    f"生产档 q={Q_PROD} / hold={H_PROD} 不在梯子上 ⇒ 锚点判据无从谈起"


def check(cid, label, ok, detail):
    CHECKS.append((cid, label, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} [{cid}] {label}｜{detail}", flush=True)


def readout(label, detail):
    """只报不判的读数：它可以是 0、可以没有差异，那种结果本身是答案，不该让整场作废"""
    print(f"  ·  [{label}·读数] {detail}", flush=True)


def pct_grid(mtx, days, fms, hold):
    """调仓网格上的「过闸池 + 四条构造的池内截面分位」，与 R.exclusion_hits 逐字同式。

    只把最后那步 `>= quantile` 留给调用方 ⇒ 刀口换档不必重算 rank（rank 是这一段
    的全部开销）。返回 (池 bool 矩阵, {构造名: 分位矩阵})。
    """
    gate = fms[RULE_EXPRS[0]]
    grid, pool_rows, pct_rows = [], [], {nm: [] for nm in RULE_NAMES}
    for i in range(0, len(days) - hold - 1, hold):
        s, d1 = days[i], days[i + 1]
        ok, _ = R.tradable_mask(s, d1, gate, mtx)
        grid.append(s)
        pool_rows.append(ok)
        for _k, nm, e, _d in VOLUME_RULES:
            pct_rows[nm].append(fms[e].loc[s].reindex(ok.index).where(ok).rank(pct=True))
    cols = mtx["close"].columns
    idx = pd.DatetimeIndex(grid)
    pool = pd.DataFrame(pool_rows, index=idx, columns=cols)
    pcts = {nm: pd.DataFrame(v, index=idx, columns=cols) for nm, v in pct_rows.items()}
    return pool, pcts


def hits_from_pct(pcts, q):
    """刀口 q 下的命中矩阵（True = 这条构造当日把这票判响）"""
    return {nm: pcts[nm].notna() & (pcts[nm] >= q) for nm in RULE_NAMES}


def seat_series(days, mtx, allow, fms, ind_map, hold, bench):
    """5 席下单层那一行：配额名单 → order_candidates → 与甲同一套 replay"""
    heads = A.build_heads(days, mtx, allow, fms[BUY_EXPR], fms[RULE_EXPRS[0]], hold=hold)
    picks = {}
    for s, h in heads.items():
        names, _ = A.make_list(h, A.QUOTA, ASHARE_BUY_TOP_N)
        buy = pd.DataFrame({"rank": range(1, len(names) + 1),
                            "板块": [board_of(c) for c in names]}, index=names)
        od, _st = order_candidates(buy, industry=ind_map, top_n=5,
                                   max_per_industry=ASHARE_ORDER_MAX_PER_INDUSTRY,
                                   max_per_board=ASHARE_ORDER_MAX_PER_BOARD)
        picks[s] = list(od.index)
    port = A.replay(days, mtx, picks, 5, hold, ASHARE_PORT_COST_ONE_WAY)
    j = pd.concat([port, bench], axis=1, join="inner").fillna(0.0)
    ann = float((j.iloc[:, 0] - j.iloc[:, 1]).dropna().mean() * TRADING_DAYS)
    return ann, picks, heads


def sabotage_mask(allow, axis):
    """接线自证用的假 mask：在原允许池上，逐场再挖掉「当日轴值最低那一只」。

    为什么要它：探针实测 5 席那一层的账单**与摘掉剔除闸完全同值**（+10.9920% 两边一字
    不差），也就是说「NOALLOW 那一场 P4 会不会红」取决于这段历史里闸**碰巧**咬没咬人——
    碰巧不咬时负对照就没有牙（09-29 那条「短窗对照被次新闸吃掉」同一个坑）。
    这一把尺不依赖闸的真实强度：只要 5 席那层真读了 allow，挖掉当日第一名必然换人。
    """
    sab = allow.copy()
    for s in sab.index:
        cand = axis.loc[s].where(sab.loc[s]).dropna()
        if len(cand):
            sab.at[s, cand.idxmin()] = False
    return sab


def rung(q, hold, pool, pcts, mtx, days, fms, col_of, universe, bench, ind_map):
    """一个 (刀口, 持有窗) 格子的三层账单"""
    t0 = time.time()
    hit = hits_from_pct(pcts, q)
    allows = {v[0]: R.exclusion_mask(v, pool, hit) for v in R.EXCL_VARIANTS}
    # 断线负对照：掏钱那两层（L2 名单腿 / L3 下单层）吃的保留池换成「参照·不剔除」
    # 那一档。刻意用同一张同形矩阵、不用 None ⇒ 代码路径一字不动，只是剔除失效。
    # L1 减法腿本来就不经过 allow，所以它不受影响（P2/P5 在负对照下仍须绿）。
    keep = {lab: (allows[REF_LAB] if (NOALLOW and lab != REF_LAB) else allows[lab])
            for lab in allows}
    ex_rows, _ = R.run_exclusion(mtx, days, hold, pool, hit)
    ex = pd.DataFrame(ex_rows).set_index("variant")
    l2 = {}
    for lab in (REF_LAB, GE3):
        port, st, _q = R.run_signal(fms[BUY_EXPR], col_of, days, mtx, ASHARE_BUY_TOP_N,
                                     hold=hold, universe=universe, quintiles=0,
                                     allow=keep[lab])
        l2[lab] = {**st, **R._excess(port, bench, "univ_ew")}
    ann5, picks, heads = seat_series(days, mtx, keep[GE3], fms, ind_map, hold, bench)
    # 只有生产那一格加算两笔（各一次 5 席回放）：闸到底咬不咬掏钱那层、以及那层有没有读 allow
    extra = (q == Q_PROD and hold == H_PROD and not EXTRA["done"])
    noex_ann, noex_diff, sab_ann, sab_diff = np.nan, np.nan, np.nan, np.nan
    if extra:
        EXTRA["done"] = True
        a_no, p_no, _h = seat_series(days, mtx, allows[REF_LAB], fms, ind_map, hold, bench)
        a_sab, p_sab, _h = seat_series(days, mtx, sabotage_mask(keep[GE3], fms[BUY_EXPR]),
                                       fms, ind_map, hold, bench)
        # 连「这一场根本没凑满名单」也算一次不同：覆盖度变化同样是接线有牙的证据
        noex_ann = a_no
        noex_diff = sum(1 for s in set(p_no) | set(picks) if picks.get(s) != p_no.get(s))
        sab_ann = a_sab
        sab_diff = sum(1 for s in set(p_sab) | set(picks) if picks.get(s) != p_sab.get(s))
        check("P8", "接线自证：挖掉当日第一名候选（本轴升序取低侧⇒即轴值最低那一只），5 席名单必须真换人",
              sab_diff > 0,
              f"挖轴换 {sab_diff}/{len(picks)} 场（账单 {sab_ann:+.6f} vs 本档 {ann5:+.6f}）"
              f"｜同格「摘掉整道剔除闸」换 {noex_diff} 场（这一个才是闸在 5 席层的"
              f"**真实强度**，0 也属于正常读数、不判红）")
    row = {
        "q": q, "hold": hold, "prod": q == Q_PROD and hold == H_PROD,
        "gate": R.ASHARE_TRADABLE_GATE, "buy_min_hits": ASHARE_BUY_MIN_HITS,
        "n_rebal": int(ex.loc[REF_LAB, "n_rebal"]),
        "n_rebal_expect": len(range(0, len(days) - hold - 1, hold)),
        "gain_days": int(ex.loc[REF_LAB, "gain_days"]),
        "avg_candidates": float(ex.loc[REF_LAB, "avg_candidates"]),
        "L1_参照_ann_return": float(ex.loc[REF_LAB, "ann_return"]),
        "L1_并集1_gain_ann": float(ex.loc[GE1, "gain_ann"]),
        "L1_并集1_gain_ir": float(ex.loc[GE1, "gain_ir"]),
        "L1_并集1_keep_ratio": float(ex.loc[GE1, "keep_ratio"]),
        "L1_并集1_avg_kept": float(ex.loc[GE1, "avg_kept"]),
        "L1_并集1_2015_2020": float(ex.loc[GE1, "gain_2015_2020"]),
        "L1_并集1_2021_2026": float(ex.loc[GE1, "gain_2021_2026"]),
        "L1_并集3_gain_ann": float(ex.loc[GE3, "gain_ann"]),
        "L2_参照_excess": l2[REF_LAB]["excess_univ_ew_ann"],
        "L2_并集3_excess": l2[GE3]["excess_univ_ew_ann"],
        "L2_并集3_turnover": l2[GE3]["one_way_turnover"],
        "L2_并集3_dd": l2[GE3]["max_drawdown"],
        "L3_5席_excess": ann5,
        "L3_不剔除_excess": noex_ann,
        "L3_席位差_摘闸_场数": noex_diff, "L3_席位差_挖轴_场数": sab_diff,
        "L3_席位数": float(np.mean([len(v) for v in picks.values()])),
        "L3_均额_亿": float(np.nanmean([float(mtx["amount20"].loc[s].reindex(v).mean())
                                       for s, v in picks.items()])) / 1e8,
        "L3_调仓日数": len(picks), "heads_数": len(heads), "秒": round(time.time() - t0, 1),
    }
    return row, picks


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    print(f"[口径] 涨停闸 {gate_desc()}｜名单 {ASHARE_BUY_TOP_N} 只｜下单 行业≤"
          f"{ASHARE_ORDER_MAX_PER_INDUSTRY}、板块≤{ASHARE_ORDER_MAX_PER_BOARD}"
          f"｜生产档 q={Q_PROD} hold={H_PROD}｜单边费率 {ASHARE_PORT_COST_ONE_WAY}"
          f"｜NEGCTL={int(NEGCTL)} NOALLOW={int(NOALLOW)} Q_ONLY={int(Q_ONLY)}", flush=True)
    wide, _ = load_panel()
    mtx = build_matrices(wide)
    exprs = sorted(set(RULE_EXPRS) | {BUY_EXPR})
    fms = factor_matrices(exprs, mtx)
    full_idx = mtx["ret_open"].index
    from_start = full_idx >= pd.Timestamp(ASHARE_PORT_START)
    n_full = int(from_start.sum())
    live = from_start & (full_idx <= pd.Timestamp(SAMPLE_END))
    n_used = int(live.sum())
    print(f"[样本] {ASHARE_PORT_START} 起 {n_full} 个交易日，钉到 {SAMPLE_END} 用 {n_used} 个"
          f"（多的 {n_full - n_used} 天已裁，不裁就没法逐位对表）", flush=True)
    mtx = {k: v.loc[full_idx[live]] for k, v in mtx.items()}
    fms = {e: m.loc[m.index[live]] for e, m in fms.items()}
    days = mtx["ret_open"].index
    col_of = {c: i for i, c in enumerate(mtx["close"].columns)}
    universe = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
                & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    bench = mtx["ret_open"].where(universe).mean(axis=1)
    ind, ind_meta = load_industry_map()
    ind_map = ind.to_dict() if ind_meta.get("loaded") else {}
    print(f"[准备] {time.time() - t0:.0f}s｜{len(days)} 个交易日｜求值 {len(exprs)} 张矩阵"
          f"｜行业表 {ind_meta.get('n')} 只", flush=True)

    print("\n===== 判据 P1 样本对齐 =====", flush=True)
    check("P1", "样本末已裁到归档那一版",
          n_used == EXPECT_LIVE_DAYS and days[-1] == pd.Timestamp(SAMPLE_END),
          f"用 {n_used} 天（应 {EXPECT_LIVE_DAYS}）｜末日 {days[-1].date()}"
          f"｜面板现有 {n_full} 天")

    q_rows, h_rows, picks_by_q = [], [], {}
    OUT_Q = os.path.join(OUT_DIR, "quantile_ladder.csv")
    OUT_H = os.path.join(OUT_DIR, "hold_ladder.csv")
    for f in (OUT_Q, OUT_H):
        if os.path.exists(f):
            os.remove(f)

    # 生产那一档排在周期梯子的**第一位**：它同时产出刀口那 8 行（B 的主产物），
    # 后面的 1/2/10 只是补 C。反过来排（1 日最先）一旦在最贵那档撑不住，
    # 两张表一行都没落盘——边算边打不是风格问题，是「崩了还剩什么」的问题。
    holds = (H_PROD,) if Q_ONLY else (H_PROD,) + tuple(h for h in H_LADDER if h != H_PROD)
    for hold in holds:
        t1 = time.time()
        pool, pcts = pct_grid(mtx, days, fms, hold)
        print(f"\n[分位] hold={hold}：{len(pool)} 个调仓日 × {pool.shape[1]} 只"
              f"，一次遍历 {time.time() - t1:.0f}s", flush=True)
        if hold == H_PROD:
            print("===== 判据 P0 快生子与生产 exclusion_hits 同式 =====")
            pool_p, hit_p = R.exclusion_hits(mtx, days, fms, hold, Q_PROD)
            same_pool = bool(pool.equals(pool_p))
            bad = [nm for nm in RULE_NAMES
                   if not hits_from_pct(pcts, Q_PROD)[nm].equals(hit_p[nm])]
            check("P0", "分位快生子的命中矩阵逐格复现 exclusion_hits",
                  same_pool and not bad,
                  f"过闸池 {'逐格相等' if same_pool else '不等'}｜四条构造命中"
                  f"{'全部逐格相等' if not bad else '不等的有：' + '、'.join(bad)}"
                  f"｜网格 {len(pool)} 场")
        qs = Q_LADDER if hold == H_PROD else (Q_PROD,)
        for q in qs:
            row, picks = rung(q, hold, pool, pcts, mtx, days, fms, col_of,
                              universe, bench, ind_map)
            line = (f"  q={q:.2f} hold={hold:>2}｜L1 减法腿 {row['L1_并集1_gain_ann']:+.4%}"
                    f"（留 {row['L1_并集1_keep_ratio']:.1%}）｜L2 名单腿 "
                    f"{row['L2_并集3_excess']:+.4%}（换手 {row['L2_并集3_turnover']:.3f}）"
                    f"｜L3 五席 {row['L3_5席_excess']:+.4%}｜n={row['n_rebal']}"
                    f"｜{row['秒']}s")
            # 持有窗那把梯子只在刀口 = 生产档时量（否则两把旋钮搅在一起，读数归不了因）
            if q == Q_PROD and not Q_ONLY:
                h_rows.append(row)
                pd.DataFrame([row]).to_csv(OUT_H, mode="a",
                                           header=not os.path.exists(OUT_H), index=False)
            if hold == H_PROD:
                q_rows.append(row)
                pd.DataFrame([row]).to_csv(OUT_Q, mode="a",
                                           header=not os.path.exists(OUT_Q), index=False)
                # 只存每档的 5 席名单（frozenset 省内存），末尾算「刀口换档换不换人」
                picks_by_q[q] = {s: frozenset(v) for s, v in picks.items()}
            print(line, flush=True)
    df = pd.DataFrame(q_rows)
    prod_row = df[(df["q"] == Q_PROD) & (df["hold"] == H_PROD)].iloc[0]

    print("\n===== 判据 P2~P4 三张归档锚点 =====")
    ax = pd.read_csv(ARCH_EX).set_index("variant")
    ab = pd.read_csv(ARCH_BL).set_index("variant")
    am = pd.read_csv(ARCH_M4)
    am = am[am["scheme"].str.startswith("E-a") & (am["slots"] == 5)
            & am["cash_mode"].str.startswith("满仓")]
    for n, (lab, mine, key) in enumerate(
            ((REF_LAB, prod_row["L1_参照_ann_return"], "ann_return"),
             (GE1, prod_row["L1_并集1_gain_ann"], "gain_ann"),
             (GE3, prod_row["L1_并集3_gain_ann"], "gain_ann"))):
        ref = float(ax.loc[lab, key])
        check(f"P2{'abc'[n]}", f"锚点·减法腿 {lab} 对归档 {ref:+.9f}",
              abs(mine - ref) < 1e-9, f"本跑 {mine:+.9f}｜差 {abs(mine - ref):.2e}｜容差 1e-9")
    for n, (lab, mine) in enumerate(((REF_LAB, prod_row["L2_参照_excess"]),
                                     (GE3, prod_row["L2_并集3_excess"]))):
        ref = float(ab.loc[lab, "excess_univ_ew_ann"])
        check(f"P3{'ab'[n]}", f"锚点·名单腿 {lab} 对归档 {ref:+.9f}",
              abs(mine - ref) < 1e-6, f"本跑 {mine:+.9f}｜差 {abs(mine - ref):.2e}")
    ref5 = float(am["excess_univ_ew_ann"].iloc[0])
    check("P4", f"锚点·下单层 5 席对归档 {ref5:+.9f}",
          abs(float(prod_row["L3_5席_excess"]) - ref5) < 1e-6,
          f"本跑 {float(prod_row['L3_5席_excess']):+.9f}｜差 "
          f"{abs(float(prod_row['L3_5席_excess']) - ref5):.2e}｜归档命中 {len(am)} 行")

    print("\n===== 判据 P5~P7 有没有牙 =====")
    kr = df["L1_并集1_keep_ratio"].to_numpy()
    diffs = np.diff(kr)
    check("P5", "刀口有牙：保留占比随刀口严格单调上升", bool(np.all(diffs > 1e-6)),
          f"相邻档差最小 {diffs.min():.2e}｜首末 {kr[0]:.4f}→{kr[-1]:.4f}"
          f"（跨度 {kr[-1] - kr[0]:.4f}）")
    x0, x1 = df.iloc[0], df.iloc[-1]
    check("P6", "刀口接进了名单腿：首末两档 L2 年化超额必须不等",
          abs(float(x1["L2_并集3_excess"]) - float(x0["L2_并集3_excess"])) > 1e-9,
          f"q={float(x0['q']):.2f} {float(x0['L2_并集3_excess']):+.6f} vs "
          f"q={float(x1['q']):.2f} {float(x1['L2_并集3_excess']):+.6f}")
    # 下单层那一条**不判红**：探针实测这一段历史里 5 席账单对刀口完全不动，
    # 而「不动」正是本表要回答的问题之一（不是尺子坏了）。接线那一问交给 P8 的破坏性自证。
    d5 = float(x1["L3_5席_excess"]) - float(x0["L3_5席_excess"])
    readout("P6b 刀口接进下单层·只报不判",
            f"首末两档 L3 {float(x0['L3_5席_excess']):+.6f} → {float(x1['L3_5席_excess']):+.6f}"
            f"｜差 {d5:+.9f}｜八档里与生产档 L3 不同的有 "
            f"{int((np.abs(df['L3_5席_excess'].to_numpy() - prod_row['L3_5席_excess']) > 1e-9).sum())}"
            f"/{len(df)} 档")
    if not Q_ONLY:
        hd = pd.DataFrame(h_rows)
        ok7 = bool((hd["n_rebal"] == hd["n_rebal_expect"]).all()
                   and (hd["gain_days"] == hd["n_rebal"] * hd["hold"]).all())
        check("P7", "周期有牙：每档 n_rebal / gain_days 等于网格算术", ok7,
              "｜".join(f"{int(r.hold)}日 n={int(r.n_rebal)}/{int(r.n_rebal_expect)}"
                        f" 天数={int(r.gain_days)}" for r in hd.itertuples()))
        print(f"\n[P7 配套读数·不判红] 刀口 {Q_PROD} 下换持有窗："
              + "｜".join(f"{int(r.hold)}日 L1 {r.L1_并集1_gain_ann:+.4%} "
                          f"L2 {r.L2_并集3_excess:+.4%} L3 {r.L3_5席_excess:+.4%}"
                          for r in hd.itertuples()))
    if not NEGCTL:
        print(f"\n[落盘读数·不判红] 每档 5 席只数 / 调仓日数 / 20 日均额："
              + "｜".join(f"q{r['q']:.2f} {r['L3_席位数']:.2f}只/{r['L3_调仓日数']}场"
                          f"/{r['L3_均额_亿']:.2f}亿" for r in q_rows))
        # 刀口在**掏钱那层**换不换人：拿真实 picks 逐日比，不靠 L3 年化反推
        qa, qb = Q_LADDER[0], Q_LADDER[-1]
        common = sorted(set(picks_by_q[qa]) & set(picks_by_q[qb]))
        ov = np.array([len(picks_by_q[qa][s] & picks_by_q[qb][s]) for s in common])
        print(f"\n[席位重合读数·不判红] 刀口 {qa:.2f} vs {qb:.2f} 的 5 席逐日重合："
              f"平均 {ov.mean():.2f}/5 只相同｜完全换人(0 只)的场次 "
              f"{int((ov == 0).sum())}/{len(ov)}｜一刀不动的场次 {int((ov == 5).sum())}"
              f"｜共 {len(common)} 个调仓日")

    red = sorted(c[0] for c in CHECKS if not c[2])
    print(f"\n[落盘] {OUT_Q}｜{OUT_H}｜整场 {time.time() - t0:.0f}s")
    print(f"===== 汇总：{len(CHECKS) - len(red)}/{len(CHECKS)} 通过 =====", flush=True)
    for cid, label, _ok, d in CHECKS:
        if not _ok:
            print(f"  ❌ [{cid}] {label}｜{d}", flush=True)
    print(f"[RED_IDS] {'、'.join(red) if red else '无'}", flush=True)
    sys.exit(1 if red else 0)


if __name__ == "__main__":
    main()
