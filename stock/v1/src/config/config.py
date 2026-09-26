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

# RD-Agent(Q) factor 循环回收下来的因子库（name/expr/formulation/4 个沙箱 IC）。
# 路径集中在这里而不是各入口自己拼：截面评估、盘前名单、看板三处都要读它，
# 拼三次就会有三份「哪条因子进了判据」的口径
ASHARE_FACTORS_JSON = os.environ.get(
    "STOCK_FACTORS", os.path.join(RDAGENT_OUTPUT_DIR, "factors.json"))

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

# ========== 「稳不稳」秤参数（run_ashare_rolling_ic.py） ==========
# 三个落点全是**新文件**：⑳ 那套归档基线（ashare_factor_eval.csv /
# ashare_redundancy_check.csv）一个字节都不动。
ASHARE_ROLLING_OUT = os.environ.get(
    "STOCK_ROLLING_OUT", os.path.join(RESULTS_DIR, "ashare_rolling_ic.csv"))
# 逐日截面 IC 宽表（date × (Rank, Pearson) × 因子，21 因子 × 4059 天，全市场实测 3.5MB）。
# 落盘是为了「换窗口/换判据不再重扫面板」——扫一次面板 738s 才是成本，窗口统计只要百毫秒。
ASHARE_ROLLING_DAILY = os.environ.get(
    "STOCK_ROLLING_DAILY", os.path.join(RESULTS_DIR, "ashare_ic_daily.csv"))
# 因子 × 自然年 的 IC 明细（哪一年掉的、最差那年掉到多少，看这张表）
ASHARE_ROLLING_YEARLY = os.environ.get(
    "STOCK_ROLLING_YEARLY", os.path.join(RESULTS_DIR, "ashare_ic_yearly.csv"))
# 滚动窗 = 252 个交易日 ≈ 1 自然年：读数直接当「这一年的 IC」念
ASHARE_ROLL_WINDOW = int(os.environ.get("STOCK_ROLL_WINDOW", "252"))
# 等分折数：按时间顺序切成 5 段各算一次 IC，看的是「段段同号」还是「靠一年撑起来」
ASHARE_ROLL_FOLDS = int(os.environ.get("STOCK_ROLL_FOLDS", "5"))
# 「近 N 年」窗（自然年），用来和全样本比符号是否还站得住
ASHARE_ROLL_RECENT_YEARS = int(os.environ.get("STOCK_ROLL_RECENT_YEARS", "3"))

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
# P0（09-24）「并集到底值不值」的两份产物，**另立文件而不是往主表添行**：主表
# 每行的 q5_ann / excl_worst_* 是「单因子五等分」口径，添进形态不同的行会让同一
# 列名出现两种含义，看板与 CHANGELOG 引用数字时分不清踩的是哪一条
#   exclusion —— 减法组合：剩余池等权 相对 不剔除等权 的年化增益（费率近零，毛收益）
#   buylist   —— 待买入短名单（安静度 top ASHARE_BUY_TOP_N）在不同剔除集下的扣费表现
ASHARE_EXCL_OUT = os.environ.get(
    "STOCK_EXCL_OUT", os.path.join(RESULTS_DIR, "ashare_portfolio_exclusion.csv"))
ASHARE_BUYLIST_OUT = os.environ.get(
    "STOCK_BUYLIST_OUT", os.path.join(RESULTS_DIR, "ashare_portfolio_buylist.csv"))

# ========== 筛选层参数（strategy/ashare_screen.py，两入口共用） ==========
# 闸门三参数（次新/容量/涨停）故意沿用上面的 ASHARE_PORT_* 名字：它们诞生于组合层，
# 日频信号与回测必须共用一份，改名或另立一套就会出现「回测里能过、实盘名单里没有」
ASHARE_SCREEN_QUANTILE = float(os.environ.get("STOCK_SCREEN_QUANTILE", "0.8"))
# 启用哪几条量能构造（level/volatility/momentum/ratio），默认全开取并集剔除
ASHARE_SCREEN_RULES = os.environ.get(
    "STOCK_SCREEN_RULES", "level,volatility,momentum,ratio")
