# -*- coding: utf-8 -*-
"""日频名单的**次日真账**：T 日给的名单买不买得到、贵多少，以及被近涨停闸挡掉的那批本来排不排得进

为什么要合成一个入口（09-24，㉒/㉓ 那两份 `shell/` 草稿的合并）：两份草稿各算各的，
但对象是同一天、同一份快照、同一套参照价 ⇒ 「名单那批的次日表现」被算了**两遍**。
两份脚本可以各自漂移到不同的判据上，而这类系统里最贵的就是「同一件事有两个数」。
合一个入口之后一次只加载一遍面板，顺带把所有历史场次串成一张表（原来一天一跑，
攒 20 天就要手敲 20 次）。

三笔账，口径全部写在这里，不在别处重抄：

    参照价     快照「昨收」列 = T 日盘面收盘（除权日则是除权参考价）
               与面板 raw_price（= 复权价 ÷ $factor，写进 bin 的那个数）逐票核对，
               相对差 >1e-4 的判为 **T+1 除权** ⇒ 那一票的滑点不可比，只计数不进统计
    可成交     成交额 > 0                       （==0 = 全天无成交：停牌或一字封死）
    一字板     今开 == 最高 == 最低 == 最新价
    买不进     一字板 且 (最新价/昨收 − 1) ≥ 板块限幅 × 0.99
    开盘滑点   今开 / 参照 − 1                  挂开盘市价单要付的价
    均价滑点   (成交额/成交量) / 参照 − 1        接近批量成交的真实价
    收盘偏移   最新价 / 参照 − 1                「如果买到」的账面涨跌
    顺延深度   名单头部有票买不到时，5 个槽位要往下扫到第几名（不重跑分散约束 ⇒ 只会更深）

板块限幅从 `config.ASHARE_BOARD_LIMIT_UP` 读（与生产同一张表），不在这里重敲数字。
次日表现**全部取外部快照**，不用面板自算 —— 避免「用写进 bin 的字节验自己」。

反事实那条腿的被挡集合**只用生产原函数**复算（`tradable_mask` / `volume_exclusion` /
`gate_vector` / `guard_ret`），且只数必须与当日 `meta_*.json` 的 `n_chase` **逐字相等**，
不等就把这一场判作废（exit≠0）。所以这条腿不可能拿一套走样的判据出账：哪天改了
`STOCK_SCREEN_RULES` 或 `ASHARE_BUY_MIN_HITS` 再去审计旧场次，对账就会失败 —— 那是
设计，不是 bug。**排序轴、闸门档、分位、共识阈值一律从该场 meta 派生**，不读今天的进程常量。

两问分别对应「挡对了没」与「挡的是不是本来也选不上」：
① 被挡那批的次日收益 vs 实际留下那 50 只的次日收益（两批按构造互不相交 ⇒ 比的是
   两批**各自的中位数**，配对差在这张表上不存在）；
② 把被挡票放回**当日生产那根轴**升序，看几只轴值 ≤ 入线值（第 50 名）：只有这些才
   可能真的挤进前 50，其余是**空挡**。这一腿不需要 T+1 ⇒ 当天就能出。

只读，不改任何产物。用 /usr/bin/python3.10（本入口不碰 qlib，只要 pandas/numpy）。

用法
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_daily_audit.py            # 所有有 meta 的场次
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_daily_audit.py 20260924   # 只看这一场
"""
import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import glob
import json
import os
import sys

import numpy as np
import pandas as pd

from config import (ASHARE_BOARD_LIMIT_UP, ASHARE_SIGNAL_DIR, ASHARE_SNAPSHOT_DIR)
import ashare_screen as scr
from ashare_screen import (VOLUME_RULES, board_of, build_matrices, buy_extra_block,
                           factor_matrices, gate_vector, guard_ret, load_panel,
                           tradable_mask, volume_exclusion)

NEAR = 0.99          # 判「封死」的贴板容差，比执行闸的 0.95 更严：那是真买不进


