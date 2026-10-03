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


def test_price_window_operators_accept_dataframe_and_series(df):
    """`ma/std/max/min` 第一参数两种写法都要成立，且语义各归各位（10-01 乙）。

    - 传 DataFrame（历史写法：两条线的生产链、`rdagent_driver` 的 SMA/STD/MAX/MIN
      翻译、8 条内置模板全这么写）→ 取 close，行为与改动前逐位相同；
    - 传 Series（模型自然写出的 `ma(volume, 60)`、`std(df['volume'], 60)`、
      `min(low, 20)`）→ 滚该列，**与对应的 ts_* 算子逐位相等**，不是"算得出个数"就算通。

    改之前只有第一种能求值，第二种当场 `KeyError: 'close'`：10-01 实测 9 条 LLM
    假设里 3 条语义合法却死在写法上（`etf/v1/temp/tmp_roles_smoke_1001/ab_prompt_1001.log`）。
    """
    # --- 放行格：DataFrame 分支仍是 close ---
    pd.testing.assert_series_equal(
        safe_eval("ma(df, 5)", df).rename(None),
        df["close"].rolling(5).mean().rename(None), check_names=False)
    for expr, want in [
        ("std(df, 20)", df["close"].rolling(20).std()),
        ("max(df, 20)", df["close"].rolling(20).max()),
        ("min(df, 20)", df["close"].rolling(20).min()),
        # --- 判红格（旧版会抛）：Series 分支 = 滚这一列 ---
        ("ma(close, 5)", df["close"].rolling(5).mean()),
        ("std(returns, 20)", df["close"].pct_change(fill_method=None).rolling(20).std()),
        ("max(high, 20)", df["high"].rolling(20).max()),
        ("min(low, 20)", df["low"].rolling(20).min()),
        ("std(df['volume'], 60)", df["volume"].rolling(60).std()),
    ]:
        pd.testing.assert_series_equal(safe_eval(expr, df).rename(None),
                                       want.rename(None), check_names=False)


@pytest.mark.parametrize("series_expr,ts_expr", [
    ("ma(close, 20)", "ts_mean(close, 20)"),
    ("std(returns, 20)", "ts_std(returns, 20)"),
    ("max(high, 20)", "ts_max(high, 20)"),
    ("min(low, 20)", "ts_min(low, 20)"),
    ("ma(volume, 60)", "ts_mean(volume, 60)"),
])
def test_series_form_equals_the_ts_operator(df, series_expr, ts_expr):
    """打通传列写法不能顺手改语义：与既有 ts_* 必须逐位相同。"""
    pd.testing.assert_series_equal(safe_eval(series_expr, df).rename(None),
                                   safe_eval(ts_expr, df).rename(None),
                                   check_names=False)


def test_mixed_df_and_series_in_one_expression(df):
    """`ma(df, 20) + std(volume, 20)`：旧版后半截抛，新版整条求得出。"""
    want = df["close"].rolling(20).mean() + df["volume"].rolling(20).std()
    pd.testing.assert_series_equal(
        safe_eval("ma(df, 20) + std(volume, 20)", df).rename(None),
        want.rename(None), check_names=False)


def test_old_df_only_behaviour_would_have_rejected_series(df, monkeypatch):
    """负对照（牙）：把第一参数归一化偷偷换回「一律取 close」的旧实现，
    传列写法必须当场判红。此格若恒过 ⇒ 上面那批 Series 断言是空的。"""
    import factor_dsl as FD
    monkeypatch.setattr(FD, "_price_series", lambda x: x["close"])
    for expr in ("ma(close, 5)", "std(returns, 20)", "min(low, 20)"):
        with pytest.raises(ValueError):
            safe_eval(expr, df)
    # DataFrame 写法在旧实现下照常 ⇒ 证明这枚牙咬的只是 Series 那一支
    pd.testing.assert_series_equal(
        safe_eval("ma(df, 5)", df).rename(None),
        df["close"].rolling(5).mean().rename(None), check_names=False)


@pytest.mark.parametrize("expr", [
    "ma(df, 'x')",      # 窗口不是数
    "std(5, 20)",       # 第一参数是标量
    "max(close, high)",  # 窗口位传了序列
    "ma(3, 20)",
])
def test_widened_operators_still_raise_on_bad_args(df, expr):
    """放宽的是「第一参数收不收 Series」，不是收任意垃圾：写错必须仍然抛。"""
    with pytest.raises(ValueError):
        safe_eval(expr, df)


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
