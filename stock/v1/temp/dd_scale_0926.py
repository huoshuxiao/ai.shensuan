# -*- coding: utf-8 -*-
"""P0-1 回撤秤（09-26 夜）：把「回撤」从一个标量变成一条能归因的时间轴。

为什么要做：全链条上回撤只有一个数 —— `run_ashare_portfolio_eval._stats` 里的
`max_drawdown = (nav/nav.cummax()-1).min()`，一个标量。**不知道最深那次发生在哪一段、
跌了多少天、几天回本、谷底那天 5 席装的是哪 5 只**。而 ㉗/㉘ 两条结论
（「留现金没换来安全」「配额把回撤从 −52.5% 修到 −49.1%」）全都只是这个标量在动
⇒ 任何降回撤的改动做完都无法判优。

这台秤跑三条腿，吃同一份面板、同一套回放时序、同一批 570 个信号日：

  锚点腿（`A`）        **全局前 50 名单 + 无名单独立闸** —— 刻意复现 ㉗ 那一批归档
                       （`order_layer_fullinvest_0925b.py` 的「现状·全局前50｜5席·满仓」）。
                       它的 `max_drawdown` 必须等于表里那一格 −53.5676%。
                       ⇒ 这条腿是**反证式自检**：对不上就说明本脚本的回放时序/成本/权重
                       实现有问题，那么另外两条腿的新读数一律作废，不许引用。
  对照腿（`B`）        板块配额名单，但把独立闸的刀口拨到 2.0（分位取值域是 (0,1]
                       ⇒ 永不命中，等价于生产里 `STOCK_BUY_EXTRA_RULES` 传空串的关闭路径）。
  生产腿（`C`）        `screen_on_date()` 出的名单（板块配额 + 共识闸≥3 + 名单独立闸
                       **开不开由这一场自己的 `ASHARE_BUY_EXTRA_RULES` 决定**）
                       → `order_candidates(5 席, 同行业≤1, 同板块≤3)` → 满仓给权。

为什么非要三条：A→C 一步跳会把「换名单形状」和「上一道新闸」记成同一笔账（㉘ 被用户挑
出来的正是这种口径分叉）。拆成 A→B（只换形状）、B→C（只上闸）才归得了因。
⚠️ 09-27 ㊹ 之后生产默认**关**着那道闸（`config.py:153` 默认空串）⇒ 本场 B 腿与 C 腿
**逐场相同才是对的**（两条腿都是「闸关」），C−B 那一列读 0 是预期、不是坏了。判据因此
跟着档位走：闸开时两腿相同 = 关闭腿失效 ⇒ 退出；闸关时两腿不同 = 有东西绕过开关在动 ⇒ 退出。

产出五张表（全部落在本目录，不碰 `stock/v1/data/` 任何归档）：
  nav_daily.csv          日频：三条腿各自的水下深度 + 当天 5 席持仓
  dd_compare.csv         三条腿一张表：年化/超额/波动/最深回撤/换手/凑不满席位的占比
  dd_episodes.csv        最深若干段水下区间：峰/谷/恢复三日、深度、跌与回的历时、谷底持仓
  dd_yearly.csv          分年度：年内最大回撤（段按谷底归属）+ 当年超额
  dd_conc.csv            谷底那几天的持仓集中度（单板块只数、行业缺口只数）

边界（照旧，念给读数的人）：历史回放没有当日收盘快照 ⇒ 生产名单额外那三道**执行层**闸
（当日真成交、收盘已贴涨停不追、ST）在三条腿里都没跑；行业映射用今天这份表回溯 2015 年。
所以这一台秤量的是「信号层的回撤形状」，不是「实盘账户的回撤」。
"""

import os
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "stock/v1/src"))
import _bootstrap  # noqa: F402,E402

OUT = os.path.join(ROOT, "stock/v1/temp/tmp_dd_scale_0926")
os.makedirs(OUT, exist_ok=True)

