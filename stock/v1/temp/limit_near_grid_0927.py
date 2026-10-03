# -*- coding: utf-8 -*-
"""P0-2 涨停裕度专项（09-27，只读、不改任何判据）

要回答的一句话：执行闸那根 `ASHARE_LIMIT_NEAR=0.95`（主板 9.5% / 双创 19% / 北交 28.5%）
画得对不对 —— 挡掉的票里有多少**其实买得进**，放进来的灰带里有多少**其实买不进**，
以及被挡那批后来到底**少赚还是少亏**（这条必须有全可投域那条对照线，不然 −2.4% 无含义）。

两根 r 分开量，因为同一根 0.95 在系统里喂的是**两个不同判据**（读代码确认，不是笔误）：

    r_同日 = [C_s/C_{s-1} - 1] / 限幅(板块)      ← ③ 名单侧：信号日**当天**已贴板就不给进候选
    r_次日 = [O_{d1}/C_s - 1] / 限幅(板块)        ← 环 2 回放第 4 道闸：**次日开盘**跳升超阈值就不建仓

一个票日只存一行、两个 r 都存 ⇒ 换哪根判据都是同一张表上切片，不重复堆行。
被挡 ⟺ r ≥ k。分箱下探到 0.50 是为了拿**剂量反应阶梯**：如果"贴板越狠、后面跌得越多"
是单调的，这道闸挡的就是真东西；如果 [0.50,0.70) 与 [0.95,1.00) 的后续收益差不多，
那 0.95 这根线画在哪都是噪音 —— 这条比"该不该拆闸"更根本。

所有 outcome 用 **bin 面板**算（历史只有 09-23/09-24 两份真快照 ⇒ 量 12 年只能用面板），
所以第 ① 步先拿那两份快照**逐票对拍**面板那套判据（双向：漏检 + 误检），
并查清面板与交易所有出入的那几只是不是除权。对拍不干净，② 的「买得进比例」只能当**下界**读。

公式（与 `ashare_screen.tradable_mask` 同构：gap 不做 guard_ret 裁剪，闸吃的就是裸值）：
    一字板(面板) = (H_d1 - L_d1)/C_d1 ≤ EPS 且 |O_d1 - C_d1|/C_d1 ≤ EPS
    买不进(0.99)  = 一字板 且 C_d1/C_s - 1 ≥ 限幅 × 0.99（与 ④ 审计那根 NEAR 同值）
    买得进        = 非一字板 且 T+1 有成交
    五日到手的钱   = O_{d1+HOLD}/O_d1 - 1（d1 开盘买进、按生产持有天数拿开盘到开盘）
    盘中触板      = H_d1/C_s - 1 ≥ 限幅（摸到板，未必封住）
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
SRC = os.path.join(ROOT, "stock", "v1", "src")
OUT = os.path.join(HERE, "tmp_limit_near_0927")
os.makedirs(OUT, exist_ok=True)

sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402,F401
from config import (ASHARE_BOARD_LIMIT_UP, ASHARE_LIMIT_NEAR, ASHARE_ORDER_TOP_N,
                    ASHARE_PORT_HOLD, ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED,
                    ASHARE_SNAPSHOT_DIR)
from ashare_screen import board_of, build_matrices, load_panel

EPS = 2e-6                      # 复权往返的相对容差：float32 约 1e-7，留一个量级余量
NEAR_SEALED = 0.99              # ④ 审计判「真封死」那根容差（run_ashare_daily_audit.NEAR）
EDGES = (0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10)
SPOT_DAYS = ("20260923", "20260924")   # 面板与真快照同时存在的两天，两天都拍


def seal_flags(hi, lo, op, cl):
    """面板侧一字板：全天最高-最低振幅与开盘-收盘差都落在浮点容差内"""
    return (((hi - lo).abs() / cl) <= EPS) & (((op - cl).abs() / cl) <= EPS)


def step1_cross_check(mtx, fc):
    """① 口径自检：两天真快照 vs 面板同一天的同一套判据（双向对拍 + 出入样本归因）"""
    print("\n===== ① 口径自检（面板能不能替快照）=====")
    prev_of = {}
    all_ok, tot_sealed, tot_miss = True, 0, 0
    for day in SPOT_DAYS:
        spot = pd.read_csv(os.path.join(ASHARE_SNAPSHOT_DIR, f"spot_{day}.csv"))
        spot["code"] = spot["代码"].str.upper().map(
            lambda s: s if s[:2] in ("SH", "SZ", "BJ") else s[:2].upper() + s[2:])
        sp = spot.set_index("code")
        s = pd.Timestamp(f"{day[:4]}-{day[4:6]}-{day[6:]}")
        prev_of[day] = mtx["close"].index[mtx["close"].index.get_loc(s) - 1]
        pan = {k: (mtx[k] / fc.where(fc > 0)).loc[s] for k in ("open", "high", "low", "close")}
        ref = (mtx["close"] / fc.where(fc > 0)).loc[prev_of[day]]
        both = [c for c in sp.index.intersection(pan["close"].index)
                if ref.get(c) == ref.get(c) and pan["close"][c] == pan["close"][c]]
        lim = pd.Series([ASHARE_BOARD_LIMIT_UP.get(board_of(c), 0.10) for c in both], index=both)
        d = pd.DataFrame({
            "昨收_快照": sp.loc[both, "昨收"].astype("float64"),
            "昨收_面板": ref.reindex(both).astype("float64"),
            "今开_快照": sp.loc[both, "今开"].astype("float64"),
            "开盘_面板": pan["open"].reindex(both).astype("float64"),
            "最高_快照": sp.loc[both, "最高"].astype("float64"),
            "最低_快照": sp.loc[both, "最低"].astype("float64"),
            "收盘_快照": sp.loc[both, "最新价"].astype("float64"),
            "收盘_面板": pan["close"].reindex(both).astype("float64"),
        })
        d["开盘滑点_快照"] = d["今开_快照"] / d["昨收_快照"] - 1
        d["开盘滑点_面板"] = d["开盘_面板"] / d["昨收_面板"] - 1
        sealed_spot = ((d["今开_快照"] == d["最高_快照"]) & (d["今开_快照"] == d["最低_快照"])
                       & (d["今开_快照"] == d["收盘_快照"]))
        sealed_pan = seal_flags(mtx["high"].loc[s], mtx["low"].loc[s],
                                mtx["open"].loc[s], mtx["close"].loc[s]).reindex(both)
        d["一字板_快照"], d["一字板_面板"] = sealed_spot, sealed_pan
        d["一字板_严格相等"] = ((mtx["high"].loc[s] == mtx["low"].loc[s])
                                & (mtx["open"].loc[s] == mtx["close"].loc[s])).reindex(both)
        d["买不进_快照"] = sealed_spot & (d["收盘_快照"] / d["昨收_快照"] - 1 >= lim * NEAR_SEALED)
        d["买不进_面板"] = sealed_pan & (d["收盘_面板"] / d["昨收_面板"] - 1 >= lim * NEAR_SEALED)
        d["无成交_快照"] = sp.loc[both, "成交额"].astype("float64") <= 0
        d["无成交_面板"] = (mtx["volume"].loc[s].reindex(both).fillna(0) <= 0)

        rel = (d["昨收_面板"] / d["昨收_快照"] - 1).abs()
        out = d[rel > 1e-4].copy()
        if len(out):
            f_now = fc.loc[s].reindex(out.index)
            f_prev = fc.loc[prev_of[day]].reindex(out.index)
            out["除权"] = (f_now / f_prev - 1).abs() > 1e-6
        diff_slip = (d["开盘滑点_面板"] - d["开盘滑点_快照"]).abs()
        miss = int((d["一字板_快照"] & ~d["一字板_面板"].astype(bool)).sum())
        extra = int((~d["一字板_快照"] & d["一字板_面板"].astype(bool)).sum())
        print(f"\n[{s:%Y-%m-%d}] 快照 {len(sp)} 行 ∩ 面板有价 = {len(both)} 只")
        print(f"  昨收参照价 相对差 中位 {rel.median():.2e}　p99 {rel.quantile(0.99):.2e}　"
              f">1e-4 的 {len(out)} 只（其中除权 {int(out['除权'].sum()) if len(out) else 0} 只）")
        print(f"  开盘滑点 逐票差 中位 {diff_slip.median():.2e}　p99 {diff_slip.quantile(0.99):.2e}　"
              f">5e-4 的 {int((diff_slip > 5e-4).sum())} 只")
        print(f"  一字板：快照 {int(sealed_spot.sum())} 只｜面板 {int(d['一字板_面板'].sum())} 只｜"
              f"漏检 **{miss}**｜误检 {extra}　｜买不进：快照 {int(d['买不进_快照'].sum())} vs "
              f"面板 {int(d['买不进_面板'].sum())}　｜无成交：快照 {int(d['无成交_快照'].sum())} "
              f"vs 面板 {int(d['无成交_面板'].sum())}")
        print(f"  容差敏感性：EPS={EPS:g} 判出 {int(d['一字板_面板'].sum())} 只｜"
              f"严格相等判出 {int(d['一字板_严格相等'].sum())} 只｜"
              f"放宽到 5e-3 判出 {int((((mtx['high'].loc[s] - mtx['low'].loc[s]).abs() / mtx['close'].loc[s]) <= 5e-3).reindex(both).sum())} 只")
        tot_sealed += int(sealed_spot.sum())
        tot_miss += miss
        all_ok &= (miss == 0 and float(diff_slip.median()) < 5e-5)
        d.to_csv(os.path.join(OUT, f"step1_对拍逐票_{day}.csv.gz"), compression="gzip")
        if len(out):
            out.to_csv(os.path.join(OUT, f"step1_参照价出入_{day}.csv"))
    print(f"\n[① 判定] 两天合计真一字板样本 {tot_sealed} 只、面板漏检 {tot_miss} 只 ⇒ "
          + ("**漏检为零**，面板这套判据可以替快照（但样本只有 "
             f"{tot_sealed} 只，② 里「买得进率」的精度别超过这个量级）"
             if all_ok else "⚠️ 有漏检或滑点对不上 ⇒ ② 的「买得进/真封死」只当下界读"))
    return all_ok, tot_sealed


def step2_grid(mtx):
    """② 全历史：一个票日一行，两个 r 与全部 outcome 同表存；另算全可投域对照线"""
    cl, op, hi, lo, vol = (mtx["close"], mtx["open"], mtx["high"],
                           mtx["low"], mtx["volume"])
    cols = cl.columns
    lim = pd.Series([ASHARE_BOARD_LIMIT_UP.get(board_of(c), 0.10) for c in cols],
                    index=cols, dtype="float32")
    f = lambda x: x.astype("float32")     # noqa: E731

    chg_s = f(cl / cl.shift(1) - 1.0)                 # 信号日当天涨幅（③ 吃这个）
    gap_1 = f(op.shift(-1) / cl - 1.0)                # 次日开盘跳升（环 2 吃这个）
    close_1 = f(cl.shift(-1) / cl - 1.0)
    ret5 = f(op.shift(-1 - ASHARE_PORT_HOLD) / op.shift(-1) - 1.0)
    touch_1 = f(hi.shift(-1) / cl - 1.0)
    sealed = seal_flags(hi.shift(-1), lo.shift(-1), op.shift(-1), cl.shift(-1))
    nofill = (vol.shift(-1).fillna(0) <= 0) | cl.shift(-1).isna()
    cannot = sealed & f(close_1 >= lim * NEAR_SEALED)
    buyable = (~sealed) & (~nofill)
    touched = f(touch_1 >= lim)
    univ = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
            & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    r_same, r_next = f(chg_s / lim), f(gap_1 / lim)

    # 对照线：全可投域（不筛涨幅）每天的「次日开盘买进、拿 HOLD 天」中位与当日中位
    ctl = ret5.where(univ).median(axis=1)
    ctl_day = chg_s.where(univ).median(axis=1)
    ctrl = pd.DataFrame({"全域五日到手中位": ctl, "全域当日涨幅中位": ctl_day})
    ctrl["年"] = ctrl.index.year
    print("\n===== 对照线（全可投域，不筛涨幅）=====")
    print(ctrl.groupby("年").agg(交易日=("全域五日到手中位", "size"),
                                 五日到手中位=("全域五日到手中位", "median"),
                                 当日涨幅中位=("全域当日涨幅中位", "median")).round(4).to_string())
    ctrl.round(6).to_csv(os.path.join(OUT, "step2_对照线_全域逐年.csv"))

    m = (np.asarray(r_same >= EDGES[0]) | np.asarray(r_next >= EDGES[0])) & np.asarray(univ)
    idx = np.where(m)
    cc = cols[idx[1]]
    df = pd.DataFrame({
        "日": cl.index[idx[0]], "板块": [board_of(c) for c in cc],
        "r同日": r_same.to_numpy()[idx], "r次日": r_next.to_numpy()[idx],
        "当日涨幅": chg_s.to_numpy()[idx], "次日开盘跳升": gap_1.to_numpy()[idx],
        "次日收盘偏移": close_1.to_numpy()[idx], "五日到手": ret5.to_numpy()[idx],
        "一字板": sealed.to_numpy()[idx], "无成交": nofill.to_numpy()[idx],
        "真封死": cannot.to_numpy()[idx], "买得进": buyable.to_numpy()[idx],
        "盘中触板": touched.to_numpy()[idx]})
    df["年"] = pd.DatetimeIndex(df["日"]).year
    df["全域五日到手中位"] = ctl.reindex(df["日"]).to_numpy()
    df["超额(五日到手-全域中位)"] = df["五日到手"] - df["全域五日到手中位"]
    print(f"\n===== ② 体检样本：r同日 或 r次日 ≥ {EDGES[0]} 且在可投域 = {len(df):,} 条票日　"
          f"{df['日'].min():%Y-%m-%d}~{df['日'].max():%Y-%m-%d}（{df['日'].nunique()} 个交易日）=====")
    df.to_pickle(os.path.join(OUT, "step2_票日明细.pkl.gz"))
    return df


def band(x, edges):
    lbl = pd.Series(np.full(len(x), "低于最低档", dtype=object), index=x.index)
    for a, b in zip(edges[:-1], edges[1:]):
        lbl[(x >= a) & (x < b)] = f"[{a:.2f},{b:.2f})"
    lbl[x >= edges[-1]] = f">={edges[-1]:.2f}"
    return lbl


def step3_readings(df):
    for key, tag in (("r同日", "同日(③名单侧)"), ("r次日", "次日(环2回放侧)")):
        sub = df[df[key] >= EDGES[0]].copy()
        sub["箱"] = band(sub[key], list(EDGES) + [9.9])
        nd = sub["日"].nunique()
        print(f"\n----- 剂量反应阶梯：{tag}（{nd} 个交易日、{len(sub):,} 条）-----")
        g = sub.groupby("箱").agg(
            票日=("箱", "size"), 一字板率=("一字板", "mean"), 无成交率=("无成交", "mean"),
            真封死率=("真封死", "mean"), 买得进率=("买得进", "mean"),
            次日开盘跳升中位=("次日开盘跳升", "median"),
            次日收盘偏移中位=("次日收盘偏移", "median"),
            五日到手中位=("五日到手", "median"), 五日均值=("五日到手", "mean"),
            超额中位=("超额(五日到手-全域中位)", "median"),
            盘中触板率=("盘中触板", "mean"), 正超额占比=("超额(五日到手-全域中位)", lambda v: (v > 0).mean()))
        g["平均每天"] = (g["票日"] / nd).round(1)
        print(g.drop(columns="票日").to_string(float_format=lambda v: f"{v:.4f}"))
        g.round(4).to_csv(os.path.join(OUT, f"step3_剂量反应_{tag}.csv"))

    print(f"\n===== 挪档边际（生产那根 k={ASHARE_LIMIT_NEAR}）=====")
    rows = []
    for key, tag in (("r同日", "同日(③名单侧)"), ("r次日", "次日(环2回放侧)")):
        sub = df[df[key] >= EDGES[0]]
        nd = sub["日"].nunique()
        segs = [(f"生产档：r ≥ {ASHARE_LIMIT_NEAR} 全被挡",
                 sub[sub[key] >= ASHARE_LIMIT_NEAR])]
        for k in (0.90, 0.99, 1.00):
            if k < ASHARE_LIMIT_NEAR:
                segs.append((f"线降到 {k:.2f} ⇒ 新放进来的 [{k:.2f},{ASHARE_LIMIT_NEAR})",
                             sub[(sub[key] >= k) & (sub[key] < ASHARE_LIMIT_NEAR)]))
            else:
                segs.append((f"线升到 {k:.2f} ⇒ 本被挡、将放进的 [{ASHARE_LIMIT_NEAR},{k:.2f})",
                             sub[(sub[key] >= ASHARE_LIMIT_NEAR) & (sub[key] < k)]))
        for name, inc in segs:
            if not len(inc):
                rows.append(dict(判据=tag, 那一段=name, 票日=0))
                continue
            rows.append(dict(判据=tag, 那一段=name, 票日=len(inc),
                             平均每天=round(len(inc) / nd, 2),
                             真封死率=round(float(inc["真封死"].mean()), 4),
                             买得进率=round(float(inc["买得进"].mean()), 4),
                             次日开盘跳升中位=float(inc["次日开盘跳升"].median()),
                             五日到手中位=float(inc["五日到手"].median()),
                             超额中位=float(inc["超额(五日到手-全域中位)"].median())))
    mt = pd.DataFrame(rows)
    print(mt.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    mt.to_csv(os.path.join(OUT, "step3_挪档边际.csv"), index=False)

    print(f"\n===== 逐年（生产档 r ≥ {ASHARE_LIMIT_NEAR}）：白挡率与超额还在不在 =====")
    for key, tag in (("r同日", "同日(③名单侧)"), ("r次日", "次日(环2回放侧)")):
        sub = df[(df[key] >= ASHARE_LIMIT_NEAR)]
        y = sub.groupby("年").agg(票日=("年", "size"), 真封死率=("真封死", "mean"),
                                  买得进率=("买得进", "mean"),
                                  五日到手中位=("五日到手", "median"),
                                  超额中位=("超额(五日到手-全域中位)", "median"),
                                  正超额占比=("超额(五日到手-全域中位)", lambda v: (v > 0).mean()))
        print(f"\n----- {tag} -----")
        print(y.round(4).to_string())
        y.round(4).to_csv(os.path.join(OUT, f"step3_逐年_{tag}.csv"))

    print(f"\n===== 分板块（生产档 r ≥ {ASHARE_LIMIT_NEAR}）=====")
    for key, tag in (("r同日", "同日(③名单侧)"), ("r次日", "次日(环2回放侧)")):
        b = df[df[key] >= ASHARE_LIMIT_NEAR].groupby("板块").agg(
            票日=("板块", "size"), 真封死率=("真封死", "mean"), 买得进率=("买得进", "mean"),
            五日到手中位=("五日到手", "median"), 超额中位=("超额(五日到手-全域中位)", "median"),
            盘中触板率=("盘中触板", "mean"))
        print(f"\n----- {tag} -----")
        print(b.round(4).to_string())
        b.round(4).to_csv(os.path.join(OUT, f"step3_分板块_{tag}.csv"))

    # 极端 r 的杂质：这些格子里 r 远超 1.10 ⇒ 复权台阶/除权，不是真涨停（闸吃裸值，
    # 生产今天也一样会被它挡，所以要报个数让人知道表尾那几行不是行情）
    jnk = df[(df["r同日"] >= 1.10) | (df["r次日"] >= 1.10)]
    print(f"\n===== 杂质行数（r ≥ 1.10，多为复权台阶/除权）：{len(jnk)} 条 / {len(df):,} 条 "
          f"= {len(jnk) / max(1, len(df)):.3%}　其中 2026 年 {int((jnk['年'] == 2026).sum())} 条")
    if len(jnk):
        jnk.nlargest(10, "r同日")[["日", "板块", "r同日", "r次日", "当日涨幅", "次日开盘跳升",
                                   "五日到手"]].to_csv(
            os.path.join(OUT, "step3_极端r样本Top10.csv"), index=False)


def main():
    t0 = time.time()
    print(f"[生产档] ASHARE_LIMIT_NEAR={ASHARE_LIMIT_NEAR}　限幅 {ASHARE_BOARD_LIMIT_UP}　"
          f"持有 {ASHARE_PORT_HOLD} 天　下单 {ASHARE_ORDER_TOP_N} 席")
    wide, _b = load_panel()
    print(f"[面板] 载入 {(time.time() - t0):.0f}s　{wide['close'].shape}")
    fc = wide["factor"].astype("float64")
    mtx = build_matrices(wide)
    del wide
    ok, n_sealed = step1_cross_check(mtx, fc)
    del fc
    df = step2_grid(mtx)
    step3_readings(df)
    if not ok:
        print("\n⚠️ ① 没通过 ⇒ 上面所有「买得进率 / 真封死率」只当**下界**读，"
              "别拿去当拆闸或挪档的依据")
    print(f"\n[完成] 用时 {(time.time() - t0):.0f}s　产物在 {OUT}")


if __name__ == "__main__":
    main()
