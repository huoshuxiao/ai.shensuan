# -*- coding: utf-8 -*-
"""股票线（A 股个股）配置

职责边界：股票线目前只做**因子研究**（RD-Agent(Q) + 本地 LLM 产因子 →
A 股全市场截面评估），不复用 ETF 线的轮动策略/分钟线/实盘那套。
两线共享的参数由 common/src/config_base.build() 按「本线根目录 + STOCK_
前缀」生成，此处只写股票线专属：qlib 数据与产物路径、A 股交易规则与费率、
截面评估参数。

数据根目录刻意留在 RD-Agent 自己的工作区里（daily_pv.h5 是官方循环实现因子时
读的同一份源数据）：股票线与 RD-Agent 用同一份数据，评估结论才可对照。
"""

import os
import sys

# stock/v1 项目根目录（config.py 位于 stock/v1/src/config/ 下）
V1_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_COMMON_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    V1_ROOT))), "common", "src")
if _COMMON_SRC not in sys.path:
    sys.path.append(_COMMON_SRC)

from config_base import build  # noqa: E402

# market="ashare"：因子库每条记录都盖这个截面口径戳。A 股个股池（5000+ 只、
# 含涨跌停与停牌）算出的 IC 与 ETF 轮动池的 IC 不可比，靠此字段隔离
globals().update(build(V1_ROOT, env_prefix="STOCK_", freq_default="daily",
                       market="ashare"))

# ========== 股票线专属数据路径 ==========
# RD-Agent 工作区目录 RDAGENT_OUTPUT_DIR 已由 config_base 按本线 RESULTS_DIR
# 生成（两线同名不同值，循环的 cwd/.env/源数据都在它下面，互不覆写）
# 因子实现与 A 股截面评估共用的源数据（qlib 社区包 A 股全市场日线）
ASHARE_DAILY_H5 = os.environ.get(
    "STOCK_DAILY_H5",
    os.path.join(RDAGENT_OUTPUT_DIR, "git_ignore_folder",
                 "factor_implementation_source_data", "daily_pv.h5"))

# ========== A 股截面评估参数 ==========
# 2010 起：与 ETF 线回测起点一致，便于两线指标同期对比
ASHARE_EVAL_START = os.environ.get("STOCK_EVAL_START", "2010-01-01")
ASHARE_EVAL_END = os.environ.get("STOCK_EVAL_END", "")        # 空 = 数据尽头
ASHARE_MIN_OBS = int(os.environ.get("STOCK_MIN_OBS", "250"))  # 单标的最少交易日
ASHARE_MIN_CS = int(os.environ.get("STOCK_MIN_CS", "100"))    # 单日截面最少样本
ASHARE_SAMPLE = int(os.environ.get("STOCK_SAMPLE", "0"))     # >0 只取前 N 只（冒烟）
ASHARE_EVAL_OUT = os.environ.get(
    "STOCK_EVAL_OUT", os.path.join(RESULTS_DIR, "ashare_factor_eval.csv"))
# 冒烟轮必须能改写落点：09-23 一次 STOCK_SAMPLE=80 的冒烟把全市场结论
# stock/v1/data/results/ashare_factor_eval.csv 盖掉了（旧写法不接受 env 覆写，
# 已 git 还原）。以后冒烟一律带 STOCK_EVAL_OUT=/tmp/...。

# ========== 判重预检参数（run_ashare_redundancy_check.py） ==========
# 0.99 不是本地阈值，是 rdagent 写死的：scenarios/qlib/developer/factor_runner.py
# 的 deduplicate_new_factors 把「与任一 SOTA 因子的逐日截面相关均值 >= 0.99」的
# 新因子整列丢掉，全丢完就抛 FactorEmptyError → 日志里表现为 Skip loop，一轮白烧。
ASHARE_RED_BAR = float(os.environ.get("STOCK_RED_BAR", "0.99"))
# 起点 2015 同组合层：判重要的是「新因子进了 SOTA 会不会被丢」，而 SOTA 面板
# 是拼接后的宽表，2010-2014 早段大量标的未上市会让逐日截面稀、相关虚高
ASHARE_RED_START = os.environ.get("STOCK_RED_START", "2015-01-01")
ASHARE_RED_END = os.environ.get("STOCK_RED_END", "")
ASHARE_RED_OUT = os.environ.get(
    "STOCK_RED_OUT", os.path.join(RESULTS_DIR, "ashare_redundancy_check.csv"))
ASHARE_RED_DETAIL = os.environ.get(
    "STOCK_RED_DETAIL",
    os.path.join(RESULTS_DIR, "ashare_redundancy_detail.csv"))