# 名单层的**独立**额外闸（09-26 接；判据本体在 strategy/ashare_screen.BUY_EXTRA_RULES）。
# 它与上面四条的差别在**用途**不在强度：不进剔除并集、不改「不该买」那张域，只在待买入
# 名单入口单独一票否决。为什么必须独立成闸（配额档 top50、570 个调仓日、扣双边 15bp，
# `shell/stock/a158_five_rule_ctrl_0926.py` → 产物 `shell/stock/tmp_alpha158/five/`）：
#   现口径（并集 4 条≥3 挡名单）              +0.28%/年　单程换手 0.224
#   改成并集 5 条（≥1 / ≥2 / ≥3 / ≥4 / ≥5） -3.22 / +0.00 / +0.04 / +0.16 / +0.08%
#   独立叠在名单层（现≥3 且 本条不命中）      +1.04%/年　单程换手 0.334
# 也就是说这条构造**只有**不参与「命中几条」的计数时才为正；塞进并集会把共识闸整体抬高
# 一格，把现口径那道保险冲掉。空串 = 关掉本特性（回到 09-26 之前的口径），键名见上表。
# ⚠️ +1.04% 是**样本内**读数（2010~2026 全窗、只量过 top50 这一档），别当承诺。
ASHARE_BUY_EXTRA_RULES = os.environ.get("STOCK_BUY_EXTRA_RULES", "low0")
# 量能构造吃哪一套成交量口径。面板的 $volume 不是真实手数，而是**复权成交量**
# = 真实手数 / $factor（09-23 探针 shell/stock/probe_live_sources4_0923.py 逐票证成：
# 54 只票跨 $factor 0.007~1.24，V·f/L=1.0000 全中，corr(log(V/L), log f) = -1.000）。
# 默认 "adj" = 面板原值，与历史基线、RD-Agent 沙箱、factors.json 的 expr 同一套口径；
# "real" 把 DSL 里的 volume 绑成 $volume*$factor（真实手数），只为口径复核对照跑而设，
# 换它等于换因子定义，不与已入库结论混用
ASHARE_VOL_BASIS = os.environ.get("STOCK_VOL_BASIS", "adj")
# ②b 日频信号名单的落盘目录（不进 RESULTS_DIR 根，免得和一次性研究产物混在一起）
ASHARE_SIGNAL_DIR = os.environ.get(
    "STOCK_SIGNAL_DIR", os.path.join(RESULTS_DIR, "daily_signal"))
