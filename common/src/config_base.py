# -*- coding: utf-8 -*-
"""ETF 线与股票线共享的配置底座。

用法（各线 src/config/config.py 内）：

    V1_ROOT = <本线根目录>
    globals().update(build(V1_ROOT, "ETF_"))     # 再写本线专属项

为什么这么切：common/ 下的模块一律 `from config import X`，靠 _bootstrap 的
裸模块名导入解析，拿到的就是「当前这条线自己的 config」。所以底座的责任是把
两线**同名**的配置生成齐（少一个名字，另一线一 import 就 AttributeError），
线级差异只通过 (本线根目录, 环境变量前缀) 两个参数与线内覆写表达。

参数 env_prefix 让两条线的覆盖变量互不串台：ETF 线仍读 ETF_DATA_DIR 等既有
变量名（保持历史用法不变），股票线读 STOCK_* 一套。

划分依据（脚本扫 import 引用算出来的，不是拍脑袋）：common/ 里被引用到的
51 个配置名全部落在这里；只剩 ETF 侧用得上的（资金/费率细则、ETF 池、策略
网格、风控搜索空间、看板、缓存文件名……）留在 etf/v1/src/config/config.py。
FREQ_MAP / LOOKBACK_BARS 虽然名义上「只有 ETF 线引用」，但本文件内
IC_THRESHOLD / GENETIC_OPERATORS / DECAY_PREDICT / AUTO_REMINING 的取值要用
它们，故也在此生成（其中 akshare_* 字段只对 ETF 线有意义，股票线忽略即可）。
"""

import json
import os


