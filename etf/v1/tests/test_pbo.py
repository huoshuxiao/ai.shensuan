# -*- coding: utf-8 -*-
"""PBO（CSCV 组合对称交叉验证）方向性已知答案。

PBO = P(logit(ω̂) ≤ 0)，ω̂ = IS 冠军配置在 OOS 的相对排名。
过拟合的定义就是"样本内挑出来的最优在样本外掉到后半"，
所以构造两类已知答案的矩阵：稳定 alpha → PBO 低；
赢在 IS 必输在 OOS → PBO 高。
"""

import numpy as np
import pandas as pd
import pytest

from pbo import build_returns_matrix, cscv_pbo


def _matrix(cols):
    return pd.DataFrame(cols)


def test_stable_alpha_gives_low_pbo():
    rng = np.random.default_rng(0)
    T = 400
    alpha = rng.normal(3e-3, 0.01, T)          # 全程稳定为正
    noise = [rng.normal(0, 0.01, T) for _ in range(3)]
    df = _matrix({"alpha": alpha, "n1": noise[0],
                  "n2": noise[1], "n3": noise[2]})
    res = cscv_pbo(df, n_splits=4, max_combinations=1000)
    assert res["pbo"] < 0.5
    assert res["passed"] is True
    assert res["oos_rank_median"] > 0.5        # 冠军在 OOS 也在头部


def test_regime_flip_family_gives_high_pbo():
    """构造"IS 冠军必然 OOS 垫底"的配置族（Bailey et al. 2017 的反例）。

    三列分段均值矩阵：a 强在前半、b 强在后半、c 在奇偶段交替。
    任取 2 段做 IS，冠军在剩下 2 段都落到后半 ⇒ PBO 接近 1。
    必须叠加 bar 级噪声：完全确定的并列会让 argmax 按下标取先，
    测不出翻转效应（实测 PBO 反而出 0）。
    """
    T, seg = 400, 100
    rng = np.random.default_rng(0)

    def build(means, noise=1e-3):
        base = np.concatenate([np.full(seg, m) for m in means])
        return base + rng.normal(0, noise, T)

    df = _matrix({
        "a": build([5e-3, 5e-3, -1e-3, -1e-3]),
        "b": build([-1e-3, -1e-3, 5e-3, 5e-3]),
        "c": build([5e-3, -1e-3, 5e-3, -1e-3]),
    })
    res = cscv_pbo(df, n_splits=4, max_combinations=1000)
    assert res["pbo"] > 0.5
    assert res["passed"] is False
    assert res["oos_rank_median"] < 0.5, "IS 冠军在 OOS 应落到后半排名"


def test_identical_configs_are_conservatively_rejected():
    """全部配置同一条收益曲线时，IS 冠军在 OOS 必然并列中位，
    logit(0.5)=0 被判为 <=0 ⇒ PBO=1。实现上宁拒不放。"""
    rng = np.random.default_rng(4)
    one = rng.normal(0, 0.01, 400)
    res = cscv_pbo(_matrix({k: one.copy() for k in "pqr"}),
                   n_splits=4, max_combinations=1000)
    assert res["pbo"] == 1.0 and res["passed"] is False


def test_odd_n_splits_is_forced_even():
    rng = np.random.default_rng(2)
    df = _matrix({k: rng.normal(0, 0.01, 300) for k in "xyz"})
    res = cscv_pbo(df, n_splits=5, max_combinations=1000)
    # 5 段无法 IS/OOS 对称；降到 4 段后组合数 = C(4,2)
    assert res["n_combinations"] == 6


def test_too_few_configs_returns_error():
    rng = np.random.default_rng(2)
    df = _matrix({k: rng.normal(0, 0.01, 300) for k in "ab"})
    res = cscv_pbo(df, n_splits=4, max_combinations=100)
    assert res["pbo"] == 1.0 and res["passed"] is False


def test_build_returns_matrix_fills_missing_with_flat():
    idx = pd.bdate_range("2022-01-03", periods=6)
    eq_a = pd.Series([100, 101, 102, 103, 104, 105], index=idx)
    eq_b = pd.Series([100, 100.5, 101, np.nan, np.nan, np.nan], index=idx)
    m = build_returns_matrix({"a": eq_a, "b": eq_b})
    assert list(m.columns) == ["a", "b"]
    assert np.isfinite(m.values).all()         # 缺失收益按空仓 0 处理
    assert len(m) == len(idx) - 1, "全 NaN 行（首行收益）被删除"
    assert m["a"].iloc[0] == pytest.approx(0.01)   # 首个有效收益 = 101/100-1
    assert m["b"].tail(3).eq(0.0).all(), "标的停更后按空仓 0 收益填充"