# 待买入名单的行数。默认 50 不是「人工下得完」的刻度，是**验证过的最小持仓规模**：
# 组合层只跑过 top50/100/200 三档（ASHARE_PORT_TOP_N），排到 20 名就落在任何一次
# 回放都没验过的更小尾部 —— 而低分侧尾部恰恰是最冷门、最小市值那一角，
# 「买冷门的 beta」这个质疑在尾部最凶。资金量小就按名单的一手金额从上往下截断，
# 截断是可执行性调整，改排序轴才是改结论
ASHARE_BUY_TOP_N = int(os.environ.get("STOCK_BUY_TOP_N", "50"))
# ========== 观察名单的**生成规则**（09-25 选项E，用户拍「改」之后落地） ==========
#   global = 全市场按安静度升序取前 ASHARE_BUY_TOP_N 只。这是 09-25 之前的口径，
#            那批账单另存 `ashare_portfolio_*_globalrank.csv`（照 `*_flat`/`*_dated` 先例）。
#   quota  = 先给每个板块留席位，段内仍按**同一根轴**取最安静的那几只；某段凑不满就用
#            全局次安静的补满（回填）。排序轴一字未改，改的是「谁有资格进名单」。
# 实测账单**分两层，方向是反的**，两边都要看：
#  · **下单层**（5 席满仓，`quota_watchlist_m4_0925{,_yearly}.csv`，570 个调仓日）：
#    年化超额 +11.70% → **+13.04%**、IR 0.48 → 0.55、下单席位凑满率 75.3% → **100%**、
#    扫描深度 30.6 → 28.3、单程换手 0.307 → 0.270，分年度 12 年里 10 年更好（输在
#    2018 −1.5pp、2025 −3.3pp），负超额年数 4 → 3。
#  · **50 只观察篮子本身**（同一根轴等权 50 只，`ashare_portfolio_eval.csv` 的
#    `list_scheme=quota` 行 vs 同日对照 `ashare_portfolio_eval_globalctrl_0925.csv`）：
#    年化超额 +1.55% → **+0.11%**（**−1.44pp/年**）、IR +0.12 → **+0.01**、
#    换手 0.186 → 0.223。只有最大回撤变好（−52.5% → −49.1%）。
# 反号的机制：配额花「安静度」买「板块分散」—— 名单尾部换进来的是没那么安静的创业板
# 票（单一板块占比 0.94 → 0.80），50 只平摊时代价摊满整张篮子；而下单层只扫最前十几名，
# 配额恰好把科创/创业/北交提到前排、让 5 席一次凑满，那一段拿到好处。
# ⚠️ 由此翻掉一条旧结论：09-24 换轴（STD(Vol,20) → SMA(Vol,20)）当时说「新轴三档都不差于
# 旧轴」，570 天重算 top50 档 全局名单 +1.55% vs 旧轴 +1.09%（新轴赢）、**配额名单
# +0.11% vs 旧轴 +1.00%（旧轴赢，新轴只剩换手只有一半这一条）**。top100/200 两档在
# 两种名单下都是旧轴更高（这一点当时就写明承认过），所以这不是新 bug，是原本就窄的
# 证据被配额踩到了。用户 09-25 看到这笔后仍选「配额当默认」，理由记在 CHANGELOG ㉘。
# **这条判据的真实身份要写清楚**：配额那张表是**够不满**的 —— 科创板 570 天里
# **570 天**填不满 10 席（那 50 只里平均只有 **0.51** 只科创板）、北交所 494/570
# （10 → 2.2）。所以 E-a 干的事不是「四段均衡」，而是「**给创业板留 10 个保底席位**」
# （名单内创业板 2.7 → 9.7 只），名单最大板块占比也只从 0.94 降到 0.80。
# 三个配额形状（20/10/10/10、每段 12、不回填）收益**逐格相同** ⇒ 唯一的自由度就是
# 「创业板上限给几只」，调其它数字是白调。配额高于某段实际供给的部分没有效果，
# 但也**没有坏处**（回填会补满），所以保留 4 段全写、不改成只写创业板一条。
ASHARE_LIST_SCHEME = os.environ.get("STOCK_LIST_SCHEME", "quota")
if ASHARE_LIST_SCHEME not in ("global", "quota"):
    raise SystemExit(f"[config] STOCK_LIST_SCHEME 只能是 global|quota，收到 "
                     f"{ASHARE_LIST_SCHEME!r}（拼错宁可报错，不静默退回默认档 —— "
                     f"名单层两套口径混在一张表里没人看得出来，正是 gate 那条注释防的事）")
# 席位表按 board_of 的四档写死顺序，env 用 "主板/创业/科创/北交" 四段斜杠串覆写
_LIST_QUOTA_RAW = os.environ.get("STOCK_LIST_QUOTA", "20/10/10/10").split("/")
if len(_LIST_QUOTA_RAW) != 4 or not all(x.strip().lstrip("-").isdigit()
                                        for x in _LIST_QUOTA_RAW):
    raise SystemExit(f"[config] STOCK_LIST_QUOTA 必须是四段整数「主板/创业板/科创板/"
                     f"北交所」，收到 {os.environ.get('STOCK_LIST_QUOTA')!r} —— 少写一段"
                     f"会被 zip 静默截断，那一整段就变成「0 个席位」，名单形状整个换掉")
ASHARE_LIST_QUOTA = dict(zip(("主板", "创业板", "科创板", "北交所"),
                             (int(x) for x in _LIST_QUOTA_RAW)))