def build(line_root, env_prefix="ETF_", freq_default="daily", market="etf"):
    """按「本线根目录 + 环境变量前缀」生成本线全套共享配置，返回 dict

    market 会随每条因子写进因子库（md/json/csv），用来标明这条因子的 IC 是在
    哪个截面上算出来的——ETF 与 A 股个股的 IC 数值不可直接横比。
    """
    # common/（本文件所在目录的上一级）：两线共用的沙箱镜像构建上下文放在它下面
    _common = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def env(name, default=""):
        return os.environ.get(env_prefix + name, default)

    d = {}
    d["MARKET"] = market

    # ========== 频率 ==========
    d["FREQ"] = env("FREQ", freq_default)   # daily | 1min | 5min | ...
    # 频率 → 数据源参数映射（akshare_* 仅 ETF 线的 data_loader 使用）
    d["FREQ_MAP"] = {
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
    d["LOOKBACK_DAYS"] = 20
    d["LOOKBACK_BARS"] = d["LOOKBACK_DAYS"] * \
        d["FREQ_MAP"][d["FREQ"]]["bars_per_day"]
    freq = d["FREQ"]

    # ========== 目录 ==========
    # line_root = 本线项目根（etf/v1 或 stock/v1），本线的**产物**目录由它派生，
    # 两条线的 data/ report/ log/ 因此天然物理隔离；只有抓来的基础行情数据例外，
    # 它落 common/data/<线>/（见下面 BASE_DATA_DIR），线内 data/ 只留研究结论
    d["V1_ROOT"] = line_root
    # 本线的本地 LLM 与覆盖项统一从 <line_root>/.env 读取。
    # override=False：已在 shell 导出的变量优先，便于临时覆盖。
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(line_root, ".env"))
    except Exception:
        pass
    d["DATA_DIR"] = env("DATA_DIR", os.path.join(line_root, "data"))
    d["RESULTS_DIR"] = os.path.join(d["DATA_DIR"], "results")
    d["REPORT_DIR"] = env("REPORT_DIR", os.path.join(line_root, "report"))
    # 实盘原始数据目录（live_orders/live_attribution/feedback_report.json 等输入）
    d["LIVE_DATA_DIR"] = os.path.join(d["DATA_DIR"], "live")
    # 基础行情数据根（09-29 进 common）：抓来的日线镜像、qlib 行情 bin、当日全
    # 市场快照、份额/净值长表这类"给所有工程喂数"的目录，统一落
    # common/data/<线名>/。别的工程（live2etf 要只读 ETF 全池日线算流动性闸）
    # 从此指这一个稳定入口，不再写死对方线内的 data/。
    # 分析产物（results / library / live / report）刻意**不跟着搬**，仍是本线私有：
    # 它们是这条线挖出来的结论，换一条线就读不到，放进 common 反而被误当成公共品。
    d["BASE_DATA_DIR"] = env(
        "BASE_DATA_DIR",
        os.path.join(_common, "data", os.path.basename(
            os.path.dirname(os.path.abspath(line_root)))))
    d["CACHE_DIR"] = os.path.join(d["BASE_DATA_DIR"], "cache")
    # qlib 行情 bin 根：容器侧 provider_uri 由它派生（见 RDAGENT_QLIB_PROVIDER）
    d["QLIB_DATA_DIR"] = os.path.join(d["BASE_DATA_DIR"], "qlib")
    d["LIBRARY_DIR"] = os.path.join(d["DATA_DIR"], "library")
    d["LOG_DIR"] = env("LOG_DIR", os.path.join(line_root, "log"))
    for _p in ("DATA_DIR", "RESULTS_DIR", "REPORT_DIR", "LIVE_DATA_DIR",
               "BASE_DATA_DIR", "CACHE_DIR",
               "LIBRARY_DIR", "LOG_DIR"):
        os.makedirs(d[_p], exist_ok=True)

    # ========== 费率（公共口径，各线按标的规则覆写） ==========
    # ETF 免印花税、佣金可谈到 1bp；A 股个股卖出另有 0.05% 印花税且 T+1，
    # 股票线在自己的 config.py 里覆盖这两个值
    d["COMMISSION_RATE"] = 0.0001
    d["SLIPPAGE"] = 0.0005

    # ========== 因子挖掘 ==========
    d["MAX_LOOPS"] = 3
    d["HYPOTHESIS_PER_LOOP"] = 3
    d["IC_THRESHOLD"] = 0.015 if freq != "daily" else 0.02
    d["MAX_FACTORS"] = 8

    # ========== 正交化 ==========
    d["ORTHOGONAL"] = {
        "enabled": True,
        "corr_threshold": 0.7,
        "method": "gram_schmidt",
        "min_factors_keep": 2,
    }

    # ========== LLM 调正交化（orthogonal_optimizer） ==========
    d["ORTHO_LLM"] = {
        "enabled": True, "max_rounds": 4,
        "threshold_range": [0.3, 0.9],
        "methods": ["gram_schmidt", "pca", "none"],
    }

    # ========== DSR ==========
    d["DSR"] = {"enabled": True, "n_trials": None, "benchmark_sharpe": 0.0}

    # ========== PBO ==========
    d["PBO"] = {"enabled": True, "n_splits": 10,
                "max_combinations": 5000, "n_configs": 20}

    # ========== 多源挖掘 ==========
    d["MULTI_SOURCE"] = {
        "enabled": True,
        "sources": ["official", "llm", "simple", "genetic"],
        # 折内（walk-forward）是否启用 official 源。official 的回收目录不分折
        # （try_official_rdagent 的 output_dir 固定），折内那一轮容器被 kill 后
        # 驱动 finally 里的回收没来得及写 ⇒ 各折都读同一份全历史 factors.json，
        # 不是点时的。ETF 线 09-30 按用户裁「丙-2」置 False；默认 True 保持原行为。
        # ⚠️ 这个键进了断点缓存的数据指纹（run_checkpoint.make_fingerprint），
        # 翻它 ⇒ 旧缓存整批作废，不会"续传命中 = 开关空转"。
        "official_in_fold": True,
        "timeout_seconds": 300,
        "merge_mode": "union_dedup",
        "corr_dedup_threshold": 0.85,
        "min_ic_per_source": 0.005,
        "weights": {"official": 1.2, "llm": 1.0,
                    "simple": 0.8, "genetic": 0.9},
    }

    # ========== 遗传编程 ==========
    d["GENETIC"] = {
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

    d["GENETIC_OPERATORS"] = {
        "binary": ["+", "-", "*", "/"],
        "unary": ["abs", "log", "sign", "neg"],
        "terminal": ["close", "open", "high", "low", "volume", "returns"],
        "window": [5, 10, 20, 60] if freq == "daily" else [5, 20, 60, 240],
        "rolling": ["ts_mean", "ts_std", "delta", "delay", "ts_sum"],
    }

    # ========== 多目标 GP ==========
    d["GENETIC_MULTI_OBJECTIVE"] = {
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

    d["EXTENDED_OBJECTIVES"] = {
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
    d["DYNAMIC_WEIGHTS"] = {
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
    d["ADAPTIVE_MUTATION"] = {
        "enabled": True, "diversity_metric": "expr_distance",
        "target_diversity": 0.6,
        "min_mutation_rate": 0.1, "max_mutation_rate": 0.8,
        "adaptation_speed": 0.3,
        "crossover_adaptive": True,
        "min_crossover_rate": 0.3, "max_crossover_rate": 0.8,
        "history_window": 5,
    }

    # ========== LLM 变异/交叉 ==========
    d["LLM_MUTATION"] = {
        "enabled": True, "llm_prob": 0.3,
        "max_llm_calls_per_gen": 5,
        "include_parent": True, "include_weakness": True,
        "temperature": 0.9, "cache_size": 200,
    }
    d["LLM_CROSSOVER"] = {
        "enabled": True, "llm_prob": 0.3,
        "max_llm_calls_per_gen": 4, "temperature": 0.85,
        "include_parents": True, "include_metrics": True,
        "include_logic": True, "cache_size": 200,
        "fallback_to_random": True,
    }
    d["LLM_GENETIC_HYBRID"] = {
        "enabled": True, "llm_seed_ratio": 0.4,
        "llm_n_seeds": 8, "n_generations": 6,
        "population_size": 30,
        "feedback_to_llm": True, "feedback_rounds": 2,
    }

    # ========== 聚类 ==========
    d["FACTOR_CLUSTERING"] = {
        "enabled": True, "method": "hierarchical",
        "n_clusters": 8, "corr_threshold": 0.6,
        "distance_metric": "correlation",
        "dbscan_eps": 0.4, "dbscan_min_samples": 2,
        "representative_mode": "highest_ic",
        "min_cluster_size": 1,
        "visualize_method": "tsne", "tsne_perplexity": 5,
        "random_state": 42,
    }

    # ========== 衰减预测 ==========
    d["DECAY_PREDICT"] = {
        "enabled": True, "model": "arima",
        "forecast_horizon": d["LOOKBACK_BARS"],
        "min_history_bars": 200 if freq == "daily" else 1000,
        "ic_window_bars": 240 if freq == "daily" else 2400,
        "ic_step_bars": 20 if freq == "daily" else 120,
        "warn_threshold": -0.3, "danger_threshold": -0.5,
        "ensemble_weights": {"arima": 0.4, "lightgbm": 0.6},
        "lightgbm_params": {"n_estimators": 100, "max_depth": 4,
                             "learning_rate": 0.05},
    }
    d["DECAY_EXPLAIN"] = {
        "enabled": True, "method": "shap",
        "n_shap_samples": 100,
        "n_lags": 10,
        "extra_features": ["ic_mean_5", "ic_std_5",
                           "ic_slope_5", "volatility"],
        "top_n_lags": 5, "save_shap_values": True,
        "plot_type": "bar",
    }
    d["LLM_SHAP_EXPLAINER"] = {
        "enabled": True, "max_factors": 5,
        "include_full_importance": True,
        "max_tokens_in_prompt": 3000,
        "save_report": True,
        "report_path": os.path.join(d["REPORT_DIR"], "llm_shap_report.md"),
        "language": "zh", "include_action_suggestion": True,
    }

    # ========== 风险预算 ==========
    d["RISK_BUDGET"] = {
        "enabled": True, "weight_method": "ic_ir",
        "max_weight_per_factor": 0.4,
        "min_weight_per_factor": 0.02,
        "decay_halflife_bars": 240 if freq == "daily" else 2400,
        "ic_lookback_bars": 480 if freq == "daily" else 4800,
    }

    # ========== 风控 ==========
    d["RISK_CONTROL"] = {
        "daily_stop_loss": -0.03,
        "max_drawdown_stop": -0.10,
        "cooldown_days": 3,
        "single_position_max": 0.95,
        "min_bars_between_trades": 5 if freq != "daily" else 1,
        "max_trades_per_day": 3 if freq != "daily" else 1,
    }

    # ========== 联合优化 ==========
    d["JOINT_LLM"] = {"enabled": True, "max_rounds": 5, "objective": "dsr"}

    # ========== 自动重挖 ==========
    # 跨运行状态（连续恶化计数 / 重挖轮数 / 冷却基准）落盘位置。
    # 必须持久化：触发器对象每次进程新建，纯内存状态会让
    # consecutive_rounds、cooldown_bars、max_remining_rounds 全部失效。
    d["REMINING_STATE_FILE"] = env(
        "REMINING_STATE_FILE",
        os.path.join(d["CACHE_DIR"], "remining_state.json"))

    d["AUTO_REMINING"] = {
        "enabled": True, "max_remining_rounds": 3,
        "cooldown_bars": d["LOOKBACK_BARS"] * 5,
        "min_decay_alerts": 2,
        "keep_old_factor_if_new_worse": True,
        "new_factor_ic_improve": 0.003,
        # indicator = DSR/PBO 联动指标（trigger_logic.DualIndicatorTrigger）
        "trigger_on": ["decay", "pbo_rising", "indicator"],
        # 重挖时启用的引擎集合（同 WALK_FORWARD.fold_engines 取值）
        "remining_engines": ["registry", "genetic"],
    }
    d["TRIGGER_LOGIC"] = {
        "enabled": True, "mode": "and",
        "dsr_threshold": 0.7, "pbo_threshold": 0.5,
        "dsr_delta_threshold": -0.1, "pbo_delta_threshold": 0.1,
        "consecutive_rounds": 2,
        "weights": {"dsr": 0.6, "pbo": 0.4},
        "weighted_threshold": 0.6,
    }

    # ========== 因子库 ==========
    d["FACTOR_LIBRARY"] = {
        "enabled": True,
        # 写库之前的静态体检（`factor_static_check.check_expr`：未来函数/未知名字/
        # 非法语法/非法属性）。默认开。为什么由它默认开而不是像 HYPOTHESIS_ROLES
        # 那样默认关：那把尺子原本只在默认关闭的多角色定稿闸里被调用一次 ⇒
        # "IC 过线 → 写库" 这条生产路上**一道静态检查都没有**，10-01 因此让
        # `delay(max(high, 5), -1)`（读下一根 K 线）带着虚高约一半的 IC 进了库
        # （+0.0551→+0.0290 去掉偷看，见 etf/v1/temp/lookahead_price_1001.py）。
        # 误杀账单：拿同一把尺子扫当时在库 45 行 ⇒ 43 放行、1 判红（就是那条
        # 偷看的）、1 条 expr 为空按放行处理（空 expr 不是未来函数，拒它等于把
        # 已存在的行冻在旧 IC 上，那是这一裁决之外的第二笔影响）。
        # 关掉=回到"IC 过线就写库"：`ETF_LIBRARY_STATIC_GATE=0`（股票线同名 STOCK_）。
        "static_gate": env("LIBRARY_STATIC_GATE", "1").strip().lower() in (
            "1", "true", "on", "yes"),
        "md_path": os.path.join(d["LIBRARY_DIR"], "factor_library.md"),
        "index_path": os.path.join(d["LIBRARY_DIR"],
                                   "factor_library_index.json"),
        "max_md_size_mb": 10,
        "archive_dir": os.path.join(d["LIBRARY_DIR"], "archive"),
        "top_n_in_summary": 20,
        "include_code": True, "include_stats": True,
    }
    # git_dir 指本线根目录即可：仓库是同一个，git 会自行上溯到 .git，
    # 而 tracked_files 按 CWD(=本线根) 相对解析，故两线各自只提交自己的库
    d["FACTOR_LIBRARY_GIT"] = {
        "enabled": True, "auto_commit": True, "auto_push": False,
        "commit_prefix": "[factor-lib]", "git_dir": line_root,
        "tracked_files": [
            os.path.relpath(d["FACTOR_LIBRARY"][k], line_root)
            for k in ("md_path", "index_path")] + [
            os.path.relpath(os.path.join(d["LIBRARY_DIR"],
                                         "factor_library.csv"), line_root)],
        "commit_author": "RD-Agent",
        "commit_email": "rdagent@local", "max_history": 100,
    }

    # ========== RL 权重 ==========
    d["RL_WEIGHT"] = {"enabled": True, "alpha": 0.5,
                      "load_path": os.path.join(d["CACHE_DIR"],
                                                "rl_weight_bandit.pkl")}

    # ========== 动画导出 ==========
    d["PARETO_ANIMATION"] = {
        "enabled": True,
        "html_path": os.path.join(d["RESULTS_DIR"], "pareto_animation.html"),
        "gif_path": os.path.join(d["RESULTS_DIR"], "pareto_animation.gif"),
        "max_generations": 10, "sample_per_gen": 40,
        "dimensions": 3, "color_by": "abs_ic",
        "auto_play": True, "frame_duration": 800,
        "save_history": True,
        "history_path": os.path.join(d["RESULTS_DIR"], "pareto_history.csv"),
    }
    d["ANIMATION_EXPORT"] = {
        # kaleido 静态导出依赖本机 Chrome/Chromium；此环境 snap chromium 启动即
        # 退出（BrowserFailedError），关掉 GIF/MP4，HTML 动画不受影响
        "enabled": True, "export_gif": False, "export_mp4": False,
        "gif_duration_ms": 700, "mp4_fps": 2,
        "width": 1200, "height": 800,
        "gif_path": os.path.join(d["RESULTS_DIR"], "pareto_animation.gif"),
        "mp4_path": os.path.join(d["RESULTS_DIR"], "pareto_animation.mp4"),
        "frames_dir": os.path.join(d["RESULTS_DIR"], "pareto_frames"),
        "keep_frames": False, "max_frames": 10,
    }
    d["ANIMATION_SHAP"] = {
        "enabled": True, "shap_source": "decay_explanation",
        "panel_ratio": [0.7, 0.3],
        "top_n_features": 8, "aggregation": "mean",
        "normalize_per_frame": True, "colormap": "Viridis",
    }

    # ========== RD-Agent ==========
    # "official" = 先走微软 RD-Agent(Q) factor 循环（official_rdagent 内置
    # 前置体检：rdagent 包/conda/docker/pyqlib/LLM 端点），依赖齐备即真正
    # 运行并把逐项链路写进日志；缺失则 ℹ️ 记录原因并降级到本地 LLM 管线
    d["RDAGENT_BACKEND"] = env("RDAGENT_BACKEND", "official")
    # 是否允许 official 源真正拉起 RD-Agent(Q) 循环。默认 True 保持既有行为；
    # 只想跑主线（回测/实盘/反馈）、不触发 ~1.5h 因子循环时，置
    # <PREFIX>RDAGENT_OFFICIAL_FALLBACK=false 即可跳过 official 源
    d["RDAGENT_USE_OFFICIAL_FALLBACK"] = env(
        "RDAGENT_OFFICIAL_FALLBACK", "true").lower() in ("1", "true", "yes")
    # 官方循环的产物/工作目录。factor 循环子进程的 CWD 就切在这里，而 rdagent
    # 的 .env、提示词 CWD 覆盖（scenarios/qlib/prompts.yaml）、因子源数据
    # git_ignore_folder/ 全按 CWD 相对路径解析 —— 所以两条线必须各有一份，
    # 共用会让 ETF 线读到股票线预生成的 daily_pv.h5。
    d["RDAGENT_OUTPUT_DIR"] = env(
        "RDAGENT_OUTPUT_DIR", os.path.join(d["RESULTS_DIR"], "rdagent_output"))
    # 官方循环在独立 conda 环境的子进程中运行（rdagent/pyqlib 依赖树与
    # 管线进程隔离，避免双 Python 环境互相污染）
    d["RDAGENT_CONDA_ENV"] = env("RDAGENT_ENV", "rdagent")
    # qlib 行情数据根：物理落位在**本线自己的** common/data/<线>/qlib/qlib_data/
    # cn_data（09-29 由 data/qlib 搬进 common），两条线各自一份 bin，不再共用
    # 宿主的 ~/.qlib（A 股个股数据曾串到 ETF 线）。
    # 为什么必须保留 qlib_data/cn_data 这两级尾巴：rdagent 的 QTDockerEnv.prepare
    # 拿挂载目录拼 <mount>/qlib_data/cn_data 做存在性检查，缺了就在容器里联网
    # 重拉数据；而容器侧真正开数据的 provider_uri 在官方模板里是写死的
    # /root/.qlib/qlib_data/cn_data（无 env 可改），所以只能搬宿主挂载点、
    # 内部两级结构原样保留。它同时被 official_rdagent 的前置体检读（判存在性）。
    _qlib_provider = env(
        "RDAGENT_QLIB_PROVIDER",
        os.path.join(os.path.abspath(d["QLIB_DATA_DIR"]),
                     "qlib_data", "cn_data"))
    d["RDAGENT_QLIB_PROVIDER"] = _qlib_provider
    # 本线喂给 dump_qlib_bin 的行情源目录（akshare 日线 csv 所在）。体检用它
    # 判 qlib bin 与 daily_pv.h5 是否落后于行情——这两步重建目前只能手跑，
    # 忘了就会让循环在旧面板上编码（白烧一轮 LLM 时间）。股票线的数据来自社区
    # 全市场包、没有本地 csv 源，故留空即跳过新鲜度比对。
    d["RDAGENT_SOURCE_DIR"] = env("RDAGENT_SOURCE_DIR", "")
    # coding 阶段 CoSTEER 演化轮数（critic 反馈逐轮累积）。rdagent 读同名环境
    # 变量，注入驱动子进程后**优先于**工作区 .env（load_dotenv 默认不覆盖已有
    # 变量），留空表示沿用 .env 里的值。
    d["RDAGENT_COSTEER_MAX_LOOP"] = env("RDAGENT_COSTEER_MAX_LOOP", "").strip()
    # 甲-2（10-04 裁）：coding 阶段 CoSTEER 知识库的**跨场落盘路径**。默认空＝一个键
    # 都不注入、行为与 10-04 之前一字不差（股票线没这行 ⇒ 保持字节级不变）。给了路径
    # 就在父进程注入 rdagent 的那两个同名键（`CoSTEER_KNOWLEDGE_BASE_PATH` 读 /
    # `CoSTEER_NEW_KNOWLEDGE_BASE_PATH` 写，前缀 `CoSTEER_`），读写同一个文件＝这一场
    # 从上一场写过的实现起步。为什么原来一定是空：site-packages
    # `knowledge_management.py:83-84` 在写路径为 None 时只打一句
    # `Dump knowledge base path is not set, skip dumping.` 就返回，10-04 那场日志
    # 3196/3205/5817 行就是这句 ⇒ 每场都从空知识库起步、上一场的实现这一场重抄一遍。
    # 它只治「重复劳动」，不治「没有新想法」——净增大概仍是 0（见 AGENT.md 同日节）。
    d["RDAGENT_COSTEER_KB_PATH"] = env("RDAGENT_COSTEER_KB_PATH", "").strip()
    # official 支线用哪个本地聊天模型＝「跑一次到底起几个模型」的唯一入口。
    # 留空＝不注入、以本线 rdagent_output/.env 的 LITELLM_CHAT_MODEL 为准（历史默认，
    # 两份 .env 各写各的）；给了值就在父进程注入、压过那份 .env。**值必须带 litellm 的
    # provider 前缀**（本仓两份 .env 都写全名 `ollama_chat/…`）——10-03 实测少前缀＝官方
    # 支第一次调用 10 连败退出。它与 LLM_MODEL（主线侧）是两个模型，两支并发⇒同时常驻。
    d["RDAGENT_LLM_MODEL"] = env("RDAGENT_LLM_MODEL", "").strip()
    # 官方支那一次 LLM 调用的**顶层 kwargs 补丁**（JSON 对象）。10-04 起留空不再等于
    # 一字不注入：留空时由下面那两把统一开关（LLM_REASONING_EFFORT / LLM_NUM_CTX）
    # 派生，见 core/official_rdagent._driver_env；显式给了就照原样透传、优先级最高。
    # 为什么要这么窄的一个口子：10-03 两轮探针实测，「关思考」在 `LITELLM_*` 那套
    # 环境变量上**表达不出来**（`LiteLLMSettings.reasoning_effort` 的类型是
    # `Literal["low","medium","high"] | None`，`litellm` 的 ollama 转换层只在它非空时
    # 才发 `think`、发出去还是恒 True），**只有走 `litellm.completion(...)` 的 kwargs
    # 这条路关得掉**。ETF 侧实测值：
    #   {"think": false, "num_ctx": 16384}   → 思考 0 字符、正文 3/3 次是合法 JSON
    # `num_ctx` 是把窗口撑到装得下真提示词（那条 hypothesis_gen 有 5502 token，
    # 生产默认窗口只让它进 2050）；代价：驻留内存 +0.5GB、长提示词 prefill 约 390s。
    d["RDAGENT_LLM_KWARGS"] = env("RDAGENT_LLM_KWARGS", "").strip()
    # 挂载点 = provider_uri 往上两级（由构造保证二者永远一致，改一边不会漏改）
    _qlib_mount = os.path.dirname(os.path.dirname(_qlib_provider))
    # factor 循环子进程最长运行时长（秒）；超时即回收并记录
    d["RDAGENT_TIMEOUT_SEC"] = int(env("RDAGENT_TIMEOUT", "7200"))
    # qlib 回测沙箱容器的资源注入（QLIB_DOCKER_* 由 rdagent 的 pydantic
    # 配置直接读取）：本机 GPU 仅 GTX 965M 2GB 不可用，关 GPU 走 CPU；
    # 默认 shm_size=16g 等于物理内存上限，容器起不来，压到 4g
    d["RDAGENT_QLIB_DOCKER_ENV"] = {
        "QLIB_DOCKER_ENABLE_GPU": env("RDAGENT_DOCKER_GPU", "false"),
        "QLIB_DOCKER_SHM_SIZE": env("RDAGENT_DOCKER_SHM", "4g"),
        # 因子/模型工作区的 qrun 回测后端：rdagent 默认 "conda" 要求宿主
        # 存在 rdagent4qlib 环境（多一份 3GB 环境且生成代码在本机执行）；
        # 统一走已建好的 local_qlib 容器，隔离且可复现
        "MODEL_CoSTEER_ENV_TYPE": env("RDAGENT_EXEC_ENV", "docker"),
        # 沙箱镜像的构建上下文（Dockerfile 所在目录）。rdagent 默认取自己
        # site-packages 里的官方 CUDA 版，每次循环都无条件重编一层 GB 级镜像；
        # 本机改用这份 slim CPU 版，且其中 ENV MLFLOW_ALLOW_FILE_STORE=true
        # 是必需的——mlflow 3.x 封了 qlib 默认使用的 ./mlruns 文件后端，
        # 容器内看不到宿主机变量，只能烤进镜像（置空则回退官方目录）。
        # 放 common/ 是因为镜像名 local_qlib:latest 全局唯一：两条线共用同一
        # 份 Dockerfile，否则换线跑一次就得重编一次镜像
        "QLIB_DOCKER_DOCKERFILE_FOLDER_PATH": env(
            "RDAGENT_DOCKERFILE_DIR", os.path.join(_common, "rdagent_docker")),
        # 容器内存上限：rdagent 默认 48g 远超本机 16G，容器 oom 时只会表现成
        # 非零退出码（难归因），提前钳到宿主可用水位
        "QLIB_DOCKER_MEM_LIMIT": env("RDAGENT_DOCKER_MEM", "10g"),
        # 数据挂载：rdagent 默认把宿主 ~/.qlib 挂进容器，两条线就会共用同一份
        # A 股 bin（ETF 线曾拿到个股数据）。这里改挂本线的 data/qlib，容器内
        # 仍绑 /root/.qlib/ 以对上模板里写死的 provider_uri。
        # 值是 Dict[str, Dict[str, str]]，pydantic 从环境变量按 JSON 解析；
        # QTDockerEnv.prepare 只取 next(iter(keys())) 当数据根做检查，所以
        # 必须且只能有一个条目
        "QLIB_DOCKER_EXTRA_VOLUMES": env(
            "RDAGENT_DOCKER_VOLUMES",
            json.dumps({_qlib_mount: {"bind": "/root/.qlib/", "mode": "rw"}})),
    }
    d["LLM_MODEL"] = env("LLM_MODEL", "gpt-4o-mini")
    d["LLM_API_KEY_ENV"] = "OPENAI_API_KEY"
    # 本地/自建 LLM 端点（Ollama: http://localhost:11434/v1、vLLM、LM Studio 等，
    # 均需 OpenAI 兼容接口）。留空走 OpenAI 官方；本地服务不校验 key，可缺省
    d["LLM_BASE_URL"] = env("LLM_BASE_URL", "").strip()
    # 思考型模型（qwen3.5 等）的三道请求体闸门。历史包袱：本仓 21 个
    # chat.completions.create 调用点**没有一个**传 max_tokens，而纯 CPU 的 ollama
    # 上 9b 不开思考封顶时实测单次 >900s 不返回（09-25）。三个键默认"不注入"，
    # 于是没设 ETF_LLM_*/STOCK_LLM_* 的那条线拿到的客户端与改动前逐字一致。
    d["LLM_MAX_TOKENS"] = int(env("LLM_MAX_TOKENS", "0") or 0)
    # "none" 才真关得掉思考：/v1 兼容口下 think:false / thinking:{type:disabled} /
    # chat_template_kwargs 三种写法实测全部无效（09-25 i34/i34b）
    d["LLM_REASONING_EFFORT"] = env("LLM_REASONING_EFFORT", "").strip()
    # 上下文窗口＝模型一次能看进多少字。0＝不注入＝沿用 ollama 服务端默认
    # （本机 0.34.2 是 4096）。为什么要这把旋钮：10-03 逐字节量过官方支那条真提示词
    # 有 5502 token，而 4096 窗口只让它进 2050 ⇒ 63% 被丢。
    # 两条腿读不了同一个键（`LITELLM_*` 那套 pydantic 设置里根本没有窗口这项），所以这里是
    # **源头**，两套映射各走各的。10-04 四臂实测（`etf/v1/temp/check_num_ctx_v1_1004.log`，
    # 同一条 5502 token 真提示词、每臂两个独立读数对表）给这两套判了**不同**的结果：
    #   主线 → core/llm_client 曾塞 extra_body 的 options.num_ctx ⇒ **空转**：`/v1` 对裸发／options
    #     内给／顶层给三种写法一律只进 2050、驻留窗口恒 4096（静默截断不报错）
    #   official 支 → core/official_rdagent._driver_env 派生成 RDAGENT_LLM_KWARGS ⇒ **有效**：
    #     litellm 那腿走原生 `/api/chat`，同一句进 5502、驻留窗口真变 16384
    # 所以这把旋钮统一的是「配置只写一次」，不是「两腿窗口都一样宽」。⇒ **10-04 用户裁「乙」＝
    # 主线那支注入已拔掉**，本键从此**只**喂 official 支；主线不再假装自己拨过窗口，超线改由下面
    # 那枚铃铛出声。主线要真撑宽窗口只剩"换通路直连 `/api/chat`"那一条（丙），今天没选。
    # 驻留内存与 prefill 的实测代价记在下面 RDAGENT_LLM_KWARGS 那段。
    d["LLM_NUM_CTX"] = int(env("LLM_NUM_CTX", "0") or 0)
    # 超窗铃铛（10-04 用户裁「乙＋铃铛」里的那枚铃铛）：请求发出去**之前**数一遍这条提示词多少
    # **字符**，超过本值就往日志打一行 ⚠️。**只叫不改**——不动请求体、不拦、不报错、不重试。
    # 为什么按字符不按 token：主线没有 tokenizer，而字符→token 实测**跨句不可迁移**（10-04 三条
    # 真件 0.743／0.597／0.925）⇒ 只能拿最密那一句（0.925 token/字）把服务端现量输入线 ≈2050
    # token 换算成**偏保守的界**：2050 ÷ 0.925 ≈ 2200 字。真超没超以 ollama 上报的
    # `prompt_eval_count` 为准（尺子 `etf/v1/temp/check_mainline_prompt_fit_1004.py`），本行只是警铃。
    # 0＝不响＝默认。留 0 守的是那条老规矩：没配 `*_LLM_*` 的线拿到的客户端与改动前逐字一致。
    d["LLM_WARN_CHARS"] = int(env("LLM_WARN_CHARS", "0") or 0)
    # 墙钟上限（秒），0=不注入（沿用 SDK 默认）。防的是"一次调用挂住整批日更"
    d["LLM_TIMEOUT"] = float(env("LLM_TIMEOUT", "0") or 0)
    # 多角色前置假设闸（hypothesis_roles）：假设生成→批判→修正→定稿。
    # 默认关 ⇒ LLMFactorAgent 走原 _generate，产出逐字节不变；开启后每轮
    # 多 4~5 次 LLM 调用（16G 纯 CPU 的 9b 上就是多 1.5~2 分钟/轮）。
    # 之所以放在底座而不是各线 config：两条线暴露同名配置是这里的规矩，
    # 差异只落在 ETF_HYPOTHESIS_ROLES / STOCK_HYPOTHESIS_ROLES 的取值上。
    d["HYPOTHESIS_ROLES"] = env("HYPOTHESIS_ROLES", "").strip().lower() in (
        "1", "true", "on", "yes")

    return d
