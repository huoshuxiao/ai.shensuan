# -*- coding: utf-8 -*-
"""研究主流程（支持日线 + 分钟线，全 pipeline）

各研究/验证/分析环节由 config.py 中的 enabled 开关控制；
单个环节异常不会中断主回测（打印 ⚠️ 后继续）。
"""

import json
import numpy as np
import pandas as pd
import _bootstrap  # noqa: F401  必须先于项目模块导入
from log_kit import setup_logging
from config import (
    FREQ, DSR, RISK_CONTROL, MULTI_STRATEGY, RISK_BUDGET,
    MULTI_SOURCE, FREQ_MAP, LOOKBACK_BARS,
    GENETIC, GENETIC_MULTI_OBJECTIVE, LLM_GENETIC_HYBRID,
    LLM_RESEARCH_PLANNER, FACTOR_LIBRARY, FACTOR_CLUSTERING,
    ORTHO_LLM, JOINT_LLM, WALK_FORWARD, STRATEGY_PBO,
    PBO_TIMELINE, PBO_RESULT_FILE, DECAY_PREDICT, DECAY_EXPLAIN,
    FACTOR_ATTRIBUTION, FACTOR_DECAY, AUTO_REMINING, TRIGGER_LOGIC,
    RESULTS_DIR,
)
from etf_universe import get_universe
from data_loader import DataLoader
from factors import FACTOR_REGISTRY
from factor_dsl import compute_ic
from factor_naming import cn_name
from factor_orthogonal import orthogonalize_factors
from risk_budget import compute_factor_weights
from strategy import IntradayRotationStrategy
from backtest import get_backtester, backtest_with_params  # noqa: F401
from dsr import deflated_sharpe_ratio, annualize_sharpe, TrialCounter
from frequency_adapter import get_adapter


def mine_factors(pool):
    """内置注册表因子（基线挖掘，同时供 walk-forward 折内复用）"""
    print("\n========== 因子挖掘（注册表） ==========")
    factors = []
    for name, fn in FACTOR_REGISTRY.items():
        impl, ics = {}, []
        for code, df in pool.items():
            try:
                f = fn(df)
                fr = df["close"].pct_change().shift(-1)
                ic = compute_ic(f, fr)
                if not np.isnan(ic):
                    impl[code] = {"factor": f, "ic": ic}
                    ics.append(ic)
            except Exception:
                continue
        if impl:
            mean_ic = float(np.mean(ics))
            std_ic = float(np.std(ics)) if len(ics) > 1 else 1.0
            factors.append({"name": name, "mean_ic": mean_ic,
                            "icir": mean_ic / (std_ic + 1e-9),
                            "impl": impl, "source": "simple"})
            print(f"  {name:20s} IC={mean_ic:+.4f}  [{cn_name(name)}]")
    factors = select_factors(factors)
    print(f"  入池因子: {len(factors)}")
    return factors


def select_factors(factors, min_ic=0.005):
    factors = [f for f in factors if abs(f.get("mean_ic", 0)) >= min_ic]
    factors.sort(key=lambda x: abs(x["mean_ic"]), reverse=True)
    return factors