from config import (ASHARE_BUY_EXTRA_RULES, ASHARE_BUY_MIN_HITS, ASHARE_BUY_TOP_N,
                    ASHARE_LIST_QUOTA,
                    ASHARE_ORDER_MAX_PER_BOARD, ASHARE_ORDER_MAX_PER_INDUSTRY,
                    ASHARE_ORDER_TOP_N, ASHARE_PORT_COST_ONE_WAY, ASHARE_PORT_HOLD,
                    ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED, ASHARE_PORT_START,
                    ASHARE_SCREEN_QUANTILE, ASHARE_TRADABLE_GATE)
from ashare_screen import (BUY_EXPR, BUY_EXTRA_EXPRS, INDUSTRY_UNKNOWN,
                           RULE_EXPRS, active_buy_extra_rules, board_of, build_matrices,
                           factor_matrices, gate_desc, load_industry_map, load_panel,
                           order_candidates, screen_on_date, tradable_mask)
import run_ashare_portfolio_eval as R

# 锚点腿必须复现的那一格（㉗ M3 补测，全局前 50｜5 席｜满仓｜双边 15bp｜hold 5）
ANCHOR_REF_DD = -0.535676
ANCHOR_REF_ANN = 0.276649
ANCHOR_TOL_DD = 1e-4          # 回撤是小数量级敏感量，1e-4 已是宽容差
TOP_N = ASHARE_ORDER_TOP_N    # 下单层席位数（生产 = 5）
TRADING_DAYS = 252


def replay_picks(days, mtx, picks, hold, cost, off):
    """信号日 i → 建仓日 j=i+1 → 持仓段 [j+1, j+1+hold)，成本记在 j；每席 1/实际只数（满仓）

    与 ㉗㉘ 两个驱动同一个 `replay(..., renorm=True)` 一字同构，唯一区别是这里
    额外把「每个持仓段的 5 只票」记下来 —— 回撤归因要的就是这一列。
    `off` 是调仓网格的起点：本脚本为了喂饱 `screen_on_date` 的「昨收」多留了几天历史，
    网格必须从 ASHARE_PORT_START 那一格起算，才与 ㉗ 归档同一批信号日。
    """
    ret = mtx["ret_open0"].to_numpy(dtype="float64")
    col_of = {c: i for i, c in enumerate(mtx["close"].columns)}
    w = np.zeros((len(days), mtx["close"].shape[1]))
    c = np.zeros(len(days))
    held = {}
    prev = None
    for i in range(off, len(days) - hold - 1, hold):
        s = days[i]
        if s not in picks:
            continue
        names, j = picks[s], i + 1
        lo, hi = j + 1, min(j + 1 + hold, len(days))
        w[lo:hi] = 0.0
        wt = 1.0 / len(names)
        for nm in names:
            w[lo:hi, col_of[nm]] = wt
        c[j] += 2 * cost * (1.0 if prev is None
                            else 1.0 - len(set(prev) & set(names)) / TOP_N)
        held[lo] = (hi, names)
        prev = names
    port = pd.Series((w * ret).sum(axis=1), index=days) - c
    # 持仓段 → 该段每一天属于哪一批票（回撤谷底那天要问的就是它）
    day_hold = {}
    for lo, (hi, names) in held.items():
        for d in days[lo:hi]:
            day_hold[d] = names
    return port, day_hold


def nav_of(port):
    return (1.0 + port.dropna()).cumprod()


def dd_of(nav):
    return nav / nav.cummax() - 1.0


