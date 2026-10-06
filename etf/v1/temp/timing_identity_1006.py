# -*- coding: utf-8 -*-
"""修法一落地的**逐位身份闸**（只读）：官方 `topk_rebalance` 必须逐位等于 09-29 那把
独立复刻尺的 `lag=2` 臂，而且必须与 `lag=1`（改之前的行为）差出一大截。

为什么这一场不是"跑一下看看"：
    09-29 的 `temp/timing_shift_0929.py` 是**逐行复刻**出来的第二台机器，当年用它的
    `lag=2` 臂量出这一刀值 27.8~29.9pp 净年化/年（族间合成全窗口 43.38% → 15.57%）。
    今天把生产那一行改成 `i + 2` 之后，"改对了"唯一的硬证据是：
        G1  官方 == 复刻 lag=2，逐位相等（max|Δ| 必须恰好 0.0）
        G1b 调仓次数逐臂相等
        G2  官方 vs 复刻 lag=1（旧行为）必须**动得起来**（牙）
    只看年化掉到 15% 附近不算数——那是同一份代码自己报的数；G1 才是两台独立机器对表。

本脚本不写 `etf/v1/data/` 一个字节，产物只落 `/tmp/timing_identity_1006/`。
跑法：
    cd etf/v1 && /usr/bin/python3.10 -u temp/timing_identity_1006.py
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
from timing_shift_0929 import replay_lag  # noqa: E402  09-29 那把逐行复刻尺，只借函数

OUT = "/tmp/timing_identity_1006"
K = 10
PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))


def ann(x):
    return float(pd.Series(x).dropna().mean() * EA.TRADING_DAYS)


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    print("=" * 78)
    print("修法一 · 官方 vs 复刻 lag=2 的逐位身份闸（lag=1 = 改之前的行为，只当牙）")
    print("=" * 78)
    for k, v in EA.run_params().items():
        print(f"  {k:14s} = {v}")

    specs, stats = R2.pick_from_eval(EA.FACTOR_EVAL_OUT, R2.TOP, PRIMARY_H)
    print(f"\n[输入] 环 1 归档 {EA.FACTOR_EVAL_OUT}：{len(stats)} 条候选 / "
          f"{len({st.get('family') for st in stats.values()})} 族")

    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    need = set(stats)
    use = [s for s in specs if EA.spec_name(s) in need]
    facs_full = EA.evaluate_factors(pool, use)
    facs = {kk: EA.slice_to_start(v, EA.EVAL_START) for kk, v in facs_full.items()}
    del facs_full, pool
    print(f"[因子] {len(facs)} 条求值成功，耗时 {time.time() - t0:.0f}s")

    fam_scores, fam_members = R2.build_family_scores(facs, stats)
    score, _used = EA.composite_score(fam_scores, {f: 1.0 / len(fam_scores)
                                                   for f in fam_scores})
    days_all = m["close"].index
    windows = {"全窗口": days_all,
               "样本外段": days_all[days_all >= pd.Timestamp("2020-01-01")]}
    targets = [("族间合成", score)] + [(f"单族·{f}", sc) for f, sc in fam_scores.items()]

    rows, fails = [], []
    for lab, sc in targets:
        print(f"\n---- {lab} ----")
        for wname, days in windows.items():
            off_net, _og, off_s = EA.topk_rebalance(sc, m, days, k=K, hold=EA.HOLD)
            l2_net, _g2, s2 = replay_lag(sc, m, days, K, EA.HOLD, lag=2)
            l1_net, _g1, s1 = replay_lag(sc, m, days, K, EA.HOLD, lag=1)
            d_ident = float((off_net - l2_net).abs().max())
            d_teeth = float((off_net - l1_net).abs().max())
            n_diff = int((off_net - l1_net).abs().gt(1e-15).sum())
            ok_ident = (d_ident == 0.0)
            ok_cnt = (s2["n_rebal"] == off_s["n_rebal"] == s1["n_rebal"])
            ok_teeth = (d_teeth > 1e-12 and n_diff > 0)
            tag = "过" if (ok_ident and ok_cnt and ok_teeth) else "红"
            print(f"  {wname:<6s} 官方净年化 {ann(off_net):+7.2%}｜复刻lag2 "
                  f"{ann(l2_net):+7.2%}｜复刻lag1(旧) {ann(l1_net):+7.2%} "
                  f"⇒ 这一刀 {(ann(l1_net) - ann(off_net)) * 100:+6.2f}pp")
            print(f"        G1 身份 max|Δ|={d_ident:.3e}（须恰好 0）{'✓' if ok_ident else '✗'}"
                  f"｜G1b 调仓 {off_s['n_rebal']}/{s2['n_rebal']}/{s1['n_rebal']} "
                  f"{'✓' if ok_cnt else '✗'}"
                  f"｜G2 牙 max|Δ|={d_teeth:.3e}、{n_diff} 格不等 "
                  f"{'✓' if ok_teeth else '✗'}  [{tag}]")
            if tag == "红":
                fails.append(f"{lab}｜{wname}")
            rows.append({"label": lab, "window": wname, "arm": "official(=i+2)",
                         "ann": ann(off_net), "sharpe": EA.portfolio_stats(off_net)["sharpe"],
                         "mdd": EA.portfolio_stats(off_net)["max_drawdown"],
                         "n_rebal": off_s["n_rebal"], "ident_max_abs_diff": d_ident,
                         "teeth_max_abs_diff": d_teeth, "teeth_days": n_diff})
            rows.append({"label": lab, "window": wname, "arm": "replica_lag2",
                         "ann": ann(l2_net), "n_rebal": s2["n_rebal"]})
            rows.append({"label": lab, "window": wname, "arm": "replica_lag1(旧口径)",
                         "ann": ann(l1_net), "n_rebal": s1["n_rebal"]})

    pd.DataFrame(rows).to_csv(os.path.join(OUT, "identity_arms.csv"), index=False)
    print("\n===== 判定 =====")
    print(f"[产物] {OUT}/identity_arms.csv｜耗时 {time.time() - t0:.0f}s")
    if fails:
        for f in fails:
            print(f"  ✗ {f}")
        print("[结论] 身份闸或牙没闭上——上面的数不能用，生产那一行要重看")
        return 1
    print("  ✅ 全部臂：官方与复刻 lag=2 逐位相等、调仓次数同、与 lag=1 差得开")
    return 0


if __name__ == "__main__":
    sys.exit(main())
