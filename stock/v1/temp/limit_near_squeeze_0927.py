# -*- coding: utf-8 -*-
"""P0-2 涨停裕度专项 · 第二问：这道闸在**真名单形状**里到底有没有牙（全历史 570 场）

第一问（`limit_near_grid_0927.py`）量的是「假想把被挡的票全买下来」的账：96.24% 其实买得进、
五天到手 −2.45%、减对照线后超额 −2.23%、13 年全为负。那笔账只说得上「这些票不该买」，
说不上「生产为这道闸付了 2.23%」—— 因为生产每场只从 6074 只里买 **5 席**，
而被挡的票天生是放量那一头，排序轴恰恰是「安静度 = SMA($volume,20) 升序」。

本脚本把那一步补完：对每个调仓场次，把**同日侧**那道追涨停闸的裕度挪开（k→9.9 = 谁都挡不着），
再走一遍**同一套生产函数**（闸门 → 量能剔除 → 共识阈值 → 名单独立闸 → 板块配额 → 5 席分散约束），
量四件事：
    ① 被挡那批里有几只真的挤得进前 50（㉓ 两天 0/29、0/31 的全历史版）
    ② 挤进来就必然有人被换掉 —— 观察名单层换几只、下单 5 席层换几只，570 场里有几场有换
    ③ 换进来 / 换出去的那批后来五天到手多少（减全可投域对照线）⇒ 这笔钱生产**实际付不付**
    ④ 若一场都不换：被挡票在轴上排到第几名（名次分布）⇒ 空挡是结构性的还是这两天运气

口径（判据一律叫生产原函数，一条都不在这里重抄）：
    候选  C = 闸门 ∧ n_hit < ASHARE_BUY_MIN_HITS ∧ 当日有成交 ∧ 收盘涨幅 < 限幅(板块)×k ∧ i ∉ ST
    轴    q_i = SMA($volume,20)_i(s)   升序；名单 = apply_board_quota(head(q|C), 50)
    下单  = order_candidates(名单, 行业映射, 5)（同一板块 / 同一行业上限那两条约束照走）
    五天到手 = O_{d1+HOLD}/O_d1 - 1（d1 = 次日开盘买进，与环 2 回放同一笔钱）
    超额     = 五天到手 - s 那天全可投域五天到手中位（不筛涨幅那条对照线）
调仓网格 = days[0::HOLD]，与环 2 回放 `run_signal` 同一把尺子 ⇒ 场次数必须 == 归档 n_rebal=570，
不等就直接退出（网格走样 ⇒ 场次级的数全不可引）。
历史场次没有当日快照 ⇒ ST 闸一律不开（与 `buy_candidates(st_codes=None)` 默认一致）；
锚点那天（有 spot_*.csv 的最后一场）单独复算并与归档 meta 对账。
"""
import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
SRC = os.path.join(ROOT, "stock", "v1", "src")
OUT = os.path.join(HERE, "tmp_limit_near_0927" + os.environ.get("LN_OUT_TAG", ""))
os.makedirs(OUT, exist_ok=True)

sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402  (裸模块名导入的引导：config / ashare_screen 都靠它)
from config import (ASHARE_BOARD_LIMIT_UP, ASHARE_BUY_MIN_HITS, ASHARE_BUY_TOP_N,
                   ASHARE_LIMIT_NEAR, ASHARE_ORDER_TOP_N, ASHARE_PORT_HOLD,
                   ASHARE_PORT_LIMIT_UP, ASHARE_PORT_MIN_AMOUNT,
                   ASHARE_PORT_MIN_LISTED, ASHARE_PORT_START, ASHARE_SCREEN_QUANTILE,
                   ASHARE_SIGNAL_DIR, RESULTS_DIR)
import ashare_screen as scr
from ashare_screen import (BUY_EXPR, VOLUME_RULES, apply_board_quota, board_of,
                           buy_extra_block, factor_matrices, gate_vector, guard_ret,
                           load_industry_map, load_panel, tradable_mask, volume_exclusion)

K_PROD = ASHARE_LIMIT_NEAR        # 生产档 = 有闸
K_OFF = 9.9                       # 拆闸 = 把裕度抬到任何涨幅都够不着（只挪刀口，不改判据形状）
HOLD = ASHARE_PORT_HOLD
NEAR_SEALED = 0.99                # 与 ④ 审计同值：判「真封死」的贴板容差


