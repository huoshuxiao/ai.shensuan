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

# ========== RD-Agent(Q) 工作区 ==========
# 官方循环的 cwd、.env（模型与模板覆盖开关）、回收产物 factors.json 都在这下面。
# 驱动只认这一个目录，ETF 线将来跑自己的循环时用同名的 etf 侧目录，互不覆写
RDAGENT_OUTPUT_DIR = os.path.join(RESULTS_DIR, "rdagent_output")
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
ASHARE_EVAL_OUT = os.path.join(RESULTS_DIR, "ashare_factor_eval.csv")

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
