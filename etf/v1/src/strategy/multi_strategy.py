# -*- coding: utf-8 -*-
"""多策略并行"""

import json
import numpy as np
import pandas as pd
from itertools import product
from config import (
    MULTI_STRATEGY, STRATEGY_GRID, RISK_CONTROL,
    INIT_CAPITAL, RISK_BUDGET, STRATEGY_LIFECYCLE, STRATEGY_PBO,
)
from factor_orthogonal import orthogonalize_factors
from risk_budget import compute_factor_weights
from strategy import IntradayRotationStrategy
from backtest import MinuteBacktester
from strategy_lifecycle import (
    StrategyLifecycleManager, FactorDecayMonitor,
    combine_with_lifecycle,
)
from strategy_pbo import filter_strategies_by_pbo


def generate_strategy_configs(n=None):
    n = n or MULTI_STRATEGY["n_strategies"]
    configs = []
    ortho_list = STRATEGY_GRID["ortho"]
    risk_list = STRATEGY_GRID["risk"]
    weight_methods = ["equal", "ic_ir", "risk_parity"]
    combos = list(product(range(len(ortho_list)),
                          range(len(risk_list)),
                          range(len(weight_methods))))
    if len(combos) > n:
        idx = np.linspace(0, len(combos) - 1, n).astype(int)
        combos = [combos[i] for i in idx]
    for i, (oi, ri, wi) in enumerate(combos):
        ortho = ortho_list[oi]
        risk = {**RISK_CONTROL, **risk_list[ri]}
        configs.append({"name": f"S{i+1}_{ortho['method']}_"
                                f"sl{abs(risk['daily_stop_loss']):.2f}_"
                                f"{weight_methods[wi]}",
                        "ortho": ortho, "risk": risk,
                        "weight_method": weight_methods[wi]})
    return configs


def run_single_strategy(config, raw_factors, pool, universe, all_ts):
    factors = orthogonalize_factors(
        raw_factors, threshold=config["ortho"]["corr_threshold"],
        method=config["ortho"]["method"])
    if not factors:
        return None
    weights = None
    if RISK_BUDGET["enabled"]:
        weights = compute_factor_weights(
            factors, pool, method=config["weight_method"])
    strategy = IntradayRotationStrategy(factors, pool, universe,
                                        factor_weights=weights)
    signals = strategy.generate_signals(all_ts)
    bt = MinuteBacktester(pool, universe, config["risk"])
    result = bt.run(signals)
    result["config"] = config
    result["factors"] = factors
    result["weights"] = weights
    return result


def _align_equities(equity_dict):
    df = pd.DataFrame({n: e["equity"] for n, e in equity_dict.items()})
    return df.sort_index().ffill().fillna(INIT_CAPITAL)


def combine_equities(equity_df, mode="equal"):
    rets = equity_df.pct_change().fillna(0.0)
    if mode == "equal" or equity_df.shape[1] == 1:
        w = np.ones(equity_df.shape[1]) / equity_df.shape[1]
    elif mode == "risk_parity":
        inv = 1.0 / (rets.std() + 1e-9)
        w = (inv / inv.sum()).values
    elif mode == "ic_weighted":
        sh = (rets.mean() / (rets.std() + 1e-9)).clip(lower=0)
        w = (sh / sh.sum()).values if sh.sum() > 1e-9 \
            else np.ones(equity_df.shape[1]) / equity_df.shape[1]
    else:
        w = np.ones(equity_df.shape[1]) / equity_df.shape[1]
    port_rets = (rets * w).sum(axis=1)
    return INIT_CAPITAL * (1 + port_rets).cumprod()