def sessions(wanted=None):
    """从产物派生要审计的场次：有 `meta_YYYYMMDD.json` 的就算一场，升序。

    不写死日期 ⇒ 每天日更跑一次，这张表自己往下长一行（㉒/㉓ 卡在 n=1、n=2 就是因为
    每一天都要人手敲一次日期）。`*.bak-*` 那几份不匹配 `meta_*.json`，自然排除。
    """
    got = sorted(os.path.basename(p)[5:13] for p in
                 glob.glob(os.path.join(ASHARE_SIGNAL_DIR, "meta_*.json")))
    got = [t for t in got if os.path.exists(os.path.join(ASHARE_SIGNAL_DIR, f"buy_{t}.csv"))]
    if wanted:
        if wanted not in got:
            raise SystemExit(f"[输入] 没有 {wanted} 这一场的名单产物（现有：{got}）")
        return [wanted]
    if not got:
        raise SystemExit(f"[输入] {ASHARE_SIGNAL_DIR} 里没有任何 meta_* 产物")
    return got


def spot_frame(tag):
    p = os.path.join(ASHARE_SNAPSHOT_DIR, f"spot_{tag}.csv")
    if not os.path.exists(p):
        return None
    sp = pd.read_csv(p, encoding="utf-8-sig", dtype={"代码": str})
    sp["key"] = sp["代码"].str.lower()
    return sp.drop_duplicates("key").set_index("key")


def next_snapshot_tag(t_tag):
    """T 之后**第一个有快照的日子**：快照是收盘后抓的，缺哪天就是哪天没跑日更。"""
    have = sorted(os.path.basename(p)[5:13] for p in
                  glob.glob(os.path.join(ASHARE_SNAPSHOT_DIR, "spot_*.csv")))
    nxt = [d for d in have if d > t_tag]
    return nxt[0] if nxt else None


def recompute_blocked(s, mtx, rm, axis, quantile, min_hits, extra_names=""):
    """复算 T 日的候选漏斗与被挡集合：与 `buy_candidates` 同一条路，一个判据都不重抄"""
    cl, raw, vol = mtx["close"], mtx["raw_price"], mtx["volume"]
    cols = cl.columns
    gate_mat = rm[VOLUME_RULES[0][2]]                  # 闸门基准恒为「量能水平」那条
    ok, _nl = tradable_mask(s, None, gate_mat, mtx)
    pool = pd.Series(ok, index=gate_mat.columns)
    _keep, detail = volume_exclusion(rm, s, pool, quantile)
    n_hit = detail["n_hit"].reindex(pool.index).fillna(0)
    keep_buy = pool & (n_hit < min_hits)               # 待买入那道「较松」的共识闸
    # 名单层独立闸**跟着这一场的 meta 走**：09-26 之前那几天根本没有这道闸，
    # 拿今天的判据去复算昨天的名单就会对账失败（而且失败得像是生产算错了）
    blocked, _fired = buy_extra_block(rm, s, pool, quantile, names=extra_names)
    keep_buy = keep_buy & ~blocked

    v = rm[axis].loc[s].reindex(cols).astype("float32")
    step1 = pool & keep_buy & v.notna()
    traded = step1 & cl.loc[s].notna() & raw.loc[s].notna() & vol.loc[s].gt(0)
    prev = cl.index[cl.index.get_loc(s) - 1]
    chg = guard_ret(cl.loc[s] / cl.loc[prev] - 1.0, raw.loc[s] / raw.loc[prev] - 1.0)
    chase = traded & chg.ge(gate_vector(cols, s))      # ST 闸在这道之后，不影响被挡集合
    return dict(cols=cols, v=v, chg=chg, chase=chase, step1=step1, traded=traded)


