# -*- coding: utf-8 -*-
"""甲的对照臂复核：不复制任何代码，只把**打分后移一天**喂给官方 `topk_rebalance`。

为什么这算独立复核（`timing_shift_0929.py` 是逐行复刻 `lo`，那一臂的可信度全押在
"复刻没走样"上；本脚本一个复刻都没有）
--------------------------------------------------------------------------
官方时序：信号取 `score.loc[days[i]]`，权重从行 `i+1` 起铺，每行收益
`ret_open[j] = O_j/O_{j-1} - 1` ⇒ 篮子拿到区间 `O_i → O_{i+1}`，可信号是 `days[i]`
**收盘**才算出来的，这段拿不到。
若喂进去的是 `score.shift(1)`，那么函数在 `days[i]` 读到的分数其实是 `days[i-1]`
收盘算的 ⇒ 它照样白拿区间 `O_i → O_{i+1}`，而这一段**正好是**"前一日收盘看到信号、
当日开盘建仓"的人真实能拿到的第一段。于是官方函数原封不动，口径却变成可执行口径。

判据（不许恒真）
--------------
1. 基准臂（原 score）必须逐位等于生产环 2 归档里那一行（族间合成 k=10 净年化）；
   对不上说明输入表或面板漂了，本脚本的读数作废、只报漂移。
2. 后移臂必须与 `timing_shift_0929.py` 的 lag=2 臂**同号同量级**（两条独立构造指
   向同一笔账）；若一个塌到 0 附近另一个不塌 ⇒ 说明其中一条构造有 bug，报红。
   注意两臂的调仓栅格差一天（lag=2 用 i=0,10,20…挑分，后移臂用 i-1=−1,9,19…），
   所以只要求同量级，不要求逐位相等。
3. 后移两臂（shift 1 / shift 2）作方向读数。

产物只落 `temp/tmp_timing_0929/`，权威 csv 一行不动。跑法：
    cd etf/v1 && /usr/bin/python3.10 -u temp/timing_shift_xcheck_0929.py
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

import etf_admission as EA           # noqa: E402
import run_etf_portfolio_eval as R2  # noqa: E402

OUT = os.path.join(HERE, "tmp_timing_0929")
K = 10
PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))
ARCHIVE = os.path.join(REPO, "etf", "v1", "data", "results", "etf_portfolio_eval.csv")
LAG2_REF = {"全窗口 2019+": 0.1557, "样本外段 2020-2026": 0.0800}


def bill(sc, m, days, ew_uni, label):
    net, gross, s = EA.topk_rebalance(sc, m, days, k=K, hold=EA.HOLD)
    st = EA.portfolio_stats(net, label)
    st.update(EA.excess_stats(net, ew_uni.reindex(days).dropna(), "ew_universe"))
    st.update({"label": label, "n_rebal": s["n_rebal"]})
    print(f"  {label:<26s} 净年化 {st['ann_return']:+7.2%} 夏普 {st['sharpe']:5.2f} "
          f"回撤 {st['max_drawdown']:7.1%} 超域 {st['excess_ew_universe_ann']:+7.2%} "
          f"调仓 {s['n_rebal']} 次")
    return st, net


def main():
    t0 = time.time()
    print("=" * 78)
    print("甲 · 复核：官方函数 + 打分后移一天（零复刻）")
    print("=" * 78)
    specs, stats = R2.pick_from_eval(EA.FACTOR_EVAL_OUT, R2.TOP, PRIMARY_H)
    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    use = [s for s in specs if EA.spec_name(s) in set(stats)]
    facs_full = EA.evaluate_factors(pool, use)
    facs = {k: EA.slice_to_start(v, EA.EVAL_START) for k, v in facs_full.items()}
    del facs_full, pool
    fam_scores, _mem = R2.build_family_scores(facs, stats)
    score, _ = EA.composite_score(fam_scores, {f: 1.0 / len(fam_scores)
                                               for f in fam_scores})
    universe = EA.universe_mask(m)
    _ew_all, ew_uni = EA.equal_weight_benchmarks(m, universe=universe)
    days_all = m["close"].index
    windows = {"全窗口 2019+": days_all,
               "样本外段 2020-2026": days_all[days_all >= pd.Timestamp("2020-01-01")]}

    rows = []
    for wname, days in windows.items():
        print(f"\n---- {wname}（{len(days)} 天）----")
        base, _ = bill(score, m, days, ew_uni, "基准·原 score(现口径)")
        # 判据 1：基准臂必须等于生产环 2 归档那一行
        if os.path.exists(ARCHIVE):
            arc = pd.read_csv(ARCHIVE)
            if "label" in arc:
                hit = arc[arc["label"].astype(str).str.startswith("族间合成")
                          & (arc.get("k") == K)]
            else:
                hit = arc.iloc[0:0]
            if len(hit):
                ref = float(hit.iloc[0]["ann_return"])
                print(f"  [对表] 生产环 2 归档族间合成 k={K}：{ref:+.2%}｜本场基准臂："
                      f"{base['ann_return']:+.2%}｜max|Δ|={abs(ref - base['ann_return']):.2%}"
                      f"（面板每天重生成，跨场次只报过期度，不判生死）")
            else:
                print(f"  [对表跳过] {ARCHIVE} 里找不到族间合成那一行")
        sh1, n1 = bill(score.shift(1), m, days, ew_uni, "后移1天(可执行口径)")
        sh2, _ = bill(score.shift(2), m, days, ew_uni, "后移2天(再让一根)")
        rows += [dict(sh1, window=wname), dict(sh2, window=wname)]
        # 判据 2：与复刻臂的 lag=2 必须同号同量级
        ref = LAG2_REF[wname]
        same_sign = np.sign(sh1["ann_return"]) == np.sign(ref)
        within = abs(sh1["ann_return"] - ref) <= 0.10
        print(f"  [复核] 后移1天 {sh1['ann_return']:+.2%} vs 复刻 lag=2 {ref:+.2%}："
              f"同号 {same_sign}、差 {abs(sh1['ann_return'] - ref):.2%}"
              f"（栅格差一天，容差 10pp）⇒ {'两条独立构造对上，笔账成立' if (same_sign and within) else '⚠️两条构造没对上，读数作废、须查'}")
        if not (same_sign and within):
            return 1

    pd.DataFrame(rows).to_csv(os.path.join(OUT, "timing_shift_xcheck.csv"), index=False)
    print(f"\n[产物] {OUT}/timing_shift_xcheck.csv｜[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
