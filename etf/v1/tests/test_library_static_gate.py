# -*- coding: utf-8 -*-
"""写库单点上的静态闸（10-01 用户裁「甲：只接闸，不动库」）。

要钉住的判决：**未来函数写法不许进因子库**，而这一层原本一道静态检查都没有
（`check_expr` 全仓只有默认关闭的多角色定稿闸一个调用点），于是
`volatility_breakout_momentum` = `delay(max(high, 5), -1) / ...` 带着被抬高
约一半的 IC 进了库（同池三臂实测 +0.0551 → +0.0348 → +0.0290）。

夹具两头都要有：放行 1 格（合法滞后）、判红 6 格（负窗口 / shift(-1) / 未知名字 /
非法语法节点 / 存量行也不许被更新 / 开关与计数），外加两格**装牙**——
把尺子换成恒返回 '' 必须让坏写法写进去（证明差异真由那一行咬住）、
把开关关掉必须让坏写法写进去（证明默认值是开而不是恒真）。
"""

import pytest

import factor_library as FL
from factor_library import FactorLibrary

BAD_EXPR = "delay(max(high, 5), -1) / ma(df, 90)"
HONEST_EXPR = "delay(max(high, 5), 1) / ma(df, 90)"


@pytest.fixture
def lib(tmp_path):
    """只写临时目录：conftest 已把 LIBRARY_DIR 指到 tmp，git 自动提交也被关掉了"""
    return FactorLibrary({"index_path": str(tmp_path / "index.json"),
                          "md_path": str(tmp_path / "lib.md"),
                          "enabled": False})


# ---------- 放行 ----------

def test_honest_lag_is_admitted(lib):
    assert lib.upsert("honest", HONEST_EXPR, 0.03, 1.0, "llm") is True
    assert lib.factors["honest"]["expr"] == HONEST_EXPR
    assert lib.rejected == {}


# ---------- 判红：四类写法 ----------

@pytest.mark.parametrize("expr,frag", [
    (BAD_EXPR, "负窗口"),                      # 库里那条原样
    ("close.pct_change(fill_method=None).shift(-1)", "shift"),
    ("foo(close, 5)", "未知名字"),
    ("[c for c in close]", "非法语法节点"),
])
def test_lookahead_and_junk_are_blocked(lib, expr, frag):
    assert lib.upsert("cand", expr, 0.05, 1.5, "llm") is False
    assert "cand" not in lib.factors          # 一行都没写
    assert frag in lib.rejected["cand"]


def test_existing_row_is_not_updated_at_all(lib):
    """已存在的条目被挡住时**一个字段都不动**：ic / icir / last_seen /
    update_count / ic_history 全保持，expr 也不覆盖"""
    lib.upsert("keep", HONEST_EXPR, 0.02, 0.7, "llm")
    snapshot = {k: v for k, v in lib.factors["keep"].items() if k != "ic_history"}
    hist_len = len(lib.factors["keep"]["ic_history"])
    assert lib.upsert("keep", BAD_EXPR, 0.99, 9.9, "llm") is False
    after = {k: v for k, v in lib.factors["keep"].items() if k != "ic_history"}
    assert after == snapshot
    assert len(lib.factors["keep"]["ic_history"]) == hist_len


def test_empty_expr_is_exempt(lib):
    """尺子对空表达式没有判断面 ⇒ 放行（在库 45 行里真有 1 行是空的，
    拒它只会把那行冻在旧 IC 上，与"不许偷看未来"无关）"""
    assert lib.upsert("blank", "", 0.006, 0.3, "simple") is True
    assert "blank" in lib.factors and lib.rejected == {}


# ---------- 开关与计数 ----------

def test_gate_default_is_on_from_config(lib):
    """默认值必须是"开"：库里那条真写法在生产默认档下就进不来"""
    assert lib.p["static_gate"] is True
    assert lib.upsert("bad", BAD_EXPR, 0.04, 1.0, "llm") is False


def test_gate_switch_off_admits_the_bad_row(tmp_path):
    """负对照：`static_gate=False` 必须退回改动前的行为（坏写法照写）"""
    off = FactorLibrary({"index_path": str(tmp_path / "i.json"),
                         "md_path": str(tmp_path / "m.md"),
                         "enabled": False, "static_gate": False})
    assert off.upsert("bad", BAD_EXPR, 0.04, 1.0, "llm") is True
    assert off.factors["bad"]["expr"] == BAD_EXPR
    assert off.rejected == {}


def test_batch_upsert_count_excludes_blocked(lib, capsys):
    """打印的那行 +N 只能数真写进去的，否则「因子库更新: +3」是假账"""
    lib.batch_upsert([{"name": "a", "expr": HONEST_EXPR, "mean_ic": 0.02},
                      {"name": "b", "expr": BAD_EXPR, "mean_ic": 0.05},
                      {"name": "c", "expr": "ma(volume, 10)", "mean_ic": 0.01}],
                     source="llm")
    out = capsys.readouterr().out
    assert "因子库更新: +2" in out and "挡掉 1" in out
    assert set(lib.factors) == {"a", "c"}


# ---------- 装牙：差异必须真由那一行/那一个默认值产生 ----------

def test_pull_the_ruler_out_and_the_bad_row_sneaks_in(lib, monkeypatch):
    monkeypatch.setattr(FL, "check_expr", lambda e: "")
    assert lib.upsert("bad", BAD_EXPR, 0.04, 1.0, "llm") is True
    assert "bad" in lib.factors


def test_production_stage_function_blocks_it(monkeypatch, lib, capsys):
    """走真入口 `main.stage_library_and_clustering`（日更链第③段的那一步），
    而不是只测 upsert 本身"""
    import main as main_mod
    monkeypatch.setattr(main_mod, "FACTOR_LIBRARY", {"enabled": True})
    monkeypatch.setattr(main_mod, "FACTOR_CLUSTERING", {"enabled": False})
    monkeypatch.setattr(FL, "get_library", lambda: lib)
    main_mod.stage_library_and_clustering(
        [{"name": "honest", "expr": HONEST_EXPR, "mean_ic": 0.03,
          "icir": 1.0, "source": "llm"},
         {"name": "sneaky", "expr": BAD_EXPR, "mean_ic": 0.06,
          "icir": 1.4, "source": "llm"}], {})
    assert set(lib.factors) == {"honest"}
    out = capsys.readouterr().out
    assert "sneaky 未入库" in out and "挡下 1 条" in out
