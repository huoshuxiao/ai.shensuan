# -*- coding: utf-8 -*-
"""ETF 全市场池组合层评估入口 —— 准入链第二环。

一句话：把第一环的候选**按族**合成一个打分（族内全员等权、族间一票一权），然后**真的**
每 hold 天按分数买前 k 只、付手续费，看能不能变成钱。回答的是
「IC 好看 ≠ 能赚钱」这一问 —— 分层收益是逐日再平衡的纸面数，只有这里是可交易口径。

合成与回放口径
--------------
    合成层级   ETF_COMPOSITE_LEVEL（默认 family）：
        family  族层（现行）：先族内合成再族间合成，见下面两条
        factor  因子层（旧口径，保留作对照）：|RankICIR| 前 PICK → 判重 → 前 TOP、权重 ∝ |RankICIR|
    族内合成   同一族的候选**全员**等权、按各自 RankIC 符号翻向：
                   F^{族}_{t,i} = mean_{j∈族} sign(IC_j)·rank_pct_i(F_{t,i})
               为什么全员不设 |ICIR| 门槛：#41 路径⑥七折样本外实测，族内全员 34.81%、
               族内先过 |RankICIR|≥0.05 反而 **24.18%**（最差折 −7.2%、回撤 −35.5%）⇒ 门槛判死。
               ⚠️这两个数都是**修法前口径**（10-07 之前 `topk_rebalance` 把调仓日那一格收益记给了
               还没建仓的新篮子，见 `etf_admission.py:1228`）；两档各吃这一刀多少**没分别量过**，
               所以"门槛判死"现在只是修法前的实测，七折那整张表要不要重跑待裁。
               为什么族内等权而不是按 |ICIR| 给权：#40 首轮实测那条 |ICIR| 排第一的量能因子
               独占 40.1% 权重、自己单跑只有 9.46%（|ICIR|/|IC|=9.2 是"稳而不强"）。
    族间合成   每条族分数再做一次日内百分位秩、然后等权平均 ⇒ **一族一票**：
                   S_{t,i} = mean_f rank_pct_i(F^f_{t,i})
               为什么改到族这一层：按条发席位时同族变体名次天然挨着，09-24 实测"前 5 条"
               全是量能一族副本；#41 实测八族配平**样本外 34.81%（七折全正、七折全跑赢可投域、
               最差折 +0.69%）** vs 现行因子层尺子同口径 22.61% ⇒ +12.2pp。
               ⚠️同上：这一整句是**修法前口径**，样本外那条账 10-07 只重算了环 2 归档那一路的
               年化与超额（见 `etf_admission.py:1228` 那句），七折那张表没重跑。
               族这一层天然替掉了"同族副本"问题，`RED_BAR` 判重在因子层路径上照旧生效。
               注意：族层合成**允许跑输最强单族**（10-07 归档 族间合成 k=10 全窗口 15.56%
               < 最强单族·动量 23.20%；修法前那版是 43.42% < 单族·反转 57.82%）——
               一族一票买的是"不押注单一逻辑"，不是收益最大化；验收看样本外那笔账。
    建仓时序   信号日 s 收盘算分 → s+1 **开盘**建仓 → 持有 hold 日到建仓后第 hold 天
               （＝下一信号日的次日）开盘平仓；权重落在行 [i+2, i+2+hold)，
               调仓日 s+1 那一格（O_s→O_{s+1}）归**上一篮**（它到 s+1 开盘才卖）
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

基准给两条，差在哪一目了然
--------------------------
    ew_all      全池等权（不筛不扣费）—— "跑赢市场平均没有"
    ew_universe 过完闸门（次新/容量/连续低量）的可投域等权 —— "跑赢真正买得到的那部分市场没有"
只用全池当基准会把大量根本买不到的僵尸基算进基准、把超额系统性压低；
另附 510300（沪深 300ETF）买入持有作第三条参照。

只读：不写因子库、不触发 git 提交。产物（默认 family 层级每张表 29 行 =
13 个标签 × 两个 k（族间合成 1 + 单族 8 + 因子层合成对照 1 + 单因子 3）= 26 行，
再 + 3 行基准；`ETF_COMPOSITE_LEVEL=factor` 那一路只有 7 个标签 × 2 + 3 = 11 行）
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
# 除合成组合外，单独回放哪几条因子（零件行，判据的对照面见下面 runs 组装处）
SINGLE_TOP = int(os.environ.get("ETF_SINGLE_TOP", "3"))
# 合成发生在哪一层：family = 一族一票（现行，#41 路径⑤）；factor = 旧的按条发席位
COMPOSITE_LEVEL = os.environ.get("ETF_COMPOSITE_LEVEL", "family").strip().lower()
if COMPOSITE_LEVEL not in ("family", "factor"):
    raise SystemExit(f"[口径] ETF_COMPOSITE_LEVEL 只认 family|factor，收到 {COMPOSITE_LEVEL!r}")


def pick_from_eval(path, top=TOP, primary=PRIMARY_H):
    """读第一环产物 → (合成用的 spec 列表, 权重用的指标表)。

    本环**不重算 IC**，只认第一环那张表：两环共用同一份 csv，才不会出现
    "因子层说 0.06、组合层用的是另一批因子"这种自相矛盾的产物。
    指标表里带 `family`（环 1 的族列），族层合成靠它分组；缺列的旧表会落到"未标族"一组，
    那等于整张表一票 —— 读数会立刻不对劲，不会静默出错。
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
                         "rank_icir": r[key], "expr": r["expr"],
                         "family": r.get("family") or "未标族"}
             for _, r in ok.iterrows()}
    return specs, stats


