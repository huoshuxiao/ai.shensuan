# -*- coding: utf-8 -*-
"""ETF 线配置（日线 + 分钟线轮动策略）

两线共享的配置名一律由 common/src/config_base.build() 生成后注入本模块的
globals()，此处只保留 **ETF 线专属**：资金与费率细则、ETF 池与筛选、
akshare 数据源降级顺序、轮动策略网格与风控搜索空间、看板、缓存文件名。

为什么能这样分：common/ 与两条线的模块都是 `from config import X`（裸模块名，
见 _bootstrap），所以同一个模块在 ETF 进程里拿到的就是本文件、在股票进程里
拿到的是 stock 的同名文件；底座保证两线暴露同名配置，差异只落在取值上。
"""

import os
import sys

# ========== 目录 ==========
# etf/v1 项目根目录（config.py 位于 etf/v1/src/config/ 下）
V1_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 共享内核路径：正常情况下入口脚本先 import _bootstrap 已经挂好，这里再补一次
# 是为了让「单独 import config」也能成立（少一层隐式顺序依赖）
_COMMON_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    V1_ROOT))), "common", "src")
if _COMMON_SRC not in sys.path:
    sys.path.append(_COMMON_SRC)

from config_base import build  # noqa: E402

# 本线的 .env 装载、DATA_DIR/RESULTS_DIR/REPORT_DIR/LOG_DIR 及其 ETF_* 覆盖、
# FREQ/LOOKBACK_BARS、挖掘/GP/LLM/因子库/RD-Agent 全套参数都在底座里
globals().update(build(V1_ROOT, env_prefix="ETF_"))

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
MIN_COMMISSION = 0.1

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
    # 这里刻意保持小池：轮动策略要的是可交易、低相关的少数标的，全市场 1.6 千只
    # 里一半以上是同指数的重复份额，放进轮动是噪声。RD-Agent 那侧不受此约束
    # （截面回归要的是样本量），它读 data/universe_all/ 的全市场池
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

# ========== RD-Agent(Q)（ETF 线 official 源默认关闭） ==========
# 本线已有自己的 qlib bin（data/qlib/qlib_data/cn_data）、daily_pv.h5 与工作区，
# 数据隔离经实测成立，但 09-22 那轮 coding 未收敛（7B 丢 MultiIndex 层级、
# CoSTEER_MAX_LOOP=4 耗尽）导致 running 被跳过、回收定义带不到官方 IC，故默认
# 仍关；要连主线一起跑用 ETF_RDAGENT_OFFICIAL_FALLBACK=true 临时打开。
# 取舍与补证路径见用户使用手册 §10.1
RDAGENT_USE_OFFICIAL_FALLBACK = os.environ.get(
    "ETF_RDAGENT_OFFICIAL_FALLBACK", "false").lower() in ("1", "true", "yes")

# ========== Walk-forward ==========
WALK_FORWARD = {"enabled": True, "n_splits": 3,
                "train_ratio": 0.7,
                "embargo_bars": LOOKBACK_BARS // 4,
                # 折内挖掘引擎：与主链同构，样本外验证才覆盖得到 GP/DSL 因子。
                # registry=注册表基线，genetic=字符串 DSL 遗传编程（折内成本主力），
                # multi_source=多源（含 RD-Agent/LLM，默认关闭：每折一次外部调用）
                "fold_engines": ["registry", "genetic"]}

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

# ========== 因子衰减监控（strategy_lifecycle.FactorDecayMonitor） ==========
FACTOR_DECAY = {
    "enabled": True,
    "ic_window_bars": 252 if FREQ == "daily" else 2400,
    "ic_min_threshold": 0.01,
    "halflife_warn": 500 if FREQ == "daily" else 4800,
    "consecutive_decay_rounds": 2,
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

# ========== 风控（本线专属部分，公共 RISK_CONTROL 在底座） ==========
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

# ========== PBO 演化 ==========
PBO_TIMELINE = {
    "enabled": True,
    "window_bars": LOOKBACK_BARS * 20,
    "step_bars": LOOKBACK_BARS * 5,
    "n_splits": 8, "max_combinations": 1000,
    "trend_window": 5, "trend_threshold": 0.1,
    "pbo_warn": 0.5, "pbo_danger": 0.7,
}

# ========== LLM 研究计划 ==========
LLM_RESEARCH_PLANNER = {"enabled": True, "days": 3,
                        "max_candidates": 5}

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
# 全市场 ETF 日线（约 1.6 千只，剔货币/债/理财后）：只喂 dump_qlib_bin.py 与
# RD-Agent(Q) 循环，主线轮动池仍是上面的 max_count 截断池，两者互不读取
UNIVERSE_ALL_DIR = os.environ.get(
    "ETF_UNIVERSE_ALL_DIR", os.path.join(os.path.dirname(CACHE_DIR),
                                         "universe_all"))
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
