# -*- coding: utf-8 -*-
"""断点 4 回归：折内挖掘与自动重挖必须走真实引擎集合。

修复前 main.py 的折内 factor_fn 写死 mine_factors(pool)（只跑 9 个注册表
因子），自动重挖同理 ⇒ 样本外验证完全覆盖不到入库的 GP/DSL/RD-Agent 因子。
"""

import numpy as np
import pandas as pd
import pytest

import main
from config import (
    WALK_FORWARD, AUTO_REMINING, GENETIC, STRATEGY_PBO, PBO_TIMELINE,
    MULTI_STRATEGY, FACTOR_DECAY,
)
from factor_dsl import compute_ic
from synth import make_daily_pool


def _fake_gp_factor(pool, name="gp_fake"):
    """伪造一个 GP 产出：结构必须与 factor_genetic 的返回一致"""
    impl, ics = {}, []
    for code, df in pool.items():
        # 用量价类表达式：与注册表里的价格因子不相关，才不会被去重吃掉
        f = df["volume"].pct_change(3) * np.sign(df["close"].diff(1))
        ic = compute_ic(f, df["close"].pct_change().shift(-1))
        impl[code] = {"factor": f, "ic": ic}
        ics.append(ic)
    m = float(np.mean(ics)) if ics else 0.0
    return {"name": name, "expr": "delta(volume, 3) * sign(delta(close, 1))",
            "mean_ic": m if abs(m) >= 0.02 else 0.05,
            "icir": 0.5, "impl": impl, "source": "genetic"}


@pytest.fixture
def pool():
    return make_daily_pool(n_codes=3, n_bars=1200)


@pytest.fixture
def small_gp(monkeypatch):
    """压小 GP 规模：真实随机搜索留给 test_genetic 验，这里只验接线"""
    monkeypatch.setitem(GENETIC, "population_size", 6)
    monkeypatch.setitem(GENETIC, "n_generations", 2)
    monkeypatch.setitem(GENETIC, "stagnation_generations", 2)


def test_mine_with_engines_routes_gp_output(pool, monkeypatch, small_gp):
    import factor_genetic
    calls = []

    def spy(p, seed_factors=None, history_path=None):
        calls.append(history_path)
        return [_fake_gp_factor(p)]

    monkeypatch.setattr(factor_genetic, "genetic_mine", spy)
    got = main.mine_with_engines(pool, ["registry", "genetic"],
                                 tag="折 1", path_prefix="wf_fold1")
    assert calls == [f"{main.RESULTS_DIR}/wf_fold1_genetic_history.csv"], \
        "折内 GP 演化史必须写独立路径，不能覆写主链产物"
    assert any(f["source"] == "genetic" for f in got), \
        "GP 产出必须进入折内候选集"
    assert all(set(f["impl"]) <= set(pool) for f in got)


def test_registry_only_engines_never_touch_gp(pool, monkeypatch):
    import factor_genetic
    monkeypatch.setattr(
        factor_genetic, "genetic_mine",
        lambda *a, **k: pytest.fail("registry-only 不该调用 GP"))
    got = main.mine_with_engines(pool, ["registry"])
    assert got and all(f["source"] == "simple" for f in got)


def test_stage_validation_passes_fold_index_into_mining(pool, monkeypatch,
                                                        small_gp):
    """walk-forward 必须以 fold=k 调 factor_fn，且折内候选含 GP 因子。"""
    import walk_forward as wf_mod
    import factor_genetic
    seen = {}

    def fake_run(p, universe, factor_fn, backtest_fn, trial_counter=None):
        got = factor_fn(p, p[next(iter(p))].index, fold=2)
        seen["n"] = len(got)
        seen["sources"] = {f["source"] for f in got}
        return {"folds": [], "summary": {}, "dsr_list": [], "pbo": {}}

    monkeypatch.setattr(factor_genetic, "genetic_mine",
                        lambda p, seed_factors=None, history_path=None:
                        [_fake_gp_factor(p)])
    monkeypatch.setattr(wf_mod, "walk_forward_run", fake_run)
    for cfg in (STRATEGY_PBO, PBO_TIMELINE, MULTI_STRATEGY):
        monkeypatch.setitem(cfg, "enabled", False)
    monkeypatch.setitem(WALK_FORWARD, "enabled", True)
    monkeypatch.setitem(WALK_FORWARD, "fold_engines", ["registry", "genetic"])

    main.stage_validation([], pool, None, pool[next(iter(pool))].index,
                          None, None)
    assert seen["n"] > 0 and "genetic" in seen["sources"]


def test_stage_lifecycle_remining_uses_engines_not_registry_only(
        pool, monkeypatch, small_gp):
    """重挖回调走引擎集合，且 DSR/PBO 结论作为 indicator 传给闭环（断点 3）。"""
    import auto_remining as ar_mod
    import factor_genetic
    captured, gp_calls = {}, []
    monkeypatch.setattr(
        factor_genetic, "genetic_mine",
        lambda p, seed_factors=None, history_path=None:
        gp_calls.append(history_path) or [])

    class LoopSpy:
        def __init__(self, mine_fn, evaluate_fn, params=None, state=None):
            captured["mine_fn"] = mine_fn
            captured["state"] = state

        def run_once(self, **kw):
            captured["kw"] = kw
            return {"triggered": False, "reason": "测试用：不触发"}

        def get_log_df(self):
            return pd.DataFrame()

    monkeypatch.setattr(ar_mod, "AutoReminingLoop", LoopSpy)
    monkeypatch.setitem(AUTO_REMINING, "enabled", True)
    monkeypatch.setitem(AUTO_REMINING, "remining_engines",
                        ["registry", "genetic"])
    monkeypatch.setitem(FACTOR_DECAY, "enabled", False)

    main.stage_lifecycle([], pool, None, pool[next(iter(pool))].index,
                         None, dsr_now=0.3, pbo_now=0.9, pbo_trend=None)
    assert captured["kw"]["indicator"]["bad_this_round"] is True, \
        "指标判据必须传入闭环，而不是在调用方另起一套"
    captured["mine_fn"](pool)
    assert gp_calls == [f"{main.RESULTS_DIR}/remining_genetic_history.csv"]