def board_limit(cols):
    """每列的板块限幅（主板 0.10 / 双创 0.20 / 北交 0.30，主板未配表回落旧常数）

    与 `limit_up_of` 同一张表；本脚本要的是**未乘裕度**的那个数，因为被拨动的就是裕度 k。
    dtype 一律 float64：与 `gate_vector` 同 dtype，自检③ 才敢要求**逐位相等**。
    """
    return pd.Series([ASHARE_BOARD_LIMIT_UP.get(board_of(c)) or ASHARE_PORT_LIMIT_UP
                      for c in cols], index=cols)


def funnel(s, mtx, rm, lim_base, k, st_codes=None):
    """复现 `buy_candidates` 的漏斗，只把涨停裕度 k 换成参数，其余一个判据不重抄

    返回 dict(v/chg/step1/traded/chase/cand/head/order)；chase = 被这道闸挡掉的集合，
    head = 按生产名单形状（板块配额 + 回填）排出来的前 50。
    """
    cl, raw, vol = mtx["close"], mtx["raw_price"], mtx["volume"]
    cols = cl.columns
    ok, _ = tradable_mask(s, None, rm[VOLUME_RULES[0][2]], mtx)
    pool = pd.Series(ok, index=cols)
    _keep, detail = volume_exclusion(rm, s, pool, ASHARE_SCREEN_QUANTILE)
    n_hit = detail["n_hit"].reindex(pool.index).fillna(0)
    keep_buy = pool & (n_hit < ASHARE_BUY_MIN_HITS)
    blocked, _fired = buy_extra_block(rm, s, pool, scr.ASHARE_BUY_EXTRA_QUANTILE)
    keep_buy = keep_buy & ~blocked

    v = rm[BUY_EXPR].loc[s].reindex(cols).astype("float32")
    step1 = pool & keep_buy & v.notna()
    traded = step1 & cl.loc[s].notna() & raw.loc[s].notna() & vol.loc[s].gt(0)
    prev = cl.index[cl.index.get_loc(s) - 1]
    chg = guard_ret(cl.loc[s] / cl.loc[prev] - 1.0, raw.loc[s] / raw.loc[prev] - 1.0)
    chase = traded & chg.ge(lim_base * k)
    is_st = pd.Series(cols.isin(st_codes or set()), index=cols)
    cand = traded & ~chase & ~(is_st if st_codes else pd.Series(False, index=cols))
    order = v.where(cand).dropna().sort_values()
    head, lst = apply_board_quota(list(order.index), ASHARE_BUY_TOP_N)
    return dict(v=v, chg=chg, step1=step1, traded=traded, chase=chase, cand=cand,
                head=head, order=order, lst=lst)


def buy_frame(head):
    """把前 50 名装成 `order_candidates` 认的那张表（rank 升序 + 板块）"""
    return pd.DataFrame({"rank": np.arange(1, len(head) + 1),
                         "板块": [board_of(c) for c in head]},
                        index=pd.Index(head, name="code"))