def run_multi_strategy(raw_factors, pool, universe, all_ts):
    print("\n========== 多策略并行 ==========")
    configs = generate_strategy_configs()
    strategies = {}
    for i, cfg in enumerate(configs):
        print(f"\n  [{i+1}/{len(configs)}] {cfg['name']}")
        try:
            r = run_single_strategy(cfg, raw_factors, pool,
                                     universe, all_ts)
            if r is None:
                continue
            strategies[cfg["name"]] = r
            print(f"    收益={r['stats']['总收益率']}  "
                  f"夏普={r['stats']['夏普比率']}")
        except Exception as e:
            print(f"    ❌ {e}")
            continue
    if not strategies:
        return {}

    pbo_filter = {}
    if STRATEGY_PBO["enabled"]:
        pbo_filter = filter_strategies_by_pbo(strategies)
        strategies = pbo_filter["filtered"]
        if not strategies:
            return {}

    equity_dict = {n: r["equity"] for n, r in strategies.items()}
    equity_df = _align_equities(equity_dict)

    decay_monitor = FactorDecayMonitor()
    decay_alerts = []
    if strategies:
        first = next(iter(strategies.values()))
        decay_alerts = decay_monitor.evaluate(first["factors"], pool)
    decay_state = decay_monitor.get_state()

    lifecycle = StrategyLifecycleManager(list(strategies.keys()))
    lifecycle_equity = combine_with_lifecycle(
        equity_df, lifecycle,
        eval_every=STRATEGY_LIFECYCLE["eval_every_bars"])

    combined = {
        "equal": combine_equities(equity_df, "equal"),
        "risk_parity": combine_equities(equity_df, "risk_parity"),
        "ic_weighted": combine_equities(equity_df, "ic_weighted"),
        "lifecycle": lifecycle_equity,
    }

    summary_rows = []
    for name, r in strategies.items():
        s = r["stats"]
        row = {"策略": name, "总收益": s["总收益率"],
               "年化": s["年化收益率"], "夏普": s["夏普比率"],
               "回撤": s["最大回撤"], "交易次数": s["交易次数"],
               "胜率": s["胜率"]}
        if pbo_filter.get("pbo_results") and name in pbo_filter["pbo_results"]:
            row["PBO"] = round(pbo_filter["pbo_results"][name].get("pbo", 1), 4)
        summary_rows.append(row)
    for mode, eq in combined.items():
        rets = eq.pct_change().dropna()
        total = eq.iloc[-1] / eq.iloc[0] - 1
        days = max((eq.index[-1] - eq.index[0]).days, 1)
        ann = (1 + total) ** (365 / days) - 1
        vol = rets.std() * np.sqrt(240 * 252)
        sharpe = ann / (vol + 1e-9)
        dd = (eq - eq.cummax()) / eq.cummax()
        summary_rows.append({"策略": f"[组合] {mode}",
                             "总收益": f"{total*100:.2f}%",
                             "年化": f"{ann*100:.2f}%",
                             "夏普": round(sharpe, 3),
                             "回撤": f"{dd.min()*100:.2f}%",
                             "交易次数": "-", "胜率": "-"})
    summary = pd.DataFrame(summary_rows)
    corr = equity_df.pct_change().dropna().corr()

    return {"strategies": {n: {"equity": r["equity"],
                                "trades": r["trades"],
                                "stats": r["stats"],
                                "config": r["config"]}
                            for n, r in strategies.items()},
            "equity_df": equity_df, "combined": combined,
            "summary": summary, "corr_matrix": corr,
            "pbo_results": pbo_filter.get("pbo_results", {}),
            "lifecycle_history": lifecycle.history,
            "lifecycle_state": lifecycle.get_state(),
            "decay_state": decay_state}


def save_multi_strategy_results(result, out_dir="."):
    if not result:
        return
    result["summary"].to_csv(f"{out_dir}/multi_summary.csv",
                              index=False, encoding="utf-8-sig")
    result["equity_df"].to_csv(f"{out_dir}/multi_equity.csv",
                                encoding="utf-8-sig")
    pd.DataFrame(result["combined"]).to_csv(
        f"{out_dir}/multi_combined.csv", encoding="utf-8-sig")
    result["corr_matrix"].to_csv(f"{out_dir}/multi_corr.csv",
                                  encoding="utf-8-sig")
    if result.get("pbo_results"):
        rows = [{"策略": n, "PBO": r.get("pbo", 1.0),
                 "通过": r.get("passed", False)}
                for n, r in result["pbo_results"].items()]
        pd.DataFrame(rows).to_csv(f"{out_dir}/multi_strategy_pbo.csv",
                                  index=False, encoding="utf-8-sig")
    if result.get("lifecycle_state") is not None and \
            not result["lifecycle_state"].empty:
        result["lifecycle_state"].to_csv(
            f"{out_dir}/multi_lifecycle_state.csv",
            index=False, encoding="utf-8-sig")
    if result.get("decay_state") is not None and \
            not result["decay_state"].empty:
        result["decay_state"].to_csv(f"{out_dir}/factor_decay.csv",
                                      index=False, encoding="utf-8-sig")
    for name, r in result["strategies"].items():
        safe = name.replace("/", "_")
        r["trades"].to_csv(f"{out_dir}/multi_trades_{safe}.csv",
                           index=False, encoding="utf-8-sig")
    with open(f"{out_dir}/multi_configs.json", "w",
              encoding="utf-8") as f:
        json.dump({n: r["config"] for n, r in result["strategies"].items()},
                  f, ensure_ascii=False, indent=2, default=str)