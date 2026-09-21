# -*- coding: utf-8 -*-
"""断点 3 回归：重挖触发器的跨运行状态与单一判据。

修复前：DualIndicatorTrigger 每次运行新建实例，consecutive_count 归零
⇒ consecutive_rounds=2 永不满足；dsr_prev/pbo_prev 恒为 None ⇒ Δ 分支失效；
ReminingTrigger 的 cooldown/max_rounds 同样只在内存里。
"""

import json

import pytest

from remining_state import ReminingState
from trigger_logic import DualIndicatorTrigger
from auto_remining import ReminingTrigger, AutoReminingLoop

Rounds = 2  # TRIGGER_LOGIC["consecutive_rounds"]


@pytest.fixture
def state_file(tmp_path):
    return str(tmp_path / "remining_state.json")


def _fresh(state_file):
    return ReminingState(path=state_file)


def test_single_bad_round_does_not_trigger(state_file):
    r = DualIndicatorTrigger(state=_fresh(state_file)).check(
        dsr_now=0.4, pbo_now=0.8, current_bar=100)
    assert r["bad_this_round"] is True
    assert r["triggered"] is False
    assert r["consecutive"] == 1


def test_two_consecutive_bad_rounds_across_processes_trigger(state_file):
    """模拟两次独立进程：每次重新构造对象，计数必须从磁盘续上。"""
    DualIndicatorTrigger(state=_fresh(state_file)).check(
        dsr_now=0.4, pbo_now=0.8, current_bar=100)
    r2 = DualIndicatorTrigger(state=_fresh(state_file)).check(
        dsr_now=0.3, pbo_now=0.9, current_bar=101)
    assert r2["consecutive"] == Rounds
    assert r2["triggered"] is True


def test_good_round_resets_consecutive_counter(state_file):
    for bar in (1, 2):
        DualIndicatorTrigger(state=_fresh(state_file)).check(
            0.4, 0.8, current_bar=bar)
    DualIndicatorTrigger(state=_fresh(state_file)).check(
        0.99, 0.05, current_bar=3)
    r = DualIndicatorTrigger(state=_fresh(state_file)).check(
        0.4, 0.8, current_bar=4)
    assert r["consecutive"] == 1, "中间好转必须把连续计数清零"


def test_delta_branch_uses_persisted_previous_metrics(state_file):
    """水平都达标但 DSR 单轮跳水：ΔDSR < dsr_delta_threshold 才算恶化。"""
    t = DualIndicatorTrigger(state=_fresh(state_file))
    t.check(0.99, 0.10, current_bar=200)
    t.check(0.96, 0.10, current_bar=201)          # Δ=-0.03，未越线
    assert t.check(0.50, 0.10, current_bar=202)["bad_this_round"] is False
    or_mode = DualIndicatorTrigger({"mode": "or"}, state=_fresh(state_file))
    # 上一轮基准已落盘，or 模式下单边 Δ 即成立
    assert or_mode.check(0.10, 0.10, current_bar=203)["bad_this_round"] is True


def test_missing_indicator_is_not_counted_as_deterioration(state_file):
    r = DualIndicatorTrigger(state=_fresh(state_file)).check(
        dsr_now=0.4, pbo_now=None, current_bar=1)
    assert r["triggered"] is False and r["bad_this_round"] is False
    assert "指标缺失" in r["reason"]
    assert _fresh(state_file).consecutive_bad_rounds == 0


def test_weighted_mode_accumulates_across_runs(state_file):
    p = {"mode": "weighted", "weighted_threshold": 0.2}
    r1 = DualIndicatorTrigger(p, state=_fresh(state_file)).check(
        0.3, 0.8, current_bar=1)
    assert r1["score"] > 0.2 and r1["triggered"] is False
    r2 = DualIndicatorTrigger(p, state=_fresh(state_file)).check(
        0.3, 0.8, current_bar=2)
    assert r2["consecutive"] == 2 and r2["triggered"] is True


def test_cooldown_survives_new_process(state_file):
    tr = ReminingTrigger(state=_fresh(state_file))
    tr.mark_remining(500, "test", "indicator")
    ok, reason, _ = ReminingTrigger(state=_fresh(state_file)).should_remining(
        510, [], indicator={"triggered": True, "reason": "x"})
    assert not ok and "冷却" in reason


def test_max_rounds_survives_new_process(state_file):
    st = _fresh(state_file)
    tr = ReminingTrigger(state=st)
    limit = tr.p["max_remining_rounds"]
    for i in range(limit):
        tr.mark_remining(10_000 + i * 10_000, "r", "indicator")
    ok, reason, _ = ReminingTrigger(state=_fresh(state_file)).should_remining(
        999_999, [], indicator={"triggered": True, "reason": "x"})
    assert not ok and "最大重挖轮数" in reason


def test_indicator_verdict_is_routed_into_remining_trigger(state_file):
    """两套判据合一：DSR/PBO 结论作为一路触发源进入 ReminingTrigger。"""
    tr = ReminingTrigger(state=_fresh(state_file))
    ok, _, ttype = tr.should_remining(
        500, [], indicator={"triggered": True, "reason": "DSR + PBO 同时恶化"})
    assert (ok, ttype) == (True, "indicator")
    ok2, _, _ = ReminingTrigger(state=_fresh(state_file)).should_remining(
        500, [], indicator={"triggered": False})
    assert ok2 is False


def test_state_is_bucketed_by_frequency(state_file):
    daily = _fresh(state_file)
    daily.record_indicator(True, dsr=0.3, pbo=0.8)
    daily.save()
    minute = ReminingState(path=state_file, freq="1min")
    assert minute.consecutive_bad_rounds == 0
    minute.record_indicator(False, dsr=0.99, pbo=0.1)
    minute.save()
    blob = json.load(open(state_file, encoding="utf-8"))
    assert set(blob) == {"daily", "1min"}
    assert _fresh(state_file).consecutive_bad_rounds == 1


def test_auto_remining_loop_reports_throttle_reason(state_file):
    st = _fresh(state_file)
    ReminingTrigger(state=st).mark_remining(1000, "x", "indicator")
    loop = AutoReminingLoop(mine_fn=lambda p: [], evaluate_fn=lambda f: {},
                            state=_fresh(state_file))
    out = loop.run_once(current_bar=1001, current_factors=[], pool={},
                        decay_alerts=[], indicator={"triggered": True})
    assert out["triggered"] is False and "冷却" in out["reason"]