def episodes(dd, day_hold, top=6, min_depth=0.05):
    """把水下曲线切成一段段：峰 → 谷 → 恢复，逐段报深度与历时

    判据不恒真的地方：「未恢复」必须显式读出来（restore=未恢复），否则最后一段会被
    悄悄当成已经爬完，最深回撤的恢复时长就凭空多出一个假数。
    """
    below = dd < 0
    out, d = [], below.values
    i, n = 0, len(d)
    while i < n:
        if not d[i]:
            i += 1
            continue
        j = i
        while j < n and d[j]:
            j += 1
        seg = dd.iloc[i:j]
        k = int(np.argmin(seg.values))
        trough_dt, depth = dd.index[i + k], float(seg.min())
        peak_dt = _peak_before(dd, i + k)
        if depth > -min_depth:
            i = j
            continue
        restored = j < n
        out.append({
            "峰日": peak_dt.date(), "谷日": trough_dt.date(),
            "恢复日": (dd.index[j].date() if restored else "未恢复"),
            "深度": depth,
            "下跌交易日": int(np.searchsorted(dd.index, trough_dt)
                             - np.searchsorted(dd.index, peak_dt)),
            "恢复交易日": (int(np.searchsorted(dd.index, dd.index[j])
                              - np.searchsorted(dd.index, trough_dt)) if restored else -1),
            "水下总交易日": int(j - i),
            "谷底持仓": " ".join(day_hold.get(trough_dt, ["—"])),
            "谷底板块": "/".join(sorted({board_of(c) for c in day_hold.get(trough_dt, [])})) or "—",
        })
        i = j
    return (pd.DataFrame(out).sort_values("深度").head(top).reset_index(drop=True))


def _peak_before(dd, pos):
    """谷底之前最近一次「净值新高」的那天 = 这段水下的起点"""
    run = dd.index[0]
    for t in range(pos, -1, -1):
        if dd.iloc[t] >= 0:
            return dd.index[t]
        run = dd.index[t]
    return run


def yearly_dd(dd, port, bench):
    """分年两张读数，**语义不同别混读**：
      年内最低位置(相对历史高点)  那一年里净值离**历史最高点**最低跌到几成（跨年不重置，
                                 所以 2015 挖的坑会把 2016~2019 那几年一起压在同一档上）
      年内自身峰谷(逐年重置)      只拿**本年**的净值自峰值算回撤（年内跌了多深，跨年清零）
    """
    rows = []
    for y, seg in dd.groupby(dd.index.year):
        j = pd.concat([port, bench], axis=1, join="inner").fillna(0.0)
        j = j[(j.index.year == y)]
        ex = float((j.iloc[:, 0] - j.iloc[:, 1]).mean() * TRADING_DAYS)
        nav_y = (1.0 + j.iloc[:, 0]).cumprod()
        rows.append({"年": int(y), "年内最低位置(相对历史高点)": float(seg.min()),
                     "年内自身峰谷(逐年重置)": float((nav_y / nav_y.cummax() - 1.0).min()),
                     "年末仍水下": float(dd[dd.index.year == y].iloc[-1]),
                     "当年超额(vs 域等权)": ex})
    return pd.DataFrame(rows)