def mine_with_engines(pool, engines, tag="", path_prefix=None):
    """按指定引擎集合挖因子，供 walk-forward 折内与自动重挖共用。

    与主链 stage_mining 同构：折内如果只跑注册表基线，样本外验证就
    完全覆盖不到实际入库的 GP/DSL/RD-Agent 因子，等于验了一批没人用的东西。
    engines 取值：registry / genetic / multi_source / mogp / hybrid。
    path_prefix：折内产物（如 GP 演化史）的落盘路径前缀，避免互相覆写。"""
    engines = engines or ["registry"]
    got = []

    def fold_path(name):
        prefix = f"{path_prefix}_" if path_prefix else ""
        return f"{RESULTS_DIR}/{prefix}{name}"

    if "registry" in engines:
        got.extend(mine_factors(pool))

    if "genetic" in engines:
        def _gp():
            from factor_genetic import genetic_mine
            return genetic_mine(pool, history_path=fold_path("genetic_history.csv"))
        got.extend(safe_stage(f"遗传编程 {tag}".strip(), _gp) or [])

    if "multi_source" in engines:
        def _ms():
            from rdagent_facade import mine_factors_multi_source
            return mine_factors_multi_source(pool)
        got.extend(safe_stage(f"多源挖掘 {tag}".strip(), _ms) or [])

    if "mogp" in engines:
        def _mogp():
            from genetic_multi_objective import multi_objective_mine
            return multi_objective_mine(pool, got)
        got.extend(safe_stage(f"多目标 GP {tag}".strip(), _mogp) or [])

    if "hybrid" in engines:
        def _hybrid():
            from llm_genetic_hybrid import llm_genetic_mine
            return llm_genetic_mine(pool, got)
        got.extend(safe_stage(f"LLM-GP 混合 {tag}".strip(), _hybrid) or [])

    if not got:
        return []
    if len(got) > 1:
        def _dedup():
            from multi_source_mining import dedup_factors
            return dedup_factors(got, pool)
        got = safe_stage(f"因子去重 {tag}".strip(), _dedup) or got
    picked = select_factors(got)
    print(f"  [{tag or 'fold'}] 引擎={engines} → 候选 {len(got)}，"
          f"过 IC 门槛 {len(picked)}")
    return picked


def quick_evaluate(factors, pool, universe, all_ts,
                   risk_params=None, n_trials=None):
    """因子集 → 信号 → 回测 → {dsr, pbo, sharpe_annual, stats}"""
    from strategy_pbo import strategy_level_pbo
    risk = risk_params or RISK_CONTROL
    weights = compute_factor_weights(factors, pool)
    strategy = IntradayRotationStrategy(factors, pool, universe,
                                        factor_weights=weights)
    signals = strategy.generate_signals(all_ts)
    bt = get_backtester(pool, universe, risk)
    result = bt.run(signals)
    eq = result["equity"]["equity"]
    rets = eq.pct_change().dropna().values
    dsr_res = deflated_sharpe_ratio(
        rets, n_trials=n_trials or DSR.get("n_trials") or len(factors))
    adapter = get_adapter()
    pbo_res = strategy_level_pbo(eq)
    return {"dsr": float(dsr_res.get("dsr", 0)),
            "pbo": float(pbo_res.get("pbo", 1)),
            "sharpe_annual": adapter.annualize_sharpe(
                dsr_res.get("sr_observed", 0)),
            "stats": result["stats"]}