def main():
    t0 = time.time()
    print(f"[生产档] k={K_PROD}　观察名单 {ASHARE_BUY_TOP_N} 只　下单 {ASHARE_ORDER_TOP_N} 席　"
          f"持有 {HOLD} 日　共识阈值 {ASHARE_BUY_MIN_HITS}　域分位 {ASHARE_SCREEN_QUANTILE}　"
          f"名单独立闸 {scr.ASHARE_BUY_EXTRA_RULES!r}（空串 = ㊹ 已退闸）　"
          f"涨停闸档 {scr.ASHARE_TRADABLE_GATE}")

    wide, _b = load_panel()
    mtx = scr.build_matrices(wide)
    del wide
    cols = mtx["close"].columns
    lim_base = board_limit(cols)
    exprs = {e for _k, _n, e, _d in scr.active_rules()} | {BUY_EXPR, VOLUME_RULES[0][2]}
    print(f"[求值] {len(exprs)} 条表达式 × 面板 {mtx['close'].shape} ...", flush=True)
    rm = factor_matrices(sorted(exprs), mtx)
    print(f"[求值] 完成 {time.time() - t0:.0f}s", flush=True)

    days = mtx["close"].index
    days = days[days >= pd.Timestamp(ASHARE_PORT_START)]
    ss = list(days[:-HOLD - 1][::HOLD])
    n_arch = int(pd.read_csv(os.path.join(RESULTS_DIR, "ashare_portfolio_eval.csv"))["n_rebal"].iloc[0])
    narrowed = bool(os.environ.get("STOCK_PORT_START") or os.environ.get("STOCK_PORT_END"))
    print(f"[网格] {len(ss)} 场 {ss[0]:%Y-%m-%d}~{ss[-1]:%Y-%m-%d}　环 2 归档 n_rebal={n_arch}　"
          f"⇒ {'⚠️ 窗口被收窄过，跳过对表（只算冒烟）' if narrowed else ('✅ 同一把尺子' if len(ss) == n_arch else '⚠️ 走样')}")
    if not narrowed and len(ss) != n_arch:
        raise SystemExit("[作废] 调仓网格与环 2 归档不一致 ⇒ 场次级读数一律不可引")

    # 次日可执行性 + 五天到手 + 对照线（一次性算全帧，切片复用）
    cl, op, hi, lo, vol = (mtx["close"], mtx["open"], mtx["high"], mtx["low"], mtx["volume"])
    ret5 = (op.shift(-1 - HOLD) / op.shift(-1) - 1).astype("float32")
    close1 = (cl.shift(-1) / cl - 1).astype("float32")
    sealed = (((hi.shift(-1) - lo.shift(-1)).abs() / cl.shift(-1)) <= 2e-6) \
        & (((op.shift(-1) - cl.shift(-1)).abs() / cl.shift(-1)) <= 2e-6)
    nofill = (vol.shift(-1).fillna(0) <= 0) | cl.shift(-1).isna()
    cannot = sealed & (close1 >= lim_base * NEAR_SEALED)
    univ = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
            & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    ctl = ret5.where(univ).median(axis=1)
    ind_map, ind_meta = load_industry_map()
    print(f"[行业映射] loaded={ind_meta['loaded']} n={ind_meta['n']}　"
          f"（5 席分散约束的输入，与生产同一份）", flush=True)

    # 自检③：我只挪了裕度、没换判据形状 —— k 回到生产档时阈值必须与 gate_vector 逐位相等
    if scr.ASHARE_TRADABLE_GATE != "board":
        raise SystemExit(f"[作废] 本脚本复现的是 board 档阈值（限幅×k）；进程档 = "
                         f"{scr.ASHARE_TRADABLE_GATE}，dated 档还要乘「那天生效的限幅」⇒ 不复现，"
                         f"要量请先 STOCK_TRADABLE_GATE=board")
    gv = gate_vector(cols, ss[-1])
    d3 = float((lim_base * K_PROD - gv).abs().max())
    print(f"\n[自检③] lim_base×{K_PROD} vs 生产 gate_vector 逐列最大差 = {d3:.1e}"
          f"（不为 0 = 本脚本的阈值与生产不是同一条，后面全部作废）")
    if d3 > 0:
        raise SystemExit("[作废] 阈值口径与生产不一致")

    rows, mv = [], []
    for i, s in enumerate(ss):
        f_on = funnel(s, mtx, rm, lim_base, K_PROD)
        f_off = funnel(s, mtx, rm, lim_base, K_OFF)
        d_on, d_off = set(f_on["head"]), set(f_off["head"])
        ins, outs = sorted(d_off - d_on), sorted(d_on - d_off)
        p_on = set(scr.order_candidates(buy_frame(f_on["head"]), ind_map,
                                        ASHARE_ORDER_TOP_N)[0].index)
        p_off = set(scr.order_candidates(buy_frame(f_off["head"]), ind_map,
                                         ASHARE_ORDER_TOP_N)[0].index)
        cut = float(f_on["v"].loc[f_on["head"][-1]])
        chase_v = f_on["v"].where(f_on["chase"]).dropna()
        # 「轴值 ≤ 入线值」只是**全局**的必要条件（配额档下不充分）；这一格查的是
        # 被挡票有没有 beat 过**自己板块**最差那一席 —— beat 不动本板席位就不可能进名单。
        # 用它当反证：若本板席位都 beat 不动却报出「换进 > 0」或反之，说明重跑走了样
        seat_cut = f_on["v"].reindex(f_on["head"]).groupby(
            [board_of(c) for c in f_on["head"]]).max().to_dict()
        # ⚠️ 不能用 `Index.map(Series)`：pandas 2.2 下它按**位置**对齐而不是按键查，
        # 键长不一样就整片填 NaN（09-27 冒烟第一版就是这么把 360 只全判成「beat 得动」的）
        own = pd.Series([seat_cut.get(board_of(c), np.nan) for c in chase_v.index],
                        index=chase_v.index, dtype="float32")
        # 该板块在现状名单里一格席位都没有（如科创板天天空着）⇒ 拆闸后回填必然补它
        beat_board = int(((chase_v < own) | own.isna()).sum()) if len(chase_v) else 0
        # 被挡票在**拆闸后那条候选队列**里的安静度名次（1 = 最先买）。
        # ⚠️ 不能在有闸那条队列上取名次：chase 的票按定义已被 cand 剔出去 ⇒ 取不到，
        # 那不是读数而是构造（09-27 冒烟第一版就踩在这上面，min 全是 NaN 才被发现）
        pos = f_off["order"].index.get_indexer(chase_v.index)
        pos = pos[pos >= 0] + 1
        rows.append({"场次": s, "被挡": int(len(chase_v)),
                     "拆闸档被挡": int(f_off["chase"].sum()),
                     "轴值≤入线": int((chase_v <= cut).sum()),
                     "拆本板席位": beat_board,
                     "被挡最小名次": int(pos.min()) if len(pos) else np.nan,
                     "被挡名次中位": float(np.median(pos)) if len(pos) else np.nan,
                     "候选队列长度": int(len(f_off["order"])),
                     "名单换进": len(ins), "名单换出": len(outs),
                     "下单换进": len(p_off - p_on),
                     "入线值": cut})
        if len(ins) and beat_board == 0:
            raise SystemExit(f"[作废] {s:%Y-%m-%d} 报出「换进 {len(ins)} 只」却没有一只 beat 得动"
                             f"本板块最差席位 ⇒ 席位重算走了样，读数不可引")
        r5, cn, nf, ka = (ret5.loc[s], sealed.loc[s], nofill.loc[s], cannot.loc[s])
        for dirn, codes, ranks in (("换进", ins, f_off["head"]), ("换出", outs, f_on["head"])):
            for c in codes:
                mv.append({"场次": s, "方向": dirn, "code": c, "板块": board_of(c),
                           "名次": (ranks.index(c) + 1) if c in ranks else np.nan,
                           "轴值": float(f_on["v"].get(c, np.nan)), "入线值": cut,
                           "一字板": bool(cn.get(c, False)), "无成交": bool(nf.get(c, False)),
                           "真封死": bool(ka.get(c, False)), "买得进": bool(not cn.get(c, False)
                                                                          and not nf.get(c, False)),
                           "五日到手": float(r5.get(c, np.nan)),
                           "超额": float(r5.get(c, np.nan) - ctl.loc[s])})
        if i % 90 == 0:
            print(f"  [{i + 1}/{len(ss)}] {s:%Y-%m-%d}　被挡 {rows[-1]['被挡']}　"
                  f"挤进前50 {rows[-1]['名单换进']}　{time.time() - t0:.0f}s", flush=True)

    df = pd.DataFrame(rows).set_index("场次")
    df["年"] = df.index.year
    df.to_csv(os.path.join(OUT, "squeeze_场次明细.csv.gz"))
    mv = pd.DataFrame(mv)
    if len(mv):
        mv.to_csv(os.path.join(OUT, "squeeze_席位变动明细.csv.gz"))

    n = len(df)
    print("\n===== ① 这道闸在名单层到底咬不咬（全历史 {} 场）=====".format(n))
    print(f"  被挡：合计 {int(df['被挡'].sum()):,} 只　场均 {df['被挡'].mean():.1f}　最多 {int(df['被挡'].max())}")
    print(f"  轴值 ≤ 入线值（**全局**意义上可能挤进前 50）：合计 {int(df['轴值≤入线'].sum())} 只　"
          f"场均 {df['轴值≤入线'].mean():.2f}")
    print(f"  其中还 beat 得动**自己板块**最差那一席的：合计 {int(df['拆本板席位'].sum())} 只　"
          f"场均 {df['拆本板席位'].mean():.3f}　（这两条都只是**必要条件松界**：配额档下回填"
          f"只补更早被跳过的票，过线也不保证进得来 ⇒ 精确数只能看下一行那个重跑）")
    print(f"  真挤进前 50（关掉闸重跑配额后被换掉的席位数）：合计 {int(df['名单换进'].sum())} 只　"
          f"场均 {df['名单换进'].mean():.3f}")
    for c, lbl in (("名单换进", "观察名单 50 席"), ("下单换进", "下单 5 席")):
        k = int((df[c] > 0).sum())
        print(f"  {lbl}层「一只都不换」的场次：{n - k}/{n} = {(n - k) / n:.1%}　有换 {k} 场"
              + (f"（最多一场换 {int(df[c].max())} 只）" if k else ""))
    mn = df["被挡最小名次"].dropna()
    print(f"  被挡票在拆闸后那条队列里的安静度名次：全历史最靠前 {int(mn.min())}（有 {int((mn <= ASHARE_BUY_TOP_N).sum())}"
          f"/{len(mn)} 场曾排进过前 {ASHARE_BUY_TOP_N}）　中位的中位 {df['被挡名次中位'].median():,.0f}"
          f"　（队列场均 {df['候选队列长度'].mean():,.0f} 只）")

    print("\n===== ② 真换了席位的那些场，换进来的是好票还是坏票 =====")
    if len(mv):
        for dirn, sub in mv.groupby("方向"):
            t = sub[sub["买得进"]]
            print(f"  {dirn}：{len(sub)} 行 / {sub['场次'].nunique()} 场　真封死 "
                  f"{sub['真封死'].mean():.2%}　买得进 {sub['买得进'].mean():.2%}　"
                  f"五天到手中位 {t['五日到手'].median():+.2%}　超额中位 {t['超额'].median():+.2%}　"
                  f"正超额占比 {(t['超额'] > 0).mean():.1%}")
    else:
        print(f"  {n} 场**一场都没换** ⇒ 这道闸在名单层是纯空挡：第一问那 −2.23% 的浮亏，"
              f"生产从来没有真的付过（因为它根本排不到那批票）")

    print("\n===== ③ 逐年 =====")
    y = df.groupby("年").agg(场次=("被挡", "size"), 被挡合计=("被挡", "sum"),
                             场均被挡=("被挡", "mean"), 可能挤进=("轴值≤入线", "sum"),
                             拆得动本板=("拆本板席位", "sum"), 真挤进=("名单换进", "sum"),
                             下单有换=("下单换进", lambda x: int((x > 0).sum())))
    print(y.round(2).to_string())
    y.round(4).to_csv(os.path.join(OUT, "squeeze_逐年.csv"))

    # 自检①②：拿有归档 meta 的那天复算并与生产对账（现读 meta，不写死数字）
    tags = sorted(os.path.basename(p)[5:13] for p in
                  glob.glob(os.path.join(ASHARE_SIGNAL_DIR, "meta_*.json")))
    print("\n===== 自检（断言不恒真）=====")
    z = int(df["拆闸档被挡"].sum())
    print(f"  ④ 反证「k={K_OFF} 那一档真等于拆掉这道闸」：{n} 场拆闸档被挡合计 = {z} 只"
          f"（判据本体没删、只是刀口抬到够不着 ⇒ 必须为 0；不为 0 = 本脚本的缩放没生效）")
    if z:
        raise SystemExit("[作废] 拆闸档仍在咬 ⇒ k 这个旋钮在本脚本里没拨动判据，全部读数不可引")
    for tg in tags[-1:]:
        s = pd.Timestamp(f"{tg[:4]}-{tg[4:6]}-{tg[6:]}")
        if s not in cl.index:
            print(f"  ① 锚点：{tg} 不在面板里，跳过")
            continue
        f = funnel(s, mtx, rm, lim_base, K_PROD)
        a = json.load(open(os.path.join(ASHARE_SIGNAL_DIR, f"meta_{tg}.json")))["buy"]
        got = dict(n_step1=int(f["step1"].sum()), n_traded=int(f["traded"].sum()),
                   n_chase=int(f["chase"].sum()))
        want = {k: int(a[k]) for k in got}
        fo = funnel(s, mtx, rm, lim_base, K_OFF)
        print(f"  ① 锚点 {tg}：归档 {want}　本脚本（ST 闸未开，历史口径统一）{got}　"
              f"⇒ {'✅ n_chase/n_step1 逐字相等' if got['n_chase'] == want['n_chase'] and got['n_step1'] == want['n_step1'] else '⚠️ 对不上账（n_cand 允许差 = ST 那批）'}"
              f"　｜n_cand 归档 {int(a['n_cand'])} vs 本脚本 {int(f['cand'].sum())}（差 = 当日 ST，历史场没快照开不了）")
        print(f"  ② 正对照 {tg}：生产档挡 {int(f['chase'].sum())} 只 → 拆闸档挡 {int(fo['chase'].sum())} 只　"
              f"前 50 换进 {len(set(fo['head']) - set(f['head']))} 只")
    print(f"\n[完成] 用时 {time.time() - t0:.0f}s　产物在 {OUT}")


if __name__ == "__main__":
    main()
