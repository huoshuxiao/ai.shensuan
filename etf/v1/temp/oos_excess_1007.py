# -*- coding: utf-8 -*-
"""修法一落地后，把「样本外超可投域等权」这句**单独**量一遍（只读）。

为什么还要再跑一场：09-29 预登记的后果是「样本外超等权可投域 +27.54pp 翻成
−0.94~−2.23pp（翻负）」。今天环 2 归档重跑完，**全窗口**那一列是
`excess_ew_universe_ann` = +3.80pp（还是正的），而分年度表的 2020~2026 **累计复利**
差是 +51pp（更大）——两个读数与「翻负」对不上。对不上的原因不是谁算错，是
**三个统计量本来就不是一回事**：

    `excess_stats`   日差算术年化 = 252·mean(r_port − r_bench)
    累计复利差        (Π(1+r_port) − Π(1+r_bench))，含基准自己的波动拖累
    年化之差          252·mean(r_port) − 252·mean(r_bench)

预登记那句用的是第一个。所以本脚本只算第一个，并且**直接调生产那个函数**
`EA.excess_stats`，不自己抄公式（抄一遍就多一台可能算错的机器）。

三道闸，边算边打（崩了也不丢已经算出来的行）：
    G0 全窗口 official 的 `excess_ew_universe_ann` 必须与刚落盘的归档
       `data/results/etf_portfolio_eval.csv` 同一格逐位相等（|Δ|<1e-12）
       —— 这一格闭上，才可以说下面的样本外读数与归档同一套算术；
    G0b 全窗口 official 净年化同样对表归档 `ann_return`；
    G1 每个臂都要与基准的**索引交集**非空且日期数一致（防止静默丢日子把超额算歪）。

产物只落 `/tmp/oos_excess_1007/`，本线 `data/` 一个字节不写。
跑法：
    cd etf/v1 && /usr/bin/python3.10 -u temp/oos_excess_1007.py
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

import etf_admission as EA            # noqa: E402
import run_etf_portfolio_eval as R2   # noqa: E402

sys.path.insert(0, HERE)
from timing_shift_0929 import replay_lag  # noqa: E402  借 lag=1 臂当「修法前」

OUT = "/tmp/oos_excess_1007"
KS = (10, 20)
PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))
OOS_FROM = pd.Timestamp("2020-01-01")


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    print("=" * 78)
    print("修法一后 · 样本外超可投域等权（`excess_stats` 日差算术年化，直调生产函数）")
    print("=" * 78)

    arch = pd.read_csv(EA.PORT_EVAL_OUT)
    arch["label"] = arch["label"].astype(str)
    print(f"[对表基准] 归档 {EA.PORT_EVAL_OUT}：{len(arch)} 行")

    specs, stats = R2.pick_from_eval(EA.FACTOR_EVAL_OUT, R2.TOP, PRIMARY_H)
    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    use = [s for s in specs if EA.spec_name(s) in set(stats)]
    facs_full = EA.evaluate_factors(pool, use)
    facs = {k: EA.slice_to_start(v, EA.EVAL_START) for k, v in facs_full.items()}
    del facs_full, pool
    print(f"[因子] {len(facs)} 条，耗时 {time.time() - t0:.0f}s")

    fam_scores, _mem = R2.build_family_scores(facs, stats)
    score, _used = EA.composite_score(fam_scores, {f: 1.0 / len(fam_scores)
                                                   for f in fam_scores})
    universe = EA.universe_mask(m)
    _ew_all, ew_uni = EA.equal_weight_benchmarks(m, universe=universe)
    days_all = m["close"].index
    windows = {"全窗口": days_all, "样本外段": days_all[days_all >= OOS_FROM]}

    # 基准自己在两个窗口的年化（不扣费、开盘到开盘）
    print("\n---- 基准·可投域等权自己 ----")
    for wname, days in windows.items():
        st = EA.portfolio_stats(ew_uni.reindex(days).fillna(0.0), wname)
        print(f"  {wname:<6s} 年化 {st['ann_return']:+7.2%}｜夏普 {st['sharpe']:.3f}"
              f"｜回撤 {st['max_drawdown']:+7.2%}｜{len(days)} 天")

    targets = [("族间合成·8族等权", score, "family_composite")]
    rev = [f for f in fam_scores if "反转" in f]
    targets += [(f"单族·{rev[0]}(修法外)", fam_scores[rev[0]], None)] if rev else []

    rows, fails = [], []
    for lab, sc, kind in targets:
        for kk in KS:
            if kind is None and kk != 10:      # 单族只当对照，省一半回放
                continue
            print(f"\n---- {lab}｜k={kk} ----")
            for wname, days in windows.items():
                net, _g, s = EA.topk_rebalance(sc, m, days, k=kk, hold=EA.HOLD)
                l1_net, _g1, _s1 = replay_lag(sc, m, days, kk, EA.HOLD, lag=1)
                ex_off = EA.excess_stats(net, ew_uni, "ew_universe")
                ex_old = EA.excess_stats(l1_net, ew_uni, "ew_universe")
                j = pd.concat([net, ew_uni], axis=1, join="inner").dropna()
                ok_join = (len(j) == len(days))
                a_off = EA.portfolio_stats(net)["ann_return"]
                a_old = EA.portfolio_stats(l1_net)["ann_return"]
                line = (f"  {wname:<6s} 修后年化 {a_off:+7.2%}｜超可投域 "
                        f"{ex_off['excess_ew_universe_ann']:+7.2%}"
                        f"（IR {ex_off['excess_ew_universe_ir']:+.2f}）"
                        f"｜修法前年化 {a_old:+7.2%}｜超可投域 "
                        f"{ex_old['excess_ew_universe_ann']:+7.2%}"
                        f"｜这一刀 {(ex_old['excess_ew_universe_ann'] - ex_off['excess_ew_universe_ann']) * 100:+6.2f}pp")
                # G0：全窗口那两格必须与刚落盘的归档逐位对上
                if wname == "全窗口" and kind is not None:
                    hit = arch[(arch["kind"] == kind) & (arch["k"] == kk)
                               & (arch["composite_level"] == "family")]
                    if len(hit) != 1:
                        line += f"\n        G0 ✗ 归档里没找到唯一那一行（命中 {len(hit)} 行）"
                        fails.append(f"{lab} k={kk} 归档定位失败")
                    else:
                        d_ex = abs(float(hit["excess_ew_universe_ann"].iloc[0])
                                   - ex_off["excess_ew_universe_ann"])
                        d_an = abs(float(hit["ann_return"].iloc[0]) - a_off)
                        ok = (d_ex < 1e-12 and d_an < 1e-12)
                        line += (f"\n        G0 对表归档 |Δ超额|={d_ex:.3e}"
                                 f"｜|Δ年化|={d_an:.3e} {'✓' if ok else '✗'}"
                                 f"｜G1 交集 {len(j)}/{len(days)} {'✓' if ok_join else '✗'}")
                        if not ok or not ok_join:
                            fails.append(f"{lab} k={kk} 全窗口对表")
                else:
                    line += (f"\n        G1 交集 {len(j)}/{len(days)} "
                             f"{'✓' if ok_join else '✗'}")
                    if not ok_join:
                        fails.append(f"{lab} k={kk} {wname} 交集缺日")
                print(line)
                rows.append({"label": lab, "k": kk, "window": wname,
                             "ann_official": a_off,
                             "excess_official": ex_off["excess_ew_universe_ann"],
                             "ir_official": ex_off["excess_ew_universe_ir"],
                             "ann_lag1_修法前": a_old,
                             "excess_lag1_修法前": ex_old["excess_ew_universe_ann"],
                             "join_days": len(j), "window_days": len(days)})

    pd.DataFrame(rows).to_csv(os.path.join(OUT, "excess_arms.csv"), index=False)
    print("\n===== 判定 =====")
    print(f"[产物] {OUT}/excess_arms.csv｜耗时 {time.time() - t0:.0f}s")
    if fails:
        for f in fails:
            print(f"  ✗ {f}")
        print("[结论] 对表闸没闭上——上面这些读数不能用，停止")
        return 1
    print("  ✅ G0 逐位对上归档、G1 交集无缺日 ⇒ 样本外那几行与归档同一套算术")
    return 0


if __name__ == "__main__":
    sys.exit(main())
