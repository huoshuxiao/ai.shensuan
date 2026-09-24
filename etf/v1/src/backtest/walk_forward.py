# -*- coding: utf-8 -*-
"""Walk-forward 验证（滚动训练-检验 + 逐折 DSR + 跨折稳定性）

对抗"全样本挑最优"的过拟合：时间轴切若干折，每折只用训练段
挖因子，在之后的测试段模拟交易；测试段表现才是可信的样本外证据。
试验次数（n_trials）逐折累计喂给 DSR。本模块不出 PBO，原因见末尾注释。"""

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


def _dsr_from_equity(equity, n_trials):
    """对一条净值曲线做 DSR 检验（样本 <30 直接判不过）。

    年化系数走 frequency_adapter：dsr.annualize_sharpe 的默认值是分钟
    常量 240*252，日线折内沿用会把年化夏普高估 √240≈15.5 倍。"""
    rets = equity.pct_change().dropna().values
    if len(rets) < 30:
        return {"dsr": 0.0, "passed": False}
    r = deflated_sharpe_ratio(rets, n_trials=n_trials)
    if "sr_observed" in r:
        ann = get_adapter()
        r["sharpe_annual"] = ann.annualize_sharpe(r["sr_observed"])
        # 门槛同批年化：逐 bar 的 SR* 只有千分之几，不换算就看不出
        # "没过 DSR" 是成绩差还是门槛本身被量纲撑大了
        if "sr0_expected_max" in r:
            r["sr0_annual"] = round(
                ann.annualize_sharpe(r["sr0_expected_max"]), 4)
    return r


def walk_forward_run(pool, universe, factor_fn, backtest_fn,
                     trial_counter=None):
    """主循环。factor_fn(train_pool, train_ts, fold=i) 只喂训练段数据挖因子，
    引擎集合由调用方决定（应与主链同构，否则验的不是同一批因子）；
    信号生成/回测用 pool 全量（策略内部 .loc[:ts] 天然不越界，
    测试区间由 test_ts 边界控制）。每折挖出的因子数计入 TrialCounter，
    使后续折的 DSR 门槛随累计试验次数收紧。"""
    print("\n========== Walk-forward + 逐折 DSR ==========")
    # 与主流程一致：时间轴取最长历史标的，短历史首位标的会截断分折窗口
    ref_code = max(pool, key=lambda c: len(pool[c]))
    all_ts = pool[ref_code].index
    splits = make_splits(all_ts)
    tc = trial_counter or TrialCounter()
    all_stats, all_dsr = [], []

    for i, (tr_s, tr_e, te_s, te_e) in enumerate(splits):
        print(f"\n--- 折 {i + 1}/{len(splits)} ---")
        print(f"  训练段 {tr_s:%Y-%m-%d} ~ {tr_e:%Y-%m-%d} | "
              f"测试段 {te_s:%Y-%m-%d} ~ {te_e:%Y-%m-%d}")
        train_pool = {c: df.loc[tr_s:tr_e] for c, df in pool.items()}
        train_pool = {c: df for c, df in train_pool.items()
                      if len(df) > 240}  # 训练样本不足一年的标的剔除
        print(f"  训练段可用标的: {len(train_pool)}/{len(pool)}"
              f"（需 >240 根 bar）")
        if not train_pool:
            print("  ⏭️ 本折跳过：训练段无标的满足长度门槛，无法挖因子")
            continue
        factors = factor_fn(train_pool,
                            train_pool[next(iter(train_pool))].index,
                            fold=i + 1)
        if not factors:
            print("  ⏭️ 本折跳过：训练段未挖出通过 IC 门槛的因子")
            continue
        print(f"  训练段挖出因子 {len(factors)} 个: "
              f"{[f.get('name', '?') for f in factors]}")
        tc.add(len(factors))

        from strategy import IntradayRotationStrategy
        test_ts = all_ts[(all_ts >= te_s) & (all_ts <= te_e)]
        strategy = IntradayRotationStrategy(factors, pool, universe)
        signals = strategy.generate_signals(test_ts)
        result = backtest_fn(pool, signals, universe, None)
        stats = result["stats"]
        equity = result["equity"]["equity"]
        n_trials_now = DSR.get("n_trials") or tc.get()
        dsr_result = _dsr_from_equity(equity, n_trials_now)
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
        all_stats.append(stats)
        all_dsr.append(dsr_result.get("dsr", 0))

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
            "dsr_list": all_dsr, "pbo": pbo_result}