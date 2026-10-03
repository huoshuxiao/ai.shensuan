# -*- coding: utf-8 -*-
"""Walk-forward 验证（滚动训练-检验 + 逐折 DSR + 合并样本外 DSR + 跨折稳定性）

对抗"全样本挑最优"的过拟合：时间轴切若干折，每折只用训练段
挖因子，在之后的测试段模拟交易；测试段表现才是可信的样本外证据。
试验次数（n_trials）逐折累计喂给 DSR。除逐折检验外，另出一条
"合并样本外"检验（把各折不相交的测试收益拼接后整体做 DSR），
因为折内 T 太短会让运气门槛高到任何策略都够不着。
本模块不出 PBO，原因见末尾注释。"""

import numpy as np
import pandas as pd
from config import WALK_FORWARD, DSR
from dsr import deflated_sharpe_ratio, TrialCounter
from frequency_adapter import get_adapter


def make_splits(timestamps):
    """切分 (训练起, 训练止, 测试起, 测试止)。
    训练/测试比 train_ratio；两段之间留 embargo_bars 根 bar 隔离带，
    防止训练段末端的窗口信息（如 shift(-1) 标签、滚动特征）
    泄漏进测试段开头。"""
    n = len(timestamps)
    n_splits = WALK_FORWARD["n_splits"]
    train_ratio = WALK_FORWARD["train_ratio"]
    embargo = WALK_FORWARD["embargo_bars"]
    window = n // n_splits
    splits = []
    for i in range(n_splits):
        start = i * window
        end = start + window if i < n_splits - 1 else n
        train_end = start + int((end - start) * train_ratio)
        test_start = min(train_end + embargo, end - 1)
        if test_start >= end - 1:
            continue
        splits.append((timestamps[start], timestamps[train_end - 1],
                       timestamps[test_start], timestamps[end - 1]))
    return splits


def _dsr_from_returns(rets, n_trials):
    """对一段逐 bar 收益做 DSR 检验（样本 <30 直接判不过）。

    年化系数走 frequency_adapter：dsr.annualize_sharpe 的默认值是分钟
    常量 240*252，日线折内沿用会把年化夏普高估 √240≈15.5 倍。"""
    r = np.asarray(pd.Series(rets).dropna(), dtype=float)
    if len(r) < 30:
        return {"dsr": 0.0, "passed": False}
    res = deflated_sharpe_ratio(r, n_trials=n_trials)
    if "sr_observed" in res:
        ann = get_adapter()
        res["sharpe_annual"] = ann.annualize_sharpe(res["sr_observed"])
        # 门槛同批年化：逐 bar 的 SR* 只有千分之几，不换算就看不出
        # "没过 DSR" 是成绩差还是门槛本身被量纲撑大了
        if "sr0_expected_max" in res:
            res["sr0_annual"] = round(
                ann.annualize_sharpe(res["sr0_expected_max"]), 4)
    return res


def _dsr_from_equity(equity, n_trials):
    return _dsr_from_returns(equity.pct_change().dropna(), n_trials)


