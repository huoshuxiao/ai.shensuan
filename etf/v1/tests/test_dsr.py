# -*- coding: utf-8 -*-
"""Deflated Sharpe Ratio 公式与边界条件。

DSR = Φ( (SR̂ - SR* - SR_b) · sqrt(T-1) / sqrt(1 - γ₁·SR̂ + (γ₂-1)/4·SR̂²) )
其中 SR* = sqrt(var)·[(1-γ)·z(1-1/N) + γ·z(1-1/(N·e))] 为 N 次独立试验下
"纯运气"能达到的期望最大夏普，γ 为 Euler–Mascheroni 常数。
"""

import numpy as np
import pytest
from scipy.stats import norm

from dsr import deflated_sharpe_ratio, expected_max_sharpe, EULER_GAMMA
from frequency_adapter import get_adapter


def test_expected_max_sharpe_closed_form():
    n = 10
    want = ((1 - EULER_GAMMA) * norm.ppf(1 - 1 / n) +
            EULER_GAMMA * norm.ppf(1 - 1 / (n * np.e)))
    assert expected_max_sharpe(n) == pytest.approx(want, rel=1e-9)


def test_expected_max_sharpe_needs_at_least_two_trials():
    assert expected_max_sharpe(1) == 0.0
    assert expected_max_sharpe(0) == 0.0


def test_deflation_threshold_rises_with_trial_count():
    a, b, c = (expected_max_sharpe(n) for n in (5, 50, 5000))
    assert a < b < c


def test_dsr_is_consistent_with_its_own_formula():
    """用返回值里的 SR̂/γ₁/γ₂ 反解 z，应还原出同一个 DSR。
    这条能挡住分母符号、sqrt(T-1) 位置这类代数错。"""
    rng = np.random.default_rng(3)
    r = rng.normal(2e-3, 0.01, 500)
    res = deflated_sharpe_ratio(r, n_trials=20)
    sr, skew, kurt = res["sr_observed"], res["skew"], res["kurt"]
    denom = np.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
    z = (sr - res["sr0_expected_max"]) * np.sqrt(res["n_samples"] - 1) / denom
    assert res["dsr"] == pytest.approx(float(norm.cdf(z)), rel=1e-9)


def test_more_trials_deflates_dsr():
    rng = np.random.default_rng(3)
    r = rng.normal(2e-3, 0.01, 500)
    assert (deflated_sharpe_ratio(r, n_trials=5)["dsr"] >
            deflated_sharpe_ratio(r, n_trials=2000)["dsr"])


def test_rejects_insufficient_sample():
    res = deflated_sharpe_ratio(np.full(29, 1e-4), n_trials=10)
    assert res["passed"] is False and res["dsr"] == 0.0


def test_rejects_zero_volatility():
    res = deflated_sharpe_ratio(np.ones(200) * 1e-4, n_trials=10)
    assert res["passed"] is False and res["dsr"] == 0.0


def test_strong_iid_drift_passes_when_few_trials():
    rng = np.random.default_rng(11)
    r = rng.normal(5e-3, 0.005, 1000)
    assert deflated_sharpe_ratio(r, n_trials=2)["passed"] is True


def test_pure_noise_does_not_pass():
    rng = np.random.default_rng(11)
    assert deflated_sharpe_ratio(
        rng.normal(0, 0.01, 1000), n_trials=50)["passed"] is False


def test_daily_annualization_differs_from_minute_default():
    """dsr.annualize_sharpe 的默认 bars_per_year 是分钟常量 240*252；
    日线必须走 adapter 的 252，否则年化夏普被高估 √240 ≈ 15.5 倍。"""
    sr_bar = 0.05
    adapter = get_adapter("daily")
    assert adapter.annualize_sharpe(sr_bar) == pytest.approx(
        sr_bar * np.sqrt(252))
    assert adapter.annualize_sharpe(sr_bar) != pytest.approx(
        sr_bar * np.sqrt(240 * 252))
