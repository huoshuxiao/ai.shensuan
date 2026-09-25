# -*- coding: utf-8 -*-
"""LLM/容器判断权收回的四道闸（09-25 边界核查的落地件）。

这四条测的都是"外来的数不许直接当判据/参数用"，所以每条都要一个**负例**：
坏输入必须被挡住，好输入必须放行，两者缺一即测试失去意义。
- #29 `run_live.sanitize_live_risk`：调参产物里的风控数字（联合优化时来自 LLM）
- #30 `main.recount_foreign_ic`：容器自报的 mean_ic（不重算就进不了 IC 闸）
- #31 `main.stage_library_and_clustering`：库里的 source 用真实引擎标记
- #32 `run_feedback.guarded_prompt_write`：提示词自改写的校验 + 自动回滚
"""

import json

import numpy as np
import pandas as pd
import pytest

import main as main_mod
import run_live
import run_feedback
from factor_dsl import compute_ic
from factor_library import FactorLibrary


# ---------- #29 实盘风控区间闸 ----------

@pytest.fixture
def live_risk_snapshot():
    """apply_optimized_config 就地改 config_live.LIVE_RISK，用例跑完还原。"""
    before = dict(run_live.LIVE_RISK)
    yield run_live.LIVE_RISK
    run_live.LIVE_RISK.clear()
    run_live.LIVE_RISK.update(before)


@pytest.mark.parametrize("key,value", [
    ("daily_stop_loss", -0.05),      # 区间内
    ("daily_stop_loss", -0.005),     # 下界含等号
    ("max_position_ratio", 1.00),    # 上界含等号
    ("max_orders_per_day", 8),       # 整数键收整数
])
def test_sanitize_accepts_in_range(key, value):
    assert run_live.sanitize_live_risk(key, value) == pytest.approx(value)


@pytest.mark.parametrize("key,value,why", [
    ("daily_stop_loss", 0.05, "正数止损=永不触发，静默失效"),
    ("daily_stop_loss", -0.90, "超出单日跌幅区间"),
    ("max_drawdown_stop", -0.01, "熔断线过浅"),
    ("max_position_ratio", 1.5, "隐含加杠杆"),
    ("max_position_ratio", 0.0, "永不建仓"),
    ("cooldown_minutes", 0, "冷却为零等于没冷却"),
    ("max_orders_per_day", 3.5, "整数键不接受小数"),
    ("daily_stop_loss", float("nan"), "NaN 让所有比较短路"),
    ("daily_stop_loss", float("inf"), "inf 同 NaN"),
    ("daily_stop_loss", True, "bool 是 int 的子类，必须显式挡"),
    ("daily_stop_loss", "-0.05", "字符串不参与比较"),
    ("not_a_live_key", 0.5, "未登记的键不放行（防 RISK_KEY_MAP 外泄）"),
])
def test_sanitize_rejects(key, value, why):
    assert run_live.sanitize_live_risk(key, value) is None, why


def test_apply_optimized_config_lands_good_and_skips_bad(live_risk_snapshot):
    """好数字落地、坏数字沿用现值 —— 半好半坏的产物是最常见形态。"""
    run_live.LIVE_RISK["daily_stop_loss"] = -0.03
    run_live.LIVE_RISK["max_position_ratio"] = 0.2
    weights = run_live.apply_optimized_config({
        "freq": "daily", "n_trials": 100,
        "risk_params": {"daily_stop_loss": -0.05,        # 合格
                        "single_position_max": 5.0,       # 越界
                        "max_trades_per_day": "many"},    # 非数值
        "factor_weights": {"mom_5": 0.6, "rev_20": 0.4}})
    assert run_live.LIVE_RISK["daily_stop_loss"] == pytest.approx(-0.05)
    assert run_live.LIVE_RISK["max_position_ratio"] == pytest.approx(0.2)
    assert weights == {"mom_5": 0.6, "rev_20": 0.4}


def test_apply_optimized_config_cooldown_conversion(live_risk_snapshot):
    """研究侧按交易日计冷却，实盘按挂钟分钟：5 天 → 7200 分钟（≤10080）。"""
    run_live.apply_optimized_config(
        {"risk_params": {"cooldown_days": 5}, "factor_weights": {}})
    assert run_live.LIVE_RISK["cooldown_minutes"] == 7200
    run_live.apply_optimized_config(
        {"risk_params": {"cooldown_days": 30}, "factor_weights": {}})
    assert run_live.LIVE_RISK["cooldown_minutes"] == 7200


# ---------- #30 容器自报 IC 的重算闸 ----------

