# -*- coding: utf-8 -*-
"""#33 容器/LLM 自述读数的解析契约。

这三块读数都从磁盘产物反解，测试用临时目录里的假产物，不碰端点也不碰生产目录。
"""

import json
import os
import time

import pandas as pd
import pytest

from llm_selfreport import (DIM_KEYS, decision_tally, harvest_vs_library,
                            save_self_eval_dims, self_report_mtime)


# ---------- ① 容器 Final Decision ----------

def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


@pytest.fixture
def log_dir(tmp_path):
    d = tmp_path / "rdagent_output"
    d.mkdir()
    # 老场次：2 SUCCESS / 3 FAIL，夹一行同前缀的噪声（`FAIL` 出现在别处也计数，
    # 因为判据是整句 `This implementation is FAIL`，噪声句不含该前缀）
    _write(d / "loop_old.log",
           "------------------Final Decision------------------\n"
           "This implementation is SUCCESS.\n"
           "------------------Final Decision------------------\n"
           "This implementation is SUCCESS.\n"
           "This implementation is FAIL.\n"
           "This implementation is FAIL.\n"
           "some log says FAIL here\n"
           "This implementation is FAIL.\n")
    _write(d / "loop_new.log", "nothing decided here\n")
    _write(d / "notes.txt", "This implementation is FAIL.\n")
    # 让 new 明确比 old 新（排序只按 mtime，不靠文件名）
    old = os.path.getmtime(d / "loop_old.log")
    os.utime(d / "loop_new.log", (old + 50, old + 50))
    os.utime(d / "loop_old.log", (old + 10, old + 10))
    return str(d)


def test_decision_tally_counts_and_orders(log_dir):
    rows = decision_tally(log_dir)
    assert [r["log"] for r in rows] == ["loop_new.log", "loop_old.log"]
    assert rows[1]["decision_success"] == 2
    assert rows[1]["decision_fail"] == 3
    assert rows[1]["n_decisions"] == 5
    assert rows[0] == {"log": "loop_new.log",
                       "ended": rows[0]["ended"],
                       "decision_success": 0, "decision_fail": 0,
                       "n_decisions": 0}


def test_decision_tally_empty_cases(log_dir, tmp_path):
    assert decision_tally(str(tmp_path / "nope")) == []
    assert decision_tally("") == []


# ---------- ② 自报 IC ↔ 库内 IC ----------

@pytest.fixture
def harvest_and_lib(tmp_path):
    h = tmp_path / "factors.json"
    _write(h, json.dumps([
        {"name": "5-day SMA of Price", "expr": "ts_mean(close,5)",
         "mean_ic": 0.00847},
        {"name": "10-day SMA of Volume", "expr": "ts_mean(volume,10)",
         "mean_ic": 0.00847},
        {"name": "只在自报里出现的", "expr": "x", "mean_ic": 0.001}]))
    lib = tmp_path / "index.json"
    # 库内是**本池重算后**的读数：与自报同号但幅度相反是真实情形（09-25 实测）
    _write(lib, json.dumps({
        "5-day SMA of Price": {"ic": -0.028649, "source": "official",
                               "last_seen": "2026-09-25 10:26:39"},
        "10-day SMA of Volume": {"ic": 0.00847, "source": "official",
                                 "last_seen": "2026-09-25 06:40:50"}}))
    return str(h), str(lib)


def test_harvest_vs_library_join(harvest_and_lib):
    h, lib = harvest_and_lib
    df = harvest_vs_library(h, lib)
    assert len(df) == 3
    by = df.set_index("因子")
    assert by.loc["5-day SMA of Price", "差值"] == pytest.approx(
        -0.028649 - 0.00847, abs=1e-9)
    # 自报与库内完全相等的行差值为 0（不是缺行）
    assert by.loc["10-day SMA of Volume", "差值"] == pytest.approx(0.0)
    # 不在库：不插值、不补零
    assert pd.isna(by.loc["只在自报里出现的", "库内 IC（本池重算）"])
    assert by.loc["只在自报里出现的", "库内来源"] == "不在库"


