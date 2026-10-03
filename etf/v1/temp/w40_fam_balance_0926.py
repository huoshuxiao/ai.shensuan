# -*- coding: utf-8 -*-
"""任务 #41 路径⑥：把「族」当成发席位的那一层，用**真实账户口径**量一遍样本外。

上一轮（`w40_walkforward_0926.py`）给的族账单是**八个族各自复利**的八条独立曲线
（日内隔夜 41.6% … 量能 −0.1%），那不是"一个账户"的收益。本轮补的是真账户：

    族内合成  →  族间合成（百分位秩等权）  →  **一次** topk_rebalance(k=10, hold=10, tier 成本)

即"族间配平"不靠子账户之间每天调拨（那样得另计一笔调仓费），而是把族分数并进同一张
总打分、只建一次仓 —— 与生产环 2 的机制完全同款，只是被合成的对象从"因子"换成"族"。
成本、滑点、涨停闸、规模闸全部走官方 `EA.topk_rebalance`，本探针不加任何新判据。

七条样本外方案（S3/S4 是明知前视、只作上界对照）
--------------------------------------------------
    S1 八族全配平：族内全部候选等权 → 八族等权（不挑族、不看任何收益）
    S2k 只用过去挑 k 族配平（k=1/2/3）：每年只用**上一折**各族单跑年化挑最好的 k 族
    S3 八族全配平，但族内先过 |RankICIR|(训练段) ≥ 0.05 再等权 —— 族内要不要门槛
    S4 前视对照：全样本最强 2 族配平（知道答案才挑，不可采纳）

对表（不许恒真）：S2k 的 k=1 与上一轮 W6c 是同一套挑族规则、同一次回放，
逐折年化必须与 `w40wf_folds.csv` 里记的 W6c 一致；不一致就是本轮的族分数或翻向漂了。

产物落 shell/，不动权威 csv。跑法：
    /usr/bin/python3.10 shell/etf/w40_fam_balance_0926.py
冒烟（只跑前 1 折）：W40FB_MAXFOLD=1 同上
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
import _bootstrap  # noqa: E402

import etf_admission as EA                        # noqa: E402
import run_etf_portfolio_eval as R2               # noqa: E402

PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))
FWD = f"fwd{PRIMARY_H}"
K = 10                      # 与生产环 2 的 TOP_K 首档一致
MIN_ABS = 0.05              # 官方 icir_weights 默认下限（S3 用它筛族内因子）
BUFFER = PRIMARY_H          # 训练段尾巴砍掉前向窗，防漏
FOLD_START = 2020
EVAL_CSV = os.path.join(TMP, "ring2data", "results", "eval_R1_baseline.csv")
W6C_REF = os.path.join(TMP, "w40wf_folds.csv")


def fam_weights(members, sgn, mode, st_tr):
    """族内成员 → 等权（带翻向）。mode='全' 用全部候选；'闸' 只留训练段 |RankICIR| 过下限者。"""
    ms = [n for n in members if mode == "全"
          or (np.isfinite(st_tr.get(n, {}).get("rank_icir", np.nan))
              and abs(st_tr[n]["rank_icir"]) >= MIN_ABS)]
    return {n: sgn[n] / len(ms) for n in ms}


def replay(score, m, days, ew_uni, label, fold, extra=None):
    net, gross, s = EA.topk_rebalance(score, m, days, k=K, hold=EA.HOLD)
    st = EA.portfolio_stats(net, label)
    st.update(EA.excess_stats(net, ew_uni.reindex(days).dropna(), "ew_universe"))
    st.update({"scheme": label, "fold": fold,
               "turnover_ann": (s["one_way_turnover"] * EA.TRADING_DAYS / EA.HOLD
                                if np.isfinite(s["one_way_turnover"]) else np.nan),
               "avg_cost_one_way": s["avg_cost_one_way"],
               "avg_amount20": s["avg_amount20"],
               "basket_size": s["basket_size"]})
    st.update(extra or {})
    return st, net


def blend(fam_scores, fams, m, days, ew_uni, label, fold):
    """族间等权：对每条族分数再做一次日内百分位秩后平均 ⇒ 八条分数权重严格相等。"""
    sc = EA.composite_score(fam_scores, {f: 1.0 / len(fams) for f in fams})[0]
    return replay(sc, m, days, ew_uni, label, fold, {"n_fams": len(fams)})


def main():
    t0 = time.time()
    print("=" * 78)
    print("#41 路径⑥ · 族层合成的真实账户样本外（八族配平 / 只用过去挑 k 族）")
    print("=" * 78)
    ev = pd.read_csv(EVAL_CSV)
    ev = ev[(ev.status == "ok") & ev[f"rank_icir_h{PRIMARY_H}"].notna()]
    fam = dict(zip(ev.name, ev.family))
    FAMS = sorted(set(fam.values()))
    specs = EA.specs_from_rows(ev[["name", "expr"]].to_dict("records"), family="候选")

    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    facs = {k: EA.slice_to_start(v, EA.EVAL_START)
            for k, v in EA.evaluate_factors(pool, specs).items()}
    del pool
    names = sorted(facs)
    if len(names) < 25:
        print(f"[对表失败] 31 条库内因子只求出 {len(names)} 条，口径漂了")
        sys.exit(1)
    members = {f: [n for n in names if fam[n] == f] for f in FAMS}
    print(f"[族] {len(FAMS)} 族 × {len(names)} 条：" +
          " ".join(f"{f}({len(members[f])})" for f in FAMS))

    universe = EA.universe_mask(m)
    _ew_all, ew_uni = EA.equal_weight_benchmarks(m, universe=universe)
    days_all = m["close"].index
    years = sorted(y for y in set(days_all.year) if y >= FOLD_START)
    nmax = int(os.environ.get("W40FB_MAXFOLD", "0"))
    years = years[:nmax] if nmax else years

    t1 = time.time()
    ric = {n: EA.daily_cs_ic(facs[n], m[FWD], min_cs=EA.MIN_CS)[1] for n in names}
    print(f"[RankIC 序列] {len(ric)} 条，耗时 {time.time() - t1:.0f}s")

    def stats_of(days):
        s = {n: EA.ic_summary(ric[n].reindex(days)) for n in names}
        return ({n: {"rank_ic": v["mean"], "rank_icir": v["icir"]} for n, v in s.items()},
                {n: float(np.sign(v["mean"])) for n, v in s.items()})

    folds, single_rows, nets = [], [], {}
    prev_single = None          # 上一折各族单跑年化（只用过去）
    basis = "（第一折：训练段）"

    # 全样本最强 2 族（S4 前视对照）：先用整段挑好，只作上界
    st_full, sgn_full = stats_of(days_all)
    fam_full_score = {f: EA.composite_score(facs, {n: sgn_full[n] / len(members[f])
                                                   for n in members[f]})[0] for f in FAMS}
    top2_full = {}
    for f in FAMS:
        st, _ = replay(fam_full_score[f], m, days_all[days_all >= pd.Timestamp(f"{FOLD_START}-01-01")],
                       ew_uni, f"full·{f}", "全样本")
        top2_full[f] = st["ann_return"]
    BEST2 = sorted(top2_full, key=lambda k: -top2_full[k])[:2]
    print(f"[S4 前视对照] 全样本最强 2 族 = {BEST2}（⚠️不可采纳）")

    for fi, Y in enumerate(years):
        tf = time.time()
        train = days_all[days_all < pd.Timestamp(f"{Y}-01-01")]
        if len(train) <= BUFFER + 30:
            print(f"[{Y}] 训练段太短（{len(train)} 天），跳过")
            continue
        train = train[:-BUFFER]
        test = days_all[(days_all >= pd.Timestamp(f"{Y}-01-01"))
                        & (days_all <= pd.Timestamp(f"{Y}-12-31"))]
        st_tr, sgn = stats_of(train)

        print(f"\n---- 折 {fi + 1}/{len(years)}：test={Y}（{len(test)} 天，"
              f"train={train[0].date()}~{train[-1].date()} {len(train)} 天）----")

        # 每折：族内合成（两种族内口径）+ 各族单跑（挑族用的读数）
        sc_all, sc_gate, single = {}, {}, {}
        for f in FAMS:
            sc_all[f] = EA.composite_score(facs, fam_weights(members[f], sgn, "全", st_tr))[0]
            wg = fam_weights(members[f], sgn, "闸", st_tr)
            if wg:
                sc_gate[f] = EA.composite_score(facs, wg)[0]
            st, _ = replay(sc_all[f], m, test, ew_uni, f"单族·{f}", Y)
            single[f] = st["ann_return"]
            single_rows.append(dict(st, scheme=f"单族·{f}｜族内{len(members[f])}条",
                                    n_fams=1, n_factors=len(members[f])))
            print(f"  [单族 {f:<5s} {len(members[f])}条] {st['ann_return']:+.2%}", end="")
        n_gate = {f: sum(1 for n in members[f] if abs(st_tr[n]["rank_icir"]) >= MIN_ABS)
                  for f in FAMS}
        print(f"\n  族内过 |RankICIR|≥{MIN_ABS} 条数：" +
              " ".join(f"{f}={n_gate[f]}" for f in FAMS))

        if fi == 0:
            pick_src = {}
            for f in FAMS:
                st, _ = replay(sc_all[f], m, train, ew_uni, f"train·{f}", Y)
                pick_src[f] = st["ann_return"]
            basis = f"训练段 {train[0].year}~{train[-1].year}"
        else:
            pick_src = prev_single
            basis = f"上一折 {years[fi - 1]}"
        ranked = sorted(pick_src, key=lambda k: -pick_src[k])
        print(f"  [挑族({basis})] " + " ".join(f"{i + 1}.{f}" for i, f in enumerate(ranked)))
        prev_single = single

        plans = {"S1 八族全配平": (sc_all, FAMS)}
        for k in (1, 2, 3):
            plans[f"S2 只用过去挑{k}族"] = (sc_all, ranked[:k])
        gf = [f for f in FAMS if n_gate[f] > 0]
        plans["S3 八族配平·族内过闸"] = (sc_gate, gf)
        plans["S4 前视最强2族(不可采纳)"] = (sc_all, BEST2)

        for lab, (src, fs) in plans.items():
            if not fs:
                print(f"  {lab:<26s} 无族可用")
                continue
            st, net = blend(src, fs, m, test, ew_uni, lab, Y)
            folds.append(st)
            nets.setdefault(lab, []).append(net)
            print(f"  {lab:<26s} 净年化 {st['ann_return']:+.2%} 夏普 {st['sharpe']:.2f} "
                  f"回撤 {st['max_drawdown']:.1%} 超域 {st['excess_ew_universe_ann']:+.2%} "
                  f"换手 {st['turnover_ann']:.1f}x 篮子成交额 {st['avg_amount20'] / 1e8:.2f}亿")
        print(f"  （本折 {time.time() - tf:.0f}s）")

    fd = pd.DataFrame(folds)
    fd.to_csv(os.path.join(TMP, "w40fb_folds.csv"), index=False)
    pd.DataFrame(single_rows).to_csv(os.path.join(TMP, "w40fb_famsingle.csv"), index=False)

    # ---- 拼接样本外日收益 = 一条只用过去得到的账户净值 ----
    b = ew_uni.reindex(days_all[days_all >= pd.Timestamp(f"{FOLD_START}-01-01")])
    rows = []
    for lab, lst in nets.items():
        j = pd.concat(lst).sort_index()
        j = j[~j.index.duplicated()]
        st = EA.portfolio_stats(j, lab)
        st.update(EA.excess_stats(j, b, "ew_universe"))
        g = fd[fd.scheme == lab]
        st.update({"scheme": lab, "n_folds": len(lst), "concat_days": int(len(j)),
                   "worst_fold": float(g["ann_return"].min()),
                   "n_folds_pos": int((g["ann_return"] > 0).sum()),
                   "n_folds_win": int((g["excess_ew_universe_ann"] > 0).sum()),
                   "turnover_ann": float(g["turnover_ann"].mean()),
                   "avg_amount20": float(g["avg_amount20"].mean())})
        rows.append(st)
    sm = pd.DataFrame(rows).sort_values("ann_return", ascending=False)
    sm.to_csv(os.path.join(TMP, "w40fb_summary.csv"), index=False)

    # ---- 对表：S2 挑 1 族必须逐折等于上一轮的 W6c（同一套挑族规则）----
    if os.path.exists(W6C_REF):
        ref = pd.read_csv(W6C_REF)
        ref = ref[ref.scheme.str.startswith("W6c")]
        mine = fd[fd.scheme == "S2 只用过去挑1族"][["fold", "ann_return"]]
        j = mine.merge(ref[["fold", "ann_return"]], on="fold", suffixes=("_新", "_旧"))
        if len(j) != len(mine) or len(j) == 0:
            print(f"\n[对表失败] W6c 可比折数 {len(j)} / {len(mine)}，规则没对上")
            sys.exit(1)
        dmax = float((j.ann_return_新 - j.ann_return_旧).abs().max())
        print(f"\n[对表] S2 挑1族 vs 上轮 W6c：{len(j)} 折可比 max|Δ净年化|={dmax:.2e}")
        if dmax > 1e-6:
            print("[对表失败] 同一套挑族规则算出两个数，族分数或翻向漂了 —— 停止")
            sys.exit(1)
        bad = j[j.ann_return_新 != j.ann_return_旧]
        if len(bad):
            print("     （逐折差值非零但 <1e-6 的折：" +
                  " ".join(f"{int(r.fold)}:{r.ann_return_新 - r.ann_return_旧:+.1e}"
                           for r in bad.itertuples()) + "）")
    else:
        print(f"\n[对表跳过] 找不到 {W6C_REF}，S2 的 k=1 无法与 W6c 对表")

    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 40)
    print("\n===== 样本外逐年净年化（族层真实账户）=====")
    print(fd[fd.scheme.str.startswith("S")].pivot(index="fold", columns="scheme",
                                                  values="ann_return").mul(100).round(1).to_string())
    print("\n===== 拼接后的样本外总账 =====")
    print(sm[["scheme", "n_folds", "concat_days", "ann_return", "sharpe", "max_drawdown",
              "excess_ew_universe_ann", "excess_ew_universe_ir", "worst_fold",
              "n_folds_pos", "n_folds_win", "turnover_ann"]].round(4).to_string(index=False))
    print(f"\n[输出] {TMP}/w40fb_{{folds,summary,famsingle}}.csv\n[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