def _foreign_factor(pool, n_codes=3, fake_ic=0.9, with_series=True):
    """模拟 `rdagent_facade._official_to_impl` 的产物：逐标的 IC 是同一个自报值。"""
    codes = list(pool)[:n_codes]
    impl = {}
    for code in codes:
        df = pool[code]
        entry = {"ic": fake_ic}
        if with_series:
            entry["factor"] = df["close"].pct_change(5)
        impl[code] = entry
    return {"name": "official_x", "mean_ic": fake_ic, "icir": fake_ic,
            "impl": impl, "expr": ""}


def test_recount_replaces_self_reported_ic(daily_pool):
    """真因子序列 + 常数自报 IC → 逐标的重算，mean_ic/icir 全部换成本池读数。"""
    f = _foreign_factor(daily_pool)
    out = main_mod.recount_foreign_ic([f], daily_pool)
    assert len(out) == 1
    expected = float(np.mean([
        compute_ic(v["factor"], daily_pool[c]["close"].pct_change().shift(-1))
        for c, v in out[0]["impl"].items()]))
    assert out[0]["mean_ic"] == pytest.approx(expected)
    assert out[0]["mean_ic"] != 0.9
    std = float(np.std([v["ic"] for v in out[0]["impl"].values()]))
    assert out[0]["icir"] == pytest.approx(expected / (std + 1e-9))
    assert len(set(v["ic"] for v in out[0]["impl"].values())) == 3


def test_recount_drops_unverifiable_factor(daily_pool):
    """重算不出来的候选直接丢：验不了的数没有入场资格。"""
    f = _foreign_factor(daily_pool, with_series=False)
    assert main_mod.recount_foreign_ic([f], daily_pool) == []


def test_recount_keeps_genuine_per_code_ic(daily_pool):
    """逐标的 IC 有离散 = 正常路径的产物，一个字段都不许动。"""
    f = _foreign_factor(daily_pool)
    for i, v in enumerate(f["impl"].values()):
        v["ic"] = 0.01 + 0.02 * i
    snapshot = json.dumps({k: v["ic"] for k, v in f["impl"].items()},
                          sort_keys=True)
    out = main_mod.recount_foreign_ic([f], daily_pool)
    assert out[0] is f and out[0]["mean_ic"] == 0.9
    assert json.dumps({k: v["ic"] for k, v in out[0]["impl"].items()},
                      sort_keys=True) == snapshot


def test_recount_passes_through_single_code_impl(daily_pool):
    """只有一个标的时自报值与重算值同形，判别不了 —— 保持原样，不猜。"""
    f = _foreign_factor(daily_pool, n_codes=1)
    out = main_mod.recount_foreign_ic([f], daily_pool)
    assert out[0]["mean_ic"] == 0.9


def test_recount_survives_partial_pool_coverage(daily_pool):
    """2 个自报标的里只有 1 个在本池：按可算的那 1 个重算，std 退化为 1.0。"""
    f = _foreign_factor(daily_pool, n_codes=2)
    missing = list(f["impl"])[1]
    del f["impl"][missing]
    f["impl"]["999999"] = {"ic": 0.9, "factor": pd.Series(dtype=float)}
    out = main_mod.recount_foreign_ic([f], daily_pool)
    assert list(out[0]["impl"]) == list(daily_pool)[:1]
    assert out[0]["icir"] == pytest.approx(out[0]["mean_ic"] / (1.0 + 1e-9))


def test_multi_source_entrypoints_are_wrapped(monkeypatch, daily_pool):
    """接线检查：折内入口必须真的过 recount_foreign_ic，否则闸是空挂的。"""
    import rdagent_facade
    seen = []
    real = main_mod.recount_foreign_ic

    def spy(factors, pool):
        got = real(factors, pool)
        seen.append([f.get("mean_ic") for f in got])
        return got

    monkeypatch.setattr(rdagent_facade, "mine_factors_multi_source",
                        lambda pool: [_foreign_factor(pool)])
    monkeypatch.setattr(main_mod, "recount_foreign_ic", spy)
    main_mod.mine_with_engines(daily_pool, ["multi_source"], tag="接线")
    assert seen and seen[0] != [0.9]


# ---------- #31 因子库来源标记 ----------

@pytest.fixture
def tmp_lib(tmp_path):
    return FactorLibrary({"index_path": str(tmp_path / "index.json"),
                          "md_path": str(tmp_path / "lib.md"),
                          "enabled": False})


def test_library_source_uses_engine_tag(monkeypatch, tmp_lib):
    """新增条目按因子自带来源写，不再一刀切 pipeline。"""
    monkeypatch.setattr(main_mod, "FACTOR_LIBRARY", {"enabled": True})
    monkeypatch.setattr(main_mod, "FACTOR_CLUSTERING", {"enabled": False})
    monkeypatch.setattr("factor_library.get_library", lambda: tmp_lib)
    main_mod.stage_library_and_clustering(
        [{"name": "hybrid_0", "expr": "x", "mean_ic": 0.01,
          "icir": 0.5, "source": "hybrid"},
         {"name": "mogp_3", "expr": "y", "mean_ic": 0.02, "icir": 0.7}],
        {})
    assert tmp_lib.factors["hybrid_0"]["source"] == "hybrid"
    assert tmp_lib.factors["mogp_3"]["source"] == "pipeline"


