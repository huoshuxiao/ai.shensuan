# -*- coding: utf-8 -*-
"""任务 #40 探针：环 2 合成为什么跑输自己最好的零件 —— 只换权重向量，其余全照官方。

背景（09-26 两次实测）：R1 基线合成 32.21%/年，而它 5 个零件里的
`位置·20日Donchian` 单跑 43.72% —— 合成比最好的零件**低 11.51pp/年**。
剔除 3 条候选后（R4）合成 30.71% vs 零件 43.72%，差反而拉大到 13.01pp。
两次都指向同一处：权重。

本探针不碰任何判据、不写权威产物（只落 `shell/etf/tmp_a158_etf/`）。做法是把官方入口
`run_etf_portfolio_eval.py` 的选条链条原样跑一遍（`pick_from_eval` →
`icir_weights(top=PICK)` → `evaluate_factors` → `dedupe_by_corr(keep_max=TOP)`），
**只用来自 R1 归档日志的权重表与净年化做逐条对表断言**（断言非恒真：任何一处漂移就
`sys.exit(1)` 并宣布本轮读数作废），然后在同一批因子、同一 k/hold/成本口径下只改权重：

    A 现行：|RankICIR| 归一                 —— 复现 32.21%，对表锚
    B 等权：|w| 一律 1/5，符号照 sign(IC)    —— 权重信息全扔掉还剩多少
    C 按 |RankIC| 归一                       —— 用"多强"而非"多稳"发权
    D 按钱加权：w ∝ 该条单跑净年化           —— **前视**，只作上界诊断，不可当成绩
    E 现行权重但剔除 量能·水平MA20（4 条）    —— 单独量那条 40.1% 权重值多少钱
    F 最强单条（Donchian 单跑）              —— 复现 43.72%，零件锚

第二轮（09-26 用户指定追问"要不要合成"）—— 只留位置族，看合成是互补还是摊薄：

    H 位置族 3 条等权                        —— Donchian+距20日高+C/MA20
    I 位置族 3 条按 |RankIC| 归一             —— 族内也要选轴的话，用哪把尺子
    J 位置族 3 条去掉 Donchian（2 条等权）    —— 反向问：Donchian 是被摊薄还是被拖累
    K 位置族 3 条 + 水平·MA5（4 条等权）      —— 掺一条不同族值多少钱

为什么要量"稳定 ≠ 强"：`量能·水平MA20` 的 RankIC 只有 −0.0377（比 Donchian 的
+0.0457 **还弱**），RankICIR 却是 −0.348（Donchian 0.152 的 2.3 倍）。ICIR =
mean(IC)/std(IC)，它奖励的是"每天都不一样但方向很稳"，而发权用的正是这个数 ⇒
合成里 40.1% 的权重落到一条单跑只有 9.46%、回撤 −39.6%、跑输可投域 2.88pp 的因子上。

跑法：`/usr/bin/python3.10 shell/etf/w40_weight_probe_0926.py`（实测见本文件底部注释与日志 log_w40_probe.txt）
产物：`shell/etf/tmp_a158_etf/w40_{replays,parts,overlap}.csv`
      + `w40_yearly.csv`（逐年复利）、`w40_yearly_summary.csv`（剥掉极值年后的保守年化）
      —— 后者是用来挡"全样本一个数被 2015/2020 一年撑起"的老坑（股票线名单层那次就是这么翻车的）。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")
TMP = os.path.join(HERE, "tmp_a158_etf")
sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402  (本线 + common 挂载顺序)

import etf_admission as EA                        # noqa: E402
import run_etf_portfolio_eval as R2               # noqa: E402

PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))
# `pick_from_eval` 读 csv 时已把 `rank_ic_h{PRIMARY_H}` 折叠成不带后缀的键：
# stats[name] 只有 rank_ic / rank_icir / expr 三项，这里再带 _h10 后缀就是 KeyError。
IC_KEY, ICIR_KEY = "rank_ic", "rank_icir"

# 对表锚一：R1 归档日志 [终选] 那 5 行原文
#   （shell/etf/tmp_a158_etf/ring2data/log_R1_baseline.txt:65-69）
ARCHIVED_W = {"量能·水平MA20": -0.401, "位置·20日Donchian": +0.176,
              "位置·距20日高": +0.152, "水平·MA5": +0.146, "位置·C/MA20": +0.125}
# 对表锚二：R1 归档的组合层净年化（k=10）。复现不上 = 官方链条或数据被动过
ARCHIVED_ANN = {"A": 0.3221, "F": 0.4372, "量能·水平MA20": 0.0946}
# 环 1 输入用 R1 那一场的归档副本。它与当前权威 csv 的 31 行**只有 `note` 一列不同**
# （环 1 后来改过注释文案），name/expr/family/各 IC 逐位相等 ⇒ 换哪份都得到同一批
# 终选；仍用 R1 那份是为了让"32.21% 复现得上"这条断言严格成立。
EVAL_CSV = os.path.join(TMP, "ring2data", "results", "eval_R1_baseline.csv")


def top_basket_overlap(sc_a, sc_b, days, k=10):
    """两套打分在**同一批信号日**上 top-k 篮子的平均重合率（Jaccard 按 |交|/k）。

    为什么不只报打分秩相关：秩相关是全截面口径，尾部（我们真正买的那 10 只）
    差多少看不出来 —— 打分相关 0.99 也可能把第 8 名换成另一族的头名。
    """
    def heads(sc):
        V = sc.reindex(index=days).to_numpy("float64")
        cols = np.asarray(sc.columns)
        out = []
        for row in V:
            ok = np.isfinite(row)
            n = min(k, int(ok.sum()))
            if n == 0:
                out.append(frozenset())
                continue
            filled = np.where(ok, row, -np.inf)
            j = np.argpartition(-filled, n - 1)[:n]
            out.append(frozenset(cols[j]))
        return out

    A, B = heads(sc_a), heads(sc_b)
    return float(np.mean([len(x & y) / k for x, y in zip(A, B)]))


def main():
    t0 = time.time()
    print("=" * 78)
    print("#40 权重方案探针 · 官方选条链条复现 + 只换权重")
    print("=" * 78)

    # ---- 1. 原样复现官方选条链（不自己排序、不自己判重）----
    specs, stats = R2.pick_from_eval(EVAL_CSV, R2.TOP, PRIMARY_H)
    weights_all, _detail = EA.icir_weights(stats, top=R2.PICK)
    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    use = [s for s in specs if EA.spec_name(s) in set(weights_all)]
    facs = {k: EA.slice_to_start(v, EA.EVAL_START)
            for k, v in EA.evaluate_factors(pool, use).items()}
    del pool
    kept, _dropped = R2.dedupe_by_corr(facs, weights_all, keep_max=R2.TOP)
    tot = sum(abs(v) for v in kept.values()) or 1.0
    wA = {k: v / tot for k, v in kept.items()}

    print(f"[终选复现] {len(wA)} 条：" +
          "  ".join(f"{n}={w:+.3f}" for n, w in wA.items()))
    if set(wA) != set(ARCHIVED_W):
        print(f"[对表失败] 终选名单与 R1 归档不一致：{sorted(wA)} vs {sorted(ARCHIVED_W)}")
        sys.exit(1)
    bad = {n: (wA[n], ARCHIVED_W[n]) for n in wA if abs(wA[n] - ARCHIVED_W[n]) > 5e-4}
    if bad:
        print(f"[对表失败] 权重与 R1 归档不符：{bad}")
        sys.exit(1)
    print("[对表通过] 5 条权重逐条与 R1 归档日志一致（<=5e-4）")

    days = m["close"].index                      # 与官方入口同一句取日
    universe = EA.universe_mask(m)
    ew_all, ew_uni = EA.equal_weight_benchmarks(m, universe=universe)
    bench_st = EA.portfolio_stats(ew_uni, "可投域等权")

    # ---- 2. 五个零件各自单跑（D 的权重来源 + 零件 vs 合成的账）----
    # 与官方同式：负 IC 的因子按 sign(IC) 翻向（做多低分侧）
    sign = {n: float(np.sign(stats[n][IC_KEY])) for n in wA}
    singles = {}
    for n in wA:
        net, _g, _s = EA.topk_rebalance(facs[n] * sign[n], m, days, k=10, hold=EA.HOLD)
        st = EA.portfolio_stats(net, n)
        st.update(EA.excess_stats(net, ew_uni, "ew_universe"))
        singles[n] = st
        print(f"  [零件 k=10] {n:<20s} 净年化 {st['ann_return']:+.2%} "
              f"回撤 {st['max_drawdown']:.1%} 超可投域 {st['excess_ew_universe_ann']:+.2%}")
    if abs(singles["量能·水平MA20"]["ann_return"] - ARCHIVED_ANN["量能·水平MA20"]) > 5e-4:
        print("[对表失败] 零件 量能·水平MA20 与 R1 归档 9.46% 不符")
        sys.exit(1)

    # ---- 3. 第一问的权重方案 A~E（唯一变量 = 权重向量）----
    ics = {n: abs(stats[n][IC_KEY]) for n in wA}
    money = {n: max(singles[n]["ann_return"], 0.0) for n in wA}
    schemes = {"A 现行|RankICIR|归一": dict(wA),
               "B 等权": {n: sign[n] / len(wA) for n in wA}}
    tC = sum(ics.values()) or 1.0
    schemes["C 按|RankIC|归一"] = {n: sign[n] * ics[n] / tC for n in wA}
    tD = sum(money.values()) or 1.0
    schemes["D 按钱加权(前视上界)"] = {n: sign[n] * money[n] / tD for n in wA}
    rest = {n: w for n, w in wA.items() if n != "量能·水平MA20"}
    tE = sum(abs(v) for v in rest.values()) or 1.0
    schemes["E 剔除量能·水平MA20"] = {n: v / tE for n, v in rest.items()}

    best = max(singles, key=lambda n: singles[n]["ann_return"])
    # ---- 3b. 第二轮追问：合成这件事本身有没有价值（用户 09-26 指定要量）----
    # 三条位置族是"同方向、判重没撤掉（两两 |秩相关| < 0.85）但赚钱能力同级"的一簇，
    # 把它们自己合一下与 Donchian 单跑对表：赢 ⇒ 该在同族内合成；输 ⇒ 摊薄是结构性的。
    pos = [n for n in wA if n.startswith("位置·")]
    if len(pos) != 3 or best not in pos:
        print(f"[对表失败] 位置族应为 3 条且含最强单条，实得 {pos} / best={best}")
        sys.exit(1)
    nonD = [n for n in pos if n != best]
    tI = sum(ics[n] for n in pos) or 1.0
    schemes["H 位置族3条等权"] = {n: sign[n] / 3.0 for n in pos}
    schemes["I 位置族3条按|RankIC|"] = {n: sign[n] * ics[n] / tI for n in pos}
    schemes["J 位置族去掉Donchian(2条等权)"] = {n: sign[n] / 2.0 for n in nonD}
    schemes["K 位置族3条+MA5(4条等权)"] = {n: sign[n] / 4.0
                                        for n in pos + ["水平·MA5"]}

    runs = [(lab, EA.composite_score(facs, w)[0]) for lab, w in schemes.items()]
    runs.append((f"F 最强单条·{best}", facs[best] * sign[best]))

    rows, scores, yearly_cols = [], {}, {}
    for lab, sc in runs:
        scores[lab] = sc
        for k in EA.TOP_K:
            net, gross, s = EA.topk_rebalance(sc, m, days, k=k, hold=EA.HOLD)
            st = EA.portfolio_stats(net, f"{lab} k={k}")
            st.update({"scheme": lab, "k": k,
                       "gross_ann_return": float(gross.mean() * EA.TRADING_DAYS),
                       "turnover_ann": (s["one_way_turnover"] * EA.TRADING_DAYS / EA.HOLD
                                        if np.isfinite(s["one_way_turnover"]) else np.nan),
                       "avg_cost_one_way": s["avg_cost_one_way"],
                       "avg_amount20": s["avg_amount20"],
                       "basket_size": s["basket_size"],
                       "n_rebal": s["n_rebal"]})
            st.update(EA.excess_stats(net, ew_uni, "ew_universe"))
            st.update(EA.excess_stats(net, ew_all, "ew_all"))
            rows.append(st)
            if k == 10:
                yearly_cols[lab] = net
            print(f"  [{lab:<24s} k={k:<3d}] 净年化 {st['ann_return']:+.2%} "
                  f"(毛 {st['gross_ann_return']:+.2%}) 夏普 {st['sharpe']:.2f} "
                  f"回撤 {st['max_drawdown']:.1%} 超可投域 "
                  f"{st['excess_ew_universe_ann']:+.2%} 换手 {st['turnover_ann']:.1f}x/年 "
                  f"篮子成交额 {st['avg_amount20'] / 1e8:.2f} 亿")

    res = pd.DataFrame(rows)

    def ann_of(match):
        return float(res[match]["ann_return"].iloc[0])

    a10 = ann_of((res.scheme == "A 现行|RankICIR|归一") & (res.k == 10))
    f10 = ann_of(res.scheme.str.startswith("F ") & (res.k == 10))
    for tag, got in (("A", a10), ("F", f10)):
        if abs(got - ARCHIVED_ANN[tag]) > 5e-4:
            print(f"[对表失败] {tag} k=10 净年化 {got:.4%} vs 归档 "
                  f"{ARCHIVED_ANN[tag]:.4%} —— 官方链条或数据已变，本轮读数全部作废")
            sys.exit(1)
    print(f"[对表通过] A={a10:.2%}(归档 32.21%)  F={f10:.2%}(归档 43.72%)")

    # ---- 4. 「稳定 ≠ 强」对照表 ----
    pt = pd.DataFrame([{
        "因子": n, "expr": stats[n]["expr"],
        "RankIC": stats[n][IC_KEY], "RankICIR": stats[n][ICIR_KEY],
        "稳/强比|ICIR|/|IC|": abs(stats[n][ICIR_KEY]) / abs(stats[n][IC_KEY]),
        "现行权重w": w, "权重占比": abs(w),
        "单跑净年化k10": singles[n]["ann_return"],
        "单跑回撤": singles[n]["max_drawdown"],
        "单跑超可投域": singles[n]["excess_ew_universe_ann"]}
        for n, w in wA.items()]).sort_values("权重占比", ascending=False)
    pt.to_csv(os.path.join(TMP, "w40_parts.csv"), index=False)

    # ---- 5. 换权重换掉了什么：打分秩相关 + top-10 篮子重合率 ----
    # 顺带一张"零件之间有多像"的矩阵（翻向后的秩相关）：三条位置族能不能互补，
    # 判重线 0.85 只说"没到撤下的程度"，不说"像 0.6 还是像 0.84" —— 后者才决定摊薄多少。
    _P, Sp = EA.cs_corr_mean({n: facs[n] * sign[n] for n in wA},
                             min_cs=EA.MIN_CS, verbose=False)
    Sp.to_csv(os.path.join(TMP, "w40_part_corr.csv"))
    _P, S = EA.cs_corr_mean(scores, min_cs=EA.MIN_CS, verbose=False)
    labA = "A 现行|RankICIR|归一"
    ovl = pd.DataFrame([{
        "方案": lab,
        "与A打分秩相关(逐日均值)": float(S.loc[lab, labA]),
        "top10篮子重合率": top_basket_overlap(scores[labA], scores[lab], days, 10),
        "top20篮子重合率": top_basket_overlap(scores[labA], scores[lab], days, 20)}
        for lab in scores if lab != labA])
    ovl.to_csv(os.path.join(TMP, "w40_overlap.csv"), index=False)
    res.to_csv(os.path.join(TMP, "w40_replays.csv"), index=False)

    # ---- 6. 逐年读数 + "最肥的一年剥掉还剩多少"（防止全样本年化被单一年份撑起）----
    # 剥法：复利。去掉第 g 年后的年化 = (Π(1+r_year)/(1+r_g))^(1/(n-1)) - 1，
    # 取"剥掉最肥那年"与"剥掉最瘦那年"两个方向的**最差**值作保守读数。
    yt = EA.yearly_table(yearly_cols)
    yt.to_csv(os.path.join(TMP, "w40_yearly.csv"), index_label="year")
    yrs = []
    for lab in yearly_cols:
        g = yt[lab]
        comp = (1 + g).prod()
        exc_best = comp / (1 + g.max())
        exc_worst = comp / (1 + g.min())
        n = len(g)
        yrs.append({"方案": lab,
                    "全样本年化": (comp ** (1.0 / n) - 1),
                    "赢的年数": int((g > 0).sum()), "年数": n,
                    "最差单年": g.min(), "最好单年": g.max(),
                    "剥掉最肥那年后年化": exc_best ** (1.0 / (n - 1)) - 1,
                    "剥掉最瘦那年后年化": exc_worst ** (1.0 / (n - 1)) - 1})
    yd = pd.DataFrame(yrs)
    yd.to_csv(os.path.join(TMP, "w40_yearly_summary.csv"), index=False)

    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 40)
    print("\n===== 读法① 五个零件：ICIR 说它最稳，钱说它最不值钱 =====")
    print(pt.round(4).to_string(index=False))
    print(f"\n[基准] 可投域等权年化 {bench_st['ann_return']:+.2%}、"
          f"回撤 {bench_st['max_drawdown']:.1%}")
    print("\n===== 读法② 各权重方案的净年化（同一批因子、同一 k/hold/成本）=====")
    print(res.pivot(index="scheme", columns="k",
                    values=["ann_return", "sharpe", "max_drawdown",
                            "excess_ew_universe_ann"]).round(4).to_string())
    print("\n===== 读法③ 换权重换掉的到底是多厚的篮子 =====")
    print(ovl.round(4).to_string(index=False))
    print(f"\n===== 读法③' 五个零件翻向后的逐日截面秩相关（判重线 {EA.RED_BAR}）=====")
    print(Sp.round(3).to_string())
    print("\n===== 读法④ 逐年复利收益（k=10，扣费后净收益）=====")
    print((yt * 100).round(1).to_string())
    print("\n===== 读法⑤ 剥掉最肥/最瘦那一年之后还剩多少（几何年化，防单一年份撑账）=====")
    print(yd.round(4).to_string(index=False))
    print("\n===== 各方案的权重表 =====")
    for lab, w in schemes.items():
        print(f"  {lab:<24s} " + "  ".join(f"{n}={v:+.3f}" for n, v in w.items()))
    print(f"[输出] {TMP}/w40_replays.csv, w40_parts.csv, w40_overlap.csv")
    print(f"[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
