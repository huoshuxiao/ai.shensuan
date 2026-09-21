# -*- coding: utf-8 -*-
"""因子 DSL 求值、IC 计算与内置名屏蔽。

IC = Spearman(factor_t, r_{t+1})，即因子值与下一期收益的秩相关。
"""

import numpy as np
import pandas as pd
import pytest

from factor_dsl import compute_ic, safe_eval, safe_spearman
from synth import make_daily_pool


@pytest.fixture(scope="module")
def df():
    return make_daily_pool(n_codes=1, n_bars=300, seed=3)["510300"]


@pytest.mark.parametrize("expr,manual", [
    ("delay(close, 1)", lambda d: d["close"].shift(1)),
    ("delta(close, 3)", lambda d: d["close"].diff(3)),
    ("ts_mean(close, 5)", lambda d: d["close"].rolling(5).mean()),
    ("ts_std(returns, 10)", lambda d: d["close"].pct_change().rolling(10).std()),
    ("abs(delta(close, 1))", lambda d: (d["close"].diff(1)).abs()),
    ("sign(delta(close, 1))", lambda d: np.sign(d["close"].diff(1))),
])
def test_dsl_operators_match_pandas(df, expr, manual):
    got = safe_eval(expr, df)
    want = manual(df)
    pd.testing.assert_series_equal(got.rename(None), want.rename(None),
                                   check_names=False)


def test_price_window_operators_take_the_dataframe(df):
    """ma/std/max/min 是 df 类算子，第一参数必须是 df 本身：
    ma(close, 5) 会在 Series 上做 ["close"] 而下标异常。"""
    pd.testing.assert_series_equal(
        safe_eval("ma(df, 5)", df).rename(None),
        df["close"].rolling(5).mean().rename(None), check_names=False)
    with pytest.raises(ValueError):
        safe_eval("ma(close, 5)", df)


def test_bare_column_names_bind_to_series_not_lambdas(df):
    """FACTOR_DSL 里的列名条目是取数 lambda，必须被同名真实 Series 覆盖，
    否则 GP 产出的 `close / delay(close, 5)` 会拿 lambda 当向量用。"""
    got = safe_eval("close / delay(close, 5) - 1", df)
    want = df["close"] / df["close"].shift(5) - 1
    pd.testing.assert_series_equal(got.rename(None), want.rename(None),
                                   check_names=False)


def test_builtins_are_blocked(df):
    with pytest.raises(ValueError):
        safe_eval("__import__('os').system('id')", df)
    with pytest.raises(ValueError):
        safe_eval("open('/etc/passwd').read()", df)


def test_unknown_symbol_raises_value_error(df):
    with pytest.raises(ValueError):
        safe_eval("not_a_real_operator(close)", df)


def test_compute_ic_detects_sign():
    """完全预测下一期收益的因子 → IC = +1；反向 → -1"""
    n = 200
    rng = np.random.default_rng(4)
    x = pd.Series(rng.normal(0, 1, n))
    forward = x * 1.0 + rng.normal(0, 1e-9, n)
    idx = pd.bdate_range("2022-01-03", periods=n)
    x.index = forward.index = idx
    assert compute_ic(x, forward) == pytest.approx(1.0)
    assert compute_ic(-x, forward) == pytest.approx(-1.0)


def test_compute_ic_needs_enough_samples():
    idx = pd.bdate_range("2022-01-03", periods=29)
    x = pd.Series(np.arange(29, dtype=float), index=idx)
    assert compute_ic(x, x * 2) == 0.0


def test_compute_ic_on_constant_factor_is_zero():
    idx = pd.bdate_range("2022-01-03", periods=100)
    const = pd.Series(1.0, index=idx)
    noise = pd.Series(np.random.default_rng(2).normal(0, 1, 100), index=idx)
    assert compute_ic(const, noise) == 0.0
    assert safe_spearman(const, noise) == 0.0


def test_nan_rows_are_dropped_before_ic():
    """滚动窗口前段是 NaN：样本数按 dropna 之后计，不足 30 判 0。"""
    idx = pd.bdate_range("2022-01-03", periods=100)
    x = pd.Series(np.arange(100, dtype=float), index=idx)
    y = x * 2
    head = x.copy()
    head.iloc[:40] = np.nan
    assert compute_ic(head, y) == pytest.approx(1.0)
    tail = x.copy()
    tail.iloc[:75] = np.nan
    assert compute_ic(tail, y) == 0.0
