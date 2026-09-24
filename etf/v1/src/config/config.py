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
# 兜底池不再手写名单：全部行情源都挂时，直接用本地已缓存的日线目录
# （data/cache/ 与 data/universe_all/）里现存过的代码，程序按数据说了算。
FALLBACK_UNIVERSE = []
FALLBACK_SOURCES = [CACHE_DIR, os.path.join(os.path.dirname(CACHE_DIR),
                                            "universe_all")]

ETF_FILTER = {
    # 成立不满 1 年的 ETF 不入池（365 自然日）：新基金建仓期收益不代表策略有效域
    "min_list_days": 365,
    # 流动性下限。口径已从「现货快照的当日成交额单点值」改成「近
    # amount_window 日成交额中位数」——一天异动/一天放量不再决定进出。
    # 取不到本地日线的候选退回快照值判定（见 etf_universe.build 计数提示）。
    "min_avg_amount": 5_000_000,
    "amount_window": 20,
    # 波动下限：取代原来的「按名称关键字（货币/短融/日利…）与代码段（511）
    # 拉黑」。货币/理财/债性品种近 vol_window 日的年化波动天然远低于此值，
    # 剔除理由由实测波动给出而不是名字，也就不再误伤同段的高波动品种。
    "min_ann_vol": 0.05,
    "vol_window": 60,
    # 截面轮动池规模上限。仍守「每指数取 1 只代表」的去重，放宽的是
    # **可选的指数个数**（20 → 100），不是同指数的第二三名重复份额——
    # 后者与「把指数拉进截面」是同一个共线错误。日均截面厚度是 IC/DSR/PBO
    # 有没有统计功效的前提，20 只池撑不起横截面结论。
    "max_count": 100,
    # 人工紧急兜底：默认空，正常准入判定全部由上面的数据阈值给出
    "exclude_keywords": [],
    "exclude_prefixes": [],
    # 当日可交易性（第三条判据，之前只有「在池内 + 上市满 365 天」两条）：
    # 当日无 bar 或当日成交额低于此值视为不可买入（卖出仍允许，用最近价）
    "min_daily_amount": 1_000_000,
}

# ========== 截面组合（策略形态） ==========
# 原形态是「每根 bar 只持有得分最高的 1 只、全进全出」：16 年只成交 12 笔，
# DSR 的期望最大夏普与 PBO 的 CSCV 都没有足够独立观测可用（实测 DSR=0、
# PBO 配置族 logits_std≈0）。改为按分数取前 top_k 只的横截面组合。
PORTFOLIO = {
    "top_k": 10,
    # equal: 1/k；score_prop: 按正分数占比（负分不参与，等价于绝对过滤）
    "weighting": "equal",
    # 单标的权重上限（占组合净值比例，超出部分按比例回摊给其余持仓）
    "max_weight": 0.30,
    # 绝对收益过滤：综合分 <= 此值的标的不入组合（全被过滤即空仓）
    "min_score": 0.0,
    # 单次调仓换手上限（占净值，Σ|Δ权重|/2 口径）。超出时按权重变动幅度
    # 从大到小裁剪，先把最必要的变动做完
    "max_turnover": 0.60,
    # 最小交易单位（份）。ETF 场内申报 100 份起，回测此前按小数股成交
    "lot_size": 100,
}

# 底座 RISK_CONTROL 的 max_trades_per_day 在日线侧默认 1，那是「单标的
# 全进全出」时代的取值；截面组合一次调仓最多 2·top_k 笔。留在 1 有两个后果：
# 一是 OFAT 风控配置族的基线落不进搜索空间（基线值不是空间里的某一档，
# 该维度白多出一档变体），二是研究调参经 run_live.RISK_KEY_MAP 映射到实盘时
# 把 max_orders_per_day 又压回 1 单，组合根本建不起来。
RISK_CONTROL["max_trades_per_day"] = PORTFOLIO["top_k"]

# ========== 行情数据源（按优先级降级） ==========
# em=东方财富(akshare)  sina=新浪  tx=腾讯
DATA_SOURCES = {
    "daily": ["em", "sina", "tx"],
    "intraday": ["em", "tx"],
}

# ========== RD-Agent(Q)（ETF 线） ==========
# 本线有自己的 qlib bin（data/qlib/qlib_data/cn_data）、daily_pv.h5 与工作区，
# 数据隔离经实测成立。09-23 上午那轮（11:27 落盘）已跑通到 running 步并带回
# 官方 IC（`ma(df,5)`、`ts_mean(volume,10)` 各 IC≈0.0085），故 official 源
# 默认与股票线一致地打开；不想让主线带上这 ~50min 循环时置
# ETF_RDAGENT_OFFICIAL_FALLBACK=false。取舍与取证见用户使用手册 §10.1
RDAGENT_USE_OFFICIAL_FALLBACK = os.environ.get(
    "ETF_RDAGENT_OFFICIAL_FALLBACK", "true").lower() in ("1", "true", "yes")
# RD-Agent 数据新鲜度比对基准：dump_qlib_bin 的输入池是全市场 ETF 缓存
# （data/universe_all/，871 只），不是主线那 20 只代表池 data/cache/——
# 拿 cache 比对会让体检天天报"过期"，因为主线缓存只按需刷新十几只。
RDAGENT_SOURCE_DIR = os.environ.get(
    "ETF_RDAGENT_SOURCE_DIR", os.path.join(DATA_DIR, "universe_all"))
# coding 阶段演化轮数。09-22 那轮 7B 把 result.h5 写成只剩 datetime 一层索引，
# 4 轮耗尽仍没过格式 critic → running 被跳过、回收不到官方 IC；critic 反馈是
# 逐轮累积的，多给轮数是最可能收敛的单一旋钮（不动数据、不改生成代码）。
# 经 official_rdagent 注入子进程环境变量，压过 rdagent_output/.env 里那份 4。
RDAGENT_COSTEER_MAX_LOOP = os.environ.get("ETF_RDAGENT_COSTEER_MAX_LOOP", "8")

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
# max_trades_per_day 的计数单位是「笔」。策略形态改成 top_k 只的截面组合后，
# 一次完整调仓最多 2·top_k 笔（卖出掉出名单的 + 买入新进名单的），
# 沿用单标的轮动时代的 1~3 笔会让调仓次日起几乎禁止一切买入——
# 闸门与形态彼此矛盾；这里按「够做完一次调仓」的量级放开。
# 频控仍主要由 min_bars_between_trades + cooldown_days 承担。
RISK_SEARCH_SPACE = {
    "daily_stop_loss": [-0.02, -0.03, -0.05],
    "max_drawdown_stop": [-0.08, -0.10, -0.15],
    "cooldown_days": [1, 3, 5],
    "min_bars_between_trades": [1, 3, 5] if FREQ == "daily"
                                else [3, 5, 10],
    "max_trades_per_day": [PORTFOLIO["top_k"], 2 * PORTFOLIO["top_k"],
                           3 * PORTFOLIO["top_k"]] if FREQ == "daily"
                          else [2 * PORTFOLIO["top_k"],
                                3 * PORTFOLIO["top_k"],
                                5 * PORTFOLIO["top_k"]],
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
