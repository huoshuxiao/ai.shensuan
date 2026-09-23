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
    # line_root = 本线项目根（etf/v1 或 stock/v1），所有产物目录由它派生，
    # 两条线的 data/ report/ log/ 因此天然物理隔离
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
    d["CACHE_DIR"] = os.path.join(d["DATA_DIR"], "cache")
    d["LIBRARY_DIR"] = os.path.join(d["DATA_DIR"], "library")
    d["LOG_DIR"] = env("LOG_DIR", os.path.join(line_root, "log"))
    for _p in ("DATA_DIR", "RESULTS_DIR", "REPORT_DIR", "LIVE_DATA_DIR",
               "CACHE_DIR", "LIBRARY_DIR", "LOG_DIR"):
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
    # qlib 行情数据根：物理落位在**本线自己的** data/qlib/qlib_data/cn_data，
    # 两条线各自一份 bin，不再共用宿主的 ~/.qlib（A 股个股数据曾串到 ETF 线）。
    # 为什么必须保留 qlib_data/cn_data 这两级尾巴：rdagent 的 QTDockerEnv.prepare
    # 拿挂载目录拼 <mount>/qlib_data/cn_data 做存在性检查，缺了就在容器里联网
    # 重拉数据；而容器侧真正开数据的 provider_uri 在官方模板里是写死的
    # /root/.qlib/qlib_data/cn_data（无 env 可改），所以只能搬宿主挂载点、
    # 内部两级结构原样保留。它同时被 official_rdagent 的前置体检读（判存在性）。
    _qlib_provider = env(
        "RDAGENT_QLIB_PROVIDER",
        os.path.join(os.path.abspath(d["DATA_DIR"]),
                     "qlib", "qlib_data", "cn_data"))
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

    return d
