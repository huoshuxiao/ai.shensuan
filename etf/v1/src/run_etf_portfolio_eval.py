# -*- coding: utf-8 -*-
"""ETF 全市场池组合层评估入口 —— 准入链第二环。

一句话：把第一环里 |RankICIR| 最高的几条因子按 ICIR 加权合成一个打分，然后**真的**
每 hold 天按分数买前 k 只、付手续费，看能不能变成钱。回答的是
「IC 好看 ≠ 能赚钱」这一问 —— 分层收益是逐日再平衡的纸面数，只有这里是可交易口径。

合成与回放口径
--------------
    合成打分   S_{t,i} = Σ_j w_j·sign(IC_j)·rank_pct_i(F_{t,i})    rank_pct ∈ (0,1]
               先按 |RankICIR| 取前 PICK(=3×TOP) 条做初选，**合成前逐日截面秩相关判重**
               （|corr| >= RED_BAR 的后来者撤下），最后最多留 TOP 条、权重重新归一：
               w_j = |RankICIR_j| / Σ_k |RankICIR_k|。
               为什么必须判重：icir_weights 只按各自名次排序，同族变体的名次天然挨着，
               09-24 实测「前 5 条」全是量能一族的副本（两两秩相关 0.943~0.987）——
               等于一条因子抄五遍取平均，合成净年化比它最好的零件还低 1.34pp/年。
               另要求 |RankICIR| >= 0.05：不设下限的话"最好的 5 条"可能全是噪声。
    建仓时序   信号日 s 收盘算分 → s+1 **开盘**建仓 → 持有 hold 日到下一信号日开盘
               用开盘价而非收盘价：本线实盘也是人工下单，收盘集合竞价成交价不可控
    日收益     p_d = Σ_i A_{i,d}·r^open_{i,d} - Σ_{i∈买入} c_i/k - Σ_{i∈卖出} c_i/k'
               逐腿计费；等规模篮子下与旧式 2c·φ（φ=新买占比）逐项相等
               c_i = 单边成本。ETF_COST_MODE=tier（默认）时 c_i = 佣金 1bp + 按该标的
               20 日均成交额分档的滑点（3/5/8/15/30bp，见 EA.SLIP_TIERS）；
               =flat 或显式传 cost 时全表压平到 COST_ONE_WAY（压力测试口径，
               与历史结论对照用它）。旧的单一 6bp 在本池是假的：日成交额中位 0.42 亿，
               10 万元单子在 4200 万成交里参与率 0.24%、在 300 万里是 3.3%。
    可交易闸门 五道全过才进候选：有行情 / 满 120 根 K 线（次新不买）/
               20 日均成交额 >= 3000 万元（容量）/ 近 10 日单日成交额**谷值** >= 3000 万
               （连续低量不买：天天有量才算，峰值读法实测与均值闸同义）/
               建仓日开盘未涨停（涨跌停近似）
               另有第六道规模闸（#17 的份额面板，`ETF_MIN_SCALE`，**默认 5 亿**，
               09-24 定档；选档过程与代价见 CHANGELOG 同节，`=0` 回到 #14 口径）
               基准的"可投域"取同一道闸门的矩阵式（EA.universe_mask），不分叉

基准给两条，差在哪一目了然
--------------------------
    ew_all      全池等权（不筛不扣费）—— "跑赢市场平均没有"
    ew_universe 过完闸门（次新/容量/连续低量）的可投域等权 —— "跑赢真正买得到的那部分市场没有"
只用全池当基准会把大量根本买不到的僵尸基算进基准、把超额系统性压低；
另附 510300（沪深 300ETF）买入持有作第三条参照。

只读：不写因子库、不触发 git 提交。产物
    data/results/etf_portfolio_eval.csv           各组合 × 各 k 的绩效
    data/results/etf_portfolio_eval_yearly.csv    分年度复利收益
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import os
import sys
import time

import numpy as np
import pandas as pd

import etf_admission as EA

# 第一环的产物是本环的输入（ICIR 排名决定用哪几条因子合成）
EVAL_CSV = EA.FACTOR_EVAL_OUT
OUT_CSV = EA.PORT_EVAL_OUT
YEARLY_CSV = EA.PORT_YEARLY_OUT
PRIMARY_H = int(os.environ.get("ETF_PRIMARY_H", "10"))
TOP = int(os.environ.get("ETF_COMPOSITE_TOP", str(EA.COMPOSITE_TOP)))
# 合成前的初选名额：先按 |RankICIR| 取 PICK 条，判重撤下同族重复者，最后最多留
# TOP 条。初选必须比终选宽，否则「前 5 条」实测全是量能一族的五个副本
# （09-24：两两秩相关 0.943~0.987），判重之后合成就只剩一条因子了。
PICK = int(os.environ.get("ETF_COMPOSITE_PICK", str(TOP * 3)))
# 除合成组合外，单独回放哪几条因子（看"合成有没有跑赢它的零件"）
SINGLE_TOP = int(os.environ.get("ETF_SINGLE_TOP", "3"))


def pick_from_eval(path, top=TOP, primary=PRIMARY_H):
    """读第一环产物 → (合成用的 spec 列表, 权重用的指标表)。

    本环**不重算 IC**，只认第一环那张表：两环共用同一份 csv，才不会出现
    "因子层说 0.06、组合层用的是另一批因子"这种自相矛盾的产物。
    """
    if not os.path.exists(path):
        raise SystemExit(f"[输入缺失] 先跑 run_etf_factor_eval.py 生成 {path}")
    res = pd.read_csv(path)
    key = f"rank_icir_h{primary}"
    ok = res[(res.get("status") == "ok") & res[key].notna()]
    if ok.empty:
        raise SystemExit("[输入] 第一环产物里没有可用因子（全被判不可求值）")
    specs = EA.specs_from_rows(ok[["name", "expr"]].to_dict("records"), family="候选")
    stats = {r["name"]: {"rank_ic": r[f"rank_ic_h{primary}"],
                         "rank_icir": r[key], "expr": r["expr"]}
             for _, r in ok.iterrows()}
    return specs, stats


def bench_ret(m, code=EA.BENCH_CODE):
    """单只基准（默认 510300 沪深300ETF）的开盘到开盘日收益。"""
    if code not in m["ret_open"].columns:
        return None
    return m["ret_open"][code].rename(f"buyhold_{code}")


def _risk_line(rk):
    """`EA.risk_readout` 的 dict → 一行体检文本（只报读数，不判生死）。

    措辞上刻意不写"末日"：规模/折溢价是每只标的**各自最近可读到**的那一天。日线
    镜像的按市场滞后已由 `data/update_etf_daily.py`（#19）拉平，但份额披露节奏仍
    不同（沪市只有月末快照），所以同一个截面日期并不存在。这行是体检报告，
    不参与任何判据。

    折溢价必须连着只数与格子数一起读：09-24 复核后这条腿已是全池口径（过闸 379 只
    只只可算，中位 −0.02% 贴着 0），但格子总共 408 个 = 每只只攒到一天多，且最大
    |·| 全在纳指 QDII 上（额度溢价混着境外前收的时差，本机分不开）。
    详见 `EA.premium_matrix` 的两条坑。
    """
    prem = ("无一只可算" if not rk["n_prem_known"] else
            f"{rk['n_prem_known']} 只有净值（格子 {rk['n_prem_cells']}、"
            f"中位 {rk['prem_med']:+.2%}、最大 |·| {rk['prem_max_abs']:.2%}）")
    return (f"{rk['n']} 只过闸标的（各取最近可读日）：规模可读 {rk['n_scale_known']} 只"
            f"（中位 {rk['scale_med']:.1f} 亿、最小 {rk['scale_min']:.1f} 亿）"
            f"｜清盘线（连续 {EA.CLEAR_DAYS} 日 <{EA.CLEAR_LINE / 1e8:.1f} 亿）"
            f"已越线 {rk['n_clearing_line']} 只、过半程 {rk['n_near_line']} 只"
            f"｜折溢价 {prem}"
            f"｜份额面板可读格子 {EA.SCALE_COVERAGE['share_cells']:.1%}")


def dedupe_by_corr(facs, weights, keep_max=None):
    """合成之前先判重：按 |RankICIR| 降序贪心保留，与已留者逐日截面秩相关
    |corr| >= EA.RED_BAR 的后来者撤下；保留够 keep_max 条即止。

    为什么必须在这一环做：`icir_weights` 只按每条因子自己的名次取前 TOP 条，
    同族变体的名次天然挨着排 —— 09-24 实测那 5 条量能变体两两秩相关
    0.943~0.987，等于把一条因子抄五遍再取平均，合成净年化反而比它最好的零件
    低 1.34pp/年。用 abs() 而不照抄第三环的有符号口径：本环合成时会按 IC 符号
    翻向（`composite_score`），-0.95 与 +0.95 在翻向之后是同一个东西。
    """
    names = sorted(weights, key=lambda n: abs(weights[n]), reverse=True)
    S = EA.cs_corr_mean({n: facs[n] for n in names},
                        min_cs=EA.MIN_CS, verbose=False)[1]
    kept, dropped = {}, []
    for n in names:
        if keep_max is not None and len(kept) >= keep_max:
            dropped.append((n, "", np.nan))          # 名额已满，不是判重撤的
            continue
        clash = [(k, float(S.loc[n, k])) for k in kept
                 if abs(float(S.loc[n, k])) >= EA.RED_BAR]
        if clash:
            dropped.append((n, clash[0][0], clash[0][1]))
            continue
        kept[n] = weights[n]
    return kept, dropped


def main():
    t0 = time.time()
    print("=" * 78)
    print("ETF 组合层回放（准入链第二环）· 运行参数")
    print("=" * 78)
    for k, v in EA.run_params().items():
        print(f"  {k:14s} = {v}")
    print(f"  primary_h      = {PRIMARY_H}")

    specs, stats = pick_from_eval(EVAL_CSV, TOP, PRIMARY_H)
    names = {EA.spec_name(s) for s in specs}
    weights, detail = EA.icir_weights(stats, top=PICK)
    if not weights:
        raise SystemExit("[合成] 没有一条因子过 |ICIR| 下限 —— 本池此刻无因子可用，"
                         "这本身就是结论，不做组合回放")
    print(f"\n[初选] {len(names)} 条候选中按 |RankICIR@h{PRIMARY_H}| >= 0.05 取 "
          f"{len(weights)} 条（负 IC 已翻向），判重前的名单：")
    for d in detail:
        print(f"    {d['name']:<22s} w={d['weight']:+.3f}  "
              f"RankICIR={d['icir']:+.3f}  RankIC={d['rank_ic']:+.4f}  {d['expr']}")

    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    print(f"[截面厚度] {EA.thickness_report(m)}")
    use = [s for s in specs if EA.spec_name(s) in set(weights)]
    facs_full = EA.evaluate_factors(pool, use)
    facs = {k: EA.slice_to_start(v, EA.EVAL_START) for k, v in facs_full.items()}
    missing = [EA.spec_name(s) for s in use if EA.spec_name(s) not in facs]
    if missing:
        raise SystemExit(f"[求值] 第一环能算、第二环算不出的因子：{missing}（口径漂移）")
    del facs_full, pool

    kept, dropped = dedupe_by_corr(facs, weights, keep_max=TOP)
    if dropped:
        print(f"\n[判重] 初选 {len(weights)} 条 → 撤下 {len(dropped)} 条"
              f"（|逐日截面秩相关| >= {EA.RED_BAR}）：")
        for n, vs, c in dropped:
            print(f"    ✂️ 撤下 {n}" +
                  (f"：与已留的 {vs} 秩相关 {c:+.3f}" if vs else "：终选名额已满"))
    tot = sum(abs(v) for v in kept.values()) or 1.0
    weights = {k: v / tot for k, v in kept.items()}
    print(f"[终选] 合成实际用 {len(weights)} 条（权重已按 |RankICIR| 重新归一）：")
    for nm, w in weights.items():
        print(f"    {nm:<22s} w={w:+.3f}  RankICIR={stats[nm]['rank_icir']:+.3f}")

    days = m["close"].index
    score, used = EA.composite_score(facs, weights)
    # 单因子也各自回放一遍：合成必须打赢它的零件，否则"合成"只是把噪声平均了一下。
    # 单因子那一行按各自 IC 的符号翻向（负 IC 的因子做多低分侧），与合成口径一致。
    runs = [("合成·" + "+".join(used), score)]
    for nm in list(weights)[:SINGLE_TOP]:
        runs.append((f"单因子·{nm}", facs[nm] * float(np.sign(stats[nm]["rank_ic"]))
                     if np.isfinite(stats[nm]["rank_ic"]) else facs[nm]))
    universe = EA.universe_mask(m)
    q_last, dom_last = m["close"].iloc[-1].notna(), universe.iloc[-1]
    print(f"[可投域] 末日 {m['close'].index[-1].date()}：有行情 {int(q_last.sum())} 只 → "
          f"过闸门 {int((q_last & dom_last).sum())} 只"
          f"（次新/容量/连续低量共剔 {int((q_last & ~dom_last).sum())} 只）；"
          f"全期日均过闸 {universe.sum(axis=1).mean():.0f} 只"
          f"｜成本口径 {EA.COST_MODE}，档位 {EA.SLIP_TIERS}")
    import fetch_etf_risk_panel as RP
    if RP.has_table(RP.SHARES_SSE_OUT) or RP.has_table(RP.SHARES_SZSE_OUT):
        gate = (f"规模 >= {EA.MIN_SCALE / 1e8:.1f} 亿（已生效）" if EA.MIN_SCALE > 0
                else "未启用（ETF_MIN_SCALE=0）")
        print(f"[规模闸] {gate}｜" + _risk_line(EA.risk_readout(
            m, codes=m["close"].columns[dom_last.values])))
    else:
        print(f"[规模闸] 未启用（{RP.RISK_DIR} 里还没有份额表，"
              f"跑 data/fetch_etf_risk_panel.py --daily / --backfill-shares）")
    ew_all, ew_uni = EA.equal_weight_benchmarks(m, universe=universe)
    b300 = bench_ret(m)

    rows, yearly_cols = [], {}
    for label, sc in runs:
        for k in EA.TOP_K:
            net, gross, s = EA.topk_rebalance(sc, m, days, k=k, hold=EA.HOLD)
            st = EA.portfolio_stats(net, name=f"{label} k={k}")
            st.update({"kind": "composite" if label.startswith("合成") else "single",
                       "label": label, "k": k, "hold": EA.HOLD,
                       "cost_one_way": EA.COST_ONE_WAY,
                       "cost_mode": EA.COST_MODE,
                       "avg_cost_one_way": s["avg_cost_one_way"],
                       "gross_ann_return": float(gross.mean() * EA.TRADING_DAYS),
                       "turnover_per_rebal": s["one_way_turnover"],
                       "turnover_ann": (s["one_way_turnover"] * EA.TRADING_DAYS / EA.HOLD
                                        if np.isfinite(s["one_way_turnover"])
                                        else np.nan),
                       "n_rebal": s["n_rebal"], "basket_size": s["basket_size"],
                       "blocked_limit_up": s["blocked_limit_up"],
                       "avg_amount20": s["avg_amount20"]})
            st.update(EA.excess_stats(net, ew_uni, "ew_universe"))
            st.update(EA.excess_stats(net, ew_all, "ew_all"))
            if b300 is not None:
                st.update(EA.excess_stats(net, b300, EA.BENCH_CODE))
            rows.append(st)
            yearly_cols[f"{label} k={k}"] = net
            print(f"  {label:<26s} k={k:<3d} 净年化 {st['ann_return']:+.2%} "
                  f"(毛 {st['gross_ann_return']:+.2%}) 夏普 {st['sharpe']:.2f} "
                  f"回撤 {st['max_drawdown']:.1%} "
                  f"超可投域 {st['excess_ew_universe_ann']:+.2%} "
                  f"｜实收单边 {s['avg_cost_one_way']:.2%} "
                  f"篮子均成交额 {s['avg_amount20'] / 1e8:.2f} 亿")

    for label, series in (("基准·全池等权(不扣费)", ew_all),
                          ("基准·可投域等权(不扣费)", ew_uni)):
        yearly_cols[label] = series
        st = EA.portfolio_stats(series, label)
        st.update({"kind": "benchmark", "label": label, "k": np.nan,
                   "hold": np.nan, "cost_one_way": 0.0})
        rows.append(st)
        print(f"  {label:<26s}      年化 {st['ann_return']:+.2%} "
              f"夏普 {st['sharpe']:.2f} 回撤 {st['max_drawdown']:.1%}")
    if b300 is not None:
        label = f"基准·{EA.BENCH_CODE}买入持有"
        yearly_cols[label] = b300
        st = EA.portfolio_stats(b300, label)
        st.update({"kind": "benchmark", "label": label, "k": np.nan, "hold": np.nan,
                   "cost_one_way": 0.0})
        rows.append(st)
        print(f"  {label:<26s}      年化 {st['ann_return']:+.2%} "
              f"夏普 {st['sharpe']:.2f} 回撤 {st['max_drawdown']:.1%}")

    res = pd.DataFrame(rows)
    front = ["label", "kind", "k", "hold", "ann_return", "gross_ann_return",
             "ann_vol", "sharpe", "max_drawdown", "turnover_per_rebal",
             "turnover_ann", "excess_ew_universe_ann", "excess_ew_universe_ir",
             "excess_ew_all_ann", f"excess_{EA.BENCH_CODE}_ann",
             "n_rebal", "basket_size", "blocked_limit_up", "avg_amount20",
             "cost_mode", "avg_cost_one_way", "cost_one_way", "n_days"]
    res = res[[c for c in front if c in res.columns]]
    res.to_csv(OUT_CSV, index=False)
    yt = EA.yearly_table(yearly_cols)
    yt.to_csv(YEARLY_CSV, index_label="year")

    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 60)
    print("\n===== 组合层绩效（扣费后净收益）=====")
    print(res.round(4).to_string(index=False))
    print("\n===== 分年度复利收益 =====")
    print((yt * 100).round(1).to_string())
    print(f"\n[输出] {OUT_CSV}\n[输出] {YEARLY_CSV}\n[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