if sum(ASHARE_LIST_QUOTA.values()) > ASHARE_BUY_TOP_N:
    raise SystemExit(f"[config] 席位合计 {sum(ASHARE_LIST_QUOTA.values())} > "
                     f"ASHARE_BUY_TOP_N {ASHARE_BUY_TOP_N} ⇒ 配额从「保底」变成「上限"
                     f"封顶」，越靠后的段越可能被整段挤掉。要么拨名单行数，要么收席位")
# P3（09-24 用户选）：同一套量能剔除，在两个位置用两个强度。
#   域/展示（signal_*.csv 的 keep 列、看板「不该买」页）= 并集，命中 >=1 条即剔
#   待买入名单（buy_*.csv 的入口闸）= 命中 >= 本值 条才挡
# 依据是 09-24 的 ⑮P0 实测 + ⑳㉑ 的两次重跑，09-25 ㉘ 又按**配额名单**重跑一次
# （data/results/ashare_portfolio_exclusion.csv 与 _buylist.csv，**涨停闸 `board` 档
# = 现在的默认档**；flat 档另存 `*_flat.csv`、dated 档另存 `*_dated.csv`、全局名单那批
# 另存 `*_globalrank.csv`（569 个调仓日）+ `_globalctrl_0925.csv`（今天面板、570 天）。
# 换闸的钱：39 行 |Δ年化超额| 中位 0.044~0.050pp、最大 0.25pp、零符号翻转 —— 这一句是
# **全局名单 + 569 天**那批的实测，配额档下换名单形状的钱大得多（见上面 ASHARE_LIST_SCHEME
# 那段），但那是换一个口径、不是换一档阈值，两件事别混着引）：
# 并集在**减法腿**完胜单条（+6.46%/年 vs 最佳单条 +4.37%，边际
# 水平 +1.11 > 动量 +0.72 > 波动 +0.25 > 比 -0.03pp。⚠️ 减法腿剔的是整个可投资域，
# 与名单形状无关，所以这四个数在 569 天/570 天、全局/配额之间一字未动，可放心引），
# 但把同一张掩码压在**待买入名单**上是净负。名单腿这一半换过两次口径：09-24 ㉑ 换轴
# （排序轴 STD(Vol,20) → SMA(Vol,20)，与 `level` 那条剔除构造变成同一条表达式），
# 09-25 ㉘ 换名单形状（全局前 50 → 配额）。**下面是现轴 + 现名单形状（配额）的账单**，
# 前两批分别另存 `ashare_portfolio_buylist_globalctrl_0925.csv`（同网格全局档）与
# `ashare_portfolio_buylist_std20axis.csv`（旧轴）可查：
#   参照·不剔除 +0.11%（IR +0.01、单程换手 0.223）
#   并集≥1 -2.79%（换手 0.465）　并集≥2 -0.66%（换手 0.286）
#   并集≥3 +0.28%　并集≥4 +0.12%
#   单条：水平 +0.51%　波动 +0.30%　动量 -1.85%　比 -2.08%
#   留一：去掉水平 -2.86%　去掉波动 -2.68%　去掉动量 -2.01%　去掉比 -1.65%
# 即并集压在名单上比不挡差 **2.9pp/年**（全局档同网格是 1.55% → -3.78%，差 5.3pp ——
# 换了名单形状这一半的钱**缩小了**，但方向和「挡得越狠越亏」的顺序一字没变）。
# ⚠️ 09-24 那句「恒空操作」在配额档下**作废**，机制要说清：
#   · 全局档确实逐字相同（「单条·量能水平」与参照差 0.0e+00、「单条·量能波动」差 1e-6、
#     两个「留一」行与「并集≥1」差 0.0e+00）—— 一根轴的低分端不可能同时是自己的高分端；
#   · 配额档下这两行**反超参照**（+0.51% / +0.30% vs +0.11%）。理由不是构造变强了，
#     而是剔除改变的是「**回填席位补进谁**」：把高分端（最吵闹）的票从池子里拿掉，
#     主板 20 席之后的回填位就换成了更安静的票 ⇒ 名单整体更安静一点。量级 0.2~0.4pp，
#     在 IR 0.01~0.05 这种噪声水位上读不出方向，只够用来否定「恒空操作」这句死话；
#   · 两个「留一」行仍然贴着「并集≥1」（差 0.08pp / 0.10pp，不再是 0）⇒ 拆掉这两条
#     毫无损失的结论 survive；
#   ⇒ 名单上的伤害仍然**全部**来自量能动量（相对参照 -1.96pp）与量能比（-2.19pp）
#     这两条近乎正交的构造。
# 取 3 的理由（配额档重量，结论不变、措辞再改准一档）：≥3 比参照高 0.17pp、≥4 高
# 0.01pp、换手 0.224 vs 0.223 ⇒ 这一档挪动的名单只数少到读不出方向。但**不再逐字
# 相同**（全局档下 ≥3 差 1e-6、≥4 才是 0），≥4 与参照也只差 8e-5。也就是说这一道闸
# 从「没咬过票的保险」变成「偶尔咬一只、咬了几乎不花钱的保险」，仍然不是调出来的参数。
# 哪一天换掉的是哪一只，需要逐日期持仓 dump 才能定位，本轮没跑（CHANGELOG ㉑ 遗留）。
# 再往下拨一档代价陡增：命中≥2 那档把超额从 +0.11% 打到 -0.66%（换手 0.223 → 0.286）。
# 定成 1 = 关闭本特性，回到「两处同一并集」的旧口径，实测 -2.79%
ASHARE_BUY_MIN_HITS = int(os.environ.get("STOCK_BUY_MIN_HITS", "3"))

