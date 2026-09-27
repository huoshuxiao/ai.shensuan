# -*- coding: utf-8 -*-
"""任务 #40 第二轮：把"换权重轴 / 换席位"这件事拿到样本外量（walk-forward）。

为什么要这一轮：`w40_weight_probe_0926.py` 量出位置族三条等权 47.72%、按 |RankIC| 48.16%
都跑赢最强单条 43.72%，但**那些方案是看着 2019-2026 全样本的结果挑出来的** ⇒ 前视。
股票线名单层那次翻车的教训（+16.95pp 全在 2015 一年、正的部分全在 2018 之前）就是这一类账，
所以 48.16% 不进任何判据，除非"只用过去"的同一套规则也能拿到。

口径（写清楚每处为什么）
------------------------
· 折：日历年 2020~2026（7 折）。**不从 2019 起折**，因为训练段必须与生产同口径 ——
  规模面板（`ETF_MIN_SCALE` 那道闸）只有 2019-01-02 之后的披露，2013~2018 的回放
  是另一个可投域，训练与测试不同域 = 假样本外。代价：2020 折只有 2019 一年（244 天）可训。
· 训练段 = 该年 1 月 1 日之前的所有日子，**再砍掉最后 10 个交易日**：h=10 的前向收益
  让最后 10 天的 IC 眼睛看到了测试年第一天之后，不砍就是漏。
· 尺子一律用官方函数：`EA.ic_summary`（ICIR=mean/std）、`EA.icir_weights`（含 min_abs 下限）、
  `R2.dedupe_by_corr`（判重线 `EA.RED_BAR`）、`EA.topk_rebalance`（开盘到开盘、tier 分档费）、
  `EA.excess_stats`。本探针不新发明任何判据，只把"看哪段数据"换掉。
· 席位/判重/权重三件事全部只喂训练段：判重本来用全量日期（`dedupe_by_corr` 内部
  `cs_corr_mean` 吃整表），这里传的是 `.loc[train]` 切过的表，否则判重这一步就在偷看未来。

八条方案（W7/W8 是"明知前视、只作对照"的上界）
------------------------------------------------
    W1 现行规则原样搬到样本外：|RankICIR| 前 15 → 判重 → 前 5，权重 ∝ |RankICIR|
    W2 W1 的席位，权重改等权
    W3 席位按 |RankIC| 前 15 → 判重 → 前 5，权重 ∝ |RankIC|
    W4 W3 的席位，权重改等权
    W5 W3 席位后再过"钱闸"：训练段单跑对可投域超额为负者撤下
    W6 族先验：7 个族各做一条"族内全员等权"的合成（不碰任何收益数据选族）
    W6c 只用过去挑族：每年选**上一段**族内合成最好的那一族
    W7 生产现状（全样本席位+权重，即 A 方案）在同一批 test 年上 —— 前视对照
    W8 全样本最强单条 Donchian —— 前视对照

产物（只落 shell/，不动权威 csv）：`w40wf_folds.csv`（逐折逐方案）、
`w40wf_summary.csv`（拼接后的样本外总账）、`w40wf_seats.csv`（每折选了谁，看稳不稳）。
跑法：`/usr/bin/python3.10 shell/etf/w40_walkforward_0926.py`
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")
TMP = os.path.join(HERE, "tmp_a158_etf")
sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402

import etf_admission as EA                        # noqa: E402
import run_etf_portfolio_eval as R2               # noqa: E402

PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))
FWD = f"fwd{PRIMARY_H}"
PICK = R2.PICK            # 初选名额（15），与官方同一常量
TOP = R2.TOP              # 终选名额（5）
MIN_ABS = 0.05            # 官方 icir_weights 的默认下限，此处不另立
BUFFER = PRIMARY_H        # 训练段尾巴要砍掉的天数 = 前向窗，防漏
FOLD_START = 2020         # 第一折的测试年（见 docstring 规模面板那条理由）
EVAL_CSV = os.path.join(TMP, "ring2data", "results", "eval_R1_baseline.csv")


def seats_by(stats, facs_tr, key):
    """训练段指标表 → (席位权重 dict, 判重撤下的名单)。与官方同款三步。"""
    w0, _ = EA.icir_weights(stats, top=PICK, key=key, min_abs=MIN_ABS)
    if not w0:
        return {}, ["（训练段没有一条过 |%s| 下限）" % key]
    kept, dropped = R2.dedupe_by_corr(facs_tr, w0, keep_max=TOP)
    tot = sum(abs(v) for v in kept.values()) or 1.0
    return {k: v / tot for k, v in kept.items()}, [d[0] for d in dropped]


def replay(score, m, days, ew_uni, label, fold, extra=None):
    """在**单折窗口**内回放（每年从空仓起建、期末清最后一篮 ⇒ 进出成本都收满）。"""
    net, _g, s = EA.topk_rebalance(score, m, days, k=10, hold=EA.HOLD)
    st = EA.portfolio_stats(net, label)
    st.update(EA.excess_stats(net, ew_uni.reindex(days).dropna(), "ew_universe"))
    st.update({"scheme": label, "fold": fold, "n_days": st["n_days"],
               "turnover_ann": (s["one_way_turnover"] * EA.TRADING_DAYS / EA.HOLD
                                if np.isfinite(s["one_way_turnover"]) else np.nan),
               "avg_cost_one_way": s["avg_cost_one_way"],
               "avg_amount20": s["avg_amount20"]})
    st.update(extra or {})
    return st, net


def main():
    t0 = time.time()
    print("=" * 78)
    print("#40 第二轮 · 样本外权重/席位 walk-forward（训练段与测试段严格分开）")
    print("=" * 78)
    ev = pd.read_csv(EVAL_CSV)
    ev = ev[(ev.status == "ok") & ev[f"rank_icir_h{PRIMARY_H}"].notna()]
    fam = dict(zip(ev.name, ev.family))
    FAMS = sorted(set(fam.values()))   # 7 个族，族名取自环 1 的 family 列
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
    universe = EA.universe_mask(m)
    _ew_all, ew_uni = EA.equal_weight_benchmarks(m, universe=universe)
    days_all = m["close"].index
    years = sorted(y for y in set(days_all.year) if y >= FOLD_START)
    nmax = int(os.environ.get("W40WF_MAXFOLD", "0"))     # 冒烟用：只跑前 nmax 折
    years = years[:nmax] if nmax else years
    print(f"[样本] {len(names)} 条因子 × {days_all[0].date()}~{days_all[-1].date()}"
          f"（{len(days_all)} 个交易日）⇒ {len(years)} 折 {years[0]}~{years[-1]}")

    # ---- 逐因子日频 RankIC 序列（整段只算一次；每折只截训练段，不再重算）----
    t1 = time.time()
    ric = {}
    for n in names:
        _i, r, _c = EA.daily_cs_ic(facs[n], m[FWD], min_cs=EA.MIN_CS)
        ric[n] = r
    print(f"[RankIC 序列] {len(ric)} 条 × h{PRIMARY_H}，耗时 {time.time() - t1:.0f}s")

    # ---- 逐折：只用训练段做决定 ----
    # `icir_weights` 认的键名是 rank_ic / rank_icir（环 1 的 csv 列名），而 `ic_summary`
    # 吐的是 mean / icir —— 这里显式改名，两把尺子才是同一把。
    def stats_of(days):
        s = {n: EA.ic_summary(ric[n].reindex(days)) for n in names}
        return ({n: {"rank_ic": v["mean"], "rank_icir": v["icir"]} for n, v in s.items()},
                {n: float(np.sign(v["mean"])) for n, v in s.items()})

    # 对表：整段重算的 IC/ICIR 必须与环 1 产物一致，否则本轮样本外用的不是同一把尺子
    st_full, sgn_full = stats_of(days_all)
    ref = {r["name"]: (r[f"rank_ic_h{PRIMARY_H}"], r[f"rank_icir_h{PRIMARY_H}"])
           for _, r in ev.iterrows()}
    d1 = max(abs(st_full[n]["rank_ic"] - ref[n][0]) for n in names)
    d2 = max(abs(st_full[n]["rank_icir"] - ref[n][1]) for n in names)
    print(f"[尺子对表] 整段重算 vs 环 1 csv：max|ΔRankIC|={d1:.2e} max|ΔRankICIR|={d2:.2e}")
    if max(d1, d2) > 2e-3:
        print("[对表失败] 本探针的 IC 与环 1 不是同一把尺子，样本外读数无意义 —— 停止")
        sys.exit(1)

    folds, seat_rows, fam_net_by_fold, nets = [], [], {}, {s: [] for s in
                                                           ["W1", "W2", "W3", "W4", "W5",
                                                            "W6c", "W7", "W8"]}
    w_full0, _ = EA.icir_weights(st_full, top=PICK)
    kept_full, _ = R2.dedupe_by_corr(facs, w_full0, keep_max=TOP)
    tot = sum(abs(v) for v in kept_full.values()) or 1.0
    w_full = {k: v / tot for k, v in kept_full.items()}          # W7 生产现状
    donch = "位置·20日Donchian"
    print(f"[W7 全样本席位] " + " ".join(f"{n}={v:+.3f}" for n, v in w_full.items()))

    for fi, Y in enumerate(years):
        tf = time.time()
        train = days_all[days_all < pd.Timestamp(f"{Y}-01-01")]
        if len(train) <= BUFFER + 30:
            print(f"[{Y}] 训练段太短（{len(train)} 天），跳过")
            continue
        train = train[:-BUFFER]
        test = days_all[(days_all >= pd.Timestamp(f"{Y}-01-01"))
                        & (days_all <= pd.Timestamp(f"{Y}-12-31"))]
        st_tr, sgn = stats_of(train)          # 训练段指标 + 翻向符号（只用过去）
        facs_tr = {n: facs[n].loc[train] for n in names}
        wI, dI = seats_by(st_tr, facs_tr, "rank_icir")          # 现行尺子的样本外版
        wC, dC = seats_by(st_tr, facs_tr, "rank_ic")            # 换尺子

        plans = {}
        plans["W1 |RankICIR|席位+|RankICIR|权"] = wI
        plans["W2 |RankICIR|席位+等权"] = ({n: sgn[n] / len(wI) for n in wI} if wI else {})
        plans["W3 |RankIC|席位+|RankIC|权"] = wC
        plans["W4 |RankIC|席位+等权"] = ({n: sgn[n] / len(wC) for n in wC} if wC else {})
        # W5：钱闸 = 训练段单跑对可投域超额为负的席位撤下（只用过去）
        # 基准名必须传 "ew_universe"：`excess_stats` 的返回键是 f"excess_{name}_ann"，
        # 传别的名字下面那行取键就 KeyError（本探针第一版就撞在这）。
        w5, gated = {}, []
        for n, w in wC.items():
            nt, _g, _s = EA.topk_rebalance(facs[n] * sgn[n], m, train, k=10, hold=EA.HOLD)
            ex = EA.excess_stats(nt, ew_uni.reindex(train), "ew_universe")
            v = ex["excess_ew_universe_ann"]
            if np.isfinite(v) and v > 0:
                w5[n] = w
            else:
                gated.append((n, v))
        if w5:
            t5 = sum(abs(v) for v in w5.values()) or 1.0
            w5 = {k: v / t5 for k, v in w5.items()}
        plans["W5 W3席位+钱闸"] = w5

        print(f"\n---- 折 {fi + 1}/{len(years)}：test={Y}（{len(test)} 天，"
              f"train={train[0].date()}~{train[-1].date()} {len(train)} 天）----")
        print(f"  W1 席位({len(wI)}) {list(wI)}｜判重撤 {len(dI)}")
        print(f"  W3 席位({len(wC)}) {list(wC)}｜判重撤 {len(dC)}｜钱闸后({len(w5)}) {list(w5)}")
        if gated:
            print("     钱闸撤下：" + " ".join(f"{n}(训练段超额{v:+.2%})" for n, v in gated))
        for tag, lst in (("W1", wI), ("W3", wC), ("W5", w5)):
            for n in lst:
                seat_rows.append({"fold": Y, "scheme": tag, "name": n,
                                  "weight": lst[n], "rank_ic_train": st_tr[n]["rank_ic"],
                                  "rank_icir_train": st_tr[n]["rank_icir"],
                                  "train_days": len(train)})

        rows_here = {}
        for lab, w in plans.items():
            key = lab.split()[0]
            if not w:
                print(f"  {lab:<34s} 无席位（训练段全不过下限）")
                continue
            sc = EA.composite_score(facs, w)[0]
            st, net = replay(sc, m, test, ew_uni, lab, Y, {"n_seats": len(w)})
            rows_here[key] = st
            nets[key].append(net)
            folds.append(st)
            print(f"  {lab:<34s} 净年化 {st['ann_return']:+.2%} 夏普 {st['sharpe']:.2f} "
                  f"回撤 {st['max_drawdown']:.1%} 超可投域 "
                  f"{st['excess_ew_universe_ann']:+.2%}")

        # W6 族内全员等权（不选族、不看收益）+ W6c 用上一段挑族
        fam_ret = {}
        for f in FAMS:
            ms = [n for n in names if fam[n] == f]
            sc = EA.composite_score(facs, {n: sgn[n] / len(ms) for n in ms})[0]
            st, net = replay(sc, m, test, ew_uni, f"W6 族内等权·{f}({len(ms)})", Y)
            folds.append(st)
            fam_ret[f] = (st["ann_return"], net)
            print(f"  [W6 族 {f:<5s} {len(ms)}条]      净年化 {st['ann_return']:+.2%} "
                  f"夏普 {st['sharpe']:.2f} 回撤 {st['max_drawdown']:.1%}")
        fam_net_by_fold[Y] = fam_ret
        if fi == 0:      # 第一折的"上一段"= 2019 训练段，各族都跑一遍用来挑
            tr_best = {}
            for f in FAMS:
                ms = [n for n in names if fam[n] == f]
                sc = EA.composite_score(facs, {n: sgn[n] / len(ms) for n in ms})[0]
                st, _ = replay(sc, m, train, ew_uni, f"train·{f}", Y)
                tr_best[f] = st["ann_return"]
            pick = max(tr_best, key=lambda k: tr_best[k])
            basis = f"训练段({train[0].year}~{train[-1].year})"
        else:
            prev = fam_net_by_fold[years[fi - 1]]
            pick = max(prev, key=lambda k: prev[k][0])
            basis = f"上一折 {years[fi - 1]}"
        ms = [n for n in names if fam[n] == pick]
        sc = EA.composite_score(facs, {n: sgn[n] / len(ms) for n in ms})[0]
        st, net = replay(sc, m, test, ew_uni, f"W6c 只用过去挑族→{pick}", Y,
                         {"n_seats": len(ms)})
        folds.append(st)
        nets["W6c"].append(net)
        print(f"  {f'W6c 挑族({basis})→{pick}':<34s} 净年化 {st['ann_return']:+.2%} "
              f"夏普 {st['sharpe']:.2f}  席位 {ms}")

        # W7 / W8 前视对照
        st, net = replay(EA.composite_score(facs, w_full)[0], m, test, ew_uni,
                         "W7 生产现状(全样本席位)", Y, {"n_seats": len(w_full)})
        folds.append(st)
        nets["W7"].append(net)
        print(f"  {'W7 生产现状(全样本席位)':<34s} 净年化 {st['ann_return']:+.2%}"
              f"（⚠️前视对照）")
        st, net = replay(facs[donch] * sgn_full[donch], m, test, ew_uni,
            "W8 Donchian单条(全样本挑)", Y, {"n_seats": 1})
        folds.append(st)
        nets["W8"].append(net)
        print(f"  {'W8 Donchian单条(全样本挑)':<34s} 净年化 {st['ann_return']:+.2%}"
              f"（⚠️前视对照）  本折耗时 {time.time() - tf:.0f}s")

    fd = pd.DataFrame(folds)
    fd.to_csv(os.path.join(TMP, "w40wf_folds.csv"), index=False)
    pd.DataFrame(seat_rows).to_csv(os.path.join(TMP, "w40wf_seats.csv"), index=False)

    # ---- 拼接所有折的样本外日收益 = 一条只用过去得到的净值 ----
    # 说明：折与折之间不连续（年初重建篮子），回撤是"拼接净值"上的回撤，
    # 比单折回撤悲观、比连续持仓乐观，只用于横向比方案。
    b = ew_uni.reindex(days_all[days_all >= pd.Timestamp(f"{FOLD_START}-01-01")])
    rows = []
    for lab, lst in nets.items():
        if not lst:
            continue
        j = pd.concat(lst).sort_index()
        j = j[~j.index.duplicated()]
        st = EA.portfolio_stats(j, lab)
        st.update(EA.excess_stats(j, b, "ew_universe"))
        st["scheme"] = lab
        st["n_folds"] = len(lst)
        st["concat_days"] = int(len(j))
        st["worst_fold"] = float(fd[fd.scheme.str.startswith(lab.split()[0])]
                                ["ann_return"].min())
        st["n_folds_win"] = int((fd[fd.scheme.str.startswith(lab.split()[0])]
                                 ["excess_ew_universe_ann"] > 0).sum())
        rows.append(st)
    sm = pd.DataFrame(rows).sort_values("ann_return", ascending=False)
    sm.to_csv(os.path.join(TMP, "w40wf_summary.csv"), index=False)

    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 40)
    print("\n===== 样本外逐年净年化（每格=该方案只用过去挑出的席位，在那一年回放）=====")
    piv = fd[fd.scheme.str.match(r"W[1-5] |W6c|W7|W8")].pivot(
        index="fold", columns="scheme", values="ann_return")
    print((piv * 100).round(1).to_string())
    print("\n===== 拼接后的样本外总账（7 折日收益首尾相接）=====")
    print(sm[["scheme", "n_folds", "concat_days", "ann_return", "sharpe",
              "max_drawdown", "excess_ew_universe_ann", "excess_ew_universe_ir",
              "n_folds_win", "worst_fold"]].round(4).to_string(index=False))
    print("\n===== 每折席位稳不稳（同一条因子被选中几折）=====")
    sr = pd.DataFrame(seat_rows)
    print(sr.groupby(["scheme", "name"]).agg(被选折数=("fold", "nunique"),
                                            平均权重=("weight", "mean")).round(3)
          .sort_values(["scheme", "被选折数"], ascending=[True, False]).to_string())
    print(f"\n[输出] {TMP}/w40wf_{{folds,summary,seats}}.csv\n[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
