# -*- coding: utf-8 -*-
"""孤儿模块接入口的回归测试。

这四个符号此前已实现却无任何调用方（写了但没接线 = 结论传不到执行端）：
1. run_live.load_optimized_config / apply_optimized_config —— 研究调参产物 → 实盘风控；
2. data_loader.PointInTimeData —— 回测与策略的唯一取数收口（防未来函数）；
3. live_attribution.decompose_live_vs_backtest —— 实盘 vs 回测逐笔分解 → 反馈闭环；
4. pbo.collect_config_equities × strategy_pbo.build_risk_config_family
   —— 真实参数配置族 → 策略级 PBO。
"""

import json
import numpy as np
import pandas as pd
import pytest

from backtest_daily import DailyBacktester
from data_loader import PointInTimeData
from live_attribution import decompose_live_vs_backtest
from pbo import build_returns_matrix, collect_config_equities
from strategy import IntradayRotationStrategy
from strategy_pbo import build_risk_config_family, strategy_level_pbo
from synth import make_daily_pool


def _factors(pool, n=2, seed=11):
    """最小可用因子结构：impl[code]["factor"] 为固定种子的随机序列。"""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        impl = {code: {"factor": pd.Series(rng.normal(0, 1, len(df)),
                                           index=df.index)}
                for code, df in pool.items()}
        out.append({"name": f"t_{i}", "mean_ic": 0.05 * (1 if i == 0 else -1),
                    "impl": impl, "source": "test"})
    return out


# ---------- 1. 研究调参 → 实盘风控 ----------

def test_apply_optimized_config_maps_and_converts(monkeypatch):
    import config_live
    from run_live import apply_optimized_config, load_optimized_config

    # 打桩成不会被别的用例读到的快照，避免污染全局 LIVE_RISK
    snapshot = dict(config_live.LIVE_RISK)
    monkeypatch.setattr("run_live.LIVE_RISK", snapshot)
    cfg = {"freq": "daily", "n_trials": 42,
           "risk_params": {"daily_stop_loss": -0.05,
                           "max_drawdown_stop": -0.15,
                           "single_position_max": 0.8,
                           "max_trades_per_day": 2,
                           "cooldown_days": 3},
           "factor_weights": {"t_0": 0.7, "t_1": 0.3}}
    w = apply_optimized_config(cfg)
    # 语义映射：研究侧字段名与实盘侧不同名，必须逐一对齐
    assert snapshot["daily_stop_loss"] == -0.05
    assert snapshot["max_drawdown_stop"] == -0.15
    assert snapshot["max_position_ratio"] == 0.8
    assert snapshot["max_orders_per_day"] == 2
    # 冷却换算：实盘熔断按挂钟时间判定，研究按交易日 → 整日计入（宁长勿短）
    assert snapshot["cooldown_minutes"] == 3 * 24 * 60
    assert w == cfg["factor_weights"]


def test_missing_artifact_leaves_live_risk_untouched(monkeypatch):
    from run_live import apply_optimized_config, load_optimized_config
    missing = "/nonexistent/optimized_params_daily.json"
    assert load_optimized_config(missing) == {}
    snapshot = {"daily_stop_loss": -0.03, "cooldown_minutes": 30}
    monkeypatch.setattr("run_live.LIVE_RISK", snapshot)
    assert apply_optimized_config(load_optimized_config(missing)) == {}
    assert snapshot == {"daily_stop_loss": -0.03, "cooldown_minutes": 30}