# ========== 板块限幅与下单层（B 路，09-24 用户选） ==========
# 用户 09-24 明确：**个人账户科创板 / 创业板 / 北交所均有权限**。这条事实有两处后果，
# 都在执行层，都不改排序判据：
#  ① 原来第三道执行闸用**单一** ASHARE_PORT_LIMIT_UP=0.095 判「贴涨停」，那是主板 ±10%
#     的近似。对另外三段它是误伤：全面板实测（shell/stock/probe_board_limits_0924.py），
#     涨幅过 0.095 的格子里有 **81%（科创）/ 85%（创业）/ 82%（北交）** 离自己的涨停
#     还远 —— 也就是说这道闸系统性地丢掉本账户明明买得进的那三段票。
#     09-23 当日**并集保留池**（2928 只）涨幅最高只有 7.25%，看着一只都没咬到；但
#     ⑰ 之后待买入入口放宽到 4729 只候选，同日实测 `n_chase` flat 40 / board 29 ⇒
#     **咬了 11 只**（科创 5→2、创业 6→0、北交 2→0），只是那 11 只排不进前 50。
#  ② 「5 只别挤在同一个板块」这类约束只能当**分散度**用，绝不能写成「某段不买」。
# ① 的落点见下面 ASHARE_TRADABLE_GATE：**回测与日频同一个开关**，默认 board。
# 已入库的那批组合层结论是 flat 档算的，所以任何拿 board 档跑出来的数都是**新口径**，
# 产物自带 gate 列，不与旧表混读。
ASHARE_BOARD_LIMIT_UP = {
    k: float(v) for k, v in zip(
        ("主板", "科创板", "创业板", "北交所"),
        os.environ.get("STOCK_BOARD_LIMIT_UP", "0.10,0.20,0.20,0.30").split(","))}