def test_library_source_is_rewritten_on_existing_row(monkeypatch, tmp_lib):
    """存量那批 pipeline 标记要靠 extra 改回来 —— upsert 的已存在分支只吃
    extra，顶层 source 参数在第二次 upsert 时不生效。"""
    monkeypatch.setattr(main_mod, "FACTOR_LIBRARY", {"enabled": True})
    monkeypatch.setattr(main_mod, "FACTOR_CLUSTERING", {"enabled": False})
    monkeypatch.setattr("factor_library.get_library", lambda: tmp_lib)
    tmp_lib.upsert("official_a", "", 0.01, 0.4, "pipeline")
    assert tmp_lib.factors["official_a"]["source"] == "pipeline"
    main_mod.stage_library_and_clustering(
        [{"name": "official_a", "expr": "z", "mean_ic": 0.03,
          "icir": 0.9, "source": "official"}], {})
    assert tmp_lib.factors["official_a"]["source"] == "official"
    assert tmp_lib.factors["official_a"]["ic"] == pytest.approx(0.03)


# ---------- #32 提示词自改写的校验 + 回滚 ----------

GOOD_BODY = "你是 ETF 量化日报助手。" + "要求：只依据给出的指标行文，不得杜撰。" * 8


def _prompt_text(body=GOOD_BODY):
    return f'SYSTEM_PROMPT_ZH = """{body}"""\nUSER_PROMPT_ZH = """写日报"""\n'


@pytest.fixture
def prompt_cwd(tmp_path, monkeypatch):
    """两个写入口都用相对 cwd 的路径，用例必须在临时 cwd 里跑。"""
    (tmp_path / "report").mkdir()
    (tmp_path / "report/report_prompts.py").write_text(_prompt_text(),
                                                       encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path / "report/report_prompts.py"


def test_check_prompt_accepts_good_file(prompt_cwd):
    assert run_feedback.check_prompt_file() is None


@pytest.mark.parametrize("text,expect", [
    ('SYSTEM_PROMPT_ZH = """ok"""\ndef f(:\n', "语法"),
    ("OTHER = 1\n", "不在了"),
    (_prompt_text("太短"), "长度"),
    (_prompt_text("很" * 9000), "长度"),
])
def test_check_prompt_rejects(prompt_cwd, text, expect):
    prompt_cwd.write_text(text, encoding="utf-8")
    problem = run_feedback.check_prompt_file()
    assert problem is not None and expect in problem


def test_guarded_write_rolls_back_broken_prompt(prompt_cwd):
    """LLM 把三引号写进正文 → 语法断裂 → 回滚，且不改调用方的返回值。"""
    def write_junk():
        prompt_cwd.write_text('SYSTEM_PROMPT_ZH = """他写了 """ 三引号"""\n',
                              encoding="utf-8")
        return {"changes": "改了三处"}
    before = prompt_cwd.read_text(encoding="utf-8")
    res = run_feedback.guarded_prompt_write(write_junk, "提示词自动改写")
    assert prompt_cwd.read_text(encoding="utf-8") == before
    assert res == {"changes": "改了三处"}


def test_guarded_write_keeps_valid_prompt(prompt_cwd):
    def write_good():
        prompt_cwd.write_text(_prompt_text(GOOD_BODY + "新增一段口径说明。" * 4),
                              encoding="utf-8")
        return {"changes": "ok"}
    run_feedback.guarded_prompt_write(write_good, "A/B 自动应用")
    assert "新增一段口径说明" in prompt_cwd.read_text(encoding="utf-8")


def test_guarded_write_reports_no_change(prompt_cwd):
    """没落笔（无 LLM 端点时 optimize_prompt 直接 return）不算改写。"""
    before = prompt_cwd.read_text(encoding="utf-8")
    assert run_feedback.guarded_prompt_write(lambda: None,
                                             "提示词自动改写") is None
    assert prompt_cwd.read_text(encoding="utf-8") == before


def test_guarded_write_without_prompt_file(tmp_path, monkeypatch, capsys):
    """本线 cwd 下没有那个相对路径：只报未接线，不崩、不凭空造文件。"""
    monkeypatch.chdir(tmp_path)
    assert run_feedback.guarded_prompt_write(lambda: {"changes": None},
                                             "提示词自动改写") == \
        {"changes": None}
    assert not (tmp_path / "report").exists()
    assert "未接线" in capsys.readouterr().out
