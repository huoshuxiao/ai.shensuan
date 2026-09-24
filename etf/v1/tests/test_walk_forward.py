# -*- coding: utf-8 -*-
"""Walk-forward 分折与折内契约。

回归覆盖两处修复：
1) factor_fn 必须收到折号与"只有训练段"的池子（样本外验证的意义所在）；
2) 折内 DSR 的年化系数走 frequency_adapter，不再沿用 dsr.py 的分钟默认值。
"""

import numpy as np
import pandas as pd
import pytest

from config import WALK_FORWARD, LOOKBACK_BARS
from frequency_adapter import get_adapter
from walk_forward import make_splits, _dsr_from_equity, walk_forward_run
from synth import make_daily_pool


def _fake_factor(pool, tag="gp_fake"):
    """把"收盘价 z 分数"包装成管线认可的因子结构（含 impl）"""
    from factor_dsl import compute_ic
    impl, ics = {}, []
    for code, df in pool.items():
        f = (df["close"] - df["close"].rolling(20).mean()) / \
            df["close"].rolling(20).std()
        ic = compute_ic(f, df["close"].pct_change().shift(-1))
        impl[code] = {"factor": f, "ic": ic}
        ics.append(ic)
    m = float(np.mean(ics))
    return {"name": tag, "expr": "zscore(close,20)", "mean_ic": m,
            "icir": m, "impl": impl, "source": "genetic"}


def _fake_backtest(pool, signals, universe, risk):
    # 长表信号每根调仓 bar 有 top_k 行，净值序列按调仓时点去重
    idx = signals.index.unique()
    eq = pd.DataFrame({"equity": 10_000 * np.exp(
        np.cumsum(np.linspace(1e-4, 3e-4, len(idx))
                  + np.random.default_rng(len(idx)).normal(0, 2e-3, len(idx))))},
        index=idx)
    return {"equity": eq, "trades": pd.DataFrame(),
            "stats": {"总收益率": "12.34%", "夏普比率": "1.5",
                      "最大回撤": "-5%", "交易次数": "7"}}


def test_make_splits_keeps_embargo_gap():
    ts = pd.bdate_range("2015-01-05", periods=1200)
    splits = make_splits(ts)
    assert splits, "样本足够时应切出折"
    embargo = WALK_FORWARD["embargo_bars"]
    for tr_s, tr_e, te_s, te_e in splits:
        assert tr_s < tr_e < te_s <= te_e, "训练段必须整体早于测试段"
        i_tr_e = ts.get_loc(tr_e)
        i_te_s = ts.get_loc(te_s)
        assert i_te_s - i_tr_e >= embargo or te_s == ts[-1]
        # 折与折之间测试段不重叠
    test_ranges = [(a, b) for _, _, a, b in splits]
    for (s1, e1), (s2, e2) in zip(test_ranges, test_ranges[1:]):
        assert e1 < s2


def test_factor_fn_gets_fold_index_and_train_only_data():
    pool = make_daily_pool(n_codes=3, n_bars=1200)
    ts = pool[next(iter(pool))].index
    splits = make_splits(ts)
    calls = []

    def factor_fn(p, idx, fold=None):
        calls.append({"fold": fold, "max_ts": idx.max()})
        return [_fake_factor(p, tag=f"gp_fold{fold}")]

    wf = walk_forward_run(pool, None, factor_fn, _fake_backtest,
                          trial_counter=None)
    assert len(calls) == len(splits)
    assert [c["fold"] for c in calls] == list(range(1, len(splits) + 1))
    for c, (tr_s, tr_e, te_s, te_e) in zip(calls, splits):
        assert c["max_ts"] <= tr_e, \
            f"折 {c['fold']} 的 factor_fn 拿到了训练段之外的数据（前视）"
    assert wf["folds"], "每折都应产出一行统计"


def test_fold_stats_record_factor_sources():
    """折内统计必须留下"本折到底验了哪些来源"的痕迹，
    否则退化回只验注册表基线时无人察觉。"""
    pool = make_daily_pool(n_codes=3, n_bars=1200)
    wf = walk_forward_run(
        pool, None,
        lambda p, idx, fold=None: [_fake_factor(p)],
        _fake_backtest, trial_counter=None)
    for row in wf["folds"]:
        assert row["折内因子来源"] == "genetic"
        assert row["折内因子数"] == 1


def test_fold_dsr_annualization_uses_daily_adapter():
    idx = pd.bdate_range("2020-01-02", periods=500)
    rng = np.random.default_rng(5)
    eq = pd.Series(10_000 * np.cumprod(
        1 + rng.normal(3e-3, 0.005, 500)), index=idx)
    res = _dsr_from_equity(eq.to_frame("equity")["equity"], n_trials=2)
    assert res["sharpe_annual"] == pytest.approx(
        res["sr_observed"] * np.sqrt(get_adapter("daily").bars_per_year))


def test_fold_with_no_factors_is_skipped():
    pool = make_daily_pool(n_codes=3, n_bars=1200)
    wf = walk_forward_run(pool, None, lambda p, idx, fold=None: [],
                          _fake_backtest, trial_counter=None)
    assert wf["folds"] == []
    assert wf["summary"] == {}


def test_train_length_gate_skips_short_folds():
    """训练段 bar 数不足 240 的折应整折跳过，而不是硬挖。"""
    pool = make_daily_pool(n_codes=3, n_bars=300)
    assert len(make_splits(pool[next(iter(pool))].index)) > 0
    calls = []
    wf = walk_forward_run(
        pool, None,
        lambda p, idx, fold=None: calls.append(len(p)) or [],
        _fake_backtest, trial_counter=None)
    assert wf["folds"] == []
    assert calls == [] or all(n == 0 for n in calls)
