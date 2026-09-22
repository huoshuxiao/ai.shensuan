# -*- coding: utf-8 -*-
"""因子正交化 + 相关性剪枝

挖掘出的因子往往高度共线（如多个动量变体）。直接等权叠加会让
单一信号被重复计权，故先按 |IC| 贪心剪掉相关系数超阈值的因子，
再对留下的因子做正交化，使每个因子贡献独立的信息维度。"""

import numpy as np
import pandas as pd
from config import ORTHOGONAL
from factor_dsl import spearman_corr_matrix


def _corr_matrix(factor_values):
    """把各因子的宽表 (date × code) stack 成长表后算 Spearman 秩相关。
    future_stack=True 走 pandas 新实现（不引入 NA 行，故不可再传 dropna）"""
    names = list(factor_values.keys())
    flat = {n: factor_values[n].stack(future_stack=True)
            for n in names}
    wide = pd.DataFrame(flat).dropna()
    return spearman_corr_matrix(wide), names


def correlation_prune(factor_values, factor_ic, threshold):
    """相关性剪枝：按 |IC| 从高到低排序，逐个考察——
    若与已保留因子中任意一个的 |Spearman| > threshold 则丢弃。
    保证留下方彼此低相关，且优先保留预测力最强的。"""
    corr, names = _corr_matrix(factor_values)
    ranked = sorted(names, key=lambda n: abs(factor_ic.get(n, 0)),
                    reverse=True)
    kept = []
    for name in ranked:
        ok = True
        for k in kept:
            if abs(corr.loc[name, k]) > threshold:
                ok = False
                break
        if ok:
            kept.append(name)
    return kept


def gram_schmidt_orthogonalize(factor_values, factor_ic):
    """修正 Gram-Schmidt 正交化（按 |IC| 降序处理）：
    q_i = v_i - Σ_{j<i} (v_i·q_j / q_j·q_j) · q_j
    即每个因子剔除掉前面（更强）因子张成的分量，残余部分与已处理
    因子线性无关。IC 排序保证强因子的信息不被弱因子分走。"""
    names = sorted(factor_values.keys(),
                   key=lambda n: abs(factor_ic.get(n, 0)), reverse=True)
    if not names:
        return {}
    flat = {n: factor_values[n].stack(future_stack=True) for n in names}
    wide = pd.DataFrame(flat).dropna()
    if wide.empty or wide.shape[1] < 2:
        return factor_values

    X = wide.values
    Q = np.zeros_like(X)
    for i in range(X.shape[1]):
        v = X[:, i].copy()
        for j in range(i):
            q = Q[:, j]
            denom = q @ q
            if denom > 1e-12:
                v -= (v @ q) / denom * q   # 剔除 q_j 方向投影
        Q[:, i] = v

    result = {}
    for i, name in enumerate(names):
        s = pd.Series(Q[:, i], index=wide.index)
        result[name] = s.unstack(level=1)
    return result


def pca_orthogonalize(factor_values, factor_ic, var_ratio=0.95):
    """PCA 正交化：各因子先 z-score 标准化，再对样本矩阵 X 做 SVD
    (X = U·S·Vᵀ)，取累计方差贡献 ≥ var_ratio 的前 k 个主成分
    PC_i = U_i · S_i 作为新因子（k≥2）。
    新因子两两正交，但不再对应原始经济含义（名字变为 PC1..PCk）。"""
    names = sorted(factor_values.keys(),
                   key=lambda n: abs(factor_ic.get(n, 0)), reverse=True)
    flat = {n: factor_values[n].stack(future_stack=True) for n in names}
    wide = pd.DataFrame(flat).dropna()
    if wide.empty or wide.shape[1] < 2:
        return factor_values

    X = wide.values
    X = (X - X.mean(0)) / (X.std(0) + 1e-9)   # z-score 标准化
    U, S, Vt = np.linalg.svd(X, full_matrices=False)
    var = S ** 2 / (S ** 2).sum()             # 各主成分方差占比
    cum = np.cumsum(var)
    k = int(np.searchsorted(cum, var_ratio) + 1)
    k = max(2, min(k, len(names)))
    PCs = U[:, :k] * S[:k]

    result = {}
    for i in range(k):
        s = pd.Series(PCs[:, i], index=wide.index)
        result[f"PC{i+1}"] = s.unstack(level=1)
    return result


def orthogonalize_factors(factors, threshold=None, method=None):
    """完整流程：宽表组装 -> 相关性剪枝 -> 按配置选正交方法
    (gram_schmidt / pca / 仅剪枝)。输出仍是 [{name, mean_ic,
    icir, impl:{code: {factor: Series}}}] 的标准因子结构。
    注意 PCA 模式下输出名字为 PC*，与输入名字不同会被自然过滤掉。"""
    cfg = ORTHOGONAL
    if not cfg["enabled"] or len(factors) < 2:
        return factors
    threshold = threshold if threshold is not None else cfg["corr_threshold"]
    method = method or cfg["method"]

    factor_values, factor_ic = {}, {}
    for f in factors:
        name = f["name"]
        factor_ic[name] = f.get("mean_ic", f.get("ic", 0.0))
        factor_values[name] = pd.DataFrame(
            {code: item["factor"] for code, item in f["impl"].items()})

    kept = correlation_prune(factor_values, factor_ic, threshold)
    kept_values = {n: factor_values[n] for n in kept}
    if method == "gram_schmidt":
        ortho = gram_schmidt_orthogonalize(kept_values, factor_ic)
    elif method == "pca":
        ortho = pca_orthogonalize(kept_values, factor_ic)
    else:
        ortho = kept_values

    new_factors = []
    for f in factors:
        if f["name"] not in ortho:
            continue
        new_impl = {}
        for code in f["impl"]:
            if code in ortho[f["name"]].columns:
                new_impl[code] = {"factor": ortho[f["name"]][code],
                                  "ic": f["impl"][code]["ic"]}
        if new_impl:
            new_factors.append({"name": f["name"],
                                "mean_ic": f["mean_ic"],
                                "icir": f.get("icir", 0.0),
                                "impl": new_impl})
    return new_factors