def build_family_scores(facs, stats):
    """因子表 → 族内全员等权合成 → (族名 → 打分表, 族名 → 成员名单)。

    族内一律**不设 IC 门槛**、全员一票（实测见模块 docstring）；每条成员按自己的
    RankIC 符号翻向，与因子层合成同一口径。
    """
    members = {}
    for n, st in stats.items():
        if n in facs:
            members.setdefault(st.get("family") or "未标族", []).append(n)
    scores = {}
    for f, ms in sorted(members.items()):
        scores[f] = EA.composite_score(
            facs, {n: float(np.sign(stats[n]["rank_ic"])) / len(ms) for n in ms})[0]
    return scores, members


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
    print(f"  合成层级       = {COMPOSITE_LEVEL}"
          + ("（族内全员等权 → 族间一族一票）" if COMPOSITE_LEVEL == "family"
             else f"（旧口径：|RankICIR| 前 {PICK} → 判重 → 前 {TOP}，权重 ∝ |RankICIR|）"))

    specs, stats = pick_from_eval(EVAL_CSV, TOP, PRIMARY_H)
    names = {EA.spec_name(s) for s in specs}
    weights, detail = EA.icir_weights(stats, top=PICK)
    if not weights:
        if COMPOSITE_LEVEL == "factor":
            raise SystemExit("[合成] 没有一条因子过 |ICIR| 下限 —— 本池此刻无因子可用，"
                             "这本身就是结论，不做组合回放")
        print("\n[因子层对照] 没有一条因子过 |ICIR| 下限 —— 只跳过对照行，族层照合成")
    else:
        print(f"\n[初选] {len(names)} 条候选中按 |RankICIR@h{PRIMARY_H}| >= 0.05 取 "
              f"{len(weights)} 条（负 IC 已翻向），判重前的名单：")
        for d in detail:
            print(f"    {d['name']:<22s} w={d['weight']:+.3f}  "
                  f"RankICIR={d['icir']:+.3f}  RankIC={d['rank_ic']:+.4f}  {d['expr']}")

    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    print(f"[截面厚度] {EA.thickness_report(m)}")
    # 族层合成要吃全部候选（一族一票），因子层只吃终选那几条
    need = set(stats) if COMPOSITE_LEVEL == "family" else set(weights)
    use = [s for s in specs if EA.spec_name(s) in need]
    facs_full = EA.evaluate_factors(pool, use)
    facs = {k: EA.slice_to_start(v, EA.EVAL_START) for k, v in facs_full.items()}
    missing = [EA.spec_name(s) for s in use if EA.spec_name(s) not in facs]
    if missing:
        raise SystemExit(f"[求值] 第一环能算、第二环算不出的因子：{missing}（口径漂移）")
    del facs_full, pool

    if weights:
        kept, dropped = dedupe_by_corr(facs, weights, keep_max=TOP)
        if dropped:
            print(f"\n[判重] 初选 {len(weights)} 条 → 撤下 {len(dropped)} 条"
                  f"（|逐日截面秩相关| >= {EA.RED_BAR}）：")
            for n, vs, c in dropped:
                print(f"    ✂️ 撤下 {n}" +
                      (f"：与已留的 {vs} 秩相关 {c:+.3f}" if vs else "：终选名额已满"))
        tot = sum(abs(v) for v in kept.values()) or 1.0
        weights = {k: v / tot for k, v in kept.items()}
        print(f"[终选] 因子层合成用 {len(weights)} 条（权重已按 |RankICIR| 重新归一）：")
        for nm, w in weights.items():
            print(f"    {nm:<22s} w={w:+.3f}  RankICIR={stats[nm]['rank_icir']:+.3f}")

    days = m["close"].index
    runs = []
    if COMPOSITE_LEVEL == "family":
        fam_scores, fam_members = build_family_scores(facs, stats)
        score, used = EA.composite_score(
            fam_scores, {f: 1.0 / len(fam_scores) for f in fam_scores})
        print(f"\n[族层合成] {len(fam_scores)} 族 × {sum(len(v) for v in fam_members.values())} "
              f"条候选，一族一票（族内全员等权、按 RankIC 符号翻向）：")
        for f, ms in fam_members.items():
            print(f"    {f:<5s} {len(ms)} 条：" + " ".join(ms))
        _pc, _sc = EA.cs_corr_mean(fam_scores, min_cs=EA.MIN_CS, verbose=False)
        if len(_sc) > 1:
            off = _sc.where(~np.eye(len(_sc), dtype=bool), np.nan)
            i, j = np.unravel_index(int(np.nanargmax(off.abs().to_numpy())), off.shape)
            print(f"    [族间秩相关] 最高一对 = {off.index[i]} × {off.columns[j]}："
                  f"|corr|={float(off.iloc[i, j]):.3f}（只报读数；一族一票，"
                  f"裁决权在环 3 判重）")

        runs.append((f"族间合成·{len(used)}族等权", score))
        for f, sc in fam_scores.items():
            runs.append((f"单族·{f}({len(fam_members[f])}条)", sc))
        if weights:
            fs, used_f = EA.composite_score(facs, weights)
            runs.append((f"因子层合成·{len(used_f)}条(旧口径对照)", fs))
    else:
        score, used = EA.composite_score(facs, weights)
        runs.append(("合成·" + "+".join(used), score))
    # 单因子也各自回放一遍，作"合成有没有跑赢它的零件"的读数。注意这条判据在两层上
    # 含义不同：因子层（旧口径）合成跑输自己最好的零件 = 权重发错，那是要修的 bug；
    # 族层（现行）合成**本来就允许**跑输最强单族（10-07 归档 k=10：族间合成 15.56%
    # < 最强单族·动量 23.20%，修法前那版是 43.42% < 单族·反转 57.82%），
    # 因为"一族一票"买的不是收益最大化而是"不把仓位押在一条因子上"——
    # 判据放在样本外那条账上（#41 七折：八族配平 34.81% vs 挑族 34.91%，
    # 而"只用过去挑最强那一族"没有稳定赢法，详见 CHANGELOG 09-26 节；⚠️那两张
    # 七折表都是**修法前口径**，10-07 只重跑了环 2 归档这一路，七折没重跑）。
    # 单因子那一行按各自 IC 的符号翻向（负 IC 的因子做多低分侧），与合成口径一致。
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
        kind = ("family_composite" if label.startswith("族间合成") else
                "family" if label.startswith("单族") else
                "composite" if label.startswith(("合成", "因子层合成")) else "single")
        for k in EA.TOP_K:
            net, gross, s = EA.topk_rebalance(sc, m, days, k=k, hold=EA.HOLD)
            st = EA.portfolio_stats(net, name=f"{label} k={k}")
            st.update({"kind": kind, "label": label, "k": k, "hold": EA.HOLD,
                       "composite_level": COMPOSITE_LEVEL,
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
    front = ["label", "kind", "composite_level", "k", "hold", "ann_return", "gross_ann_return",
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
