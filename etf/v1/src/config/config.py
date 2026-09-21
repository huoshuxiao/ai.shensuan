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

# ========== 目录 ==========
# etf/v1 项目根目录（config.py 位于 etf/v1/src/config/ 下）
V1_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 研究管线/各模块的本地 LLM 与覆盖项统一从 etf/v1/.env 读取。
# 此前研究侧不加载任何 .env，config 里的 ETF_LLM_* 只认已导出的 shell 变量，
# etf/v1/.env 形同虚设；在此显式装载后它才是研究侧 LLM 的单一来源
# （与 RD-Agent loop 用的 rdagent_output/.env[LITELLM_*] 各一套）。
# override=False：已在 shell 导出的变量优先，便于临时覆盖。
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(V1_ROOT, ".env"))
except Exception:
    pass

# 统一数据输出目录（所有运行产物/缓存/实盘记录），可用 ETF_DATA_DIR 覆盖
DATA_DIR = os.environ.get("ETF_DATA_DIR", os.path.join(V1_ROOT, "data"))
# 研究/回测产物（equity/trades/signals/因子分析/动画等）
RESULTS_DIR = os.path.join(DATA_DIR, "results")
# 报告输出目录（所有人类可读报告统一写到此处），可用 ETF_REPORT_DIR 覆盖
REPORT_DIR = os.environ.get("ETF_REPORT_DIR",
                            os.path.join(V1_ROOT, "report"))
