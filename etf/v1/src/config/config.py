# -*- coding: utf-8 -*-
"""全局配置（支持日线 + 分钟线）"""

import os

# ========== 频率配置 ==========
# 可用环境变量 ETF_FREQ 覆盖（run_daily_backtest.py / run_minute_backtest.py 依赖此机制）
FREQ = os.environ.get("ETF_FREQ", "daily")  # daily | 1min | 5min | 15min | 30min | 60min

# 频率 → akshare 参数映射
FREQ_MAP = {
    "daily":  {"akshare_period": "daily", "is_intraday": False,
               "bars_per_day": 1,    "bars_per_year": 252},
    "1min":   {"akshare_period": "1",  "is_intraday": True,
               "bars_per_day": 240,  "bars_per_year": 240 * 252},
    "5min":   {"akshare_period": "5",  "is_intraday": True,
               "bars_per_day": 48,   "bars_per_year": 48 * 252},
    "15min":  {"akshare_period": "15", "is_intraday": True,
               "bars_per_day": 16,   "bars_per_year": 16 * 252},
    "30min":  {"akshare_period": "30", "is_intraday": True,
               "bars_per_day": 8,    "bars_per_year": 8 * 252},
    "60min":  {"akshare_period": "60", "is_intraday": True,
               "bars_per_day": 4,    "bars_per_year": 4 * 252},
}

# 自动算 LOOKBACK
def _get_lookback_bars(freq, days=20):
    return days * FREQ_MAP[freq]["bars_per_day"]

LOOKBACK_DAYS = 20
LOOKBACK_BARS = _get_lookback_bars(FREQ, LOOKBACK_DAYS)

# ========== 回测区间 ==========
BACKTEST_START = "2022-01-01" if FREQ == "daily" else "2024-01-01"
BACKTEST_END   = "2024-12-31"

# ========== 资金 ==========
INIT_CAPITAL = 10_000
POSITION_RATIO = 0.95
MIN_TRADE_AMOUNT = 100

# ========== 费率 ==========
COMMISSION_RATE = 0.0001
MIN_COMMISSION = 0.1
SLIPPAGE = 0.0005

# ========== ETF 池 ==========
ETF_FILTER = {
    "min_list_days": 252,
    "min_avg_amount": 5_000_000,
    "max_count": 20,
    "exclude_keywords": ["货币", "短融", "同业存单", "现金"],
}

# ========== 因子挖掘 ==========
MAX_LOOPS = 3
HYPOTHESIS_PER_LOOP = 3
IC_THRESHOLD = 0.015 if FREQ != "daily" else 0.02
MAX_FACTORS = 8

# ========== 正交化 ==========
ORTHOGONAL = {
    "enabled": True,
    "corr_threshold": 0.7,
    "method": "gram_schmidt",
    "min_factors_keep": 2,
}

# ========== LLM 调正交化（orthogonal_optimizer） ==========
ORTHO_LLM = {
    "enabled": True, "max_rounds": 4,
    "threshold_range": [0.3, 0.9],
    "methods": ["gram_schmidt", "pca", "none"],
}

# ========== DSR ==========
DSR = {"enabled": True, "n_trials": None, "benchmark_sharpe": 0.0}

# ========== PBO ==========
PBO = {"enabled": True, "n_splits": 10,
       "max_combinations": 5000, "n_configs": 20}

