# -*- coding: utf-8 -*-
"""丙-2 回归：折内挖掘必须停用 official 这一源，主线与自动重挖照旧吃它。

背景（09-30 探针 `etf/v1/temp/official_pit_probe_0930.py` 实量）：
`try_official_rdagent()` 的 `output_dir` 默认是不分折的固定目录 ⇒ 三折回收的是同一份
**全历史面板**上挖出来的 `factors.json`（9 条候选，戳 09-29 18:36），各折挑中 2/3/3 条、
两两只重合 1~2 条。`recount_foreign_ic` 只在本折重算了 IC，**没人重算"候选从哪来"**。
判据一行没改动的候选来源不是点时的 ⇒ walk-forward 折内那一段不再接受 official。

六格夹具（放宽/豁免类必须同批交付）：折内 2 格 + 不传 fold 3 格 + 配置层 1 格。
"""

import pytest

import multi_source_mining as msm


def _mk_cfg(sources, official_in_fold=False):
    return {"sources": list(sources), "timeout_seconds": 30,
            "min_ic_per_source": 0.005, "merge_mode": "none",
            "official_in_fold": official_in_fold,
            "weights": {s: 1.0 for s in sources}}


def _stub_source(name):
    return {"name": f"{name}_f", "mean_ic": 0.05, "icir": 1.0,
            "source": name, "impl": {}}


@pytest.fixture
def wired(monkeypatch):
    """把四个源跑器换成探针：记录谁被调过，并返回一条该源自己的因子"""
    called = []
    runners = {}
    for src in ("official", "llm", "simple", "genetic"):
        def _make(s=src):
            def _runner(pool):
                called.append(s)
                return [_stub_source(s)]
            return _runner
        runners[src] = _make()
        monkeypatch.setattr(msm, f"_run_{src}", runners[src])

    class _Lib:
        def __init__(self):
            self.upserted = []

        def batch_upsert(self, factors, source=""):
            self.upserted.append((source, len(factors)))

        def save_markdown(self):
            pass

    lib = _Lib()
    monkeypatch.setattr(msm, "get_library", lambda: lib)
    monkeypatch.setattr(msm, "MULTI_SOURCE", _mk_cfg(["official", "llm", "simple"],
                                                     official_in_fold=False))
    return called, lib


def test_fold_disables_official(wired):
    """① 折内：official 跑器一次都不被调，其余两源照跑"""
    called, _ = wired
    msm.multi_source_mine({}, fold=2)
    assert "official" not in called
    assert sorted(called) == ["llm", "simple"]


def test_fold_official_never_reaches_library(wired):
    """② 折内：落库那一环也拿不到 official（不是只少打一行日志）"""
    called, lib = wired
    msm.multi_source_mine({}, fold=1)
    assert [s for s, _n in lib.upserted] == ["llm", "simple"]
    assert "official" not in called


def test_no_fold_still_uses_official(wired):
    """③ 正对照：不传 fold ⇒ official 必须被调（主线与自动重挖的旧行为不许被误伤）"""
    called, lib = wired
    msm.multi_source_mine({})
    assert "official" in called
    assert sorted(called) == ["llm", "official", "simple"]
    assert [s for s, _n in lib.upserted] == ["official", "llm", "simple"]


def test_fold_zero_is_still_in_fold(monkeypatch):
    """④ fold=0 是折号、不是"没传"：走真 `is not None` 判断而非 truthiness"""
    called = []
    for src in ("official", "llm"):
        monkeypatch.setattr(msm, f"_run_{src}",
                            lambda pool, s=src: (called.append(s), [_stub_source(s)])[1])
    monkeypatch.setattr(msm, "_run_simple", lambda pool: [])
    monkeypatch.setattr(msm, "_run_genetic", lambda pool: [])
    monkeypatch.setattr(msm, "MULTI_SOURCE", _mk_cfg(["official", "llm"],
                                            official_in_fold=False))

    class _Lib:
        def batch_upsert(self, factors, source=""):
            pass

        def save_markdown(self):
            pass

    monkeypatch.setattr(msm, "get_library", lambda: _Lib())
    msm.multi_source_mine({}, fold=0)
    assert called == ["llm"]


def test_sources_without_official_untouched(monkeypatch):
    """⑤ 配置里本来就没有 official（股票线那份不是这样，但换皮配置会）⇒ 行为逐字不变"""
    called = []
    for src in ("llm", "simple"):
        monkeypatch.setattr(msm, f"_run_{src}",
                            lambda pool, s=src: (called.append(s), [_stub_source(s)])[1])
    monkeypatch.setattr(msm, "MULTI_SOURCE", _mk_cfg(["llm", "simple"],
                                            official_in_fold=False))

    class _Lib:
        def batch_upsert(self, factors, source=""):
            pass

        def save_markdown(self):
            pass

    monkeypatch.setattr(msm, "get_library", lambda: _Lib())
    msm.multi_source_mine({}, fold=3)
    assert sorted(called) == ["llm", "simple"]


def test_facade_and_entry_pass_fold_through(monkeypatch):
    """⑥ 接线腿：fold 必须从 rdagent_facade 穿到 multi_source_mine，
    只在支线加个形参 = 恒真的那把尺子永远不红"""
    import rdagent_facade
    import sys
    assert msm.MULTI_SOURCE.get("enabled"), \
        "夹具前提：本线 MULTI_SOURCE.enabled 必须为 True，否则 facade 根本不走多源"
    seen = {}

    def _mine(pool, fold=None):
        seen["fold"] = fold
        return []

    stub = type("m", (), {"multi_source_mine": staticmethod(_mine)})
    monkeypatch.setitem(sys.modules, "multi_source_mining", stub)
    rdagent_facade.mine_factors_multi_source({}, fold=3)
    assert seen["fold"] == 3

    seen.clear()
    rdagent_facade.mine_factors_multi_source({})
    assert seen["fold"] is None


def test_switch_on_restores_official_in_fold(monkeypatch):
    """⑦ 开关拨回 True ⇒ 折内照旧吃 official：证明剔源来自那颗开关，不是写死的 if"""
    called = []
    for src in ("official", "llm"):
        monkeypatch.setattr(msm, f"_run_{src}",
                            lambda pool, s=src: (called.append(s), [_stub_source(s)])[1])

    class _Lib:
        def batch_upsert(self, factors, source=""):
            pass

        def save_markdown(self):
            pass

    monkeypatch.setattr(msm, "get_library", lambda: _Lib())
    monkeypatch.setattr(msm, "MULTI_SOURCE", _mk_cfg(["official", "llm"],
                                                    official_in_fold=True))
    msm.multi_source_mine({}, fold=1)
    assert called == ["official", "llm"]


def test_etf_production_config_has_the_switch_off():
    """⑧ 生产态：本线 config 真把折内 official 关了（读进程里的真字典，不抄注释）"""
    from config import MULTI_SOURCE as ETF_MS
    assert ETF_MS.get("official_in_fold") is False
    assert "official" in ETF_MS["sources"], "主线必须还留着 official，否则这条改动越界成了全线停源"