# 实盘原始数据目录（live_orders/live_attribution/feedback_report.json 等输入）
LIVE_DATA_DIR = os.path.join(DATA_DIR, "live")
# 行情缓存目录
CACHE_DIR = os.path.join(DATA_DIR, "cache")
# 因子库目录
LIBRARY_DIR = os.path.join(DATA_DIR, "library")
# 日志目录（log_kit 双写目标），可用 ETF_LOG_DIR 覆盖
LOG_DIR = os.environ.get("ETF_LOG_DIR", os.path.join(V1_ROOT, "log"))
for _d in (DATA_DIR, RESULTS_DIR, REPORT_DIR, LIVE_DATA_DIR,
           CACHE_DIR, LIBRARY_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

# ========== 回测区间 ==========
# BACKTEST_END 未设置或为空时（此处或环境变量），自动取当天日期
BACKTEST_START = os.environ.get("BACKTEST_START") or "2010-01-01"
BACKTEST_END = os.environ.get("BACKTEST_END") or ""
if not BACKTEST_END:
    from datetime import date
    BACKTEST_END = date.today().strftime("%Y-%m-%d")

# ========== 资金 ==========
INIT_CAPITAL = 10_000
POSITION_RATIO = 0.95
MIN_TRADE_AMOUNT = 100

# ========== 费率 ==========
COMMISSION_RATE = 0.0001
MIN_COMMISSION = 0.1
SLIPPAGE = 0.0005

# ========== ETF 池 ==========
# 东财/新浪接口均不可用时的兜底：常见宽基/行业 ETF（code, name）
FALLBACK_UNIVERSE = [
    ("510300", "沪深300ETF"), ("510500", "中证500ETF"),
    ("510050", "上证50ETF"), ("159915", "创业板ETF"),
    ("512100", "中证1000ETF"), ("588000", "科创50ETF"),
    ("512880", "证券ETF"), ("512760", "芯片ETF"),
    ("159928", "消费ETF"), ("512690", "酒ETF"),
    ("513100", "纳指ETF"), ("513500", "标普500ETF"),
    ("159941", "纳指ETF(深)"), ("518880", "黄金ETF"),
    ("511990", "华宝添益"),
]

ETF_FILTER = {
    # 成立不满 1 年的 ETF 不入池（365 自然日）：新基金建仓期收益不代表策略有效域
    "min_list_days": 365,
    "min_avg_amount": 5_000_000,
    # 截面轮动池规模：每指数取"上市最早且流动性达标"的代表，再按成交额截断 Top N。
    # 全市场(1000+)逐只拉日线不现实，且池内股票型 ETF 才是策略有效域
    "max_count": 20,
    "exclude_keywords": ["货币", "短融", "同业存单", "现金",
                         "日利", "添益", "添利", "活期", "理财"],
    # 511=上交所债券/货币 ETF 段：价格近似债券，混入轮动是噪声
    "exclude_prefixes": ["511"],
}

# ========== 行情数据源（按优先级降级） ==========
# em=东方财富(akshare)  sina=新浪  tx=腾讯
DATA_SOURCES = {
    "daily": ["em", "sina", "tx"],
    "intraday": ["em", "tx"],
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
                "embargo_bars": LOOKBACK_BARS // 4,
                # 折内挖掘引擎：与主链同构，样本外验证才覆盖得到 GP/DSL 因子。
                # registry=注册表基线，genetic=字符串 DSL 遗传编程（折内成本主力），
                # multi_source=多源（含 RD-Agent/LLM，默认关闭：每折一次外部调用）
                "fold_engines": ["registry", "genetic"]}

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
    # 最佳 |IC| 连续这么多代无提升即早停：搜索已收敛时
    # 不再空转剩余世代的种群评估
    "stagnation_generations": 3,
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
    # 同单目标 GP：最佳 |IC| 连续无提升即早停
    "stagnation_generations": 3,
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
    # 归因评估窗：只截最近 window 根 bar 重跑回测。日频 500 bar≈2 年；
    # 原 LOOKBACK_BARS*100(=2000) 大于日频全样本(~1575)，截窗形同虚设
    "eval_window_bars": 500 if FREQ == "daily" else LOOKBACK_BARS * 20,
    # 归因因子数上限：Shapley 需枚举子集，实测 11 因子去重后仍 965 次
    # 回测(5.5h)；按 |mean_ic| 取前 8，不同子集上界降到 2^8=256
    "max_factors": 8,
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
    "report_path": os.path.join(REPORT_DIR, "llm_shap_report.md"),
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
    # 配置族开关：True 时 PBO 用"真实回测过的风控参数组合"做 CSCV 变体，
    # 覆盖参数选择偏差；False 退化为单一收益序列的降权伪变体（只测平滑敏感度）。
    "use_config_family": True,
    # OFAT（一次只动一维）配置族上限：全笛卡尔积 = Π|RISK_SEARCH_SPACE| 档，
    # 逐档回测不现实，故按维度邻域截断到此数量。
    "max_family_configs": 12,
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
# 跨运行状态（连续恶化计数 / 重挖轮数 / 冷却基准）落盘位置。
# 必须持久化：触发器对象每次进程新建，纯内存状态会让
# consecutive_rounds、cooldown_bars、max_remining_rounds 全部失效。
REMINING_STATE_FILE = os.environ.get(
    "ETF_REMINING_STATE_FILE",
    os.path.join(CACHE_DIR, "remining_state.json"))

AUTO_REMINING = {
    "enabled": True, "max_remining_rounds": 3,
    "cooldown_bars": LOOKBACK_BARS * 5,
    "min_decay_alerts": 2,
    "keep_old_factor_if_new_worse": True,
    "new_factor_ic_improve": 0.003,
    # indicator = DSR/PBO 联动指标（trigger_logic.DualIndicatorTrigger）
    "trigger_on": ["decay", "pbo_rising", "indicator"],
    # 重挖时启用的引擎集合（同 WALK_FORWARD.fold_engines 取值）
    "remining_engines": ["registry", "genetic"],
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
    "enabled": True,
    "md_path": os.path.join(LIBRARY_DIR, "factor_library.md"),
    "index_path": os.path.join(LIBRARY_DIR,
                               "factor_library_index.json"),
    "max_md_size_mb": 10,
    "archive_dir": os.path.join(LIBRARY_DIR, "archive"),
    "top_n_in_summary": 20,
    "include_code": True, "include_stats": True,
}
FACTOR_LIBRARY_GIT = {
    "enabled": True, "auto_commit": True, "auto_push": False,
    "commit_prefix": "[factor-lib]", "git_dir": V1_ROOT,
    "tracked_files": [
        os.path.join("data", "library", "factor_library.md"),
        os.path.join("data", "library", "factor_library_index.json"),
        os.path.join("data", "library", "factor_library.csv")],
    "commit_author": "RD-Agent",
    "commit_email": "rdagent@local", "max_history": 100,
}

# ========== RL 权重 ==========
RL_WEIGHT = {"enabled": True, "alpha": 0.5,
             "load_path": os.path.join(CACHE_DIR,
                                       "rl_weight_bandit.pkl")}

# ========== LLM 研究计划 ==========
LLM_RESEARCH_PLANNER = {"enabled": True, "days": 3,
                        "max_candidates": 5}

# ========== 动画导出 ==========
PARETO_ANIMATION = {
    "enabled": True,
    "html_path": os.path.join(RESULTS_DIR, "pareto_animation.html"),
    "gif_path": os.path.join(RESULTS_DIR, "pareto_animation.gif"),
    "max_generations": 10, "sample_per_gen": 40,
    "dimensions": 3, "color_by": "abs_ic",
    "auto_play": True, "frame_duration": 800,
    "save_history": True,
    "history_path": os.path.join(RESULTS_DIR, "pareto_history.csv"),
}
ANIMATION_EXPORT = {
    # kaleido 静态导出依赖本机 Chrome/Chromium；此环境 snap chromium 启动即
    # 退出（BrowserFailedError），关掉 GIF/MP4，HTML 动画不受影响
    "enabled": True, "export_gif": False, "export_mp4": False,
    "gif_duration_ms": 700, "mp4_fps": 2,
    "width": 1200, "height": 800,
    "gif_path": os.path.join(RESULTS_DIR, "pareto_animation.gif"),
    "mp4_path": os.path.join(RESULTS_DIR, "pareto_animation.mp4"),
    "frames_dir": os.path.join(RESULTS_DIR, "pareto_frames"),
    "keep_frames": False, "max_frames": 10,
}
ANIMATION_SHAP = {
    "enabled": True, "shap_source": "decay_explanation",
    "panel_ratio": [0.7, 0.3],
    "top_n_features": 8, "aggregation": "mean",
    "normalize_per_frame": True, "colormap": "Viridis",
}

# ========== RD-Agent ==========
# "official" = 先走微软 RD-Agent(Q) factor 循环（official_rdagent 内置
# 前置体检：rdagent 包/conda/docker/pyqlib/LLM 端点），依赖齐备即真正
# 运行并把逐项链路写进日志；缺失则 ℹ️ 记录原因并降级到本地 LLM 管线
RDAGENT_BACKEND = os.environ.get("ETF_RDAGENT_BACKEND", "official")
# 是否允许 official 源真正拉起 RD-Agent(Q) 循环。默认 True 保持既有行为；
# 只想跑主线（回测/实盘/反馈）、不触发 ~1.5h 因子循环时，置
# ETF_RDAGENT_OFFICIAL_FALLBACK=false 即可跳过 official 源、走 llm/simple/GP
RDAGENT_USE_OFFICIAL_FALLBACK = os.environ.get(
    "ETF_RDAGENT_OFFICIAL_FALLBACK", "true").lower() in ("1", "true", "yes")
# 官方循环在独立 conda 环境的子进程中运行（rdagent/pyqlib 依赖树与
# 管线进程隔离，避免双 Python 环境互相污染）
RDAGENT_CONDA_ENV = os.environ.get("ETF_RDAGENT_ENV", "rdagent")
# factor 循环子进程最长运行时长（秒）；超时即回收并记录
RDAGENT_TIMEOUT_SEC = int(os.environ.get("ETF_RDAGENT_TIMEOUT", "7200"))
# qlib 回测沙箱容器的资源注入（QLIB_DOCKER_* 由 rdagent 的 pydantic
# 配置直接读取）：本机 GPU 仅 GTX 965M 2GB 不可用，关 GPU 走 CPU；
# 默认 shm_size=16g 等于物理内存上限，容器起不来，压到 4g
RDAGENT_QLIB_DOCKER_ENV = {
    "QLIB_DOCKER_ENABLE_GPU": os.environ.get("ETF_RDAGENT_DOCKER_GPU", "false"),
    "QLIB_DOCKER_SHM_SIZE": os.environ.get("ETF_RDAGENT_DOCKER_SHM", "4g"),
    # 因子/模型工作区的 qrun 回测后端：rdagent 默认 "conda" 要求宿主
    # 存在 rdagent4qlib 环境（多一份 3GB 环境且生成代码在本机执行）；
    # 统一走已建好的 local_qlib 容器，隔离且可复现
    "MODEL_CoSTEER_ENV_TYPE": os.environ.get("ETF_RDAGENT_EXEC_ENV", "docker"),
    # 沙箱镜像的构建上下文（Dockerfile 所在目录）。rdagent 默认取自己
    # site-packages 里的官方 CUDA 版，每次循环都无条件重编一层 GB 级镜像；
    # 本机改用本地这份 slim CPU 版，且其中 ENV MLFLOW_ALLOW_FILE_STORE=true
    # 是必需的——mlflow 3.x 封了 qlib 默认使用的 ./mlruns 文件后端，
    # 容器内看不到宿主机变量，只能烤进镜像（置空则回退官方目录）
    "QLIB_DOCKER_DOCKERFILE_FOLDER_PATH": os.environ.get(
        "ETF_RDAGENT_DOCKERFILE_DIR",
        f"{RESULTS_DIR}/rdagent_docker"),
    # 容器内存上限：rdagent 默认 48g 远超本机 16G，容器 oom 时只会表现成
    # 非零退出码（难归因），提前钳到宿主可用水位
    "QLIB_DOCKER_MEM_LIMIT": os.environ.get("ETF_RDAGENT_DOCKER_MEM", "10g"),
}
LLM_MODEL = os.environ.get("ETF_LLM_MODEL", "gpt-4o-mini")
LLM_API_KEY_ENV = "OPENAI_API_KEY"
# 本地/自建 LLM 端点（Ollama: http://localhost:11434/v1、vLLM、LM Studio 等，
# 均需 OpenAI 兼容接口）。留空走 OpenAI 官方；本地服务不校验 key，可缺省
LLM_BASE_URL = os.environ.get("ETF_LLM_BASE_URL", "").strip()

# ========== 多 LLM 生成 ==========
MULTI_LLM_GEN = {
    "mode": "write_and_vote", "n_samples_per_model": 1,
    "min_models_required": 2, "blind_eval": True,
    "randomize_order": True, "merge_top_k": 2,
    "enable_merge": False, "concurrency": 4,
    "timeout_seconds": 60,
}

# ========== 缓存 ==========
UNIVERSE_CACHE = os.path.join(CACHE_DIR, "etf_universe_cache.csv")
# 全市场 ETF 上市日期持久缓存 {code: "YYYY-MM-DD"}：按"最早上市"选代表
# 需要先拿到所有候选的上市日期（逐只拉取），缓存后增量补拉
ETF_LIST_DATE_CACHE = os.path.join(CACHE_DIR, "etf_list_dates.json")
TRIAL_COUNTER_FILE = os.path.join(CACHE_DIR, "trial_counter.json")
PBO_RESULT_FILE = os.path.join(RESULTS_DIR, "pbo_result.json")

# ========== 看板 ==========
DASHBOARD = {
    "title": "ETF 量化系统",
    "output_dir": RESULTS_DIR, "auto_refresh_seconds": 60,
}