# ========== Walk-forward ==========
WALK_FORWARD = {"enabled": True, "n_splits": 3,
                "train_ratio": 0.7,
                "embargo_bars": LOOKBACK_BARS // 4}

# ========== 多源挖掘 ==========
MULTI_SOURCE = {
    "enabled": True,
    "sources": ["official", "llm", "simple", "genetic"],
    "timeout_seconds": 300,
    "merge_mode": "union_dedup",
    "corr_dedup_threshold": 0.85,
    "min_ic_per_source": 0.005,
    "weights": {"official": 1.2, "llm": 1.0,
                "simple": 0.8, "genetic": 0.9},
}

# ========== 遗传编程 ==========
GENETIC = {
    "enabled": True,
    "population_size": 30, "n_generations": 8,
    "elite_ratio": 0.2, "mutation_rate": 0.3,
    "crossover_rate": 0.5, "tournament_size": 3,
    "max_expr_depth": 4, "min_ic_to_survive": 0.005,
    "random_state": 42, "use_cluster_seeds": True,
    "keep_top_n": 8,
}

GENETIC_OPERATORS = {
    "binary": ["+", "-", "*", "/"],
    "unary": ["abs", "log", "sign", "neg"],
    "terminal": ["close", "open", "high", "low", "volume", "returns"],
    "window": [5, 10, 20, 60] if FREQ == "daily"
              else [5, 20, 60, 240],
    "rolling": ["ts_mean", "ts_std", "delta", "delay", "ts_sum"],
}

# ========== 多目标 GP ==========
GENETIC_MULTI_OBJECTIVE = {
    "enabled": True,
    "objectives": ["ic", "low_turnover", "low_corr"],
    "population_size": 40, "n_generations": 8,
    "elite_ratio": 0.2, "mutation_rate": 0.3,
    "crossover_rate": 0.5, "tournament_size": 3,
    "max_expr_depth": 4, "pareto_front_size": 15,
    "weights": {"ic": 0.5, "low_turnover": 0.25, "low_corr": 0.25},
    "random_state": 42,
}

EXTENDED_OBJECTIVES = {
    "enabled": True,
    "objectives": {
        "ic": {"direction": "max", "weight": 0.30},
        "low_turnover": {"direction": "min", "weight": 0.15},
        "low_corr": {"direction": "min", "weight": 0.15},
        "stability": {"direction": "max", "weight": 0.25},
        "simplicity": {"direction": "max", "weight": 0.15},
    },
    "stability_method": "ic_ttest",
    "simplicity_method": "depth_based",
    "max_expr_depth_for_simplicity": 5,
    "max_node_count": 30,
    "pareto_objectives": ["ic", "low_turnover", "stability",
                          "simplicity"],
    "pareto_front_size": 20,
}

# ========== 动态权重 ==========
DYNAMIC_WEIGHTS = {
    "enabled": True, "schedule": "three_phase",
    "explore_ratio": 0.3, "converge_ratio": 0.4,
    "refine_ratio": 0.3,
    "phases": {
        "explore": {"ic": 0.35, "low_turnover": 0.10,
                    "low_corr": 0.15, "stability": 0.15,
                    "simplicity": 0.25},
        "converge": {"ic": 0.35, "low_turnover": 0.15,
                     "low_corr": 0.15, "stability": 0.25,
                     "simplicity": 0.10},
        "refine": {"ic": 0.25, "low_turnover": 0.20,
                   "low_corr": 0.15, "stability": 0.30,
                   "simplicity": 0.10},
    },
    "smooth_transition": True, "transition_bars": 2,
}

# ========== 自适应变异 ==========
ADAPTIVE_MUTATION = {
    "enabled": True, "diversity_metric": "expr_distance",
    "target_diversity": 0.6,
    "min_mutation_rate": 0.1, "max_mutation_rate": 0.8,
    "adaptation_speed": 0.3,
    "crossover_adaptive": True,
    "min_crossover_rate": 0.3, "max_crossover_rate": 0.8,
    "history_window": 5,
}

# ========== LLM 变异/交叉 ==========
LLM_MUTATION = {
    "enabled": True, "llm_prob": 0.3,
    "max_llm_calls_per_gen": 5,
    "include_parent": True, "include_weakness": True,
    "temperature": 0.9, "cache_size": 200,
}
LLM_CROSSOVER = {
    "enabled": True, "llm_prob": 0.3,
    "max_llm_calls_per_gen": 4, "temperature": 0.85,
    "include_parents": True, "include_metrics": True,
    "include_logic": True, "cache_size": 200,
    "fallback_to_random": True,
}
LLM_GENETIC_HYBRID = {
    "enabled": True, "llm_seed_ratio": 0.4,
    "llm_n_seeds": 8, "n_generations": 6,
    "population_size": 30,
    "feedback_to_llm": True, "feedback_rounds": 2,
}

# ========== 聚类 ==========
FACTOR_CLUSTERING = {
    "enabled": True, "method": "hierarchical",
    "n_clusters": 8, "corr_threshold": 0.6,
    "distance_metric": "correlation",
    "dbscan_eps": 0.4, "dbscan_min_samples": 2,
    "representative_mode": "highest_ic",
    "min_cluster_size": 1,
    "visualize_method": "tsne", "tsne_perplexity": 5,
    "random_state": 42,
}

# ========== 归因 ==========
FACTOR_ATTRIBUTION = {
    "enabled": True, "method": "shapley",
    "n_samples": 100 if FREQ != "daily" else 200,
    "min_contribution": 0.001,
    "eval_window_bars": LOOKBACK_BARS * 100,
    "top_n": 10,
}

# ========== 衰减预测 ==========
DECAY_PREDICT = {
    "enabled": True, "model": "arima",
    "forecast_horizon": LOOKBACK_BARS,
    "min_history_bars": 200 if FREQ == "daily" else 1000,
    "ic_window_bars": 240 if FREQ == "daily" else 2400,
    "ic_step_bars": 20 if FREQ == "daily" else 120,
    "warn_threshold": -0.3, "danger_threshold": -0.5,
    "ensemble_weights": {"arima": 0.4, "lightgbm": 0.6},
    "lightgbm_params": {"n_estimators": 100, "max_depth": 4,
                        "learning_rate": 0.05},
}
DECAY_EXPLAIN = {
    "enabled": True, "method": "shap",
    "n_shap_samples": 100,
    "n_lags": 10,
    "extra_features": ["ic_mean_5", "ic_std_5",
                       "ic_slope_5", "volatility"],
    "top_n_lags": 5, "save_shap_values": True,
    "plot_type": "bar",
}
LLM_SHAP_EXPLAINER = {
    "enabled": True, "max_factors": 5,
    "include_full_importance": True,
    "max_tokens_in_prompt": 3000,
    "save_report": True,
    "report_path": "llm_shap_report.md",
    "language": "zh", "include_action_suggestion": True,
}

# ========== 因子衰减监控（strategy_lifecycle.FactorDecayMonitor） ==========
FACTOR_DECAY = {
    "enabled": True,
    "ic_window_bars": 252 if FREQ == "daily" else 2400,
    "ic_min_threshold": 0.01,
    "halflife_warn": 500 if FREQ == "daily" else 4800,
    "consecutive_decay_rounds": 2,
}

# ========== 风险预算 ==========
RISK_BUDGET = {
    "enabled": True, "weight_method": "ic_ir",
    "max_weight_per_factor": 0.4,
    "min_weight_per_factor": 0.02,
    "decay_halflife_bars": 240 if FREQ == "daily" else 2400,
    "ic_lookback_bars": 480 if FREQ == "daily" else 4800,
}

# ========== 多策略 ==========
MULTI_STRATEGY = {
    "enabled": True, "n_strategies": 5,
    "capital_per_strategy": 10_000,
    "diversify_by": "ortho_risk", "combine_mode": "equal",
}
STRATEGY_GRID = {
    "ortho": [
        {"corr_threshold": 0.5, "method": "gram_schmidt"},
        {"corr_threshold": 0.7, "method": "gram_schmidt"},
        {"corr_threshold": 0.9, "method": "gram_schmidt"},
        {"corr_threshold": 0.7, "method": "pca"},
        {"corr_threshold": 0.7, "method": "none"},
    ],
    "risk": [
        {"daily_stop_loss": -0.03, "max_drawdown_stop": -0.10},
        {"daily_stop_loss": -0.05, "max_drawdown_stop": -0.15},
        {"daily_stop_loss": -0.02, "max_drawdown_stop": -0.08},
        {"daily_stop_loss": -0.04, "max_drawdown_stop": -0.12},
        {"daily_stop_loss": -0.03, "max_drawdown_stop": -0.20},
    ],
}
STRATEGY_PBO = {
    "enabled": True, "n_splits": 8,
    "max_combinations": 2000,
    "pbo_threshold": 0.5, "min_equity_bars": 100,
}

# ========== 生命周期 ==========
STRATEGY_LIFECYCLE = {
    "enabled": True,
    "eval_window_bars": LOOKBACK_BARS * 10,
    "eval_every_bars": LOOKBACK_BARS,
    "sharpe_threshold": -0.5,
    "max_negative_windows": 3,
    "min_weight": 0.05, "weight_decay": 0.5,
    "min_active_strategies": 2,
    "recover_sharpe": 0.5,
}

# ========== 风控 ==========
RISK_CONTROL = {
    "daily_stop_loss": -0.03,
    "max_drawdown_stop": -0.10,
    "cooldown_days": 3,
    "single_position_max": 0.95,
    "min_bars_between_trades": 5 if FREQ != "daily" else 1,
    "max_trades_per_day": 3 if FREQ != "daily" else 1,
}
RISK_SEARCH_SPACE = {
    "daily_stop_loss": [-0.02, -0.03, -0.05],
    "max_drawdown_stop": [-0.08, -0.10, -0.15],
    "cooldown_days": [1, 3, 5],
    "min_bars_between_trades": [1, 3, 5] if FREQ == "daily"
                                else [3, 5, 10],
    "max_trades_per_day": [1, 2, 3] if FREQ == "daily"
                          else [3, 5, 10],
}
RISK_LLM_MAX_ROUNDS = 4

# ========== 联合优化 ==========
JOINT_LLM = {"enabled": True, "max_rounds": 5, "objective": "dsr"}

# ========== 自动重挖 ==========
AUTO_REMINING = {
    "enabled": True, "max_remining_rounds": 3,
    "cooldown_bars": LOOKBACK_BARS * 5,
    "min_decay_alerts": 2,
    "keep_old_factor_if_new_worse": True,
    "new_factor_ic_improve": 0.003,
    "trigger_on": ["decay", "pbo_rising"],
}
TRIGGER_LOGIC = {
    "enabled": True, "mode": "and",
    "dsr_threshold": 0.7, "pbo_threshold": 0.5,
    "dsr_delta_threshold": -0.1, "pbo_delta_threshold": 0.1,
    "consecutive_rounds": 2,
    "weights": {"dsr": 0.6, "pbo": 0.4},
    "weighted_threshold": 0.6,
}

# ========== PBO 演化 ==========
PBO_TIMELINE = {
    "enabled": True,
    "window_bars": LOOKBACK_BARS * 20,
    "step_bars": LOOKBACK_BARS * 5,
    "n_splits": 8, "max_combinations": 1000,
    "trend_window": 5, "trend_threshold": 0.1,
    "pbo_warn": 0.5, "pbo_danger": 0.7,
}

# ========== 因子库 ==========
FACTOR_LIBRARY = {
    "enabled": True, "md_path": "factor_library.md",
    "index_path": "factor_library_index.json",
    "max_md_size_mb": 10,
    "archive_dir": "factor_library_archive",
    "top_n_in_summary": 20,
    "include_code": True, "include_stats": True,
}
FACTOR_LIBRARY_GIT = {
    "enabled": True, "auto_commit": True, "auto_push": False,
    "commit_prefix": "[factor-lib]", "git_dir": ".",
    "tracked_files": ["factor_library.md",
                      "factor_library_index.json",
                      "factor_library.csv"],
    "commit_author": "RD-Agent",
    "commit_email": "rdagent@local", "max_history": 100,
}

# ========== RL 权重 ==========
RL_WEIGHT = {"enabled": True, "alpha": 0.5,
             "load_path": "rl_weight_bandit.pkl"}

# ========== LLM 研究计划 ==========
LLM_RESEARCH_PLANNER = {"enabled": True, "days": 3,
                        "max_candidates": 5}

# ========== 动画导出 ==========
PARETO_ANIMATION = {
    "enabled": True, "html_path": "pareto_animation.html",
    "gif_path": "pareto_animation.gif",
    "max_generations": 10, "sample_per_gen": 40,
    "dimensions": 3, "color_by": "abs_ic",
    "auto_play": True, "frame_duration": 800,
    "save_history": True, "history_path": "pareto_history.csv",
}
ANIMATION_EXPORT = {
    "enabled": True, "export_gif": True, "export_mp4": True,
    "gif_duration_ms": 700, "mp4_fps": 2,
    "width": 1200, "height": 800,
    "gif_path": "pareto_animation.gif",
    "mp4_path": "pareto_animation.mp4",
    "frames_dir": "pareto_frames",
    "keep_frames": False, "max_frames": 10,
}
ANIMATION_SHAP = {
    "enabled": True, "shap_source": "decay_explanation",
    "panel_ratio": [0.7, 0.3],
    "top_n_features": 8, "aggregation": "mean",
    "normalize_per_frame": True, "colormap": "Viridis",
}

# ========== RD-Agent ==========
RDAGENT_BACKEND = "llm"
RDAGENT_USE_OFFICIAL_FALLBACK = True
LLM_MODEL = "gpt-4o-mini"
LLM_API_KEY_ENV = "OPENAI_API_KEY"

# ========== 多 LLM 生成 ==========
MULTI_LLM_GEN = {
    "mode": "write_and_vote", "n_samples_per_model": 1,
    "min_models_required": 2, "blind_eval": True,
    "randomize_order": True, "merge_top_k": 2,
    "enable_merge": False, "concurrency": 4,
    "timeout_seconds": 60,
}

# ========== 缓存 ==========
CACHE_DIR = "data_cache"
UNIVERSE_CACHE = "etf_universe_cache.csv"
TRIAL_COUNTER_FILE = "trial_counter.json"
PBO_RESULT_FILE = "pbo_result.json"

# ========== 看板 ==========
DASHBOARD = {
    "title": "ETF 量化系统",
    "output_dir": ".", "auto_refresh_seconds": 60,
}