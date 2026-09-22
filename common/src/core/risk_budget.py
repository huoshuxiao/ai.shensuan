# -*- coding: utf-8 -*-
"""风险预算加权

把"该给每个因子多少权重"转化为滚动样本外统计问题：
用滚动窗口的 IC 序列估计每个因子的稳定性，稳定者多配。"""

import numpy as np
import pandas as pd
from config import RISK_BUDGET
from factor_dsl import safe_spearman


def rolling_ic(factor, forward_ret, window=4800, min_periods=100):
    """滚动 IC 序列：每 step=window/20 根 bar 取一个样本点，
    对 [i-window, i) 窗口内因子值与下期收益求 Spearman 相关。
    常量窗口（std≈0）无定义，直接跳过该点。"""
    df = pd.concat([factor, forward_ret], axis=1).dropna()
    if df.empty:
        return pd.Series(dtype=float)

    step = max(1, window // 20)
    vals, idx = [], []
    for i in range(window, len(df), step):
        sub = df.iloc[i - window:i]
        a, b = sub.iloc[:, 0], sub.iloc[:, 1]
        if a.std() < 1e-9 or b.std() < 1e-9:
            continue
        vals.append(safe_spearman(a, b))
        idx.append(df.index[i])
    return pd.Series(vals, index=idx)


def _equal_weights(names):
    """等权基线：w_i = 1/n"""
    n = len(names)
    return {name: 1.0 / n for name in names}


def _ic_ir_weights(ic_series, names, decay_halflife=None):
    """ICIR 加权：w_i ∝ |ICIR_i| = |mean(IC)/std(IC)|。
    ICIR 高意味着因子不仅有效而且稳定，是风险预算意义上的
    "单位预测噪声换多少信号"。样本 <20 个 IC 点的因子权重记 0；
    全部为 0 时回退等权。"""
    weights_raw = {}
    for name in names:
        s = ic_series.get(name)
        if s is None or len(s) < 20:
            weights_raw[name] = 0.0
            continue
        s = s.dropna()
        if len(s) < 20:
            weights_raw[name] = 0.0
            continue
        icir = s.mean() / (s.std() + 1e-9)
        weights_raw[name] = abs(icir)
    total = sum(weights_raw.values())
    if total < 1e-9:
        return _equal_weights(names)
    return {name: weights_raw[name] / total for name in names}


def _cap_weights(weights, min_w, max_w):
    """权重限幅：任意因子 ∈ [min_w, max_w]，截断后重新归一化，
    防止单因子权重过大（变相分散化约束）。"""
    capped = {k: max(min_w, min(max_w, v)) for k, v in weights.items()}
    total = sum(capped.values())
    if total < 1e-9:
        return weights
    return {k: v / total for k, v in capped.items()}


def compute_factor_weights(factors, pool, method=None):
    """因子加权入口。method:
    - equal: 等权
    - ic / ic_ir: 滚动 ICIR 绝对值加权（ic 亦走 ICIR，稳健性更好）
    - ic_ir_capped: ICIR 加权后再限幅归一
    注：滚动 IC 只用参考标的（pool 第一只）计算，作为全池的代理。"""
    cfg = RISK_BUDGET
    method = method or cfg["weight_method"]
    names = [f["name"] for f in factors]

    if method == "equal" or len(names) < 2:
        return _equal_weights(names)

    if method in ("ic", "ic_ir", "ic_ir_capped"):
        ref_code = next(iter(pool))
        df = pool[ref_code]
        fwd_ret = df["close"].pct_change().shift(-1)  # 下一期收益（预测目标）
        ic_series = {}
        for f in factors:
            impl = f.get("impl", {})
            if ref_code in impl:
                ic_series[f["name"]] = rolling_ic(
                    impl[ref_code]["factor"], fwd_ret,
                    window=cfg["ic_lookback_bars"])

        w = _ic_ir_weights(ic_series, names,
                           decay_halflife=cfg["decay_halflife_bars"])
        if method == "ic_ir_capped":
            w = _cap_weights(w, cfg["min_weight_per_factor"],
                             cfg["max_weight_per_factor"])
        return w

    return _equal_weights(names)