def main():
    t0 = time.time()
    print(f"[口径] 涨停闸={ASHARE_TRADABLE_GATE}（{gate_desc()}）｜观察名单 {ASHARE_BUY_TOP_N} 只"
          f"｜下单 {TOP_N} 席·同行业≤{ASHARE_ORDER_MAX_PER_INDUSTRY}"
          f"·同板块≤{ASHARE_ORDER_MAX_PER_BOARD}｜满仓给权｜hold {ASHARE_PORT_HOLD} 日"
          f"｜单边费率 {ASHARE_PORT_COST_ONE_WAY}｜共识闸≥{ASHARE_BUY_MIN_HITS}"
          f"｜域分位 {ASHARE_SCREEN_QUANTILE}｜名单形状 {ASHARE_LIST_QUOTA}")
    wide, _bench_open = load_panel()
    mtx = build_matrices(wide)
    exprs = sorted(set(RULE_EXPRS) | {BUY_EXPR} | set(BUY_EXTRA_EXPRS))
    fms = factor_matrices(exprs, mtx)
    # `screen_on_date` 要拿「昨收」比，所以截断必须**多留 ASHARE_PORT_START 前的一段
    # 历史**当参照。留够之后，调仓网格从 `off` 那一格起算 —— 于是两条腿的信号日集合
    # 与 ㉗ 归档**逐字同一批日期**（它的 days[0] == 本脚本的 days[off]）：
    #   信号日 = days[off], days[off+hold], …   （前 off 天只当「昨天」用，不出名单）
    # 两条腿共用同一个 `days`，所以净值/回撤是同一批日历日上的对照。
    cut = np.searchsorted(mtx["ret_open"].index, pd.Timestamp(ASHARE_PORT_START))
    start = max(cut - ASHARE_PORT_HOLD, 1)
    off = cut - start
    days_all = mtx["ret_open"].index
    mtx = {k: v.loc[days_all[start:]] for k, v in mtx.items()}
    fms = {e: m.loc[m.index[start:]] for e, m in fms.items()}
    days = mtx["ret_open"].index
    universe = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
                & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    bench = mtx["ret_open"].where(universe).mean(axis=1)
    # 基准与两条腿一样，从 ASHARE_PORT_START 起算（多留的那几天只服务「昨收」）
    bench = bench[bench.index >= pd.Timestamp(ASHARE_PORT_START)]
    gate = fms[RULE_EXPRS[0]]
    ind, ind_meta = load_industry_map()
    ind_map = ind.to_dict() if ind_meta.get("loaded") else {}
    print(f"[准备] {time.time() - t0:.0f}s｜{len(days)} 个交易日"
          f"｜行业表 loaded={ind_meta.get('loaded')}")

    # ---------- 复现 570 个调仓日的两套名单 ----------
    pool_df, hit_df = R.exclusion_hits(mtx, days[off:], fms, ASHARE_PORT_HOLD,
                                       ASHARE_SCREEN_QUANTILE)
    allow_union = R.exclusion_mask(("并集", "x", tuple(range(len(R.VOLUME_RULES))),
                                    ASHARE_BUY_MIN_HITS), pool_df, hit_df)
    picks_anchor, picks_quota, picks_prod = {}, {}, {}
    t1 = time.time()
    for i in range(off, len(days) - ASHARE_PORT_HOLD - 1, ASHARE_PORT_HOLD):
        s, d1 = days[i], days[i + 1]
        # —— 锚点腿：全局前 50（不套配额、不上独立闸），复现 ㉗ 那批归档
        ok, _ = tradable_mask(s, d1, fms[BUY_EXPR], mtx)
        cand = fms[BUY_EXPR].loc[s].where(ok & allow_union.loc[s]).dropna().sort_values()
        if len(cand) >= ASHARE_BUY_TOP_N:
            l50 = list(cand.index[:ASHARE_BUY_TOP_N])
            buy = pd.DataFrame({"rank": range(1, len(l50) + 1),
                                "板块": [board_of(c) for c in l50]}, index=l50)
            od, _st = order_candidates(buy, ind_map, TOP_N,
                                       ASHARE_ORDER_MAX_PER_INDUSTRY,
                                       ASHARE_ORDER_MAX_PER_BOARD)
            picks_anchor[s] = list(od.index)
        # —— 中间腿：配额名单，但**独立闸刀口拨到 2.0**（分位取值域是 (0,1] ⇒ 永不命中
        #    = 生产里 STOCK_BUY_EXTRA_RULES 空串那条关闭路径的等价物）。
        #    没有这条腿，A→C 之差就把「换名单形状」和「上新闸」混成一笔账（㉘ 被挑出来的
        #    正是这种口径分叉），C 那一档的代价就归不了因。
        r_q = screen_on_date(s, d1, mtx, fms, gate, ASHARE_SCREEN_QUANTILE,
                             top_n=ASHARE_BUY_TOP_N, st_codes=None,
                             buy_min_hits=ASHARE_BUY_MIN_HITS,
                             buy_extra_quantile=2.0)
        if len(r_q["buy"]) >= TOP_N:
            od, _st = order_candidates(r_q["buy"], ind_map, TOP_N,
                                       ASHARE_ORDER_MAX_PER_INDUSTRY,
                                       ASHARE_ORDER_MAX_PER_BOARD)
            picks_quota[s] = list(od.index)
        # —— 生产腿：判据单点 screen_on_date 本身（配额 + 共识闸 + 名单独立闸@默认 0.80）
        r = screen_on_date(s, d1, mtx, fms, gate, ASHARE_SCREEN_QUANTILE,
                           top_n=ASHARE_BUY_TOP_N, st_codes=None,
                           buy_min_hits=ASHARE_BUY_MIN_HITS)
        buy = r["buy"]
        if len(buy) >= TOP_N:
            od, _st = order_candidates(buy, ind_map, TOP_N,
                                       ASHARE_ORDER_MAX_PER_INDUSTRY,
                                       ASHARE_ORDER_MAX_PER_BOARD)
            picks_prod[s] = list(od.index)
    print(f"[名单] 复现完成：锚点腿 {len(picks_anchor)} 场、配额无闸腿 {len(picks_quota)} 场、"
          f"生产腿 {len(picks_prod)} 场　{time.time() - t1:.0f}s")
    # 关闭腿是否真的「关」：判据跟着**这一场的档位**走，不跟今天的默认走。
    # 闸开着（㊶ 当时）⇒ B≠C 才说明刀口 2.0 那一拨等价于关闭、而生产那一拨在咬；
    # 闸关着（㊹ 之后的默认）⇒ B==C 才是对的，两腿都是关闭路径。
    gate_on = bool(active_buy_extra_rules())
    if gate_on and picks_quota == picks_prod:
        raise SystemExit("⇒ 本场独立闸**启用**（`ASHARE_BUY_EXTRA_RULES`="
                         f"{ASHARE_BUY_EXTRA_RULES!r}）却两腿名单逐场相同"
                         " ⇒ 关闭腿的 2.0 那一拨没等价于关，或生产腿根本没闸在咬，"
                         "下面的 C−B 之差没有意义，别看")
    if (not gate_on) and picks_quota != picks_prod:
        raise SystemExit("⇒ 本场独立闸**关闭**（默认空串）两腿却不同"
                         " ⇒ 有判据绕过了这个开关，B/C 不可比")
    print("⇒ " + ("闸开：B≠C 如期（C−B 就是这道闸的钱）" if gate_on
                  else "闸关（㊹ 之后的默认）：B==C 如期，C−B 那一列读 0 是预期"))

    # ---------- 锚点腿必须复现归档那一格 ----------
    # 两条腿都砍掉那 `off` 天「只当昨天用」的前置行，日收益序列的起点/长度才与 ㉗ 一致
    port_a, hold_a = replay_picks(days, mtx, picks_anchor, ASHARE_PORT_HOLD,
                                  ASHARE_PORT_COST_ONE_WAY, off)
    port_a = port_a.iloc[off:]
    nav_a = nav_of(port_a)
    dd_a = dd_of(nav_a)
    got_dd, got_ann = float(dd_a.min()), float(port_a.dropna().mean() * TRADING_DAYS)
    print(f"\n[自检·锚点腿] 本脚本 max_drawdown={got_dd:.6f}　年化={got_ann:.6f}"
          f"　归档={ANCHOR_REF_DD:.6f}/{ANCHOR_REF_ANN:.6f}")
    if abs(got_dd - ANCHOR_REF_DD) > ANCHOR_TOL_DD:
        raise SystemExit(f"⇒ 锚点腿复现失败（回撤差 {got_dd - ANCHOR_REF_DD:+.6f}）"
                         f"⇒ 本脚本的回放实现与归档不同形，生产腿读数一律作废")
    print("⇒ 通过：回放时序/成本/权重与 ㉗ 归档同形，下面的生产腿读数有资格被引用")

    # ---------- 三条腿同表：A→B 只换名单形状，B→C 只上新闸 ----------
    def leg_row(label, picks):
        port, hold = replay_picks(days, mtx, picks, ASHARE_PORT_HOLD,
                                  ASHARE_PORT_COST_ONE_WAY, off)
        port = port.iloc[off:]
        dd = dd_of(nav_of(port))
        p = port.dropna()
        b = bench.reindex(p.index).fillna(0.0)
        memb = [set(v) for v in picks.values()]
        turn = float(np.mean([1.0] + [1.0 - len(memb[i - 1] & memb[i]) / TOP_N
                                      for i in range(1, len(memb))]))
        return {"腿": label,
                "年化净收益": float(p.mean() * TRADING_DAYS),
                "超额(vs 域等权)": float((p - b).mean() * TRADING_DAYS),
                "波动": float(p.std() * np.sqrt(TRADING_DAYS)),
                "最深回撤": float(dd.min()),
                "单程换手": turn,
                "凑不满5席场次占比": float(np.mean([len(v) < TOP_N for v in picks.values()]))
                }, port, hold, dd

    row_a, _pa, _ha, _da = leg_row("A 全局前50（复现归档）", picks_anchor)
    row_b, port_b, hold_b, dd_b = leg_row("B 配额名单·无独立闸", picks_quota)
    # C 这一行的标签跟着本场档位写：闸关着还标「+独立闸@0.80」就是拿旧口径的名字
    # 挂新读数（㊹ 之后默认是关的）
    row_c, port_p, hold_p, dd_p = leg_row(
        "C 配额名单+独立闸@0.80（生产）" if gate_on
        else "C 配额名单·独立闸本场关闭（生产）", picks_prod)
    cmp = pd.DataFrame([row_a, row_b, row_c])
    nav_p = nav_of(port_p)

    # 逐年的 C−B：全样本之差可能全是某一年撑出来的（㊲ 的 +16.95pp 全在 2015 一年
    # 就是前车之鉴），所以先按年拆开看，再决定这句话能不能说成「年均」
    def year_excess(port):
        p = port.dropna()
        j = pd.concat([p, bench.reindex(p.index).fillna(0.0)], axis=1)
        return (j.iloc[:, 0] - j.iloc[:, 1]).groupby(j.index.year).mean() * TRADING_DAYS

    def _dd_min_by_year(port):
        """**逐年重置**的年内峰谷回撤（那年自己跌了多深），不是相对历史高点的位置"""
        p = port.dropna()
        nav = (1.0 + p).cumprod()
        peak = nav.groupby(nav.index.year).cummax()      # 峰值每年元旦清零
        return (nav / peak - 1.0).groupby(nav.index.year).min()

    ex_a, ex_b, ex_c = year_excess(port_a), year_excess(port_b), year_excess(port_p)
    dd_a_s, dd_b_s, dd_c_s = (_dd_min_by_year(x) for x in (port_a, port_b, port_p))
    yrx = pd.DataFrame({"超额_A": ex_a, "超额_B": ex_b, "超额_C": ex_c,
                        "C−B": ex_c - ex_b, "B−A": ex_b - ex_a,
                        "年内自身峰谷_A": dd_a_s, "年内自身峰谷_B": dd_b_s,
                        "年内自身峰谷_C": dd_c_s}).fillna(0.0)
    yrx.loc["逐年平均"] = yrx.mean()
    eps = episodes(dd_p, hold_p)
    yr = yearly_dd(dd_p, port_p, bench)
    # 三条腿的下单席位逐场并排落盘：换一道闸到底换掉几席，要能一只一只数出来
    # 三条腿必须吃**同一批信号日**，否则表里的差含「窗口不一样」的水分
    if not (picks_anchor.keys() == picks_quota.keys() == picks_prod.keys()):
        raise SystemExit(f"⇒ 三条腿的调仓日集合不齐"
                         f"（A {len(picks_anchor)} / B {len(picks_quota)} / C {len(picks_prod)}）"
                         f"⇒ 对照表不是同窗口之差，别看")
    seats = pd.DataFrame({f"{k}_{j}": [v[j - 1] if len(v) >= j else "" for v in p.values()]
                          for k, p in (("A", picks_anchor), ("B", picks_quota),
                                       ("C", picks_prod)) for j in range(1, TOP_N + 1)},
                         index=pd.DatetimeIndex(sorted(picks_prod), name="信号日"))
    nav = pd.DataFrame({
        "日收益_生产C": port_p, "净值_生产C": nav_p, "水下_生产C": dd_p,
        "水下_配额无闸B": dd_b.reindex(nav_p.index),
        "水下_锚点A": dd_a.reindex(nav_p.index),
        "基准_域等权日收益": bench.reindex(port_p.index),
        "当日5席": [" ".join(hold_p.get(d, [])) for d in port_p.index],
    })
    nav["基准净值"] = (1.0 + bench.reindex(port_p.index).fillna(0.0)).cumprod()
    nav["基准水下"] = nav["基准净值"] / nav["基准净值"].cummax() - 1.0
    conc = pd.DataFrame([{
        "日": d.date(), "只数": len(v),
        "单板块最多": int(pd.Series([board_of(c) for c in v]).value_counts().max() or 0),
        "行业未知只数": sum(1 for c in v if ind_map.get(c, INDUSTRY_UNKNOWN) == INDUSTRY_UNKNOWN),
        "持仓": " ".join(v)} for d, v in sorted(hold_p.items())])

    nav.to_csv(os.path.join(OUT, "nav_daily.csv"), index_label="日期")
    cmp.to_csv(os.path.join(OUT, "dd_compare.csv"), index=False)
    yrx.to_csv(os.path.join(OUT, "dd_yearly_threelegs.csv"), index_label="年")
    seats.to_csv(os.path.join(OUT, "dd_seats.csv"), index_label="信号日")
    eps.to_csv(os.path.join(OUT, "dd_episodes.csv"), index=False)
    yr.to_csv(os.path.join(OUT, "dd_yearly.csv"), index=False)
    conc.to_csv(os.path.join(OUT, "dd_conc.csv"), index=False)

    print("\n################ 三条腿对照（同一批 570 个信号日、同一套时序与费率）"
          "################")
    with pd.option_context("display.width", 200):
        print(cmp.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))
    print("读法：A→B 只换**名单形状**（全局前50 → 板块配额），B→C 只上**名单独立闸**"
          "⇒ 两笔差各自归因，不混账")
    assert abs(cmp.loc[0, "最深回撤"] - got_dd) < 1e-12, "⇒ 同一条腿两次算法不一致，实现有问题"

    n_diff = int((seats[[f"B_{i}" for i in range(1, 6)]].to_numpy()
                  != seats[[f"C_{i}" for i in range(1, 6)]].to_numpy()).any(axis=1).sum())
    print(f"\n独立闸在 5 席这一层换掉了几只票：{n_diff}/{len(seats)} 个调仓日的篮子不同")
    print("\n—— 逐年的三条腿超额（先判「全样本之差是不是某一年撑出来的」）——")
    with pd.option_context("display.width", 220):
        print(yrx.to_string(float_format=lambda v: f"{v:+.4f}"))
    # 集中度第一笔账：满仓给权下每席固定 1/5，所以「出现在几成调仓日」× 20% 就是
    # 常年压在一只票上的资金比例 —— 这是 P1-5（单票/单板块的尾部账）的输入
    freq = Counter(x for v in picks_prod.values() for x in v)
    shr = pd.DataFrame([{"票": k, "在仓的调仓日占比": c / len(picks_prod),
                         "常年占用的资金": c / len(picks_prod) / TOP_N}
                        for k, c in freq.most_common(10)])
    shr.to_csv(os.path.join(OUT, "dd_seat_share.csv"), index=False)
    print(f"\n—— 5 席这一层常驻的那几只（570 个调仓日里在仓的天数占比；满仓每席 1/{TOP_N}）——")
    print(shr.to_string(index=False, float_format=lambda v: f"{v:.1%}"))
    print("\n—— 最深的水下区间（谷底那天装的什么票，第一次看得见）——")
    with pd.option_context("display.width", 220, "display.max_colwidth", 46):
        print(eps.to_string(index=False))
        print("\n—— 分年度 ——")
        print(yr.to_string(index=False))
    print(f"\n[输出] {OUT}/nav_daily.csv｜dd_compare.csv｜dd_yearly_threelegs.csv"
          f"｜dd_seats.csv｜dd_episodes.csv｜dd_yearly.csv｜dd_conc.csv"
          f"　总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