# 「贴板」余量：涨幅 ≥ 限幅×本系数才算追不得（0.095 = 0.10×0.95，与旧阈值对齐）
ASHARE_LIMIT_NEAR = float(os.environ.get("STOCK_LIMIT_NEAR", "0.95"))
# 各段限幅的**生效日**（`dated` 档用）：格式 板块:生效日:生效前限幅。
# 为什么需要这一张表：限幅不是常量，是历史事件。上面 ASHARE_BOARD_LIMIT_UP 那张表
# 只按代码前缀分档、**不看日期** ⇒ 用今天的规则跑历史。实测过面板的起点（09-24，
# instruments/all.txt 6160 行）：科创板最早 start 就是 2019-07-22、北交所 596 只里
# 只有 1 只 start=2021-10-27（早于开市日 12 个交易日），**唯独创业板有 837/1449 只
# 在 2020-08-24 之前就上市了**，它们的行段横跨改革前后。所以 `dated` 档真正会改判的
# 只有创业板那一段（回测窗口内 2015-01-05~2020-08-23，约 5.7 年）。
# 生效前那一档取什么值也要有出处：
#   创业板 2020-08-24 前 ±10%（注册制改革当日才改 20%）；
#   科创板 ±20% 随 2019-07-22 首批上市同步生效，之前该段无票；
#   北交所的 30% 不是开市才有的 —— 它的前身精选层 2020-07-27 起就是 ±30%，所以
#     2021-10-27 那只票在开市前也是 30%，取 0.30 而不是 0.10（取 0.10 会把它误判成追不得）；
#   主板 ±10% 自 1996 年涨跌停制度起就是它，写 1990-01-01 当哨兵值（面板最早数据 2000 年）。
# 未建模的两条，别把 dated 当成完全贴真实规则：主板 ST 股 ±5%（闸里不看名称，
# 而 ⑰ 之后 ST 名单只在日频侧有当日快照）、以及新股上市首日/前几日不设涨跌幅
# （组合层有 ASHARE_PORT_MIN_LISTED 次新闸挡着，影响面小）。生效日存 **ISO 字符串**
# （config 不引 pandas，比较在 ashare_screen 里转一次 Timestamp 并缓存）。
ASHARE_BOARD_LIMIT_SINCE = {
    k: (v0, float(v1)) for k, v0, v1 in (
        x.split(":") for x in os.environ.get(
            "STOCK_BOARD_LIMIT_SINCE",
            "主板:1990-01-01:0.10,科创板:2019-07-22:0.10,"
            "创业板:2020-08-24:0.10,北交所:2021-11-15:0.30").split(","))}
_missing = set(ASHARE_BOARD_LIMIT_UP) - set(ASHARE_BOARD_LIMIT_SINCE)
if _missing:
    raise SystemExit(f"[config] STOCK_BOARD_LIMIT_SINCE 缺板块 {sorted(_missing)}："
                     f"限幅表有的板块必须有生效日，否则 dated 档会静默用错阈值")
# 涨停闸吃哪一套阈值，**回测与日频共用这一个开关**（两条路径不许各拿一份判据，
# 否则迟早出现「回测里能过、实盘名单里没有」）：
#   board = 按板块限幅 × ASHARE_LIMIT_NEAR ⇒ 主板 9.5% / 科创 19% / 创业 19% / 北交 28.5%。
#           默认。这是 09-24 那条「三段权限齐」的事实在代码里的落点，也是**今天下单
#           该用的口径**（实盘只有当下这一档规则，不存在「按日期选规则」）。
#   flat  = 全线单一 ASHARE_PORT_LIMIT_UP=0.095（主板 ±10% 的近似）。**已入库的那批
#           组合层结论全在这一档下算的**，所以复现历史基线要显式开它。
#   dated = 板块限幅 × ASHARE_LIMIT_NEAR，但**按上面的生效日取当时那一档**（回测专用：
#           board 对 2020-08 之前的创业板放得太松、flat 对之后挡得太多，两档都不干净，
#           dated 才是「按当天真实规则能不能买」）。判「哪一天」用的是**买入日 d1**
#           （次日开盘），不是信号日。日频侧 d1 不存在，退化成与 board 同值。
# 主板那一档三路口径**逐字相同** ⇒ 换开关带来的差异只来自科创/创业/北交三段。
# 每条产物行都带 gate 列，防的就是「同一张表里混了两种口径没人知道」。
ASHARE_TRADABLE_GATE = os.environ.get("STOCK_TRADABLE_GATE", "board")
if ASHARE_TRADABLE_GATE not in ("flat", "board", "dated"):
    raise SystemExit(f"[config] STOCK_TRADABLE_GATE 只能是 flat|board|dated，收到 "
                     f"{ASHARE_TRADABLE_GATE!r}（拼错宁可报错，不静默退回默认档）")