def walk_forward_run(pool, universe, factor_fn, backtest_fn,
                     trial_counter=None, checkpoint=None, wide_pool=None):
    """主循环。factor_fn(train_pool, train_ts, fold=i) 只喂训练段数据挖因子，
    引擎集合由调用方决定（应与主链同构，否则验的不是同一批因子）；
    信号生成/回测用 pool 全量（策略内部 .loc[:ts] 天然不越界，
    测试区间由 test_ts 边界控制）。每折挖出的因子数计入 TrialCounter，
    使后续折的 DSR 门槛随累计试验次数收紧。
    两道 DSR 读数并列：逐折（各段自己的 T）与合并样本外（各段 T 之和），
    后者是现状几何下唯一可判定的那条。
    checkpoint（可选，run_checkpoint.RunCheckpoint）：段级断点缓存。传了就在
    每折开挖前先问一次"上一场这一折挖过没有"，命中则整个容器循环不起。
    **只缓存因子**：信号/回测/DSR 每折照原样重算，续传不改判据。
    wide_pool（可选，WP-2）：全市场镜像的宽面板。配 `WALK_FORWARD["pit_pool"]`
    打开时，每折的**候选池改在本折训练段内点时挑**（挖与交易用同一批），
    挡掉"按今日成交额挑 100 只再喂给 2010 年那一折"的幸存者+前视泄漏。"""
    print("\n========== Walk-forward + 逐折 DSR ==========")
    pit_cfg = WALK_FORWARD.get("pit_pool") or {}
    use_pit = bool(pit_cfg.get("enabled")) and wide_pool is not None
    if pit_cfg.get("enabled") and wide_pool is None:
        print("  ⚠️ pit_pool 已开但没传宽面板 ⇒ 本轮仍按今日池跑（泄漏入口未关）")
    # 与主流程一致：时间轴取最长历史标的，短历史首位标的会截断分折窗口
    ref_code = max(pool, key=lambda c: len(pool[c]))
    all_ts = pool[ref_code].index
    splits = make_splits(all_ts)
    tc = trial_counter or TrialCounter()
    all_stats, all_dsr = [], []
    # 各折测试段的逐 bar 收益，段间时间不相交 ⇒ 可按时间顺序拼成一条长样本外序列
    oos_rets = []

    for i, (tr_s, tr_e, te_s, te_e) in enumerate(splits):
        print(f"\n--- 折 {i + 1}/{len(splits)} ---")
        print(f"  训练段 {tr_s:%Y-%m-%d} ~ {tr_e:%Y-%m-%d} | "
              f"测试段 {te_s:%Y-%m-%d} ~ {te_e:%Y-%m-%d}")
        # 本折用哪一批标的：点时池（只用训练段内的信息挑）或现状池（今日成交额挑的 100 只）
        fold_pool, fold_universe, pit_read = pool, universe, None
        if use_pit:
            from fold_pool import FoldUniverse, select_pit_pool
            sel, pit_read = select_pit_pool(
                wide_pool, tr_s, tr_e,
                min_share=pit_cfg.get("min_share", 0.5),
                max_codes=pit_cfg.get("max_codes", 100))
            if not sel:
                print("  ⏭️ 本折跳过：点时池在该训练段挑不出任何标的"
                      f"（min_share={pit_cfg.get('min_share')}）")
                continue
            fold_pool = {c: wide_pool[c] for c in sel}
            fold_universe = FoldUniverse(wide_pool, sel)
            print(f"  🕰️ 点时池：截断前 {pit_read['点时池_截断前']} 只 → 入选 "
                  f"{pit_read['点时池_入选']} 只｜日均厚 {pit_read['日均厚']} 只｜"
                  f"截面尺子有牙（≥30 只）的天数 {pit_read['≥30只天%']}%"
                  f"｜过闸天数占比中位 {pit_read['过闸天数占比中位']}"
                  f"｜其中不在今日池的新面孔 {len(set(sel) - set(pool))} 只")
        train_pool = {c: df.loc[tr_s:tr_e] for c, df in fold_pool.items()}
        train_pool = {c: df for c, df in train_pool.items()
                      if len(df) > 240}  # 训练样本不足一年的标的剔除
        print(f"  训练段可用标的: {len(train_pool)}/{len(fold_pool)}"
              f"（需 >240 根 bar）")
        if not train_pool:
            print("  ⏭️ 本折跳过：训练段无标的满足长度门槛，无法挖因子")
            continue
        train_idx = train_pool[next(iter(train_pool))].index
        fold_key = f"折 {i + 1} 因子"
        if checkpoint:
            factors = checkpoint.resolve(
                fold_key,
                lambda: factor_fn(train_pool, train_idx, fold=i + 1),
                label=f"折 {i + 1} 训练段挖因子")
        else:
            factors = factor_fn(train_pool, train_idx, fold=i + 1)
        if not factors:
            print("  ⏭️ 本折跳过：训练段未挖出通过 IC 门槛的因子")
            continue
        print(f"  训练段挖出因子 {len(factors)} 个: "
              f"{[f.get('name', '?') for f in factors]}")
        # 续传命中的折不许再加第二遍：它那一笔在上一场就已经落进 trial_counter.json
        # （不变式「缓存条目存在 ⇔ 试验数已入账」靠下面 record 紧跟 add 守住）
        if not (checkpoint and checkpoint.replayed):
            tc.add(len(factors))
            if checkpoint:
                checkpoint.record(fold_key, factors, tc=tc,
                                  label=f"折 {i + 1} 训练段挖因子")

        from strategy import IntradayRotationStrategy
        test_ts = all_ts[(all_ts >= te_s) & (all_ts <= te_e)]
        strategy = IntradayRotationStrategy(factors, fold_pool, fold_universe)
        signals = strategy.generate_signals(test_ts)
        result = backtest_fn(fold_pool, signals, fold_universe, None)
        stats = result["stats"]
        equity = result["equity"]["equity"]
        # 续传命中的折要用**上一场这一折当时的**累计 N 做门槛，不能读现在的账本：
        # 现在的账本已走到整场末尾，拿它算折 1 会把跨场对不上的读数写进折表
        n_trials_now = DSR.get("n_trials") or (
            checkpoint.replay_n_trials
            if (checkpoint and checkpoint.replayed
                and checkpoint.replay_n_trials) else tc.get())
        dsr_result = _dsr_from_equity(equity, n_trials_now)
        leg = equity.pct_change().dropna()
        if len(leg):
            oos_rets.append(leg)
        print(f"  测试段: 收益={stats.get('总收益率')}  "
              f"夏普={stats.get('夏普比率')}  "
              f"交易={stats.get('交易次数')}  "
              f"DSR={dsr_result.get('dsr', 0):.4f}（累计试验 N={n_trials_now}"
              f"，运气门槛年化={dsr_result.get('sr0_annual')}）")
        stats["fold"] = i + 1
        stats["测试段bar数"] = int(len(test_ts))
        stats["n_trials"] = n_trials_now
        stats["DSR"] = round(dsr_result.get("dsr", 0), 4)
        stats["DSR通过"] = dsr_result.get("passed", False)
        # 门槛与 DSR 并排放：只报 DSR 就看不出"差多少"，而 DSR 是个概率、
        # 年化夏普才是能被业务读出来的量
        stats["运气门槛年化"] = dsr_result.get("sr0_annual")
        # 记录本折实际验证的因子来源，便于发现"只验了注册表基线"这类退化
        stats["折内因子数"] = len(factors)
        stats["折内因子来源"] = "/".join(sorted(
            {str(f.get("source", "?")) for f in factors}))
        # 池口径与宽度一起进折表：不记这两列就看不出"这一折的判决是拿哪批标的跑的"
        # （点时池与今日池的折表数字不可比，混在一张表里读会读成漂移）
        stats["池口径"] = "点时" if use_pit else "今日"
        stats["挖掘层宽度"] = len(train_pool)
        if pit_read:
            stats["日均厚"] = pit_read["日均厚"]
            stats["截面有牙天%"] = pit_read["≥30只天%"]
            stats["点时新面孔"] = len(set(sel) - set(pool))
        all_stats.append(stats)
        all_dsr.append(dsr_result.get("dsr", 0))

    merged = {}
    if oos_rets:
        # 拼接而不是取折均：DSR 的运气门槛 SR* ≈ sqrt((1+0.5·SR̂²)/T)·极值系数，
        # T 是**每段自己的** bar 数 —— 3 折各 402 bar 时每段门槛都被推到年化
        # 4.33（09-24 探针 shell/i15_threshold_probe_0924.py，实测最好的折 1.47），
        # 于是"折内 DSR 全 False"只是分折切出来的几何，不是策略判决。拼成一条
        # 样本外序列后 T 变成各段之和，门槛才落到可判定的量级（train_ratio
        # 0.5 下三段拼接 ≈2.0 千 bar → 过线所需年化 1.31）。代价：拼接抹掉了
        # 折与折之间的空档，隐含"样本外收益在时间上可复利串联"这一假定，
        # 因此它与逐折读数并列报告，不替换逐折。
        oos = pd.concat(oos_rets).sort_index()
        n_trials_now = DSR.get("n_trials") or tc.get()
        merged = _dsr_from_returns(oos.values, n_trials_now)
        # 几何随读数一起落盘：门槛是 T 的函数，不记 T/N 的 DSR 没法跨轮比较
        merged["n_segments"] = len(oos_rets)
        merged["train_ratio"] = WALK_FORWARD["train_ratio"]
        print(f"\n--- 合并样本外检验（{len(oos_rets)} 段拼接）---")
        print(f"  bar={merged.get('n_samples', len(oos))}  "
              f"年化夏普={merged.get('sharpe_annual', 0):.4f}  "
              f"DSR={merged.get('dsr', 0):.4f}  "
              f"运气门槛年化={merged.get('sr0_annual')}  "
              f"通过={merged.get('passed', False)}（累计试验 N={n_trials_now}）")

    summary = {}
    if all_stats:
        sharpes = np.array([float(s["夏普比率"]) for s in all_stats])
        summary = {
            "折数": len(all_stats),
            "验证段合计bar": int(sum(s["测试段bar数"] for s in all_stats)),
            "平均收益": np.mean([float(s["总收益率"].strip("%"))
                              for s in all_stats]),
            "平均夏普": float(sharpes.mean()),
            # 各折是不同时间段，样本外夏普跨折的离散度就是"这条结论稳不稳"
            "夏普跨折std": (float(sharpes.std(ddof=1))
                          if len(sharpes) > 1 else 0.0),
            "正夏普折数": int((sharpes > 0).sum()),
            "平均DSR": np.mean(all_dsr),
            "DSR通过折数": sum(1 for d in all_dsr if d > 0.95)}
        if merged:
            # 合并段与逐折并列：这两条读数是"同一段样本外证据"的两种取法，
            # 只有拼接那条可判定，逐折那条在现状几何下恒 False
            summary.update({
                "合并样本外段数": len(oos_rets),
                "合并样本外bar": int(merged.get("n_samples", len(oos))),
                "合并样本外夏普": round(
                    float(merged.get("sharpe_annual", 0.0)), 4),
                "合并样本外DSR": round(float(merged.get("dsr", 0.0)), 4),
                "合并门槛年化": merged.get("sr0_annual"),
                "合并DSR通过": bool(merged.get("passed", False))})

    # 这里**不再**算 PBO。旧实现把各折测试段净值喂给 cscv_pbo：折与折的时间段
    # 互不相交，build_returns_matrix 对齐后每列只有自己那段非零、其余填 0，
    # 于是"IS 上选冠军"退化成"看这一段的日历落在哪折"，冠军的 OOS 排名只能取
    # {1/4, 2/4, 3/4}（3 折实测 PBO=0.9444，纯切分假象）。CSCV 的前提是 N 个
    # 配置在同一条时间轴上竞争，walk-forward 的折不满足；参数选择偏差那部分
    # 由 main.py 的策略级配置族 PBO（真实回测过的 OFAT 档）承担。
    pbo_result = {}

    print("\n--- Walk-forward 汇总 ---")
    for k, v in summary.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    return {"folds": all_stats, "summary": summary,
            "dsr_list": all_dsr, "merged_oos_dsr": merged,
            "pbo": pbo_result}