def test_harvest_vs_library_accepts_wrapped_json(tmp_path):
    h = _write(tmp_path / "f.json", json.dumps(
        {"factors": [{"name": "a", "mean_ic": 0.01}]}))
    df = harvest_vs_library(h, None)
    assert list(df["因子"]) == ["a"] and df["库内来源"][0] == "不在库"


def test_harvest_vs_library_missing_files(tmp_path, harvest_and_lib):
    h, lib = harvest_and_lib
    assert harvest_vs_library(str(tmp_path / "nope.json"), lib).empty
    # 有自报、无库索引：整列留空而不是报错
    df = harvest_vs_library(h, str(tmp_path / "no_index.json"))
    assert len(df) == 3 and df["库内 IC（本池重算）"].isna().all()


def test_self_report_mtime(tmp_path, harvest_and_lib):
    h, _ = harvest_and_lib
    assert self_report_mtime(h).startswith(time.strftime("%Y-%m-%d"))
    assert self_report_mtime(str(tmp_path / "nope")) == "—"
    assert self_report_mtime(None) == "—"


# ---------- ③ 自评四维与"端点在不在" ----------

def _eval_result():
    return {"summary": {"n_reports": 2, "avg_score": 58.0},
            "results": [
                {"date": "2026-09-20", "final_score": 58.0, "grade": "D",
                 "llm_score": {"scores": {"accuracy": 22, "completeness": 18,
                                          "actionability": 20, "logic": 21},
                               "total": 81},
                 "objective": {"objective_score": 4.3}},
                # 缺 scores 的历史形状：四维留 None，不能让缺字段变成 0 分
                {"date": "2026-09-19", "final_score": 40.0, "grade": "D",
                 "llm_score": {}, "objective": {}}]}


def test_save_self_eval_dims_llm_mode(tmp_path):
    path = f"{tmp_path}/report/self_eval_dims.json"
    payload = save_self_eval_dims(_eval_result(), True, path)
    assert payload["mode"] == "llm" and payload["endpoint_on"] is True
    assert payload["summary"]["avg_score"] == 58.0
    assert payload["n_batch"] == 2 and payload["n_days_total"] == 2
    # 合并写之后 detail 按日期升序，与批次入参顺序无关，所以按日期取行
    by_date = {d["date"]: d for d in payload["detail"]}
    assert [by_date["2026-09-20"][k] for k in DIM_KEYS] == [22, 18, 20, 21]
    assert by_date["2026-09-20"]["llm_total"] == 81
    assert by_date["2026-09-20"]["objective"] == 4.3
    assert by_date["2026-09-20"]["mode"] == "llm"
    # 缺 scores 的历史形状：四维留 None，不能让缺字段变成 0 分
    assert by_date["2026-09-19"][DIM_KEYS[0]] is None
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["mode"] == "llm"      # 真的落盘了


def test_save_self_eval_dims_template_mode(tmp_path):
    """端点不在时共享层给模板分，mode 必须落成 template，否则一片 A 无从分辨。"""
    payload = save_self_eval_dims(_eval_result(), False,
                                  f"{tmp_path}/d.json")
    assert payload["mode"] == "template" and payload["endpoint_on"] is False
    assert all(d["mode"] == "template" for d in payload["detail"])


def test_save_self_eval_dims_mode_is_per_day(tmp_path):
    """端点断过一天必须看得见：顶层 mode 只代表本批，历史行保留自己那天的评法。"""
    path = f"{tmp_path}/d.json"
    save_self_eval_dims(_eval_result(), False, path)
    payload = save_self_eval_dims(
        {"results": [{"date": "2026-09-21", "final_score": 61.0,
                      "grade": "C", "llm_score": {"total": 60},
                      "objective": {}}],
         "summary": {}}, True, path)
    assert payload["mode"] == "llm"
    by_date = {d["date"]: d for d in payload["detail"]}
    assert by_date["2026-09-20"]["mode"] == "template"
    assert by_date["2026-09-21"]["mode"] == "llm"


