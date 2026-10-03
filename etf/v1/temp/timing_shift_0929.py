# -*- coding: utf-8 -*-
"""甲：量「每次调仓新篮子早拿一根 K 线」到底值多少个点（只读，不改判据、不碰权威 csv）。

病根（逐行读过，非推测）
------------------------
`EA.topk_rebalance` 声明的时序是「信号日 s 收盘算分 → s+1 开盘建仓」，docstring
甚至写了「调仓日 d1 的收益仍归旧篮子」（etf_admission.py:1188）。但代码把新权重
从 `lo = i + 1`（= d1 那一行）开始铺（:1224-1226），而每一行的收益是
`ret_open[d] = O_d/O_{d-1} - 1`（:298），于是 d1 那一行记给新篮子的钱是
**从 s 开盘到 d1 开盘** —— 可 s 的信号要到 s 收盘才知道，这段拿不到。
真正可执行的那一段应从 `O_{d1}` 起算，即权重应铺在 `lo = i + 2` 那一行往后。

现有三条 topk_rebalance 测试全用平值池（价格恒 10 元、毛收益恒 0，
tests/test_etf_admission.py:303/314/714）⇒ 把权重挪一天在平值池上差是 0，**这条
偏差没有任何一条测试盯着**。本探针就是给它补上第一次实测。

三臂对照（同一份打分、同一次加载、只差 `lo` 那一格）
--------------------------------------------------
    lag=1  现口径（官方函数本身，作为基准臂）
    lag=2  可执行口径：新篮子从建仓**次日**那行才开始拿收益
    lag=3  敏感性：再多让一根。若代价随 lag 单调变大 ⇒ 偏差确实是信号衰减，不是噪音

判据（不许恒真）
--------------
1. **复刻必须逐位等于官方**：我的 lag=1 臂与 `EA.topk_rebalance` 的净日收益
   max|Δ| 必须 < 1e-18，否则复刻走样、全场读数作废（直接 exit 1）。
2. **两臂必须真的有牙**：max|Δ(1,2)| 必须 > 0 且差异天数 > 0；若两臂恒等，
   只准报「无牙」，不准报结论。
3. 成本腿**不动**（两臂都记在 i+1），所以臂间差纯粹来自"哪几天的收益记给哪个篮子"，
   不含费用口径差。代价：lag=2 那臂费用比持仓早一天，一天复利量级 ~1e-4，忽略。

产物只落 `temp/tmp_timing_0929/`。跑法：
    cd etf/v1 && /usr/bin/python3.10 -u temp/timing_shift_0929.py
冒烟（只跑合成那条，不跑八族）：TS_FAST=1 同上
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "etf", "v1", "src"))
import _bootstrap  # noqa: E402,F401

import etf_admission as EA          # noqa: E402
import run_etf_portfolio_eval as R2  # noqa: E402

OUT = os.path.join(HERE, "tmp_timing_0929")
K = 10
PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))
FWD = f"fwd{PRIMARY_H}"
LAGS = (1, 2, 3)
FAST = os.environ.get("TS_FAST", "0") == "1"


def replay_lag(score, m, days, k, hold, lag, cost=None, min_listed=None,
               min_amount=None, min_scale=None):
    """`EA.topk_rebalance` 的逐行复刻，唯一改动是权重起始行 `lo = i + lag`。

    lag=1 与官方逐项相等（本函数存在的全部理由：不改生产代码也能量另一臂）。
    费用、闸门、翻向、缺失格按 0 的处理一律照抄 :1198-1251。
    """
    min_listed = EA.MIN_LISTED if min_listed is None else min_listed
    min_amount = EA.MIN_AMOUNT if min_amount is None else min_amount
    min_scale = EA.MIN_SCALE if min_scale is None else min_scale
    ncols = m["close"].shape[1]
    col_of = {c: i for i, c in enumerate(m["close"].columns)}
    w = np.zeros((len(days), ncols), dtype="float64")
    cost_arr = np.zeros(len(days), dtype="float64")
    prev, phis, n_rebal, limits, sizes, amts = None, [], 0, [], [], []
    slip_rows = []
    n_missing = 0
    for i in range(0, len(days) - hold - 1, hold):
        s, d1 = days[i], days[i + 1]
        ok, n_block = EA.tradable_mask(m, s, d1, min_listed, min_amount,
                                       min_scale=min_scale)
        cand = score.loc[s].where(ok).dropna()
        if len(cand) < max(1, k // 2):
            continue
        basket = list(cand.sort_values(ascending=False).index[:k])
        if not basket:
            continue
        c = EA.cost_series(m, s, cost)
        pset = set(prev or [])
        bset = set(basket)
        buys = [b for b in basket if b not in pset]
        sells = [b for b in prev or [] if b not in bset]
        cost_arr[i + 1] += (float(c[buys].sum()) / max(1, len(basket))
                            + float(c[sells].sum()) / max(1, len(prev or basket)))
        slip_rows.append(float(c[basket].mean()))
        phi = 1.0 if prev is None else 1.0 - len(pset & bset) / k
        lo, hi = i + lag, min(i + lag + hold, len(days))
        w[lo:hi] = 0.0
        if lo < len(days):
            w[lo:hi, [col_of[b] for b in basket]] = 1.0 / len(basket)
        prev, n_rebal = basket, n_rebal + 1
        phis.append(phi)
        limits.append(n_block)
        sizes.append(len(basket))
        amts.append(float(m["amount20"].loc[s, basket].mean()))
        Rsub = m["ret_open"].iloc[lo:hi]
        n_missing += int(Rsub[basket].isna().to_numpy().sum())
    if slip_rows:
        cost_arr[-1] += slip_rows[-1]
    R = np.nan_to_num(m["ret_open"].reindex(index=days).to_numpy("float64"), nan=0.0)
    gross = pd.Series((w * R).sum(axis=1), index=days)
    net = gross - pd.Series(cost_arr, index=days)
    return net, gross, {"n_rebal": n_rebal,
                        "one_way_turnover": float(np.mean(phis)) if phis else np.nan,
                        "exposed_days": int((np.abs(w).sum(axis=1) > 0).sum()),
                        "n_missing_in_basket": n_missing}


def row(label, arm, net, s, ew_uni):
    st = EA.portfolio_stats(net, arm)
    st.update(EA.excess_stats(net, ew_uni.reindex(net.index).dropna(), "ew_universe"))
    st.update({"label": label, "arm": arm, "n_rebal": s["n_rebal"],
               "exposed_days": s["exposed_days"],
               "turnover_ann": (s["one_way_turnover"] * EA.TRADING_DAYS / EA.HOLD
                                if np.isfinite(s["one_way_turnover"]) else np.nan)})
    print(f"    {label:<22s} {arm:<7s} 净年化 {st['ann_return']:+7.2%} "
          f"夏普 {st['sharpe']:5.2f} 回撤 {st['max_drawdown']:7.1%} "
          f"超域 {st['excess_ew_universe_ann']:+7.2%} "
          f"在仓 {s['exposed_days']:4d}/{len(net):4d} 天 "
          f"调仓 {s['n_rebal']:3d} 次")
    return st, net


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    print("=" * 78)
    print("甲 · 调仓时序早一根的代价（同一份打分，只挪权重起始行）")
    print("=" * 78)
    for k, v in EA.run_params().items():
        print(f"  {k:14s} = {v}")
    print(f"  hold/K          = {EA.HOLD}/{K}   lag 臂 = {LAGS}")

    specs, stats = R2.pick_from_eval(EA.FACTOR_EVAL_OUT, R2.TOP, PRIMARY_H)
    weights, _ = EA.icir_weights(stats, top=R2.PICK)
    print(f"\n[输入] 环 1 归档 {EA.FACTOR_EVAL_OUT}：{len(stats)} 条候选 / "
          f"{len({st.get('family') for st in stats.values()})} 族；"
          f"因子层初选 {len(weights)} 条（族层合成不吃这个名额）")

    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    need = set(stats)
    use = [s for s in specs if EA.spec_name(s) in need]
    facs_full = EA.evaluate_factors(pool, use)
    facs = {k: EA.slice_to_start(v, EA.EVAL_START) for k, v in facs_full.items()}
    del facs_full, pool
    print(f"[因子] {len(facs)} 条求值成功，耗时 {time.time() - t0:.0f}s")

    fam_scores, fam_members = R2.build_family_scores(facs, stats)
    score, _used = EA.composite_score(fam_scores, {f: 1.0 / len(fam_scores)
                                                   for f in fam_scores})
    universe = EA.universe_mask(m)
    _ew_all, ew_uni = EA.equal_weight_benchmarks(m, universe=universe)
    days_all = m["close"].index
    windows = {"全窗口 2019+": days_all,
               "样本外段 2020-2026": days_all[days_all >= pd.Timestamp("2020-01-01")]}
    targets = [("族间合成(现行口径)", score)] + ([] if FAST else
                 [(f"单族·{f}", sc) for f, sc in fam_scores.items()])

    rows, series = [], {}
    for lab, sc in targets:
        print(f"\n---- {lab} ----")
        for wname, days in windows.items():
            off_net, _g, off_s = EA.topk_rebalance(sc, m, days, k=K, hold=EA.HOLD)
            n1, g1, s1 = replay_lag(sc, m, days, K, EA.HOLD, lag=1)
            dmax = float((n1 - off_net).abs().max())
            if not dmax < 1e-18:
                print(f"[复刻失败] {lab}｜{wname}：lag=1 与官方 max|Δ|={dmax:.3e} —— "
                      f"复刻走样，本读数作废，停止")
                return 1
            if s1["n_rebal"] != off_s["n_rebal"]:
                print(f"[复刻失败] 调仓次数 {s1['n_rebal']} != 官方 {off_s['n_rebal']}")
                return 1
            print(f"  [{wname}] 复刻对表通过：{len(days)} 天、{s1['n_rebal']} 次调仓、"
                  f"max|Δ|={dmax:.1e}（<1e-18 才算过）")
            stats_arm, nets = {}, {}
            for lag in LAGS:
                if lag == 1:
                    st, net = row(lab, f"lag={lag}", n1, s1, ew_uni)
                else:
                    nL, _gL, sL = replay_lag(sc, m, days, K, EA.HOLD, lag=lag)
                    st, net = row(lab, f"lag={lag}", nL, sL, ew_uni)
                stats_arm[f"lag={lag}"], nets[f"lag={lag}"] = st, net
                rows.append(dict(st, window=wname))
            a, b = stats_arm["lag=1"], stats_arm[f"lag={LAGS[1]}"]
            delta = float(b["ann_return"] - a["ann_return"])
            n_diff = int((nets["lag=1"] - nets[f"lag={LAGS[1]}"]).abs().gt(1e-15).sum())
            if wname == "全窗口 2019+" and lab == targets[0][0]:
                if n_diff == 0 or abs(delta) < 1e-12:
                    print(f"  [无牙] 两臂逐日恒等（差异 {n_diff} 天、Δ年化 {delta:+.2e}）"
                          f"⇒ 这根偏移在当前数据上不产生差别，不得据此报结论")
                    return 1
                print(f"  [有牙] 差异 {n_diff} 个交易日 / {len(days)} 天；"
                      f"现口径 - 可执行口径 = 净年化 {-delta:+.2%}、"
                      f"夏普 {-b['sharpe'] + a['sharpe']:+.2f}、"
                      f"回撤 {b['max_drawdown'] - a['max_drawdown']:+.1%}")
                series["lag1"], series["lag2"] = nets["lag=1"], nets[f"lag={LAGS[1]}"]

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "timing_shift_arms.csv"), index=False)
    pd.DataFrame(series).to_csv(os.path.join(OUT, "timing_shift_nets_composite.csv"))

    print("\n===== 逐年净年化：现口径(lag=1) vs 可执行(lag=2) =====")
    a, b = series["lag1"], series["lag2"]
    yb = pd.DataFrame({"lag1": a, "lag2": b})
    yr = ((1 + yb).groupby(yb.index.year).prod() - 1).mul(100).round(2)
    yr["Δ(现-可)"] = (yr["lag1"] - yr["lag2"]).round(2)
    print(yr.to_string())
    print("\n===== 汇总（同一窗口内三臂并排）=====")
    pd.set_option("display.width", 250)
    print(df.pivot_table(index=["window", "label"], columns="arm",
                         values="ann_return").mul(100).round(2).to_string())
    comp = df[df.label == "族间合成(现行口径)"]
    for wname in windows:
        sub = comp[comp.window == wname].set_index("arm")
        print(f"\n[{wname}] 现行口径比可执行口径多报净年化 "
              f"{(sub.loc['lag=1', 'ann_return'] - sub.loc['lag=2', 'ann_return']) * 100:+.2f}pp"
              f"（lag=3 再多让一根："
              f"{(sub.loc['lag=2', 'ann_return'] - sub.loc['lag=3', 'ann_return']) * 100:+.2f}pp"
              f"）")
    fam = comp[comp.label.str.startswith("单族")]
    if len(fam):
        dd = (fam.set_index("arm").loc["lag=1", "ann_return"]
              - fam.set_index("arm").loc["lag=2", "ann_return"])
        print(f"[八族] 单族层面这笔账的分布："
              + " ".join(f"{v * 100:+.2f}" for v in np.sort(np.asarray(dd, dtype=float))))
    print(f"\n[产物] {OUT}/timing_shift_{{arms,nets_composite}}.csv（权威 csv 一行未动）")
    print(f"[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