def bill(sp, mtx, s, codes, label, rank_map=None):
    """一批票在 T+1 的可成交性账。除权/无成交/一字板**都不进收益账**：
    前者的参考价不是名单里那个收盘，后两者是「想买买不到」，记成收益会高估反事实。
    `rank_map` 只给名单那一批（要按名次算顺延深度），被挡那批没有名次可言。"""
    rows = []
    for c in codes:
        key = c.lower()
        if key not in sp.index:
            continue
        r = sp.loc[key]
        ref = float(r["昨收"])
        o, h, l, last = (float(r["今开"]), float(r["最高"]),
                         float(r["最低"]), float(r["最新价"]))
        amt, q = float(r["成交额"]), float(r["成交量"])
        lim = ASHARE_BOARD_LIMIT_UP.get(board_of(c), 0.10)
        vwap = amt / q if q > 0 else np.nan
        pan = float(mtx["raw_price"].loc[s, c]) if c in mtx["raw_price"].columns else np.nan
        rows.append(dict(code=c, name=r["名称"], 板块=board_of(c),
                         名次=(rank_map or {}).get(c, np.nan),
                         除权=bool(pan == pan) and abs(pan / ref - 1) > 1e-4,
                         无成交=amt <= 0, 一字板=(o == h == l == last),
                         买不进=(o == h == l == last) and (last / ref - 1) >= lim * NEAR,
                         开盘滑点=o / ref - 1,
                         均价滑点=vwap / ref - 1 if vwap == vwap else np.nan,
                         收盘偏移=last / ref - 1,
                         触及涨停=(h / ref - 1) >= lim * NEAR))
    d = pd.DataFrame(rows).set_index("code")
    t = d[~d["无成交"] & ~d["一字板"] & ~d["除权"]]
    print(f"\n===== {label}：快照查到 {len(d)} 只（进账 {len(t)} 只；全天无成交 "
          f"{int(d['无成交'].sum())}、一字板 {int(d['一字板'].sum())}、"
          f"T+1 除权 {int(d['除权'].sum())}）=====")
    for col in ("开盘滑点", "均价滑点", "收盘偏移"):
        q = t[col].dropna()
        print(f"  {col}：中位 {q.median():+.3%}　均值 {q.mean():+.3%}　"
              f"p10 {q.quantile(.1):+.3%}　p90 {q.quantile(.9):+.3%}")
    print(f"  次日收阳 {(t['收盘偏移'] > 0).sum()}/{len(t)} 只　"
          f"盘中触及涨停 {int(d['触及涨停'].sum())} 只（触及 ≠ 买不进）")
    return d, t


