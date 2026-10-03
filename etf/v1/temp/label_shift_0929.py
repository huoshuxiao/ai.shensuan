# -*- coding: utf-8 -*-
"""丙：把「标签」那一腿也换成可执行口径，量同一把尺子重新打分后还剩多少。

为什么必须量这一腿
------------------
`timing_shift_0929.py` 只挪了回放里"记收益的起始行"，**因子名单、符号、权重全靠
环 1 那把尺子**：环 1 的前向收益 `fwd_h[t] = Π(1+r_{t+1..t+h}) - 1` 是从 **t 日收盘**
起算的（etf_admission.py:260-273），而 t 日收盘这个价位本线实盘（人工下单）买不到。
⇒ 只修回放，喂给它的还是那批"抢得到一天"的因子。本脚本把两腿一起换到可执行口径。

两把尺子（h=hold=10，与回放同一持有期）
--------------------------------------
    A 现口径   L_A[t] = Π_{k=1..h}(1+ret_close[t+k]) - 1   = P_{t+h}/P_t - 1
                ⇒ t 收盘就成交（买不到）
    B 可执行   L_B[t] = O_{t+h+1}/O_{t+1} - 1
                = forward_returns(ret_open, h).shift(-1)
                ⇒ 信号 t 收盘出、t+1 开盘建仓、t+1+h 开盘卖出 —— 与 lag=2 那臂
                   逐位同持有一致（回放真拿到手的那一段）

判据（不许恒真、不许无牙）
--------------------------
1. **这台 IC 机器先验过**：A 臂逐条重算的 `sign(RankIC)` 必须与环 1 归档
   `etf_factor_eval.csv` 一致（|IC|>0.005 者若翻向 ⇒ 我的尺子和环 1 的不是同一台，
   拿它判 B 臂无效，exit 1）。近零者翻向只报 ⚠️ 不拦。
2. **B 臂必须有牙**：L_A 与 L_B 两份标签矩阵 max|Δ| 必须 > 0，且 31 条里至多 25 条
   的 RankIC 序列两臂相同；若两臂逐条恒等 ⇒ 换标签在这份数据上不产生差别，
   只准报「无牙」，exit 1。
3. **回放复刻再验一次**：`replay_lag(lag=1)` 必须逐位等于官方 `topk_rebalance`
   （<1e-18），否则本脚本引用的 lag=2 读数作废。

复用不复制：`replay_lag` 直接从 `timing_shift_0929.py` import（那场已过对表闸）。
产物只落 `temp/tmp_label_shift_0929/`，`data/results/` 一行不动。跑法：
    cd etf/v1 && /usr/bin/python3.10 -u temp/label_shift_0929.py
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
sys.path.insert(0, HERE)

import etf_admission as EA            # noqa: E402
import run_etf_portfolio_eval as R2   # noqa: E402
from timing_shift_0929 import replay_lag, K  # noqa: E402

OUT = os.path.join(HERE, "tmp_label_shift_0929")
PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))
FWD_A = f"fwd{PRIMARY_H}"
SIGN_TOL = 0.005
MIN_ABS = 0.05


def exec_label(m, h):
    """L_B：t 收盘出信号 → t+1 开盘建仓 → t+1+h 开盘卖出，真正拿到的那段。"""
    return EA.forward_returns(m["ret_open"], h).shift(-1)


def ic_table(facs, label, tag):
    """31 条候选 × 一把标签 → RankIC 序列与汇总（与环 2 同一套调用）。"""
    ric = {n: EA.daily_cs_ic(facs[n], label, min_cs=EA.MIN_CS)[1] for n in facs}
    rows = []
    for n, s in ric.items():
        su = EA.ic_summary(s)
        rows.append({"name": n, "arm": tag, "rank_ic": su["mean"],
                      "rank_icir": su["icir"], "n_days": int(s.notna().sum())})
    return ric, pd.DataFrame(rows).set_index("name")


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    print("=" * 78)
    print("丙 · 标签也换成可执行口径：谁还活着 / 合成还剩多少")
    print("=" * 78)
    print(f"  hold/K = {EA.HOLD}/{K}   primary_h = {PRIMARY_H}   min_cs = {EA.MIN_CS}")

    specs, stats = R2.pick_from_eval(EA.FACTOR_EVAL_OUT, R2.TOP, PRIMARY_H)
    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    use = [s for s in specs if EA.spec_name(s) in set(stats)]
    facs_full = EA.evaluate_factors(pool, use)
    facs = {k: EA.slice_to_start(v, EA.EVAL_START) for k, v in facs_full.items()}
    del facs_full, pool
    days = m["close"].index
    print(f"[因子] {len(facs)} 条 × {len(days)} 天，耗时 {time.time() - t0:.0f}s")

    lab_a, lab_b = m[FWD_A], exec_label(m, PRIMARY_H)
    # 判据 2a：两把尺子必须真的不一样
    dl = (lab_a - lab_b).abs().to_numpy()
    max_dl = float(np.nanmax(dl)) if np.isfinite(dl).any() else float("nan")
    cov = int((lab_a.notna() & lab_b.notna()).to_numpy().sum())
    print(f"\n[标签对表] A(收盘成交) vs B(开盘建仓)：共同有效格 {cov}、"
          f"max|Δ|={max_dl:.4f}、med|Δ|={float(np.nanmedian(dl)):.5f}")
    if not max_dl > 0:
        print("[无牙] 两份标签逐格相等 ⇒ 换标签不产生差别，不得据此报结论")
        return 1

    print(f"[RankIC] A 臂 {len(facs)} 条 …")
    ric_a, ta = ic_table(facs, lab_a, "A现口径")
    print(f"[RankIC] B 臂 {len(facs)} 条 …  累计 {time.time() - t0:.0f}s")
    ric_b, tb = ic_table(facs, lab_b, "B可执行")

    # 判据 2b：IC 序列层面两臂必须真的分开
    same = sum(1 for n in facs
               if np.array_equal(np.nan_to_num(ric_a[n].to_numpy()),
                                 np.nan_to_num(ric_b[n].to_numpy())))
    print(f"[有牙] 31 条里两臂 RankIC 序列完全相同的 {same} 条（必须 ≤25 才算分开）")
    if same > 25:
        print("[无牙] 两臂几乎同一条尺子 ⇒ 停止")
        return 1

    # 判据 1：A 臂必须先等于环 1 那台尺子
    arc = pd.read_csv(EA.FACTOR_EVAL_OUT)
    arc = arc[arc["status"] == "ok"].set_index("name")
    key = f"rank_ic_h{PRIMARY_H}"
    both = ta.join(arc[[key]], how="inner")
    flip = both[(both["rank_ic"].abs() > SIGN_TOL)
                & (np.sign(both["rank_ic"]) != np.sign(both[key]))]
    drift = float((both["rank_ic"] - both[key]).abs().max())
    print(f"[A 臂对表] {len(both)} 条可比：max|Δ RankIC|={drift:.5f}（跨场次只报过期度）；"
          f"|IC|>{SIGN_TOL} 却翻向的 {len(flip)} 条")
    if len(flip):
        print("  ⚠️ 翻向名单：" + " ".join(f"{i}({r.rank_ic:+.4f} vs 归档{r[key]:+.4f})"
                                        for i, r in flip.iterrows()))
        return 1

    cmp_ = ta.join(tb, lsuffix="_A", rsuffix="_B", how="outer")
    cmp_["family"] = [stats.get(n, {}).get("family", "未标族") for n in cmp_.index]
    cmp_["符号"] = np.where(np.sign(cmp_["rank_ic_A"]) == np.sign(cmp_["rank_ic_B"]),
                            "同", "翻")
    cmp_["过闸A"] = cmp_["rank_icir_A"].abs() >= MIN_ABS
    cmp_["过闸B"] = cmp_["rank_icir_B"].abs() >= MIN_ABS
    cmp_ = cmp_.sort_values(["family", "rank_icir_A"], ascending=[True, False])
    cmp_.to_csv(os.path.join(OUT, "label_shift_ic.csv"))

    print("\n===== 31 条候选：两把尺子的读数（RankIC / RankICIR）=====")
    print(cmp_[["family", "rank_ic_A", "rank_ic_B", "rank_icir_A", "rank_icir_B",
                "符号", "过闸A", "过闸B"]].round(4).to_string())
    print(f"\n[换人账] 符号翻向 {int((cmp_['符号'] == '翻').sum())}/{len(cmp_)} 条；"
          f"|RankICIR|≥{MIN_ABS} 过闸 A {int(cmp_['过闸A'].sum())} 条 → "
          f"B {int(cmp_['过闸B'].sum())} 条")

    universe = EA.universe_mask(m)
    _ew_all, ew_uni = EA.equal_weight_benchmarks(m, universe=universe)
    days_2020 = days[days >= pd.Timestamp("2020-01-01")]
    windows = {"全窗口 2019+": days, "样本外段 2020-2026": days_2020}

    def composite_from(ric_df, sc_source):
        """族内全员等权（按该臂自己的 RankIC 符号翻向）→ 族间一族一票。"""
        st = {n: {"rank_ic": r["rank_ic"], "family": r["family"]}
              for n, r in ric_df.iterrows()}
        fam_scores, members = R2.build_family_scores(sc_source, st)
        score, _ = EA.composite_score(fam_scores,
                                      {f: 1.0 / len(fam_scores) for f in fam_scores})
        return score, fam_scores, members

    sc_a, fam_a, mem_a = composite_from(ta, facs)
    sc_b, fam_b, mem_b = composite_from(tb, facs)
    print(f"\n[族] {len(mem_a)} 族 × {sum(len(v) for v in mem_a.values())} 条："
          + " ".join(f"{f}({len(v)})" for f, v in sorted(mem_a.items())))

    rows = []

    def bill(sc, lag, wname, label, m_days):
        net, _g, s = replay_lag(sc, m, m_days, K, EA.HOLD, lag=lag)
        st = EA.portfolio_stats(net, label)
        st.update(EA.excess_stats(net, ew_uni.reindex(m_days).dropna(), "ew_universe"))
        st.update({"label": label, "lag": lag, "window": wname,
                   "n_rebal": s["n_rebal"], "exposed_days": s["exposed_days"]})
        print(f"  {label:<30s} lag={lag} 净年化 {st['ann_return']:+7.2%} "
              f"夏普 {st['sharpe']:5.2f} 回撤 {st['max_drawdown']:7.1%} "
              f"超域 {st['excess_ew_universe_ann']:+7.2%} 在仓 {s['exposed_days']}天")
        rows.append(st)
        return st, net

    # 判据 3：复刻再验一次（只用一场，省一次官方回放）
    off_net, _og, off_s = EA.topk_rebalance(sc_a, m, days, k=K, hold=EA.HOLD)
    rep_net, _rg, _rs = replay_lag(sc_a, m, days, K, EA.HOLD, lag=1)
    dmax = float((off_net - rep_net).abs().max())
    if not dmax < 1e-18:
        print(f"\n[复刻失败] lag=1 与官方 max|Δ|={dmax:.3e} ⇒ 引用 lag=2/3 的读数作废")
        return 1
    print(f"\n[复刻对表] lag=1 逐位等于官方：max|Δ|={dmax:.1e}、{off_s['n_rebal']} 次调仓")

    for wname, md in windows.items():
        print(f"\n---- {wname}（{len(md)} 天）----")
        print("  [A 现口径尺子挑的因子]")
        bill(sc_a, 1, wname, "A名单·现口径回放(生产那一行)", md)
        bill(sc_a, 2, wname, "A名单·可执行回放", md)
        bill(sc_b, 2, wname, "B名单·可执行回放(两腿同口径)", md)

    print("\n---- 八族各自在 B 尺子下的单跑（可执行回放，全窗口）----")
    for f, sc in fam_b.items():
        bill(sc, 2, "全窗口 2019+", f"B尺子·单族·{f}({len(mem_b[f])}条)", days)
    for f, sc in fam_a.items():
        bill(sc, 2, "全窗口 2019+", f"A尺子·单族·{f}({len(mem_a[f])}条)", days)

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "label_shift_arms.csv"), index=False)
    comp = df[df.label.str.contains("名单")]
    print("\n===== 这笔账拆成两半：尺子腿 vs 回放腿 =====")
    for wname in windows:
        sub = comp[comp.window == wname].set_index("label")
        a1 = sub.loc["A名单·现口径回放(生产那一行)", "ann_return"]
        a2 = sub.loc["A名单·可执行回放", "ann_return"]
        b2 = sub.loc["B名单·可执行回放(两腿同口径)", "ann_return"]
        print(f"[{wname}] 生产那一行 {a1:+.2%} "
              f"→ 只还回放腿 {a2:+.2%}（-{a1 - a2:.2%}）"
              f"→ 再还尺子腿 {b2:+.2%}（-{a2 - b2:.2%}）"
              f"｜合计 -{a1 - b2:.2%}")
        e1 = sub.loc["A名单·现口径回放(生产那一行)", "excess_ew_universe_ann"]
        e2 = sub.loc["B名单·可执行回放(两腿同口径)", "excess_ew_universe_ann"]
        print(f"            跑赢等权可投域：{e1:+.2%} → {e2:+.2%}")
    print(f"\n[产物] {OUT}/label_shift_{{ic,arms}}.csv（data/results 一行未动）")
    print(f"[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