def test_save_self_eval_dims_merges_across_batches(tmp_path):
    """日更只评 1 份时必须往上接，不能像共享层那样把 20 行历史冲成 1 行。"""
    path = f"{tmp_path}/d.json"
    save_self_eval_dims(_eval_result(), True, path)
    day21 = {"results": [{"date": "2026-09-21", "final_score": 71.5,
                          "grade": "B",
                          "llm_score": {"scores": {"accuracy": 30},
                                        "total": 70}, "objective": {}}],
             "summary": {"n_reports": 1, "avg_score": 71.5}}
    payload = save_self_eval_dims(day21, True, path)
    assert payload["n_batch"] == 1
    assert payload["n_days_total"] == 3
    assert [d["date"] for d in payload["detail"]] == [
        "2026-09-19", "2026-09-20", "2026-09-21"]
    # 未进本批的旧行原样保留（四维不能被清零）
    assert {d["date"]: d[DIM_KEYS[0]]
            for d in payload["detail"]}["2026-09-20"] == 22


def test_save_self_eval_dims_same_day_overwrites(tmp_path):
    """同日重跑（补数据/重评）以新为准，不留双行。"""
    path = f"{tmp_path}/d.json"
    save_self_eval_dims(_eval_result(), True, path)
    again = {"results": [{"date": "2026-09-20", "final_score": 90.0,
                          "grade": "A",
                          "llm_score": {"scores": {"accuracy": 1},
                                        "total": 90},
                          "objective": {}}], "summary": {}}
    payload = save_self_eval_dims(again, True, path)
    assert payload["n_days_total"] == 2
    fresh = [d for d in payload["detail"] if d["date"] == "2026-09-20"]
    assert len(fresh) == 1 and fresh[0]["final_score"] == 90.0
    assert fresh[0][DIM_KEYS[0]] == 1


def test_save_self_eval_dims_caps_at_keep_days(tmp_path):
    """只留最近 keep_days 天：日更一年也不会把文件堆大。"""
    path = f"{tmp_path}/d.json"
    batch = {"results": [{"date": f"2026-08-{d:02d}", "final_score": 50.0,
                          "grade": "D", "llm_score": {}, "objective": {}}
                         for d in range(1, 13)], "summary": {}}
    save_self_eval_dims(batch, True, path)
    payload = save_self_eval_dims(
        {"results": [{"date": "2026-09-01", "final_score": 50.0,
                      "grade": "D", "llm_score": {}, "objective": {}}],
         "summary": {}}, True, path, keep_days=5)
    assert [d["date"] for d in payload["detail"]] == [
        "2026-08-09", "2026-08-10", "2026-08-11", "2026-08-12", "2026-09-01"]


def test_save_self_eval_dims_rewrites_corrupt_file(tmp_path):
    """旧文件坏掉（截断/手改）时整份重写，不能挡住今天的落盘。"""
    path = tmp_path / "d.json"
    path.write_text('{"detail": [trunc', encoding="utf-8")
    payload = save_self_eval_dims(_eval_result(), True, str(path))
    assert payload["n_days_total"] == 2
    with open(str(path), encoding="utf-8") as f:
        assert len(json.load(f)["detail"]) == 2


def test_save_self_eval_dims_handles_empty_result(tmp_path):
    payload = save_self_eval_dims({}, True, f"{tmp_path}/e.json")
    assert payload["detail"] == [] and payload["summary"] == {}
    assert payload["n_batch"] == 0 and payload["n_days_total"] == 0
    assert os.path.exists(f"{tmp_path}/e.json")


def test_empty_batch_keeps_existing_history(tmp_path):
    """本批为空（当日无日报快照）不能把已有历史清空 —— 这是合并写的意义。"""
    path = f"{tmp_path}/e.json"
    save_self_eval_dims(_eval_result(), True, path)
    payload = save_self_eval_dims({}, True, path)
    assert payload["n_batch"] == 0 and payload["n_days_total"] == 2