def safe_stage(name, fn):
    print(f"\n---------- {name} ----------")
    try:
        return fn()
    except Exception as e:
        import traceback
        print(f"  ⚠️ {name} 失败（跳过）: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


def stage_mining(pool, raw_factors, tc):
    """多源挖掘 / GP / 多目标 GP / LLM-GP 混合，追加进 raw_factors"""
    if MULTI_SOURCE["enabled"]:
        def _ms():
            from rdagent_facade import mine_factors_multi_source
            got = mine_factors_multi_source(pool) or []
            if got:
                tc.add(len(got))
                raw_factors.extend(got)
                print(f"  多源挖掘追加: {len(got)}")
        safe_stage("多源因子挖掘", _ms)

    if GENETIC["enabled"]:
        def _gp():
            from factor_genetic import genetic_mine
            seeds = []
            if LLM_RESEARCH_PLANNER["enabled"]:
                from llm_research_planner import generate_research_plan
                plan = generate_research_plan(
                    LLM_RESEARCH_PLANNER["days"]) or {}
                for day in plan.get("plan", []):
                    for expr in day.get("candidate_exprs", [])[:
                            LLM_RESEARCH_PLANNER["max_candidates"]]:
                        seeds.append({"name": f"plan_{expr[:12]}",
                                      "expr": expr})
            got = genetic_mine(pool, seed_factors=seeds or None) or []
            tc.add(len(got))
            raw_factors.extend(got)
            print(f"  遗传编程产出: {len(got)}")
        safe_stage("遗传编程挖掘", _gp)

    if GENETIC_MULTI_OBJECTIVE["enabled"]:
        def _mogp():
            from genetic_multi_objective import multi_objective_mine
            got = multi_objective_mine(pool, raw_factors) or []
            tc.add(len(got))
            raw_factors.extend(got)
            print(f"  多目标 GP 产出: {len(got)}")
        safe_stage("多目标遗传编程", _mogp)

    if LLM_GENETIC_HYBRID["enabled"]:
        def _hybrid():
            from llm_genetic_hybrid import llm_genetic_mine
            got = llm_genetic_mine(pool, raw_factors) or []
            tc.add(len(got))
            raw_factors.extend(got)
            print(f"  LLM-GP 混合产出: {len(got)}")
        safe_stage("LLM-GP 混合挖掘", _hybrid)

    if len(raw_factors) > 1:
        def _dedup():
            from multi_source_mining import dedup_factors
            return dedup_factors(raw_factors, pool)
        raw_factors[:] = safe_stage("因子去重", _dedup) or raw_factors
    return select_factors(raw_factors)


def stage_library_and_clustering(raw_factors, pool):
    if FACTOR_LIBRARY["enabled"]:
        def _lib():
            from factor_library import get_library
            lib = get_library()
            lib.batch_upsert(raw_factors, source="pipeline")
            lib.save_markdown()
            print(f"  因子库: {len(lib.factors)} 条")
        safe_stage("因子库入库", _lib)

    if FACTOR_CLUSTERING["enabled"]:
        def _clu():
            from factor_clustering import (cluster_factors, dedup_by_cluster,
                                           save_clustering)
            clu = cluster_factors(raw_factors, pool)
            if not clu:
                return None
            save_clustering(clu)
            reps = dedup_by_cluster(raw_factors, clu)
            if reps:
                raw_factors[:] = reps
                print(f"  聚类去重后: {len(reps)}")
            return clu
        safe_stage("因子聚类", _clu)
    return raw_factors


def stage_orthogonalize(raw_factors, pool, universe, all_ts):
    """返回 (factors, risk_params)。联合优化 > LLM 正交调参 > 默认"""
    if JOINT_LLM["enabled"]:
        def _joint():
            from joint_optimizer import joint_optimize
            best = joint_optimize(
                raw_factors,
                lambda fs, risk: quick_evaluate(fs, pool, universe,
                                                all_ts, risk))
            if best and best.get("factors"):
                print(f"  联合优化: ortho={best['ortho']} "
                      f"DSR={best['metrics'].get('dsr', 0):.4f}")
                return best["factors"], best.get("risk") or RISK_CONTROL
            return None
        out = safe_stage("LLM 联合优化", _joint)
        if out:
            return out

    if ORTHO_LLM["enabled"] and len(raw_factors) > 1:
        def _ortho():
            from orthogonal_optimizer import optimize_orthogonalization
            best = optimize_orthogonalization(
                raw_factors,
                lambda fs: quick_evaluate(fs, pool, universe, all_ts))
            if best and best.get("factors"):
                return best["factors"]
            return None
        factors = safe_stage("LLM 正交化调参", _ortho)
        if factors:
            return factors, RISK_CONTROL

    return (orthogonalize_factors(raw_factors) or raw_factors), RISK_CONTROL


def stage_validation(raw_factors, pool, universe, all_ts, eq, tc,
                     factors=None, weights=None, risk_params=None):
    def _wf():
        from walk_forward import walk_forward_run

        def factor_fn(p, idx, fold=None):
            return mine_with_engines(
                p, WALK_FORWARD.get("fold_engines"),
                tag=f"折 {fold}", path_prefix=f"walk_forward_fold{fold}")

        def backtest_fn(p, signals, uni, risk):
            bt = get_backtester(p, uni, risk or RISK_CONTROL)
            return bt.run(signals)

        # 试验次数由 walk_forward 逐折累加进 tc，折内不再重复计数
        wf = walk_forward_run(pool, universe, factor_fn, backtest_fn,
                              trial_counter=tc)
        pd.DataFrame(wf["folds"]).to_csv(
            f"{RESULTS_DIR}/walk_forward_{FREQ}.csv",
            index=False, encoding="utf-8-sig")
    if WALK_FORWARD["enabled"]:
        safe_stage("Walk-forward 验证", _wf)

    pbo_now = None

    def _pbo():
        nonlocal pbo_now
        from strategy_pbo import strategy_level_pbo
        configs = _pbo_config_family()
        res = strategy_level_pbo(eq, configs=configs)
        pbo_now = float(res.get("pbo", 1))
        with open(PBO_RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2,
                      default=str)
        print(f"  PBO={pbo_now:.4f}  "
              f"配置族={res.get('config_family')}×{res.get('n_configs')}  "
              f"通过={res.get('passed')}")

    def _pbo_config_family():
        """构造真实配置族的收益矩阵，供 CSCV 判断"挑参数"这件事本身
        有多大概率是过拟合。任一环节不满足（无终态因子/净值太短/回测全
        失败）时返回 None，让 strategy_level_pbo 退化为伪变体族。

        配置族 = 终态因子集（含研究侧 ICIR 权重）× OFAT 风控参数邻域，
        每档跑一次全样本回测；除风控档外，信号口径与第 6 步完全一致，
        这样"base 档"就是即将投产的那条净值，PBO 衡量的是在它周围
        挑过参数之后结论翻转的概率（López de Prado, CSCV 2017）。"""
        if not (STRATEGY_PBO.get("use_config_family") and factors
                and risk_params and len(eq) >=
                STRATEGY_PBO["min_equity_bars"]):
            return None
        try:
            from pbo import collect_config_equities, build_returns_matrix
            from strategy_pbo import build_risk_config_family
            sets = build_risk_config_family(
                risk_params,
                max_configs=STRATEGY_PBO.get("max_family_configs"))
            eqs = collect_config_equities(
                pool, universe, {"final": factors}, sets,
                IntradayRotationStrategy,
                type(get_backtester(pool, universe, risk_params)),
                all_ts=all_ts, strategy_kwargs={"factor_weights": weights})
            if len(eqs) < 3:
                return None
            return build_returns_matrix(eqs)
        except Exception as e:
            print(f"  ⚠️ 真实配置族构造失败，改用伪变体族: {e}")
            return None
    if STRATEGY_PBO["enabled"]:
        safe_stage("策略级 PBO", _pbo)

    pbo_trend = None

    def _timeline():
        nonlocal pbo_trend
        from pbo_timeline import compute_pbo_timeline, detect_trend
        pdf = compute_pbo_timeline({"main": eq})
        if pdf is not None and not pdf.empty:
            pdf.to_csv(f"{RESULTS_DIR}/pbo_timeline.csv",
                       index=False, encoding="utf-8-sig")
            pbo_trend = detect_trend(pdf)
            print(f"  PBO 趋势: {pbo_trend}")
    if PBO_TIMELINE["enabled"]:
        safe_stage("PBO 演化时间线", _timeline)

    def _multi():
        from multi_strategy import (run_multi_strategy,
                                    save_multi_strategy_results)
        ms = run_multi_strategy(raw_factors, pool, universe, all_ts)
        save_multi_strategy_results(ms)
        print(f"  多策略: {len(ms['strategies'])} 个，"
              f"组合夏普={ms['summary'].get('combined_sharpe')}")
    if MULTI_STRATEGY["enabled"]:
        safe_stage("多策略并行回测", _multi)
    return pbo_now, pbo_trend


def stage_analysis(factors, weights, pool, universe, all_ts):
    def _decay():
        from factor_decay_predict import predict_factor_decay
        df = predict_factor_decay(factors, pool)
        if df is not None and not df.empty:
            cols = [c for c in ["factor", "current_ic", "predicted_ic",
                                "alert"] if c in df.columns]
            if "factor" in df.columns:
                df["中文名"] = df["factor"].map(cn_name)
                cols.insert(1, "中文名")
            print(df[cols].to_string(index=False))
    if DECAY_PREDICT["enabled"]:
        safe_stage("因子衰减预测", _decay)

    def _explain():
        from decay_explain import explain_all
        explain_all(factors, pool)
    if DECAY_EXPLAIN["enabled"]:
        safe_stage("衰减可解释性", _explain)

    def _shap_tl():
        from shap_timeline import (SHAPTimelineTracker,
                                   analyze_all_shap_trends)
        tracker = SHAPTimelineTracker()
        tracker.track(factors, pool)
        analyze_all_shap_trends(factors)
    if DECAY_EXPLAIN["enabled"]:
        safe_stage("SHAP 时间线追踪", _shap_tl)

    def _attr():
        from factor_attribution import run_attribution, save_attribution
        res = run_attribution(factors, pool, universe, all_ts,
                              RISK_CONTROL, factor_weights=weights)
        if res:
            save_attribution(res)
    if FACTOR_ATTRIBUTION["enabled"]:
        safe_stage("因子归因", _attr)


def stage_lifecycle(factors, pool, universe, all_ts, risk_params,
                    dsr_now, pbo_now, pbo_trend):
    alerts = []

    def _monitor():
        from strategy_lifecycle import FactorDecayMonitor
        nonlocal alerts
        alerts = FactorDecayMonitor().evaluate(factors, pool) or []
        if alerts:
            print(f"  衰减告警: {[a['factor'] for a in alerts]}")
    if FACTOR_DECAY["enabled"]:
        safe_stage("因子衰减监控", _monitor)

    if not AUTO_REMINING["enabled"]:
        return

    def _remining():
        from trigger_logic import DualIndicatorTrigger
        from auto_remining import AutoReminingLoop
        from remining_state import ReminingState
        # 单一状态对象：指标计数与重挖节流落在同一份持久化文件里
        state = ReminingState()
        check = {}
        if TRIGGER_LOGIC["enabled"]:
            check = DualIndicatorTrigger(state=state).check(
                dsr_now=dsr_now, pbo_now=pbo_now,
                current_bar=len(all_ts)) or {}
            print(f"  DSR/PBO 联动: 本轮恶化={check.get('bad_this_round')} "
                  f"连续 {check.get('consecutive')}/"
                  f"{TRIGGER_LOGIC['consecutive_rounds']} 轮 → "
                  f"触发={check.get('triggered')}（{check.get('reason')}）")
        loop = AutoReminingLoop(
            mine_fn=lambda p: mine_with_engines(
                p, AUTO_REMINING.get("remining_engines"), tag="重挖",
                path_prefix="remining"),
            evaluate_fn=lambda fs: quick_evaluate(fs, pool, universe,
                                                 all_ts, risk_params),
            state=state)
        out = loop.run_once(current_bar=len(all_ts),
                            current_factors=factors, pool=pool,
                            decay_alerts=alerts, pbo_trend=pbo_trend,
                            indicator=check)
        if not out.get("triggered"):
            print(f"  未触发自动重挖：{out.get('reason')}")
            return
        log = loop.get_log_df()
        if log is not None and not log.empty:
            log.to_csv(f"{RESULTS_DIR}/auto_remining_log.csv",
                       index=False, encoding="utf-8-sig")
        print(f"  重挖采纳: {out.get('adopted')}")
    safe_stage("自动重挖闭环", _remining)


def main():
    setup_logging(f"research_{FREQ}")
    adapter = get_adapter()
    print("=" * 60)
    print(f"  ETF 量化系统 | 频率={FREQ} "
          f"({'日线' if not adapter.is_intraday else '分钟线'})")
    print(f"  回看窗口: {LOOKBACK_BARS} bar "
          f"(≈{LOOKBACK_BARS / adapter.bars_per_day:.1f} 天)")
    print(f"  年化系数: {adapter.bars_per_year}")
    print("=" * 60)

    # 1. ETF 池
    print("\n[1/9] 构建 ETF 池...")
    universe = get_universe()
    codes = universe.universe["code"].tolist()

    # 2. 数据
    print(f"\n[2/9] 加载 {FREQ} 数据...")
    loader = DataLoader(freq=FREQ)
    pool = loader.load_pool(codes)
    if not pool:
        print("❌ 无数据")
        return

    tc = TrialCounter()
    tc.reset()
    # 时间轴锚定历史最长的标的：池按成交额排序，首位常是近年新 ETF，
    # 若以它作参考会把全部阶段截到短历史，长历史代表（如 510050 2005 起）闲置
    ref_code = max(pool, key=lambda c: len(pool[c]))
    all_ts = pool[ref_code].index
    print(f"  时间轴参考 {ref_code}: {all_ts[0]:%Y-%m-%d} ~ "
          f"{all_ts[-1]:%Y-%m-%d} ({len(all_ts)} bars)")

    # 3. 因子挖掘（注册表基线 + 多源/GP/多目标/混合）
    print("\n[3/9] 因子挖掘...")
    raw_factors = mine_factors(pool)
    tc.add(len(raw_factors))
    raw_factors = stage_mining(pool, raw_factors, tc)
    if not raw_factors:
        print("❌ 无因子")
        return

    # 4. 因子库 + 聚类（正交化前保留 expr/source）
    print("\n[4/9] 因子库与聚类...")
    raw_factors = stage_library_and_clustering(raw_factors, pool)

    # 5. 正交化 / 联合优化
    print("\n[5/9] 正交化...")
    factors, risk_params = stage_orthogonalize(raw_factors, pool,
                                                universe, all_ts)
    if not factors:
        print("❌ 正交化后无因子")
        return

    # 6. 信号
    print("\n[6/9] 生成信号...")
    weights = compute_factor_weights(factors, pool)
    strategy = IntradayRotationStrategy(
        factors, pool, universe, factor_weights=weights)
    signals = strategy.generate_signals(all_ts)
    print(f"  bar 数: {len(signals)}")

    # 7. 回测 + DSR
    print("\n[7/9] 回测...")
    bt = get_backtester(pool, universe, risk_params)
    result = bt.run(signals)
    eq = result["equity"]["equity"]
    rets = eq.pct_change().dropna().values
    n_trials = DSR.get("n_trials") or tc.get()
    dsr_res = deflated_sharpe_ratio(rets, n_trials=n_trials)
    dsr_res["sharpe_annual"] = adapter.annualize_sharpe(
        dsr_res.get("sr_observed", 0))

    # 8. 验证（walk-forward / PBO / 多策略）
    print("\n[8/9] 稳健性验证...")
    pbo_now, pbo_trend = stage_validation(raw_factors, pool, universe,
                                          all_ts, eq, tc,
                                          factors=factors, weights=weights,
                                          risk_params=risk_params)

    # 9. 分析与闭环
    print("\n[9/9] 分析与反馈闭环...")
    stage_analysis(factors, weights, pool, universe, all_ts)
    stage_lifecycle(factors, pool, universe, all_ts, risk_params,
                    float(dsr_res.get("dsr", 0)), pbo_now, pbo_trend)

    def _plot():
        from plot import plot_equity
        plot_equity(result["equity"],
                    save_path=f"{RESULTS_DIR}/equity_{FREQ}.png")
    safe_stage("净值曲线图", _plot)

    # 保存
    print("\n保存结果...")
    result["equity"].to_csv(f"{RESULTS_DIR}/equity_{FREQ}.csv",
                             encoding="utf-8-sig")
    result["trades"].to_csv(f"{RESULTS_DIR}/trades_{FREQ}.csv",
                             index=False, encoding="utf-8-sig")
    signals.to_csv(f"{RESULTS_DIR}/signals_{FREQ}.csv",
                   encoding="utf-8-sig")
    pd.DataFrame([dsr_res]).to_csv(
        f"{RESULTS_DIR}/dsr_{FREQ}.csv", index=False,
        encoding="utf-8-sig")
    # 固定名副本（app 看板 / 反馈分析器读取）
    result["equity"].to_csv(f"{RESULTS_DIR}/equity.csv",
                             encoding="utf-8-sig")
    result["trades"].to_csv(f"{RESULTS_DIR}/trades.csv",
                             index=False, encoding="utf-8-sig")
    signals.to_csv(f"{RESULTS_DIR}/signals.csv",
                   encoding="utf-8-sig")
    pd.DataFrame([dsr_res]).to_csv(f"{RESULTS_DIR}/dsr.csv",
                                    index=False,
                                    encoding="utf-8-sig")

    with open(f"{RESULTS_DIR}/optimized_params_{FREQ}.json",
              "w", encoding="utf-8") as f:
        json.dump({"freq": FREQ, "n_factors": len(factors),
                   "factor_weights": weights,
                   "risk_params": risk_params,
                   "n_trials": n_trials,
                   "bars_per_year": adapter.bars_per_year},
                  f, ensure_ascii=False, indent=2, default=str)

    print("\n" + "=" * 60)
    print("  绩效")
    print("=" * 60)
    for k, v in result["stats"].items():
        print(f"  {k:10s}: {v}")
    print(f"\n  DSR: {dsr_res.get('dsr', 0):.4f}")
    print("\n🎉 完成！")


if __name__ == "__main__":
    main()