def audit_one(t_tag, mtx, rm, out_rows):
    """一场次的三笔账；返回 False 表示对账失败、这一场作废"""
    meta = json.load(open(os.path.join(ASHARE_SIGNAL_DIR, f"meta_{t_tag}.json")))
    mb = meta["buy"]
    axis, quantile = mb["expr"], float(meta["quantile"])
    min_hits = int(mb["buy_min_hits"])
    extra = meta.get("buy_extra_rules", [])           # 缺键 = 那一场还没有独立闸
    extra_names = ",".join(r["key"] for r in extra)
    archived_gate = meta.get("tradable_gate")
    if archived_gate != scr.ASHARE_TRADABLE_GATE:
        # 审计的是**那一天**的判据：进程档与产物档不一致时按产物走，并说出来
        print(f"[闸门] 该场归档档 = {archived_gate}，当前进程档 = {scr.ASHARE_TRADABLE_GATE} "
              f"⇒ 按 {archived_gate} 复算")
        scr.ASHARE_TRADABLE_GATE = archived_gate
    print(f"\n################ 场次 {t_tag}　"
          f"axis={axis}　gate={archived_gate}　quantile={quantile}　"
          f"buy_min_hits={min_hits}　独立闸={extra_names or '无'}　"
          f"入线值={mb['quiet_cut']:,.1f} ################")

    s = pd.Timestamp(f"{t_tag[:4]}-{t_tag[4:6]}-{t_tag[6:]}")
    r = recompute_blocked(s, mtx, rm, axis, quantile, min_hits, extra_names)
    print(f"[复算] n_step1={int(r['step1'].sum())} n_traded={int(r['traded'].sum())} "
          f"n_chase={int(r['chase'].sum())}　（生产 meta："
          f"{mb['n_step1']}/{mb['n_traded']}/{mb['n_chase']}）")
    if int(r["chase"].sum()) != int(mb["n_chase"]) or int(r["step1"].sum()) != int(mb["n_step1"]):
        print("  ⚠️ **[对账失败]** 复算与生产逐字不等 ⇒ 判据抄错或该场产物已被别的口径覆盖，"
              "这一场的账单作废")
        return False

    blocked = list(r["cols"][r["chase"].to_numpy()])
    bd = pd.DataFrame({"板块": [board_of(c) for c in blocked],
                       "轴值": [float(r["v"][c]) for c in blocked],
                       "T日涨幅": [float(r["chg"][c]) for c in blocked]},
                      index=pd.Index(blocked, name="code"))
    print(f"[被挡 {len(bd)} 只] {bd['板块'].value_counts().to_dict()}　当日涨幅 "
          f"{bd['T日涨幅'].min():.1%}~{bd['T日涨幅'].max():.1%}")

    t1 = next_snapshot_tag(t_tag)
    sp = spot_frame(t1) if t1 else None
    row = dict(场次=t_tag, 轴=axis, 被挡=len(bd))
    buy = pd.read_csv(os.path.join(ASHARE_SIGNAL_DIR, f"buy_{t_tag}.csv"), dtype={"code": str})

    if sp is None:
        print(f"[无 T+1 快照] 没有比 {t_tag} 更新的 spot_*.csv ⇒ 次日表现两腿做不了，"
              f"只出空挡检验（这一腿不需要 T+1）")
        row.update(名单进账=np.nan, 名单买不进=np.nan, 名单收盘偏移=np.nan,
                   被挡进账=np.nan, 被挡一字板=np.nan, 被挡收盘偏移=np.nan)
    else:
        print(f"[T+1 快照] spot_{t1}.csv")
        dl, tl = bill(sp, mtx, s, list(buy["code"]),
                      f"名单：{t_tag} 那 {len(buy)} 只在 {t1} 的可成交性",
                      rank_map=dict(zip(buy["code"], buy["rank"])))
        db, tb = bill(sp, mtx, s, blocked, "反事实：被近涨停闸挡掉的那批，同期表现")
        p = mtx["raw_price"].loc[s].reindex(blocked).astype("float64")
        q = sp.loc[[c.lower() for c in blocked if c.lower() in sp.index], "昨收"]
        q.index = [i.upper() for i in q.index]
        rel = (p / q.reindex(blocked).astype("float64") - 1).abs().dropna()
        print(f"\n[参照价核对] 面板盘面价 vs 快照昨收（被挡 {len(blocked)} 只）：相对差中位 "
              f"{rel.median():.2e}、最大 {rel.max():.2e}、>1e-4 的 {(rel > 1e-4).sum()} 只")
        print("\n===== 两批并排（同一天、同一份快照、同一套参照价口径）=====")
        for col in ("开盘滑点", "均价滑点", "收盘偏移"):
            a, b = tb[col].dropna(), tl[col].dropna()
            print(f"  {col}：被挡 {a.median():+.3%} vs 名单 {b.median():+.3%}"
                  f"　差 {(a.median() - b.median()) * 100:+.2f}pp")
        print(f"  次日收阳比例：被挡 {(tb['收盘偏移'] > 0).mean():.0%} vs 名单 "
              f"{(tl['收盘偏移'] > 0).mean():.0%}")
        print(f"  ⚠️ 反事实的天花板：被挡那批里 {int((db['一字板'] | db['无成交']).sum())} 只 "
              "T+1 一字板/无成交 ⇒ 拆闸也买不到，那部分「少赚」不成立")

        opath = os.path.join(ASHARE_SIGNAL_DIR, f"order_{t_tag}.csv")
        if os.path.exists(opath):
            order = pd.read_csv(opath, dtype={"code": str})
            print(f"\n===== 下单那 {len(order)} 只在 {t1} 实付的价 =====")
            for _, orow in order.iterrows():
                c = orow["code"]
                if c not in dl.index:
                    print(f"  第{int(orow['slot'])}槽 {c} {orow['name']}　快照查无此票")
                    continue
                d = dl.loc[c]
                tagx = ("【买不进】" if d["买不进"] else
                        "【除权，滑点不可比】" if d["除权"] else "")
                print(f"  第{int(orow['slot'])}槽 {c} {str(d['name']).strip():<6} {d['板块']:<4} "
                      f"开盘 {d['开盘滑点']:+.2%}　均价 {d['均价滑点']:+.2%}　"
                      f"收盘 {d['收盘偏移']:+.2%}　{tagx}")
            reb = dl[~dl["买不进"] & ~dl["无成交"] & ~dl["除权"]].sort_values("名次")
            print(f"  顺延深度：名单头部买不进/无成交 "
                  f"{int(dl['买不进'].sum() + dl['无成交'].sum())} 只 ⇒ 可买头部名次 "
                  + " / ".join(str(int(x)) for x in reb["名次"].head(8)) +
                  "（只按名次扫，没重跑板块 ≤3、行业 ≤1 ⇒ 真实要扫的只会更深）")
        row.update(名单进账=len(tl), 名单买不进=int(dl["买不进"].sum() + dl["无成交"].sum()),
                   名单收盘偏移=float(tl["收盘偏移"].median()),
                   被挡进账=len(tb), 被挡一字板=int((db["一字板"] | db["无成交"]).sum()),
                   被挡收盘偏移=float(tb["收盘偏移"].median()))

    # 空挡检验：放开这道闸，被挡的会不会挤进前 50
    cut = float(mb["quiet_cut"])
    inside = bd[bd["轴值"] <= cut]
    ratio = float(bd["轴值"].min()) / cut if len(bd) and cut else np.nan
    print(f"\n===== 空挡检验（按该场那根轴 {axis} 升序，入线值 = 第 {mb['top_n']} 名 = {cut:,.1f}）=====")
    print(f"  被挡 {len(bd)} 只里轴值 ≤ 入线值的：**{len(inside)} 只**"
          f"　其余 {len(bd) - len(inside)} 只不挡也排不进去 ⇒ 拆闸这一场"
          + ("**换掉 " + str(len(inside)) + " 只**" if len(inside) else "**一只不换**"))
    print("  轴值最低的 5 只（越小越先买 ⇒ 最接近挤进来的）：")
    print(bd.nsmallest(5, "轴值")[["板块", "T日涨幅", "轴值"]]
          .to_string(float_format=lambda x: f"{x:.4f}"))
    row.update(挤进前50=len(inside), 最低除入线=ratio)
    out_rows.append(row)
    return True


