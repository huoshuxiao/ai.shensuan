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