# ========== 交易规则与费率（A 股个股，与 ETF 明显不同） ==========
# T+1：当日买入不可当日卖出；整手 100 股
T_PLUS_ONE = True
LOT_SIZE = 100
# 佣金万 2.5 双边（ETF 线是可谈到 1bp 的，故在底座外覆写）
COMMISSION_RATE = 0.00025
MIN_COMMISSION = 5.0
SLIPPAGE = 0.001
# 印花税只在卖出侧计（2023-08 起 0.05%；此处按卖出费率单列，
# 供将来股票线自己做净收益回测时使用，ETF 线无此项）
STAMP_TAX_RATE_SELL = 0.0005

# ========== 组合层验证参数（run_ashare_portfolio_eval.py） ==========
# 起点 2015 而非 2010：截面评估用的面板含 5677 只（含已退市），但 2010-2014
# 的可交易池与今日结构差异过大（板指/创业板早期样本稀薄），组合层结论按
# 「近十年」给；同期还含 2015 流动性危机与 2016 熔断，是有用的压力段
ASHARE_PORT_START = os.environ.get("STOCK_PORT_START", "2015-01-01")
ASHARE_PORT_END = os.environ.get("STOCK_PORT_END", "")           # 空 = 数据尽头
# 因子窗口最长 20 日 + 成交额均值 20 日，切盘前多留这么多日历日做暖机
ASHARE_PORT_WARMUP_DAYS = int(os.environ.get("STOCK_PORT_WARMUP", "120"))
ASHARE_PORT_TOP_N = [int(x) for x in
                     os.environ.get("STOCK_PORT_TOP_N", "50,100,200").split(",")]
ASHARE_PORT_HOLD = int(os.environ.get("STOCK_PORT_HOLD", "5"))   # 调仓周期（交易日）
ASHARE_PORT_MIN_AMOUNT = float(os.environ.get("STOCK_PORT_MIN_AMOUNT", "2e7"))
ASHARE_PORT_MIN_LISTED = int(os.environ.get("STOCK_PORT_MIN_LISTED", "60"))
# 单边综合费率 15bp = 佣金万2.5 + 印花税万5按卖出摊半(万2.5) + 滑点万10。
# 印花税 2023-08-28 才从 0.1% 减半到 0.05%，样本大头在旧税率下，故不按新税率取
ASHARE_PORT_COST_ONE_WAY = float(os.environ.get("STOCK_PORT_COST", "0.0015"))
ASHARE_PORT_LIMIT_UP = float(os.environ.get("STOCK_PORT_LIMIT_UP", "0.095"))
ASHARE_PORT_QUINTILES = int(os.environ.get("STOCK_PORT_QUINTILES", "5"))
# 输出路径可覆写：冒烟轮写到 /tmp，免得把正式结论盖掉（同 ASHARE_EVAL_OUT 的约定）
ASHARE_PORT_OUT = os.environ.get(
    "STOCK_PORT_OUT", os.path.join(RESULTS_DIR, "ashare_portfolio_eval.csv"))

# ========== 筛选层参数（strategy/ashare_screen.py，两入口共用） ==========
# 闸门三参数（次新/容量/涨停）故意沿用上面的 ASHARE_PORT_* 名字：它们诞生于组合层，
# 日频信号与回测必须共用一份，改名或另立一套就会出现「回测里能过、实盘名单里没有」
ASHARE_SCREEN_QUANTILE = float(os.environ.get("STOCK_SCREEN_QUANTILE", "0.8"))
# 启用哪几条量能构造（level/volatility/momentum/ratio），默认全开取并集剔除
ASHARE_SCREEN_RULES = os.environ.get(
    "STOCK_SCREEN_RULES", "level,volatility,momentum,ratio")
# ②b 日频信号名单的落盘目录（不进 RESULTS_DIR 根，免得和一次性研究产物混在一起）
ASHARE_SIGNAL_DIR = os.environ.get(
    "STOCK_SIGNAL_DIR", os.path.join(RESULTS_DIR, "daily_signal"))

# ========== 手动成交账本（②c run_ashare_position.py） ==========
# 成交由人工录入到 CSV，系统只读不写账本；放在 LIVE_DATA_DIR 而不是 RESULTS_DIR，
# 因为它是**用户数据**（丢了就没了），而 results/ 全是可重算的研究产物
ASHARE_FILLS_CSV = os.environ.get(
    "STOCK_FILLS_CSV", os.path.join(LIVE_DATA_DIR, "manual_fills.csv"))
ASHARE_POSITION_OUT = os.environ.get(
    "STOCK_POSITION_OUT", os.path.join(LIVE_DATA_DIR, "positions.csv"))
ASHARE_ACCOUNT_OUT = os.environ.get(
    "STOCK_ACCOUNT_OUT", os.path.join(LIVE_DATA_DIR, "account.csv"))
# 录入价与当日盘面收盘价的偏离超过这个比例就报警（创业板/科创板限 20%，留 1% 容差，
# 只抓「代码打错 / 价格小数点点错」这类录入事故，不做投资决策判断）
ASHARE_FILL_PRICE_TOL = float(os.environ.get("STOCK_FILL_PRICE_TOL", "0.21"))