def main():
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    ss = sessions(wanted)
    print(f"[审计场次] 从 {ASHARE_SIGNAL_DIR} 派生：" + "、".join(ss))

    wide, _b = load_panel()
    mtx = build_matrices(wide)
    del wide
    # 一次求值覆盖所有场次用到的轴：旧场次可能是换轴前那根，跟着 meta 走
    axes, extra_exprs = set(), set()
    for t in ss:
        m = json.load(open(os.path.join(ASHARE_SIGNAL_DIR, f"meta_{t}.json")))
        axes.add(m["buy"]["expr"])
        extra_exprs |= {r["expr"] for r in m.get("buy_extra_rules", [])}
    exprs = (({e for _k, _n, e, _d in scr.active_rules()} | axes | extra_exprs
              | {VOLUME_RULES[0][2]}))
    print(f"[求值] {len(exprs)} 条表达式（该场轴 + 启用剔除构造 + 闸门基准"
          f"{' + 名单独立闸' if extra_exprs else ''}）")
    rm = factor_matrices(sorted(exprs), mtx)

    rows, bad = [], []
    for t in ss:
        if not audit_one(t, mtx, rm, rows):
            bad.append(t)

    print("\n################ 跨场次汇总（每跑一次日更自己长一行）################")
    # 只放**跨场次可比**的列：入线值/轴值是各场那根轴自己的量纲（手），换轴就不可并排，
    # 所以这里留「最低 ÷ 入线」那个无量纲倍数，绝对值在上一节逐场打印过了
    cols = ["轴", "被挡", "挤进前50", "最低除入线", "名单进账", "名单买不进",
            "名单收盘偏移", "被挡进账", "被挡一字板", "被挡收盘偏移"]
    show = pd.DataFrame(rows).set_index("场次").reindex(columns=cols)
    for c in ("被挡", "挤进前50", "名单进账", "名单买不进", "被挡进账", "被挡一字板"):
        show[c] = show[c].astype("Int64")
    show["名单收盘偏移"] = show["名单收盘偏移"].map(lambda x: f"{x:+.2%}" if x == x else "—")
    show["被挡收盘偏移"] = show["被挡收盘偏移"].map(lambda x: f"{x:+.2%}" if x == x else "—")
    show["最低除入线"] = show["最低除入线"].map(lambda x: f"{x:.2f}×" if x == x else "—")
    print(show.to_string())
    print("\n读法：「挤进前50」= 0 说明那一场这道闸在名单层是**空挡**的（拆了也不多买一只）；"
          "「最低除入线」= 被挡里最接近挤进来的那只相对入线值的倍数，**倍数越小越脆**"
          "（贴板票天然放量，而现轴就是量能水平）。右边四列「买不进/一字板」才是这道闸真正"
          "在挡的东西 —— 两者都为 0 的天数越多，它越接近一道没有账的闸。")
    if bad:
        raise SystemExit(f"[作废场次] {bad}：复算与生产对不上账 ⇒ 退出码非 0，别引用这一场的数")


if __name__ == "__main__":
    main()