# 下单层：从 ASHARE_BUY_TOP_N 的观察名单里挑 ASHARE_ORDER_TOP_N 只真下单。
# 这是**纯执行性筛选**（09-24 的 B 路）：不新造 alpha、不改排序轴，只在已有名次上
# 叠两条分散约束 —— 同一实体行业最多几只、同一板块最多几只。约束语义按①：
# 是「别全挤一块」，不是「某段拉黑」。行业未知（映射缺口）的票**不参与**行业去重，
# 否则北交所那种 85% 缺映射的一段会被结构性排除。
# 权重 = 1/**实际挑到的只数**（等权满仓）。09-25 之前是 1/ASHARE_ORDER_TOP_N、缺的席位
# 留现金，实测账单（`data/results/order_layer_fullinvest_0925b.csv`，570 个调仓日）：
# 5 席有 24.7% 的日子凑不满、平均 6.6% 资金闲置，换满仓后历史超额 +9.90% → +11.70%/年，
# 而最大回撤一字未变 ⇒ 那笔现金没换来安全。副作用记在案：只挑到 3 只时每只 33%。
ASHARE_ORDER_TOP_N = int(os.environ.get("STOCK_ORDER_TOP_N", "5"))
ASHARE_ORDER_MAX_PER_INDUSTRY = int(os.environ.get("STOCK_ORDER_MAX_PER_INDUSTRY", "1"))
# 一段最多占 3/5 = 60%。为什么不是更严的 2/5：**09-24 冒烟实测**，当日观察名单 50 只
# 只落在两个板块（主板 46 / 创业板 4，科创板 0、北交所 0），cap=2 时上限就是
# 2+2=4 只 —— 一个永远凑不满的约束不是安全，是把「少买一只」变成参数的隐藏后果
# （满仓给权之后这条更要紧：凑不满不再体现在空仓上，而是直接顶高单票权重）。
# 改成 3 之后仍能拦住 ⑬ 那条实测漂移（2025 年 5 个席位里 2.92 只北交所是历史最高，
# 但 2024~2025 有过 4/5 只落在北交所的调仓日，cap=3 正好卡住那一类）
ASHARE_ORDER_MAX_PER_BOARD = int(os.environ.get("STOCK_ORDER_MAX_PER_BOARD", "3"))

# ========== qlib bin 日更（data/update_qlib_bin_daily.py） ==========
# 全市场收盘快照 CSV + 逐场记账表的落位。放在 data/ 而不是 results/：它是
# **行情中间产物**（每天一份、可重算但不该进版本库），而 results/ 里的东西
# 都是要入库的研究结论；.gitignore 对这一个目录单独放行。
ASHARE_SNAPSHOT_DIR = os.environ.get(
    "STOCK_SNAPSHOT_DIR", os.path.join(DATA_DIR, "daily_snapshot"))

# ========== 行业分类落盘（data/fetch_industry_map.py，09-24） ==========
# 面板只有 OHLCV + factor，没有任何行业字段 ⇒ 「5 只会不会全挤在同一个板块」这类
# 集中风险原本判不了。这份映射是**可重算的外部参考数据**（新浪 + 深市官方两源），
# 所以落 cache/ 而不是 results/（results/ 里全是要入库的研究结论）。
ASHARE_INDUSTRY_CSV = os.environ.get(
    "STOCK_INDUSTRY_CSV", os.path.join(DATA_DIR, "cache", "industry_map.csv"))
# 超过这个天数就重新抓（行业分类变动很慢，30 天足够日频名单用）
ASHARE_INDUSTRY_MAX_AGE = int(os.environ.get("STOCK_INDUSTRY_MAX_AGE", "30"))

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
