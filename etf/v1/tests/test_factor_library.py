# -*- coding: utf-8 -*-
"""因子库落盘契约：JSON 索引的字段集是实盘/看板共同依赖的面。"""

import json

import pytest

from factor_library import FactorLibrary


@pytest.fixture
def lib(tmp_path):
    return FactorLibrary({"index_path": str(tmp_path / "index.json"),
                          "md_path": str(tmp_path / "lib.md"),
                          "enabled": False})


SCHEMA = {"name", "expr", "ic", "icir", "source", "status",
          "first_seen", "last_seen", "update_count", "ic_history"}


def test_upsert_writes_expected_schema(lib):
    lib.upsert("gp_0", "ts_mean(returns, 20)", 0.012, 0.8, "genetic")
    rec = lib.factors["gp_0"]
    assert SCHEMA <= set(rec)
    assert rec["status"] == "active" and rec["update_count"] == 1
    assert len(rec["ic_history"]) == 1
    assert rec["ic_history"][0]["ic"] == pytest.approx(0.012)
    assert rec["ic_history"][0]["icir"] == pytest.approx(0.8)


def test_repeated_upsert_appends_history_not_new_row(lib):
    lib.upsert("f", "close", 0.01, 0.5, "pipeline")
    lib.upsert("f", "close", 0.02, 0.9, "pipeline")
    assert len(lib.factors) == 1
    assert lib.factors["f"]["update_count"] == 2
    assert [h["ic"] for h in lib.factors["f"]["ic_history"]] == \
        pytest.approx([0.01, 0.02])


def test_batch_upsert_maps_pipeline_candidate_fields(lib):
    """挖掘引擎产出用 mean_ic，库字段用 ic —— 这层映射不能断。"""
    lib.batch_upsert([{"name": "a", "expr": "delta(close, 5)",
                       "mean_ic": 0.03, "icir": 1.1},
                      {"name": "b", "expr": "ma(close, 10)", "ic": 0.02}],
                     source="pipeline")
    assert lib.factors["a"]["ic"] == pytest.approx(0.03)
    assert lib.factors["b"]["ic"] == pytest.approx(0.02)
    assert lib.factors["b"]["icir"] == 0.0


def test_extra_metrics_can_be_persisted(lib):
    """GP 算出的 turnover/stability 默认不落盘（batch_upsert 不传 extra），
    upsert 显式传 extra 时才保留 —— 这条锁住"想留就能留"的口子。"""
    lib.batch_upsert([{"name": "a", "expr": "x", "mean_ic": 0.01}],
                     source="genetic")
    assert "turnover" not in lib.factors["a"]
    lib.upsert("b", "x", 0.01, 0.5, "genetic",
               extra={"turnover": 0.3, "stability": 0.8})
    assert lib.factors["b"]["turnover"] == 0.3


def test_mark_status_and_get_active(lib):
    lib.upsert("keep", "x", 0.01, 0.5, "pipeline")
    lib.upsert("rot", "y", -0.01, -0.2, "pipeline")
    lib.mark_status("rot", "retired", "IC 连续为负")
    assert lib.get_active() == ["keep"]
    assert lib.factors["rot"]["status_reason"] == "IC 连续为负"


def test_index_round_trip(tmp_path):
    path = str(tmp_path / "index.json")
    a = FactorLibrary({"index_path": path, "md_path": str(tmp_path / "m.md"),
                       "enabled": False})
    a.upsert("x", "close", 0.01, 0.5, "pipeline")
    a._save_index()
    blob = json.load(open(path, encoding="utf-8"))
    assert blob["x"]["expr"] == "close"
    b = FactorLibrary({"index_path": path, "md_path": str(tmp_path / "m.md"),
                       "enabled": False})
    assert list(b.factors) == ["x"]