def test_research_artifact_roundtrips_through_disk(tmp_path, monkeypatch):
    """main.py 落盘 → run_live 读取，路径与键名必须逐字对齐。"""
    from run_live import apply_optimized_config, load_optimized_config
    path = tmp_path / "optimized_params_daily.json"
    payload = {"freq": "daily", "factor_weights": {"t_0": 1.0},
               "risk_params": {"daily_stop_loss": -0.02}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    snapshot = {"daily_stop_loss": -0.03}
    monkeypatch.setattr("run_live.LIVE_RISK", snapshot)
    cfg = load_optimized_config(str(path))
    assert cfg["freq"] == "daily"
    assert apply_optimized_config(cfg) == {"t_0": 1.0}
    assert snapshot["daily_stop_loss"] == -0.02


# ---------- 2. PointInTimeData 取数收口 ----------

def test_pit_never_sees_future_bars(daily_pool):
    code = next(iter(daily_pool))
    df = daily_pool[code]
    pit = PointInTimeData(daily_pool)
    mid = df.index[len(df) // 2]
    hist = pit.get_history(code, mid, lookback=30)
    assert hist.index.max() <= mid
    assert len(hist) == 30
    # 取价同样越不出 date
    assert pit.get_price(code, mid) == df.loc[mid, "close"]
    last = df.index[-1]
    assert pit.get_history(code, mid, lookback=10_000).index.max() <= mid
    # 该日无 bar（未上市/停牌）→ None，而不是向后借一根
    assert pit.get_price(code, last + pd.Timedelta(days=1)) is None


def test_backtester_and_strategy_price_via_pit(daily_pool):
    """回测取价与策略打分必须与手工按 .loc[:ts] 截断的结果一致。"""
    code = next(iter(daily_pool))
    df = daily_pool[code]
    ts = df.index[200]
    bt = DailyBacktester(daily_pool, None)
    assert bt._price(code, ts) == df.loc[:ts, "close"].iloc[-1]
    assert bt._price(code, df.index[-1] + pd.Timedelta(days=3)) is None

    strategy = IntradayRotationStrategy(_factors(daily_pool), daily_pool, None)
    manual = PointInTimeData(daily_pool)
    assert strategy.pit.get_history(code, ts, 60).equals(
        manual.get_history(code, ts, 60))


# ---------- 3. 实盘 vs 回测逐笔分解 ----------

def _write_orders(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def test_decompose_pairs_by_nearest_timestamp(tmp_path):
    """回测里同一 (code, side) 有多笔时，应配对到时间最近的那笔，
    价差符号统一为"正=执行劣于回测"。"""
    live = tmp_path / "live_orders.csv"
    btp = tmp_path / "trades.csv"
    _write_orders(live, [
        # 实盘 3/10 中午买入 10.5，最近的回测成交是 3/09 的 10.0（而非 1/05）
        {"time": "2024-03-10 12:00:00", "code": "510300", "side": "BUY",
         "price": 10.5, "amount": 9000, "status": "FILLED"},
        # 卖贱为正：回测 11.0，实盘 10.89 → +1%
        {"time": "2024-03-20 12:00:00", "code": "510300", "side": "SELL",
         "price": 10.89, "amount": 9000, "status": "DRY_RUN"},
        # 未成交订单不参与配对
        {"time": "2024-03-21 12:00:00", "code": "510500", "side": "BUY",
         "price": 1.0, "amount": 100, "status": "REJECTED"},
    ])
    _write_orders(btp, [
        {"date": "2024-01-05", "code": "510300", "action": "BUY",
         "price": 8.0},
        {"date": "2024-03-09", "code": "510300", "action": "BUY",
         "price": 10.0},
        {"date": "2024-03-19", "code": "510300", "action": "SELL",
         "price": 11.0},
    ])
    df = decompose_live_vs_backtest(str(live), str(btp))
    assert len(df) == 2
    buy = df[df["side"] == "BUY"].iloc[0]
    assert buy["bt_price"] == 10.0              # 最近邻，而非第一笔
    assert buy["slippage"] == pytest.approx(0.05)
    assert buy["lag_days"] == pytest.approx(1.5)   # 正 = 实盘晚于回测
    assert buy["bt_time"] == "2024-03-09"
    sell = df[df["side"] == "SELL"].iloc[0]
    assert sell["slippage"] == pytest.approx(0.01)
    assert sell["lag_days"] == pytest.approx(1.5)
    assert set(df["code"].astype(str)) == {"510300"}   # REJECTED 已剔除


def test_decompose_returns_empty_without_evidence(tmp_path):
    assert decompose_live_vs_backtest(
        str(tmp_path / "no.csv"), str(tmp_path / "no2.csv")).empty
    live = tmp_path / "live_orders.csv"
    btp = tmp_path / "trades.csv"
    _write_orders(live, [{"time": "2024-03-10 09:35:00", "code": "510300",
                          "side": "BUY", "price": 10.5, "amount": 9000,
                          "status": "FILLED"}])
    _write_orders(btp, [{"date": "2024-03-09", "code": "510500",
                         "action": "BUY", "price": 10.0}])
    assert decompose_live_vs_backtest(str(live), str(btp)).empty


def test_feedback_analyzers_accept_both_freq_trade_schemas(tmp_path):
    """回测成交表日线用 date、分钟线用 datetime（backtest_daily/minute 各写各的）。
    反馈侧写死任一列名，都会让另一种频率下 run_feedback 整链 KeyError。"""
    from turnover_analyzer import TurnoverAnalyzer
    live = tmp_path / "live_orders.csv"
    btp = tmp_path / "trades.csv"
    _write_orders(live, [
        {"time": "2024-03-10 09:35:00", "code": "510300", "side": "BUY",
         "price": 10.5, "amount": 9000, "status": "FILLED"},
        {"time": "2024-03-11 09:35:00", "code": "510300", "side": "SELL",
         "price": 10.6, "amount": 9000, "status": "FILLED"}])
    for col, ts in (("date", "2024-03-09"),
                    ("datetime", "2024-03-09 10:30:00")):
        _write_orders(btp, [{"code": "510300", "action": "BUY",
                             "price": 10.0, "amount": 9000, col: ts},
                            {"code": "510300", "action": "SELL",
                             "price": 11.0, "amount": 9000, col: ts}])
        st = TurnoverAnalyzer(str(live), str(btp)).analyze()
        assert st["bt_avg_daily_orders"] == 2.0     # 同一天 2 笔
        assert st["live_avg_daily_orders"] == 1.0   # 跨两天各 1 笔
        assert st["order_ratio"] == pytest.approx(0.5)
        df = decompose_live_vs_backtest(str(live), str(btp))
        assert len(df) == 2
        assert df["bt_time"].unique().tolist() == ["2024-03-09"]


# ---------- 4. 真实配置族 → 策略级 PBO ----------

def test_risk_config_family_is_ofat_and_bounded():
    base = {"a": 1, "b": 2, "c": 3}
    space = {"a": [1, 9], "b": [2, 8, 7], "c": [3, 6]}
    fam = build_risk_config_family(base, search_space=space)
    assert list(fam)[0] == "base"               # 样本内被选中的配置必须在族内
    assert fam["base"] == base
    # OFAT：除基线外每档只与基线相差一个维度
    for name, p in fam.items():
        if name == "base":
            continue
        diff = [k for k in base if p[k] != base[k]]
        assert len(diff) == 1 and name == f"{diff[0]}={p[diff[0]]}"
    # 基线取值不重复入族
    assert "a=1" not in fam and "b=2" not in fam
    # 截断时按维度轮换：每维至少贡献一档
    narrow = build_risk_config_family(base, search_space=space, max_configs=4)
    assert len(narrow) == 4
    dims = {"c" if n.startswith("c=") else "a" if n.startswith("a=")
            else "b" if n.startswith("b=") else "base" for n in narrow}
    assert dims == {"base", "a", "b", "c"}


def test_collect_config_equities_names_by_config_keys(daily_pool):
    """配置名要能溯源到具体参数档（dict 入参用键名，list 入参用 F{i}/R{i}）。"""
    pool = {c: df.iloc[:150] for c, df in daily_pool.items()}
    factors = _factors(pool)
    base = {"daily_stop_loss": -0.03, "max_drawdown_stop": -0.10,
            "cooldown_days": 1, "min_bars_between_trades": 1,
            "max_trades_per_day": 1}
    sets = {"base": base, "stop=-0.05": {**base, "daily_stop_loss": -0.05},
            "dd=-0.20": {**base, "max_drawdown_stop": -0.20}}
    idx = pool[next(iter(pool))].index
    eqs = collect_config_equities(pool, None, [factors], sets,
                                  IntradayRotationStrategy, DailyBacktester,
                                  all_ts=idx)
    assert len(eqs) == 3
    # 名字 = 因子集名_风控档名；dict 用键名，list 退回下标 F{i}/R{i}
    assert set(eqs) == {"F0_base", "F0_stop=-0.05", "F0_dd=-0.20"}
    by_list = collect_config_equities(pool, None, [factors], list(sets.values()),
                                      IntradayRotationStrategy, DailyBacktester,
                                      all_ts=idx)
    assert set(by_list) == {"F0_R0", "F0_R1", "F0_R2"}
    for eq in eqs.values():
        assert isinstance(eq, pd.Series) and len(eq) > 0
    # 换风控档必须换净值（信号与回测确实吃到了各自的参数）
    assert len({round(float(e.iloc[-1]), 6) for e in eqs.values()}) > 1


def test_strategy_level_pbo_prefers_real_config_family(daily_pool):
    pool = {c: df.iloc[:200] for c, df in daily_pool.items()}
    factors = _factors(pool)
    base = {"daily_stop_loss": -0.03, "max_drawdown_stop": -0.10,
            "cooldown_days": 1, "min_bars_between_trades": 1,
            "max_trades_per_day": 1}
    sets = build_risk_config_family(base, search_space={
        "daily_stop_loss": [-0.02, -0.03, -0.05],
        "max_drawdown_stop": [-0.08, -0.10, -0.15]})
    idx = pool[next(iter(pool))].index
    fam = collect_config_equities(pool, None, [factors], sets,
                                  IntradayRotationStrategy, DailyBacktester,
                                  all_ts=idx)
    assert len(fam) >= 3
    eq = list(fam.values())[0]
    real = strategy_level_pbo(eq, n_splits=4, max_combinations=50,
                              configs=build_returns_matrix(fam))
    assert real["config_family"] == "real"
    assert real["n_configs"] == len(fam)
    assert "error" not in real
    # 不传配置族时退化为伪变体族，接口保持向后兼容
    pseudo = strategy_level_pbo(eq, n_splits=4, max_combinations=50)
    assert pseudo["config_family"] == "pseudo"
    assert pseudo["n_configs"] == 6             # _make_pseudo_configs 默认档数
    # 两条路径都必须给出可判定的 PBO
    for res in (real, pseudo):
        assert 0.0 <= res["pbo"] <= 1.0
        assert isinstance(res["passed"], bool)


def test_short_equity_still_refuses_gracefully():
    eq = pd.Series([1.0, 1.001, 0.999],
                   index=pd.bdate_range("2024-01-01", periods=3))
    res = strategy_level_pbo(eq)
    assert res["pbo"] == 1.0 and res["passed"] is False
    assert res.get("error")


def test_main_stage_validation_uses_real_family(tmp_path, daily_pool,
                                                monkeypatch):
    """直调生产入口 `main.stage_validation`：此前一次真实全量跑暴露出
    `_pbo_config_family` 里 `build_risk_config_family` 未导入（NameError 被
    try 吞掉 → 静默退化成伪变体族），只有跑这条函数才能发现。"""
    import json
    import config
    from main import stage_validation

    for key in ("WALK_FORWARD", "PBO_TIMELINE", "MULTI_STRATEGY"):
        monkeypatch.setitem(getattr(config, key), "enabled", False)
    monkeypatch.setitem(config.STRATEGY_PBO, "enabled", True)
    monkeypatch.setitem(config.STRATEGY_PBO, "use_config_family", True)
    out = str(tmp_path / "pbo_result.json")
    monkeypatch.setattr("main.PBO_RESULT_FILE", out)

    pool = {c: df.iloc[:260] for c, df in daily_pool.items()}
    idx = pool[next(iter(pool))].index
    factors = _factors(pool)
    weights = {"t_0": 0.7, "t_1": 0.3}
    risk_params = dict(config.RISK_CONTROL)
    strategy = IntradayRotationStrategy(factors, pool, None,
                                       factor_weights=weights)
    eq = DailyBacktester(pool, None, risk_params).run(
        strategy.generate_signals(idx))["equity"]["equity"]

    pbo_now, pbo_trend = stage_validation(
        factors, pool, None, idx, eq, None,
        factors=factors, weights=weights, risk_params=risk_params)
    res = json.loads((tmp_path / "pbo_result.json").read_text("utf-8"))
    assert "error" not in res, res
    assert res["config_family"] == "real"
    # OFAT 档数 = 1 + Σ(|space|-1)，且受 max_family_configs 截断
    expected = min(1 + sum(len(v) - 1 for v in
                           config.RISK_SEARCH_SPACE.values()),
                   config.STRATEGY_PBO["max_family_configs"])
    assert res["n_configs"] == expected
    assert 0.0 <= pbo_now <= 1.0
    assert pbo_trend is None                       # 时间线已关闭
    # 关掉配置族开关后必须退回伪变体族（证明开关真的接上了）
    monkeypatch.setitem(config.STRATEGY_PBO, "use_config_family", False)
    stage_validation(factors, pool, None, idx, eq, None,
                     factors=factors, weights=weights,
                     risk_params=risk_params)
    res_off = json.loads((tmp_path / "pbo_result.json").read_text("utf-8"))
    assert res_off["config_family"] == "pseudo"
