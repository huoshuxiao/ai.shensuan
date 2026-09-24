# -*- coding: utf-8 -*-
"""ETF 因子准入链三环的共用底座：全市场池装载 + 截面评估原语 + 判重口径。

一句话：本线此前的因子准入只有 `|IC|>=0.005` 一道门槛（main.py:select_factors）
+ 跨源相关 0.85 判重 + 聚类 0.6 去重，而且**全部在主线那 20 只代表池上算**。
本模块把评估口径搬到 `data/universe_all/` 的 871 只全市场池上，为三个入口
（run_etf_factor_eval / run_etf_portfolio_eval / run_etf_redundancy_check）
提供同一份装载与同一套算子。它不 import 主线 strategy/backtest，不写因子库。

为什么要单独一份、而不是复用股票线的 `ashare_screen`
----------------------------------------------------
两条线的**数据形态根本不是一回事**，硬共用一份实现必然有一侧将就：

| 维度 | A 股个股线 | 本线（ETF） |
|---|---|---|
| 行情载体 | qlib bin `daily_pv.h5`，含 `$factor` 复权因子 | 逐只 CSV `data/universe_all/*_daily.csv`，**已是前复权**（列只有 date,open,high,low,close,volume,amount，无 factor 列）⇒ `$factor≡1`，股票线那套「复权价 ÷ factor 折回盘面价」的对看护栏在这里没有对象 |
| 收益失真来源 | `$factor` 的**假台阶**（除权重构把历史整体平移，真涨跌仍在盘面价里） | **份额折算**造成的隔日数量级跳变（实测 37 只标的各有 1~2 次 >3× 或 <1/3 的跳变，单日伪收益最高 +410%） |
| 停牌 | 常见，`pct_change` 必须 `fill_method=None` | 实测 OHLCV 缺失率 0.0000，缺口表现为**整行缺失**（未上市/已退市）而不是 NaN |
| 池子稳定性 | 5677 只含已退市，幸存者偏差需专门声明 | 871 只全为存续 ETF，且**逐年扩容**（2013 中位 28 只 → 2026 中位 871 只）；「池子自己在长」是本线主要的时变污染源 ⇒ 截面厚度必须逐日报告 |
| 涨跌停 | 主板 ±10% / 双创 ±20% / 北交所 ±30% | 多数 ±10%，跟踪创业板/科创板者 ±20% |
| 无效标的 | ST | **货基/短融/理财型 ETF**：250 日年化波动 <0.013，与真权益标的（最低 0.1141）之间有清晰空档 |

三个入口共用本模块。**闸门口径一旦出现第二种写法，评估结论就作废** —— 这句是股票线
`ashare_screen` 用一次返工换来的教训，原样搬过来。

池子口径：只读 `data/universe_all/` 的 871 只全市场池
----------------------------------------------------
主线那 20 只「每指数最早上市代表」池（`data/cache/`）日均只有约 7.7 只当日有行情，
横截面统计量在那个样本量上基本是噪声 —— 这是本仓库已实测过的结论（见
`etf/v1/src/data/fetch_etf_universe_all.py` 的模块 docstring 与 config 中
`RDAGENT_SOURCE_DIR` 那段注释）。本模块一律用全市场池，且**不写回任何主线产物**。

实测数据形态（来路 `shell/probe_etf_universe_all_0924.py`、
`shell/probe_etf_guard_0924.py`，输出已归档为同名 .log）
-------------------------------------------------------
    文件 871 个，列名 100% 统一为 date,open,high,low,close,volume,amount（部分表头带 BOM）
    交易日 4062 个（2010-01-04 ~ 2026-09-22）；单标的行数 min 242 / med 977 / max 4062
    OHLCV 缺失率 0.0000；无重复日期、无乱序索引
    每日有效标的数：全历史中位 81，最近 500 天中位 859
      分年 med 2013→28  2016→58  2019→101  2021→288  2022→410  2024→639  2026→871
    截面 <30 只的日子占 21.2%，且全部落在 2015 年前
    |日收益|>0.11 共 485 个样本、分散在 233 只；没有任何一只越界数超过自身 K 线数的 5%
    价格隔日数量级跳变（>3× 或 <1/3）37 只各 1~2 次   ← 即份额折算
    250 日年化波动：20 只 <0.013，其余最低 0.1141（中间是空档）
    近 20 日成交额 med 4488 万元；<1000 万元的 163 只、<3000 万元的 363 只

主要公式
--------
    前向 h 日收益   r_{i,t→t+h} = Π_{k=1..h}(1 + r_{i,t+k}) - 1
    截面 Pearson    IC_t      = corr_i(F_{t,i}, r_{t,i})                qlib 报表口径
    截面 Spearman   RankIC_t  = corr_i(rank(F_{t,i}), rank(r_{t,i}))     qlib 的 Rank IC
    ICIR            = mean_t(IC_t) / std_t(IC_t)
    稳定性的两个正交读数   win = 占比_t sign(IC_t)=sign(mean IC)；t 值 = mean/(std/√T)
    分层            q_{i,t} = ceil(Q · rank_pct_i(F_{t,i}))    Q1 最低分 → Q5 最高分
    层日收益        g_{b,t} = mean_{i: q_{i,t}=b}(r_{i,t})      层内等权、逐日再平衡
    多空            LS_t = g_{Q5,t} - g_{Q1,t}
    单调性          mono = spearman_corr([1..Q], [252·mean_t g_{b,t}])
    单边换手        φ_t = 1 - |b_t ∩ b_{t-1}| / |b_{t-1}|，年化 = 252 · mean_t(φ_t)
    判重            redundancy(F_a, F_b) = mean_t corr_i(F_{a,t,i}, F_{b,t,i})

只读约定：本模块不写 `data/library/`、不改 factors.json、不触发 git 提交；
产物一律落到 `data/results/`（路径见下方 *_OUT 常量）。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import csv
import glob
import os
import time

import numpy as np
import pandas as pd

from config import COMMISSION_RATE, RESULTS_DIR, SLIPPAGE, UNIVERSE_ALL_DIR
from factor_dsl import safe_eval

try:                          # scipy 只是可选加速路径，缺了走 numpy 逐行回退
    from scipy.stats import rankdata as _rankdata
except Exception:             # pragma: no cover
    _rankdata = None

# ========== 池子与口径（全部 ETF_* 环境变量可覆盖；常量在本文件，不动 config.py） ==========
UNIVERSE_DIR = os.environ.get("ETF_UNIVERSE_ALL_DIR", UNIVERSE_ALL_DIR)
# 评估窗口起点默认 2019-01-01，依据就是上面那张分年截面厚度表：2013 年池内中位只有
# 28 只、2018 年 82 只，那段日期的"截面 IC"其实是几只宽基的时序噪声；2019 年起
# med 101 只、2021 年 288 只，截面才开始有资格谈排序。看全历史用
# ETF_EVAL_START=2010-01-01 覆盖（届时 21.2% 的日子会被 MIN_CS 闸门丢弃）。
EVAL_START = os.environ.get("ETF_EVAL_START", "2019-01-01") or None
EVAL_END = os.environ.get("ETF_EVAL_END", "") or None
# 当日有效标的数下限。取 30 有两个理由：(1) 与主线 factor_dsl.compute_ic 里
# 「样本 < 30 即无统计意义」同口径，两处数字才能互认；(2) 实测全历史 21.2% 的天在
# 30 只以下且全部落在 2015 年前，这道线正好把"池子还没长起来"的年代剔掉而不伤主窗口。
MIN_CS = int(os.environ.get("ETF_MIN_CS", "30"))
# 单标的最少 K 线数：连 ts_std(returns,20) 的窗都填不满的标的没资格进截面
MIN_BARS = int(os.environ.get("ETF_MIN_BARS", "120"))
# 组合层次新闸门：满 120 根 K 线（约半年）才买 —— 新 ETF 建仓期跟踪误差未收敛
MIN_LISTED = int(os.environ.get("ETF_MIN_LISTED", "120"))
# 货基/短融/理财型 ETF 的剔除线（20 日滚动年化波动的中位数）。实测 20 只 <0.013、
# 其余最低 0.1141，0.03 落在这段空档正中 ⇒ 阈值取 0.015~0.11 之间任何值结论都不变，
# 它不是拟合出来的参数，而是数据自己给出的分界。
MIN_ANN_VOL = float(os.environ.get("ETF_MIN_ANN_VOL", "0.03"))
# 单日收益的数据护栏（不是策略参数）。本池最宽的**合法**涨跌停是双创 ETF 的 ±20%，
# 故 |r|>0.35 不可能是行情。处理方式与股票线**相反且有意**：股票线的假台阶是复权
# 重构、真实涨跌仍在盘面价里，所以 clip 到限值；ETF 的份额折算那天整天都不是行情，
# clip 会把一笔不存在的 ±35% 永久留在累乘收益里，所以直接置 NaN 让跨该日的整段作废。
RET_LIMIT = float(os.environ.get("ETF_RET_LIMIT", "0.35"))
# 组合层容量闸门（20 日均成交额，元）。缓存里的 amount 就是真钱（无前复权换算问题）。
# 3000 万元 = 「10 万元单子占当日成交额 0.3%」，实测池内 363 只够不到这条线。
MIN_AMOUNT = float(os.environ.get("ETF_MIN_AMOUNT", str(3e7)))
# 连续低量闸门（交易日）。amount20 是均值，会被月初几天撑起来：一只常年无人交易、
# 偶尔一天放量 3 千万的标的能通过均值闸，但建仓那天大概率根本没对手盘。
# 判据用**强读法**：近 N 日**每一天**的单日成交额都要够量（rolling min），不是
# "有一天够量就行"（rolling max）。09-24 实测两种读法差一个数量级：
#   峰值读法  末日剔 0 只、全期日均可投域 209→209 —— 数学上被均值闸蕴含，等于没加
#   谷值读法  N=5/10/20 ⇒ 全期日均 209→174/163/150，末日 277→239/230/226
# 只有谷值读法能表达"买进去出不来"这个形态，故取它。N=10（两周）是拍的初值，
# 敏感性用 ETF_AMT_STREAK 覆盖后重跑本环，不靠注释定论。
AMT_STREAK = int(os.environ.get("ETF_AMT_STREAK", "10"))
# 成交额分档单边滑点（元/元，不含佣金）。单一 5bp 在本池是假的：日成交额中位 0.42 亿，
# 一笔 10 万元单子在 4200 万成交里参与率 0.24%，在 300 万里是 3.3% —— 同一个滑点数
# 盖不住相差两个数量级的冲击。分档按 20 日均成交额切，档位对齐容量叙述口径
# （3000 万 = 10 万元单子占 0.3%）。ETF_COST_MODE=flat 时本表整体失效，回到单一 COST。
SLIP_TIERS = ((1e9, 0.0003), (3e8, 0.0005), (1e8, 0.0008), (3e7, 0.0015), (0.0, 0.0030))
COST_MODE = os.environ.get("ETF_COST_MODE", "tier").strip().lower()
HORIZONS = tuple(int(x) for x in os.environ.get("ETF_HORIZONS", "5,10,20").split(","))
QUINTILES = int(os.environ.get("ETF_QUINTILES", "5"))
TRADING_DAYS = 252
# 涨跌停近似闸门（建仓日开盘相对信号日收盘的跳升上限）。588/589 是科创板 ETF 段按
# ±20% 判，其余按 ±10%。深市跟踪创业板指的 ETF 混在 159 段里、无法从代码区分，
# 对它们本闸门**偏严**（会少买几只其实可成交的），影响逐行报在 blocked_limit 列。
LIMIT_PLAIN = float(os.environ.get("ETF_LIMIT_PLAIN", "0.099"))
LIMIT_TECH = float(os.environ.get("ETF_LIMIT_TECH", "0.199"))
# 组合层单边费率：与主线同源（ETF 免印花税，佣金 1bp + 滑点 5bp = 6bp）
COST_ONE_WAY = float(os.environ.get("ETF_PORT_COST", str(COMMISSION_RATE + SLIPPAGE)))
TOP_K = tuple(int(x) for x in os.environ.get("ETF_TOP_K", "10,20").split(","))
HOLD = int(os.environ.get("ETF_HOLD", "10"))
# 合成打分取前几条因子（按 |RankICIR|）
COMPOSITE_TOP = int(os.environ.get("ETF_COMPOSITE_TOP", "5"))

# ========== 规模闸（#17：清盘线代理，2026-09-24 定为默认开启 5 亿） ==========
# 0 ⇒ 不启用。选档过程与代价全部实测在 `shell/probe_etf_scale_gate_0924.py` →
# `shell/etf_scale_gate_0924.log`，两档真实跑批在 `shell/ring2_scale_0924/`：
#   关 162.7 只日均 / 2 亿 161.2 / **5 亿 153.7**；合成 k=10 净年化 +30.58% /
#   +31.13% / +32.22%，回撤 −21.0% / −21.0% / **−17.1%**。
# 取 5 亿的理由是**回撤那一档**（本轮所有改动里最大的一次风险改善），不是那 1.09pp
# 净年化 —— 后者不 robust：同一档在 k=20 上把合成从 +28.41% 打到 +27.06%。所以这
# 一道闸目前的身份是"风险控制阀"，其在样本外是否还站得住属 #15 的验证范围。
# 已知软肋：份额面板里沪市只有月末快照（ffill≤25 交易日）⇒ 贴在线上的沪市标的读到的
# 可能是上月末规模。#18（镜像滞后）解决后应复核这条线要不要换真净值口径。
MIN_SCALE = float(os.environ.get("ETF_MIN_SCALE", "500000000"))
# 份额的披露节奏两个市场完全不同：深市逐日、沪市只有月末快照 ⇒ 取最近一次披露
# 向前填充。填充窗（交易日）就是"这条规模最多信多久"，超窗落回 NaN = 不知道，
# 而不是拿三个月前的数字冒充今天的规模。
SCALE_FFILL = int(os.environ.get("ETF_SCALE_FFILL", "25"))
# 清盘条款本身（合同条款，非交易所规则）：连续 CLEAR_DAYS 个交易日资产净值 < 5000 万
CLEAR_DAYS = int(os.environ.get("ETF_CLEAR_DAYS", "60"))
CLEAR_LINE = float(os.environ.get("ETF_CLEAR_LINE", str(5e7)))
# 规模面板的可读度回执，由 `load_scale_matrix` 填、入口脚本打印。单独一个 dict 是
# 因为"覆盖了多少格子"决定了规模闸是判据还是装饰品 —— 每次开闸都必须看得见这个数。
SCALE_COVERAGE = {"share_cells": np.nan, "share_dates": 0}

# ========== 产物路径（只写 results/，文件名带 etf_ 前缀以区别主链产物） ==========
FACTOR_EVAL_OUT = os.path.join(RESULTS_DIR, "etf_factor_eval.csv")
FACTOR_LAYER_OUT = os.path.join(RESULTS_DIR, "etf_factor_layers.csv")
PORT_EVAL_OUT = os.path.join(RESULTS_DIR, "etf_portfolio_eval.csv")
PORT_YEARLY_OUT = os.path.join(RESULTS_DIR, "etf_portfolio_eval_yearly.csv")
REDUNDANCY_OUT = os.path.join(RESULTS_DIR, "etf_redundancy_check.csv")
REDUNDANCY_DETAIL_OUT = os.path.join(RESULTS_DIR, "etf_redundancy_detail.csv")
# 因子库：**只读**，判重环节拿它当在库基准
FACTOR_LIBRARY_CSV = os.environ.get(
    "ETF_FACTOR_LIBRARY",
    os.path.join(os.path.dirname(RESULTS_DIR), "library", "factor_library.csv"))
# 判重红线。本线现行规则是 multi_source_mining 的跨源相关 0.85 判重 + 聚类 0.6 去重，
# rdagent 的 deduplicate_new_factors 用 0.99。这里以**本线会真正触发的 0.85** 为判据，
# 另把 0.99（进 rdagent 循环必被丢）单列一栏，两套口径都看得见。
RED_BAR = float(os.environ.get("ETF_RED_BAR", "0.85"))
RDAGENT_BAR = 0.99
NEAR_DUP = 0.70        # 危险区下界：不触发判重但已是同一簇

OHLCVA = ["open", "high", "low", "close", "volume", "amount"]
BENCH_CODE = os.environ.get("ETF_BENCH", "510300")   # 沪深 300ETF：等权之外的第二条参照


# ==================== 数据装载 ====================

def limit_of(code):
    """该标的的涨跌停近似上限：科创板 ETF 段 ±20%，其余 ±10%（理由见 LIMIT_TECH 注释）。"""
    return LIMIT_TECH if str(code).startswith(("588", "589")) else LIMIT_PLAIN


def load_pool(universe_dir=None, min_bars=MIN_BARS, min_ann_vol=MIN_ANN_VOL,
              start=None, end=None, verbose=True):
    """读 `*_daily.csv` 成全市场池，返回 {code: 单标的 DataFrame(date × OHLCVA)}。

    三道**池子级**筛选（在本层一次做完，三环共用，绝不允许入口脚本再各写一遍）：
    1. 列必须齐（date + open/high/low/close/volume/amount）；
    2. K 线数 >= min_bars；
    3. 近 250 日**护栏后**的 20 日年化波动中位数 >= min_ann_vol ⇒ 剔货基/短融/理财型。
       用中位数而不是末值：一只"早年权益、近年转型成货基"的标的后半段在截面里就是
       噪声，末值口径会把它的前半段留下、等于偷偷换了一批标的进池。
       护栏必须在波动之前上，否则份额折算那天 +410% 会把伪序列抬成"高波动好标的"。
    """
    d = universe_dir or UNIVERSE_DIR
    files = sorted(glob.glob(os.path.join(d, "*_daily.csv")))
    t0 = time.time()
    pool, bad_col, too_short, cash_like = {}, [], [], []
    for f in files:
        code = os.path.basename(f).split("_")[0]
        try:
            # utf-8-sig：实测部分文件表头带 BOM，用 utf-8 会把首列名读成 "\ufeffdate"
            df = pd.read_csv(f, encoding="utf-8-sig")
        except Exception:
            bad_col.append(code)
            continue
        if "date" not in df.columns or not set(OHLCVA) <= set(df.columns):
            bad_col.append(code)
            continue
        df = df.assign(date=pd.to_datetime(df["date"], errors="coerce"))
        df = df.dropna(subset=["date"]).drop_duplicates("date", keep="last")
        df = (df.set_index("date")[OHLCVA]
                .apply(pd.to_numeric, errors="coerce").sort_index())
        df = df[df["close"].notna() & (df["close"] > 0)]
        if len(df) < min_bars:
            too_short.append(code)
            continue
        r = guard_ret(df["close"].pct_change(fill_method=None))
        med_vol = float(r.rolling(20).std().tail(250).median() * np.sqrt(TRADING_DAYS))
        if not np.isfinite(med_vol) or med_vol < min_ann_vol:
            cash_like.append(code)
            continue
        if start:
            df = df.loc[df.index >= pd.Timestamp(start)]
        if end:
            df = df.loc[df.index <= pd.Timestamp(end)]
        if len(df) < min_bars:
            too_short.append(code)
            continue
        # float32 存价/量：871 只全历史约 95 万行 × 6 列，float64 多吃 40MB，
        # 而 7 位有效数字对 3 位小数的 ETF 报价足够（统计处一律升 float64）
        pool[code] = df.astype("float32")
    if verbose:
        print(f"[数据] {d}：{len(files)} 个文件 → 有效 {len(pool)} 只"
              f"（剔货基/低波动 {len(cash_like)}、K 线不足 {len(too_short)}、"
              f"列不齐 {len(bad_col)}），耗时 {time.time() - t0:.0f}s")
    return pool


def guard_ret(ret, limit=RET_LIMIT):
    """单日收益数据护栏：|r| > limit 判份额折算/拼接伪影，**置 NaN 剔除**。

        r̃_t = r_t · 1{|r_t| <= limit}

    与股票线同名函数（`ashare_screen.guard_ret`）实现相反且有意，理由见模块
    docstring 的对照表与 RET_LIMIT 注释。
    """
    return ret.where(ret.abs() <= limit)


def forward_returns(ret, h):
    """前向 h 日收益（截面 IC 的标签）。

        r_{i,t→t+h} = Π_{k=1..h} (1 + r_{i,t+k}) - 1

    用累乘而不是 close.shift(-h)/close - 1，是为了让护栏生效：中间任何一天被判为
    伪影（NaN），累乘会把整段前向收益传染成 NaN；用价格比值则伪影"除得回去"，
    护栏白装。跨缺失日整段作废，绝不做部分累乘 —— 少乘一天的序列会伪装成
    "这段行情很稳"，那是比缺失更坏的产物。
    """
    acc = pd.DataFrame(1.0, index=ret.index, columns=ret.columns, dtype="float64")
    for k in range(1, h + 1):
        acc = acc * (1.0 + ret.shift(-k))
    return acc - 1.0


def build_matrices(pool, horizons=HORIZONS):
    """单标的池 → 派生宽表 dict（DataFrame 均为 date × code），一次算全供三环共用。

    - `ret`       护栏后日收益 r_t = P_t/P_{t-1} - 1（`fill_method=None` 与本线
                  factor_dsl 的 `returns` 定义同式；本线缺口是整行缺失，填不填一样，
                  但写法一致能防止将来有停牌数据时口径漂移）
    - `ret_open`  开盘到开盘收益：组合层「信号日收盘算分 → 次日开盘建仓」的可执行口径
    - `fwd{h}`    前向 h 日收益（见 forward_returns）
    - `thickness` 当日有效标的数 = 该日 close 非缺失只数（报告它＝报告池子长到多大了）
    - `listed_days` 该标的累计已有 K 线数（次新闸门）
    - `amount20`  20 日均成交额（元）
    - `ann_vol20` 20 日滚动年化波动
    """
    idx = None
    for df in pool.values():
        idx = df.index if idx is None else idx.union(df.index)
    idx = idx.sort_values()
    codes = sorted(pool)
    mats = {c: pd.DataFrame({k: pool[k][c] for k in codes}, index=idx)
                .astype("float64") for c in OHLCVA}
    cl = mats["close"]
    mats["ret"] = guard_ret(cl.pct_change(fill_method=None))
    mats["ret_open"] = guard_ret(mats["open"].pct_change(fill_method=None))
    mats["thickness"] = cl.notna().sum(axis=1)
    mats["listed_days"] = cl.notna().cumsum()
    mats["amount20"] = mats["amount"].rolling(20, min_periods=1).mean()
    # 不在此处放"连续低量"那道闸：它要用到闸门参数 min_amount（在 universe_mask
    # 里可覆盖），预计算成布尔矩阵就会把 min_amount 焊死在模块默认值上。
    # amount20 用 min_periods=1：它是「有多少根算多少根」的容量度量。默认
    # min_periods=20 会把它变成 NaN，等于在显式的次新闸门之外偷偷再加一道
    # 「至少 20 根 K 线」的门槛——生产里 MIN_LISTED=120 早已盖住这道槛（改动
    # 前后裁判数字逐位相同），但调用方一旦把 min_listed 调到 20 以下做敏感性
    # 检查，就会看到「闸门明明放了，可交易只数还是 0」。波动那道闸反过来必须
    # 严格：3 根 bar 的年化波动没有意义，宁可它是 NaN。
    mats["ann_vol20"] = mats["ret"].rolling(20).std() * np.sqrt(TRADING_DAYS)
    for h in horizons:
        mats[f"fwd{h}"] = forward_returns(mats["ret"], h)
    return mats


def take_window(mats, start=EVAL_START, end=EVAL_END):
    """按评估窗口切行。

    顺序必须是「先全历史算因子/收益，再切窗口」：暖机段（rolling 窗未满、标的上市
    初期）留在矩阵里只用于算因子，不进统计。反过来先切窗口再算因子，会把窗口头
    若干日的因子值整体做成 NaN，等于白白丢掉几年样本。
    传 start=None 即不切。
    """
    s = pd.Timestamp(start) if start else None
    e = pd.Timestamp(end) if end else None
    out = {}
    for k, v in mats.items():
        if s is not None:
            v = v.loc[v.index >= s]
        if e is not None:
            v = v.loc[v.index <= e]
        out[k] = v
    return out


def slice_to_start(df, start=None):
    """把一张宽表切到评估窗口起点（暖机段只用于算因子，不进统计）。与 take_window 同义，
    单列出来是给"逐张因子宽表边算边切"的调用方用的（一次留 32 张全历史表白吃 500MB）。"""
    if not start:
        return df
    return df.loc[df.index >= pd.Timestamp(start)]


def thickness_by_year(m, min_cs=MIN_CS):
    """逐年截面厚度表。本池是"边跑边长个子"的（2013 中位 28 只 → 2026 中位 871 只），
    不报这张表就没法解释早年的 IC 为什么抖 —— 那不是因子失效，是当天只有几十只可排。"""
    t = pd.Series(m["thickness"])
    g = t.groupby(t.index.year)
    return pd.DataFrame({"days": g.size(), "cs_median": g.median(), "cs_min": g.min(),
                         "days_thin": g.apply(lambda k: int((k < min_cs).sum()))})


def thickness_report(m, min_cs=MIN_CS):
    """截面厚度描述（每个入口都印一份，防止"IC 变好"其实只是"池子变厚"）。"""
    t = pd.Series(m["thickness"])
    v = t[t >= min_cs]
    return {"days_total": int(len(t)), "days_used": int(len(v)),
            "days_thin": int((t < min_cs).sum()),
            "cs_median": float(np.median(v)) if len(v) else np.nan,
            "cs_p10": float(np.percentile(v, 10)) if len(v) else np.nan,
            "cs_max": float(t.max()),
            "first": str(t.index[0].date()), "last": str(t.index[-1].date())}


# ==================== 因子求值 ====================

def spec_name(spec):
    return spec.get("name") or spec["expr"]


def evaluate_factors(pool, specs, verbose=True, every=250):
    """按主线 DSL（`common/src/core/factor_dsl.safe_eval`）逐标的求值 → {name: 宽表}。

    与主线 `_attach_impl`、股票线截面评估同一条求值路径，报出来的数才能与在库
    26 条因子横向比；抄一份 pandas 版本迟早和主线分叉（股票线已在 amount20 上分叉过一次）。
    每只标的只遍历一次、内层跑完全部表达式（871 只 << 表达式数 × 全历史）。
    本线的 high/low/volume/amount 都是真值，不像股票线要用 close 占位补齐。

    全标的都不可求值的表达式不出现在结果里，调用方拿 specs 对账 ——
    "进不了 DSL"本身就是准入结论之一，必须报出来。
    """
    codes = sorted(pool)
    acc = {spec_name(sp): {} for sp in specs}
    dead = set()
    t0 = time.time()
    for n, code in enumerate(codes, 1):
        df = pool[code]
        # float64 副本：DSL 里 rolling().std()/corr() 在 float32 上会丢精度出假 NaN
        env = pd.DataFrame({"open": df["open"].astype("float64"),
                            "high": df["high"].astype("float64"),
                            "low": df["low"].astype("float64"),
                            "close": df["close"].astype("float64"),
                            "volume": df["volume"].astype("float64"),
                            "amount": df["amount"].astype("float64")})
        for sp in specs:
            nm = spec_name(sp)
            if nm in dead:
                continue
            try:
                s = safe_eval(sp["expr"], env)
            except Exception:
                dead.add(nm)
                continue
            if s is None or not len(s) or s.isna().all():
                continue
            acc[nm][code] = s
        if verbose and every and n % every == 0:
            print(f"    求值 {n}/{len(codes)} 只，已用 {time.time() - t0:.0f}s")
    out = {}
    for nm, d in acc.items():
        if d:
            idx = None
            for s in d.values():
                idx = s.index if idx is None else idx.union(s.index)
            # DatetimeIndex 用 sort_values（Index 没有 sort_index 这个方法）
            out[nm] = pd.DataFrame(d).reindex(index=idx.sort_values()).astype("float64")
    if verbose:
        print(f"[求值] {len(specs)} 条表达式 × {len(codes)} 只 → 成表 {len(out)} 列"
              + (f"，全标的不可求值 {len(dead)} 条：" + "、".join(sorted(dead))
                 if dead else "")
              + f"，耗时 {time.time() - t0:.0f}s")
    return out


# ==================== 截面统计原语 ====================

def _row_ranks(A, mask):
    """逐行平均秩（只在 mask 位上排名，无效位归 0）。

    做法：无效位先填 +inf 排到最后，有效位的秩不受影响，再把无效位归 0。
    并列取平均秩 ⇒ 与 pandas `Series.rank()` 等价，能和暴力版逐日对拍。
    """
    B = np.where(mask, A, np.inf)
    if _rankdata is not None:
        R = np.asarray(_rankdata(B, axis=1), dtype="float64")
    else:                                    # pragma: no cover - 无 scipy 时的回退
        R = np.empty(B.shape, dtype="float64")
        for i in range(B.shape[0]):
            _v, inv, cnt = np.unique(B[i], return_inverse=True, return_counts=True)
            cum = np.cumsum(cnt)
            R[i] = ((cum + cum - cnt + 1) / 2.0)[inv]
    return np.where(mask, R, 0.0)


def _row_pearson(A, B, mask, n, keep):
    """逐行 Pearson，行内只用 mask 位；废行返回 NaN。

        IC_t = Σ(x-x̄)(y-ȳ) / √(Σ(x-x̄)²·Σ(y-ȳ))      x,y ∈ 当日有效截面

    常量日（某侧方差为 0）给 **NaN 而不是 0**：记 0 等于把"这天没法判"当成
    "这天预测失败"，会系统性把 |IC| 往下拉。
    """
    nn = np.maximum(n, 1).astype("float64")
    X = np.where(mask, A, 0.0)
    Y = np.where(mask, B, 0.0)
    sx, sy = X.sum(1), Y.sum(1)
    cov = (X * Y).sum(1) - sx * sy / nn
    vx = (X * X).sum(1) - sx * sx / nn
    vy = (Y * Y).sum(1) - sy * sy / nn
    with np.errstate(invalid="ignore", divide="ignore"):
        r = cov / np.sqrt(vx * vy)
    out = np.full(A.shape[0], np.nan)
    ok = keep & (vx > 0) & (vy > 0) & (n >= 2)
    out[ok] = r[ok]
    return out


def daily_cs_ic(fac, fwd, min_cs=MIN_CS, fwd_rank=None):
    """逐日截面 IC（Pearson 与 Spearman 两口径并列，因为两者回答的不是一个问题）。

        IC_t     = corr_i(F_{t,i}, r_{i,t→t+h,i})            原始值，qlib 报表口径
        RankIC_t = corr_i(rank(F_{t,i}), rank(r_{i,t→t+h,i})) 秩，抗离群值

    i 跑遍「当日两侧都有值」的标的；该数 < min_cs 的日子整日丢弃（理由见 MIN_CS 注释：
    30 只以下的相关系数标准误已 >0.18，任何"IC"都只是抽样噪声，且主线 compute_ic
    本来就用同一条 n<30 门槛）。
    本池价格量级横跨 0.3~100 元、量能横跨三个数量级，原始值 Pearson 极易被少数
    高价/大成交标的主导 ⇒ **准入判据用 RankIC**，IC 只作对照列出。
    返回 (Series[ic], Series[rank_ic], Series[当日有效标的数])。
    `fwd_rank` 让调用方缓存同一张前向收益的秩，跨因子省掉整套 rankdata。
    （**标签**侧的秩可以缓存，**因子**侧不行：mask 是"两侧都有值"，换期限就换 mask，
    被排除的格子当日在因子侧的秩分布也随之改变 —— 缓存它等于偷换定义。）
    """
    B = fwd.reindex(index=fac.index, columns=fac.columns).to_numpy("float64")
    A = fac.to_numpy("float64")
    mask = np.isfinite(A) & np.isfinite(B)
    n = mask.sum(axis=1)
    keep = n >= min_cs
    ic = _row_pearson(A, B, mask, n, keep)
    rb = fwd_rank if fwd_rank is not None else _row_ranks(B, mask)
    ric = _row_pearson(_row_ranks(A, mask), rb, mask, n, keep)
    return (pd.Series(ic, index=fac.index), pd.Series(ric, index=fac.index),
            pd.Series(n, index=fac.index))


def ic_summary(ic, n=None, min_cs=MIN_CS):
    """IC 序列 → 汇总指标 dict。

        ICIR = mean_t(IC_t) / std_t(IC_t)
        胜率 = 占比_t sign(IC_t) = sign(mean_t IC_t)
        t 值 = mean / (std/√T)，T = 有效天数；|t|<2 基本就是噪声
    胜率与 t 值是 ICIR 之外的两个**正交**稳定性读数：ICIR 高可能只是因为 std 小
    （分母效应），胜率才说明"方向稳不稳"，t 值说明"样本够不够"。
    """
    x = pd.Series(ic).dropna()
    if not len(x):
        return {"mean": np.nan, "icir": np.nan, "t": np.nan, "win": np.nan,
                "days": 0, "cs_median": np.nan, "cs_min": np.nan, "days_thin": np.nan}
    m, sd = float(x.mean()), float(x.std())
    out = {"mean": m, "icir": m / (sd + 1e-9),
           "t": m / (sd / np.sqrt(len(x))) if sd > 0 else np.nan,
           "win": float((np.sign(x) == np.sign(m)).mean()), "days": int(len(x))}
    if n is not None:
        nn = pd.Series(n).reindex(x.index).to_numpy("float64")
        out["cs_median"] = float(np.median(nn)) if len(nn) else np.nan
        out["cs_min"] = float(nn.min()) if len(nn) else np.nan
        out["days_thin"] = int((pd.Series(n) < min_cs).sum())
    return out


def quintile_layers(fac, ret, q=QUINTILES, min_cs=MIN_CS):
    """截面 q 分层的逐日等权层收益 + 高分侧换手。

        q_{i,t} = ceil(q · rank_pct_i(F_{t,i}))        Q1 最低分 → Qq 最高分
        g_{b,t} = mean_{i: q_{i,t}=b}(r_{i,t})          层内等权、逐日再平衡
        LS_t    = g_{Qq,t} - g_{Q1,t}
        φ_t     = 1 - |b_t ∩ b_{t-1}| / |b_{t-1}|       （最高分层的单边换手）

    分层用**日收益**而不是 h 日前向收益，是有意让分层与期限无关：一条表达式的
    单调性只需算一次、三个期限共用（否则因子数 × 期限数会把同一件事重复算）。
    """
    pct = fac.rank(axis=1, pct=True).to_numpy("float64")     # 当日截面百分位秩 ∈(0,1]
    lab = np.ceil(pct * q)
    R = ret.reindex(index=fac.index, columns=fac.columns).to_numpy("float64")
    m = np.isfinite(R) & np.isfinite(lab)
    lab = np.where(m, lab, 0.0)
    counts = m.sum(1)
    keep = counts >= min_cs
    days, codes_n = R.shape
    gs = np.full((q, days), np.nan)
    memb = np.zeros((q, days, codes_n), dtype=bool)
    for b in range(1, q + 1):
        sel = m & (lab == b)
        cnt = sel.sum(1)
        gs[b - 1] = np.where(cnt > 0,
                             np.where(sel, np.nan_to_num(R), 0.0).sum(1)
                             / np.maximum(cnt, 1), np.nan)
        memb[b - 1] = sel                                   # (days, codes)
    # 高分层成员逐日对比：φ_t = 1 - |b_t ∩ b_{t-1}| / |b_{t-1}|（首日无前值 ⇒ 记 0）
    prev = np.roll(memb[-1], 1, axis=0)
    prev[0] = False
    pc = prev.sum(1)
    inter = (memb[-1] & prev).sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        phi = np.where(pc > 0, 1.0 - inter / np.maximum(pc, 1), np.nan)
    idx = fac.index
    return {"layers": pd.DataFrame(gs.T, index=idx,
                                   columns=[f"Q{i + 1}" for i in range(q)]),
            "ls": pd.Series(gs[q - 1] - gs[0], index=idx),
            "counts": pd.Series(counts, index=idx),
            "keep": keep,
            "phi": pd.Series(phi, index=idx),
            "top_n": pd.Series(memb[-1].sum(1), index=idx)}


def layer_summary(q):
    """quintile_layers 的结果 → 指标 dict（分层年化、单调性、多空、高分侧换手）。

        年化层收益 = 252 · mean_t(g_{b,t})      （取均值不累乘，见下面那句提醒）
        单调性     = spearman_corr([1..q], [年化层收益])
        多空年化   = 252 · mean_t(LS_t)          LS = g_Qq - g_Q1
        高分侧年化单边换手 = 252 · mean_t(φ_t)

    提醒：这些层组合**逐日再平衡**，在 ETF 上根本做不到（双边 6bp × 252 次会吃掉
    一切），所以它们只用于读单调性与斜率，不能拿去和组合层的扣费净收益比大小 ——
    可交易口径只在 run_etf_portfolio_eval.py 出。
    """
    keep = q["keep"]
    gs = q["layers"].to_numpy("float64")
    ann = [float(np.nanmean(np.where(keep, gs[:, b], np.nan)) * TRADING_DAYS)
           for b in range(gs.shape[1])]
    ls = np.where(keep, q["ls"].to_numpy("float64"), np.nan)
    lsm = np.nanmean(ls)
    phi = np.where(keep, q["phi"].to_numpy("float64"), np.nan)
    topn = np.where(keep, q["top_n"].to_numpy("float64"), np.nan)
    return {"q_ann": ann,
            "q_spread_ann": ann[-1] - ann[0],
            "q_top_ann": ann[-1],
            "q_top_excess_ann": ann[-1] - float(np.mean(ann)),
            "mono": _spearman_of(list(range(1, len(ann) + 1)), ann),
            "ls_ann": float(lsm * TRADING_DAYS),
            "ls_win": float(np.nanmean(np.sign(ls) == np.sign(lsm))),
            "top_turnover_ann": float(np.nanmean(phi) * TRADING_DAYS),
            "top_avg_n": float(np.nanmean(topn))}


def _spearman_of(xs, ys):
    """两组数的 Spearman 秩相关（就 q 个分层点，按定义算，不为一行调用拉 scipy）。"""
    if len(xs) < 3:
        return np.nan
    rx = pd.Series(xs).rank().to_numpy("float64")
    ry = pd.Series(ys).rank().to_numpy("float64")
    if rx.std() == 0 or ry.std() == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def _corr_block(X, Y):
    """(na,n) 与 (nb,n) 的两两 Pearson 块（列已对齐；n = 当日参与标的数）。"""
    n = X.shape[1]
    xm = X - X.mean(1, keepdims=True)
    ym = Y - Y.mean(1, keepdims=True)
    sx = np.sqrt((xm * xm).sum(1))
    sy = np.sqrt((ym * ym).sum(1))
    with np.errstate(invalid="ignore", divide="ignore"):
        c = (xm @ ym.T) / np.outer(sx, sy)
    # 常量行/列 corr 无定义：置 0 以免污染累加，判重时由 spearman 列与常量告警列复核
    return np.nan_to_num(c, nan=0.0)


def cs_corr_mean(mats_a, mats_b=None, min_cs=MIN_CS, verbose=True, day_chunk=500):
    """逐日截面相关的时间平均 —— 判重主口径。

        corr_t(F_a, F_b) = corr_i(F_{a,t,i}, F_{b,t,i})   i ∈ 当日全部候选齐备的标的
        redundancy(a,b)  = mean_t corr_t

    为什么不能"把两列拉平后整体求相关"：本池从 28 只扩到 871 只，拉平后那个数把
    「不同日期」当成同一个截面，后段样本天然占优，任何两条时序因子都能算出虚高的
    正相关。逐日算再取均值才是"这两个因子在同一天排序像不像"。
    成对样本一致：任一候选在该 (日, 标的) 缺失，该标的当日就不参与（与 rdagent
    deduplicate_new_factors 的 complete-case 处理同等）。

    返回 (pearson, spearman) 两张 len(A)×len(B) 均值矩阵；mats_b 省略时对 A 内部两两。
    `day_chunk` 只是内存分块：统计是逐日累加的，按天切块再相加与一次算完**逐位等价**，
    加它纯粹因为 53 张 (1900×871) 的 float64 立体块 ≈ 740MB 不好一次摊开。
    """
    solo = mats_b is None
    names = list(mats_a)
    cols_b = names if solo else list(mats_b)
    if not names:
        e = pd.DataFrame()
        return e, e.copy()
    blocks_a = [mats_a[k] for k in names]
    blocks_b = blocks_a if solo else [mats_b[k] for k in cols_b]
    dates = blocks_a[0].index
    codes = pd.Index(blocks_a[0].columns)
    for mm in blocks_a[1:] + blocks_b:
        dates = dates.intersection(mm.index)
        codes = codes.intersection(pd.Index(mm.columns))
    if not len(dates) or len(codes) < min_cs:
        raise SystemExit(f"[判重] 对齐后只剩 {len(codes)} 只 / {len(dates)} 天，"
                         f"不足 MIN_CS={min_cs}")
    na, nb = len(blocks_a), len(blocks_b)
    p_sum = np.zeros((na, nb))
    s_sum = np.zeros((na, nb))
    n_day = n_skip = 0
    for t0 in range(0, len(dates), max(1, day_chunk)):
        sub = dates[t0:t0 + max(1, day_chunk)]
        A = np.stack([m.reindex(index=sub, columns=codes).to_numpy("float64")
                      for m in blocks_a])                # (Na, 块内天数, codes)
        B = A if solo else np.stack(
            [m.reindex(index=sub, columns=codes).to_numpy("float64")
             for m in blocks_b])
        full = np.isfinite(A).all(axis=0) & np.isfinite(B).all(axis=0)  # (天, codes)
        for t in range(len(sub)):
            sel = full[t]
            if sel.sum() < min_cs:
                n_skip += 1
                continue
            xa, xb = A[:, t][:, sel], B[:, t][:, sel]
            p_sum += _corr_block(xa, xb)
            # mask 必须按各自形状给：候选集 31 行、在库集 21 行，共用一张
            # ones(xa.shape) 在 A×B 跨集比对时会直接把 xb 撑爆
            s_sum += _corr_block(_row_ranks(xa, np.ones(xa.shape, bool)),
                                 _row_ranks(xb, np.ones(xb.shape, bool)))
            n_day += 1
    if not n_day:
        raise SystemExit(f"[判重] 没有任何一天满足截面样本数要求，检查 ETF_MIN_CS")
    if verbose:
        print(f"[判重] 对齐 {len(dates)} 天 × {len(codes)} 只；有效 {n_day} 天、"
              f"因截面过薄或候选缺失丢弃 {n_skip} 天")
    return (pd.DataFrame(p_sum / n_day, index=names, columns=cols_b),
            pd.DataFrame(s_sum / n_day, index=names, columns=cols_b))


# ==================== 因子库（只读） ====================

def load_active_library(path=None, verbose=True):
    """读因子库的 active 条目（**只读**），返回 [{name, expr, ic, icir, source}]。

    expr 为空的条目（内置注册表因子）单独计数报出来：它们进不了 DSL 求值，也就进不了
    判重 —— 这是判重口径的一个已知盲区，必须让读数的人看见，而不是默默少比几条。
    """
    p = path or FACTOR_LIBRARY_CSV
    if not os.path.exists(p):
        if verbose:
            print(f"[因子库] {p} 不存在：判重只在候选两两之间做")
        return []
    with open(p, encoding="utf-8-sig") as f:      # 表头带 BOM，与行情缓存同一坑
        rows = list(csv.DictReader(f))
    out, blank, inactive = [], 0, 0
    for r in rows:
        if str(r.get("status", "")).strip().lower() != "active":
            inactive += 1
            continue
        expr = (r.get("expr") or "").strip()
        if not expr:
            blank += 1
            continue

        def _f(key):
            try:
                return float(r.get(key))
            except (TypeError, ValueError):
                return np.nan

        out.append({"name": r.get("name") or expr[:20], "expr": expr,
                    "ic": _f("ic"), "icir": _f("icir"),
                    "source": r.get("source", "")})
    if verbose:
        notes = []
        if blank:
            notes.append(f"expr 为空的内置因子 {blank} 条（判重覆盖不到）")
        if inactive:
            notes.append(f"非 active {inactive} 条已跳过")
        print(f"[因子库] 只读 {p}：条目 {len(rows)}，可求值的 active {len(out)} 条"
              + ("；" + "，".join(notes) if notes else ""))
    return out


# ==================== 候选因子族 ====================
# 表达式一律写进主线 DSL（safe_eval 的受限命名空间），与在库因子同一条求值路径。
# family 是"族"级判据的载体 —— 准入要回答的是「这一族在 ETF 截面上活不活」，
# 不是某一条表达式的运气。note 写清每条为什么值得占一行。
FAMILY_SPECS = [
    # ---- 价格水平族：股票线整族测死（负 IC 只是低价股效应），ETF 上重测而非默认沿用 ----
    {"family": "价格水平", "name": "水平·MA5", "expr": "ma(df,5)",
     "note": "收盘价水平本身"},
    {"family": "价格水平", "name": "水平·MA20", "expr": "ma(df,20)", "note": ""},
    # ---- 动量族 ----
    {"family": "动量", "name": "动量·20d", "expr": "close/delay(close,20)-1",
     "note": "1 月动量"},
    {"family": "动量", "name": "动量·120d", "expr": "close/delay(close,120)-1",
     "note": "半年动量"},
    {"family": "动量", "name": "动量·120d跳20",
     "expr": "delay(close,20)/delay(close,120)-1",
     "note": "剔掉最近 20 日的中长期动量（与反转族分开看）"},
    # ---- 反转族（值越大=近期涨得越多，做多取负）----
    {"family": "反转", "name": "反转·1d", "expr": "close/delay(close,1)-1",
     "note": "隔日反转"},
    {"family": "反转", "name": "反转·2d", "expr": "close/delay(close,2)-1", "note": ""},
    {"family": "反转", "name": "反转·5d", "expr": "close/delay(close,5)-1",
     "note": "5 日短期反转"},
    # ---- 趋势位置族 ----
    {"family": "趋势位置", "name": "位置·C/MA20", "expr": "close/ma(df,20)-1",
     "note": "相对自身均线偏离"},
    {"family": "趋势位置", "name": "位置·距20日高", "expr": "close/max(df,20)-1",
     "note": "离区间高点多远（≤0）"},
    {"family": "趋势位置", "name": "位置·20日Donchian",
     "expr": "(close-min(df,20))/(max(df,20)-min(df,20)+1e-9)",
     "note": "区间位置 ∈[0,1]"},
    # ---- 波动族 ----
    {"family": "波动", "name": "波动·STD5", "expr": "ts_std(returns,5)",
     "note": "库内 gp_0 同构造"},
    {"family": "波动", "name": "波动·STD20", "expr": "ts_std(returns,20)", "note": ""},
    {"family": "波动", "name": "波动·归一STD20", "expr": "std(df,20)/ma(df,20)",
     "note": "除掉价格量级的波动"},
    {"family": "波动", "name": "波动·振幅当日", "expr": "(high-low)/close",
     "note": "当日振幅"},
    {"family": "波动", "name": "波动·振幅MA20", "expr": "ts_mean((high-low)/close,20)",
     "note": "20 日均振幅"},
    # ---- 量能族：水平/波动/动量/比值 四构造，与股票线同一分族口径 ----
    {"family": "量能", "name": "量能·水平MA5", "expr": "ts_mean(volume,5)",
     "note": "量能水平"},
    {"family": "量能", "name": "量能·水平MA20", "expr": "ts_mean(volume,20)", "note": ""},
    {"family": "量能", "name": "量能·波动STD5", "expr": "ts_std(volume,5)",
     "note": "量能波动"},
    {"family": "量能", "name": "量能·波动STD20", "expr": "ts_std(volume,20)", "note": ""},
    {"family": "量能", "name": "量能·动量5d", "expr": "volume/delay(volume,5)-1",
     "note": "放量程度"},
    {"family": "量能", "name": "量能·比5/20",
     "expr": "ts_mean(volume,5)/ts_mean(volume,20)",
     "note": "自身归一，把量能水平除掉"},
    {"family": "量能", "name": "量能·成交额MA20", "expr": "ts_mean(df['amount'],20)",
     "note": "成交额水平（缓存里就是真钱）"},
    {"family": "量能", "name": "量能·成交额STD20", "expr": "ts_std(df['amount'],20)",
     "note": ""},
    # ---- 价量交互族 ----
    {"family": "价量交互", "name": "价量·20日量价相关",
     "expr": "returns.rolling(20).corr(volume)",
     "note": "放量涨/缩量跌的对称性"},
    {"family": "价量交互", "name": "价量·VWAP偏离5d",
     "expr": "close/(ts_sum(df['amount'],5)/ts_sum(volume,5))-1",
     "note": "相对 5 日成交额加权均价"},
    {"family": "价量交互", "name": "价量·Amihud20",
     "expr": "ts_mean(abs(returns)/(df['amount']+1e-9),20)*1e9",
     "note": "单位成交额推动的价格（非流动性）"},
    # ---- 日内/隔夜分解族：ETF 特有（跨境 ETF T+0、日内价含外盘/期指信息） ----
    {"family": "日内隔夜", "name": "隔夜·跳空", "expr": "open/delay(close,1)-1",
     "note": "昨收→今开"},
    {"family": "日内隔夜", "name": "日内·当日涨跌", "expr": "close/open-1",
     "note": "今开→今收"},
    {"family": "日内隔夜", "name": "日内·收盘位置", "expr": "(close-open)/(high-low+1e-9)",
     "note": "收盘在当日区间的位置"},
    {"family": "日内隔夜", "name": "隔夜·20日均跳空",
     "expr": "ts_mean(open/delay(close,1)-1,20)", "note": "持续的隔夜溢价/折价"},
]


def family_specs(family=None):
    """按族筛候选（family 传逗号分隔串；空则全给）。"""
    if not family:
        return list(FAMILY_SPECS)
    want = {x.strip() for x in str(family).split(",") if x.strip()}
    got = [s for s in FAMILY_SPECS if s["family"] in want]
    if not got:
        raise SystemExit(f"[候选] 未知因子族 {sorted(want)}，可选 "
                         f"{sorted({s['family'] for s in FAMILY_SPECS})}")
    return got


def specs_from_rows(rows, family="在库"):
    """把 [{name, expr}]（因子库条目或外部 json）包成 spec，与族候选走同一套函数。"""
    out = []
    for r in rows:
        e = (r.get("expr") or "").strip()
        if not e:
            continue
        out.append({"family": family, "name": r.get("name") or e[:24], "expr": e,
                    "note": family})
    return out


# ==================== 组合层原语 ====================

def slippage_of(amt):
    """20 日均成交额 → 单边滑点（不含佣金）。档位见 `SLIP_TIERS`。

        slip_i = f(amount20_i)   f: >=10 亿→3bp, >=3 亿→5bp, >=1 亿→8bp,
                                 >=3000 万→15bp, 其余→30bp
    NaN 落到最贵档（30bp）而不是最便宜档：算不出容量的标的按最难成交处理，
    宁可对僵尸基苛刻，也不要在回测里给它们一个 6bp 的幻觉。
    """
    a = float(amt) if amt is not None else np.nan
    if not np.isfinite(a):
        return SLIP_TIERS[-1][1]
    for floor, bp in SLIP_TIERS:
        if a >= floor:
            return bp
    return SLIP_TIERS[-1][1]


def cost_series(m, s, cost=None):
    """信号日 s 各标的的单边成本（小数）。

        c_i = 佣金 + slip(amount20_{s,i})     （COST_MODE=tier）
        c_i = cost                            （显式传 float 或 COST_MODE=flat）
    显式传 `cost` 优先于环境变量：压力测试要能一次性把全表压平到 30bp/60bp，
    那时代码档位与历史结论才可对照。
    """
    if cost is not None or COST_MODE != "tier":
        c = COST_ONE_WAY if cost is None else float(cost)
        return pd.Series(float(c), index=m["close"].columns)
    a20 = m["amount20"].loc[s]
    return a20.map(slippage_of) + COMMISSION_RATE


def amount_floor(m, low_run_days=AMT_STREAK):
    """近 `low_run_days` 个交易日的**单日成交额谷值**矩阵（连续低量闸门的左端）。

        floor_{t,i} = min(amount_{t-low_run_days+1..t, i})
    取谷值不取峰值：`floor >= min_amount` 等价于"这些天里**每一天**都够量"，那才是
    挂单出得去的形态；峰值只要求最近一个月里碰巧有过一天够量，实测与均值闸几乎同义
    （末日剔 0 只，见 `shell/etf_lowvol_gate_0924.log`）。缺行（未上市/已退市）使
    窗口 NaN ⇒ 比较判 False ⇒ 不挡，宁可不挡也不虚构低量；退市归 `close.notna()` 管。
    结果缓存在 `m` 里（键 `amt_floor<N>`）：回放每 hold 天调一次 `tradable_mask`，
    不缓存就是每调一次重算一遍 1865×871 的 rolling。
    """
    key = f"amt_floor{low_run_days}"
    if key not in m:
        m[key] = m["amount"].rolling(low_run_days, min_periods=low_run_days).min()
    return m[key]


def load_scale_matrix(m, ffill=SCALE_FFILL, verbose=True):
    """份额面板（#17 采集器落的长表）× 本池收盘价 → 资产规模矩阵（date × code，元）。

        A_{t,i} = shares_{t,i} × close_{t,i}
    为什么用收盘而不是净值：本机拿不到净值历史（同花顺只有最新一日快照），但
    实测 `(份额×收盘)/(份额×净值)` 的 P5~P95 = 0.991~1.023（中位 1.004）⇒ 前复权
    收盘是合格的净值代理，误差远小于 5000 万这条线要判的量级差。净值攒够之后
    （满 `CLEAR_DAYS` 个交易日）应把这里换成真净值，代理口径要一并撤掉。

    缺口处理是这门口径的要害：深市份额逐日、沪市只有月末快照，两市场并集才是 100%
    覆盖。所以份额先按 `ffill` 向前填充至多 `ffill` 个交易日 —— 它表达的是"这条
    规模最多信多久"；超出窗口就是 NaN（不知道），**不是**"没有规模"，更不是 0。
    NaN 在闸门里判 False ⇒ 规模闸不因此剔谁，缺数据的后果由 `verbose` 报出来。
    """
    import fetch_etf_risk_panel as RP       # 只读它的长表，绝不在裁判进程里触发抓取
    key = f"scale_ffill{ffill}"
    if key in m:
        return m[key]
    sh = RP.load_shares_matrix()
    if sh.empty:
        raise SystemExit(f"[规模闸] {RP.RISK_DIR} 里没有份额表，先跑 "
                         f"data/fetch_etf_risk_panel.py --backfill-shares / --daily")
    idx, cols = m["close"].index, m["close"].columns
    sh = sh.reindex(index=idx, columns=cols).sort_index().ffill(limit=ffill)
    aum = sh * m["close"]
    m[key] = aum
    if verbose or not SCALE_COVERAGE.get("share_dates"):
        # 覆盖度是"这门口径能不能信"的读数，静默调用也要把它填上，
        # 否则 risk_readout 打印的覆盖率会停在初值 NaN
        want = m["close"].notna()
        SCALE_COVERAGE["share_cells"] = float(
            (aum.notna() & want).to_numpy().sum() / want.to_numpy().sum())
        SCALE_COVERAGE["share_dates"] = int(sh.notna().any(axis=1).sum())
    if verbose:
        print(f"[规模面板] 份额 {sh.index.min().date()}~{sh.index.max().date()}"
              f"（{SCALE_COVERAGE['share_dates']} 个披露日）"
              f"｜深市 {'有' if RP.has_table(RP.SHARES_SZSE_OUT) else '无'}"
              f" 沪市 {'有' if RP.has_table(RP.SHARES_SSE_OUT) else '无'}"
              f"｜ffill={ffill} 交易日"
              f"｜与日线对齐后可读格子 {SCALE_COVERAGE['share_cells']:.1%}")
    return aum


def streak_below(b, n=None):
    """逐列累计"连续为真"的天数计数器（清盘线读数的载体）。

        s_{t,i} = s_{t-1,i} + 1  若 b_{t,i}   否则 0
    为什么自己写而不用 `rolling(n).min()`：`rolling` 要 n 个非 NaN 才吐数，而份额
    面板的缺口结构性存在（沪市只有月末），那样算出的"连续 60 日"会因为一个空洞而
    整段变 NaN —— 空洞在语义上是"不知道"，不是"没连续"。这里的递推把 NaN 当 False
    （断掉计数），保守方向与规模闸一致：不确定时不判它该清盘。
    O(T) 行、每行一次向量操作，1875×871 实测不到 0.1 秒。
    """
    v = b.fillna(False).values.astype(np.int64)
    out = np.empty_like(v)
    run = np.zeros(v.shape[1], dtype=np.int64)
    for i in range(v.shape[0]):
        run = np.where(v[i] == 1, run + 1, 0)
        out[i] = run
    return pd.DataFrame(out, index=b.index, columns=b.columns)


def premium_matrix(m, ffill=5):
    """折溢价矩阵（date × code，小数）：`prem = close / nav - 1`。

    净值只有同花顺的当日快照（本机无净值历史，见 #17 探源），所以这张表**只能从
    采集器开始攒的那天起算** —— 攒到第 4 个交易日就有 4 天可看，但窗口长度是
    采集天数决定的，不是这里能挑的。`ffill` 只给 5 天：净值缺一天还可以说"昨天的
    净值勉强能用"，缺一周就是节假日后或源方漏披露，那时候的"折溢价"其实是
    6 天涨跌的累积，已经不是这个量的定义了。缺的地方一律 NaN。

    两个必须连带读的坑（09-24 复核，日线镜像与净值同日都到 09-23）：
      1) **覆盖靠"当天采到过净值"，不是历史长度**。池内 851 只里 814 只在 09-23 就有
         当日净值，同日截面中位 **-0.033%**、P5 -0.745%、P95 +0.174% ⇒ 场内 ETF 的
         折溢价本来就是"贴着 0"的量；剩下 37 只净值只到 09-22，被 ffill 顶上来那一格
         混进了当日涨跌。#14 探源时写的"配对上只剩 13 只、全是 513 段、中位 +8.49%"
         是**镜像比净值晚一天**造成的假稀疏，不是覆盖率上限，别再引用那组数。
      2) **跨境段的大尾部不是算错，是用不了**。|折溢价| 最大的 12 只全是纳斯达克 QDII
         （159509 纳指科技 +29.5%、159501 +14.3%、513100 +13.0%…）：里面既有外汇额度
         限制的真溢价，也有 QDII 净值按境外市场**前一交易日**收盘算、盘中价已含当晚
         涨跌的时差错位 —— 本机数据分不开这两份。把它当"买贵了"的信号用会系统性做空
         美股行情。
    """
    import fetch_etf_risk_panel as RP
    key = f"prem_ffill{ffill}"
    if key in m:
        return m[key]
    nav = RP.load_nav_matrix()
    if nav.empty:
        raise SystemExit("[折溢价] 净值长表是空的，先跑 data/fetch_etf_risk_panel.py --daily")
    nav = nav.reindex(index=m["close"].index,
                      columns=m["close"].columns).sort_index().ffill(limit=ffill)
    m[key] = m["close"] / nav - 1
    return m[key]


def clearing_streak(m, days=CLEAR_DAYS, line=CLEAR_LINE, ffill=SCALE_FFILL):
    """清盘线条款计数器：连续多少个交易日 `A_{t,i} < line`（返回整数矩阵）。

    条款原文是"连续 60 个工作日基金资产净值低于 5000 万 ⇒ 须发起清盘/合并"，
    这里读的是**它的前置量**，不是"会不会清盘"的预测：一只连 40 天在 5000 万以下的
    标的，即便最后没清盘，也已经是没人买、申赎套利盘不愿覆盖成本的状态。
    规模用 `load_scale_matrix` 的代理口径（份额×收盘），因此沪市标的的连续段会被
    月末快照 + ffill 撑长 —— 那是数据源的披露节奏，不是这只基金的属性，读数时
    必须连带 `SCALE_COVERAGE` 一起看。
    """
    aum = load_scale_matrix(m, ffill=ffill, verbose=False)
    # 只认"知道它低于线"的那些天：NaN 在 streak_below 里断计数，但必须先写清楚，
    # 否则读者会以为缺口也参与连续段（份额有结构性缺口，见 load_scale_matrix）
    return streak_below(aum.notna() & (aum < line))


def _last_valid(row):
    """一行的最后一个非 NaN 值（整列版本见 risk_readout 里的 apply）"""
    row = row.dropna()
    return row.iloc[-1] if len(row) else np.nan


def risk_readout(m, codes=None, lookback=SCALE_FFILL):
    """给一行的风险体检：清盘线连续天数 + 折溢价 + 规模分位（只读，不改判据）。

    返回 dict 供入口打印。`codes` 一般传当日过闸名单 —— 关心的是"我正要买的这
    几只风险如何"，全池的分布另有规模闸代价探针去量。

    **每只标的各取自己"最近可读到"的那一天**，不取全池末日同一行：份额披露节奏按
    市场不同（沪市只有月末快照），拿 `aum.loc[aum.index[-1]]` 这一行读数会得出一堆
    假 0。（09-24 之前还叠着一条"日线镜像沪市到 09-22、深市停在 09-21"的错位，
    已由 `data/update_etf_daily.py`（#19）拉平，取数口径本身不变。）
    代价是这个读数没有共同的时间截面 —— 它是"体检报告"，不是"排序因子"，
    绝不能进判据。
    清盘线连续天数取该列最后一个**份额可读**日的计数（ffill 之内），否则月末快照
    之外的日期会把连续段无声切断。
    """
    import fetch_etf_risk_panel as RP
    aum = load_scale_matrix(m, verbose=False)
    cols = list(codes) if codes is not None else list(aum.columns)
    cols = [c for c in cols if c in aum.columns]
    A = aum[cols]
    streak = clearing_streak(m)[cols].where(A.notna()).ffill(limit=lookback).apply(_last_valid)
    day = A.index.max()
    a = A.apply(_last_valid) / 1e8
    # 净值是"只能向前攒"的那条腿：一天都没有就不报折溢价，报 NaN 让人看不见
    if RP.has_table(RP.NAV_THS_OUT):
        P = premium_matrix(m)[cols]
        prem = P.apply(_last_valid)
        n_prem_cells = int(P.notna().sum().sum())
    else:
        prem = pd.Series(np.nan, index=cols)
        n_prem_cells = 0
    return {
        "date": day,
        "n": len(cols),
        # 规模可读性分两个读数：末 lookback 窗内 + 过闸格子的全期占比。只看末日
        # 单行会被日线镜像的市场错位带偏（深市份额有 09-22 而镜像停在 09-21 ⇒ 末日
        # 交集为 0，看着像"面板全废"，其实只是差一天）
        "n_scale_known": int(a.notna().sum()),
        "cells_known": float((A.notna() & universe_mask(m)[cols]).to_numpy().sum()
                             / max(1, universe_mask(m)[cols].to_numpy().sum())),
        "n_clearing_line": int((streak >= CLEAR_DAYS).sum()),
        "n_near_line": int(((streak >= CLEAR_DAYS // 2) & (streak < CLEAR_DAYS)).sum()),
        "n_below_line": int((a < CLEAR_LINE / 1e8).sum()),
        "scale_med": float(a.median()) if a.notna().any() else np.nan,
        "scale_min": float(a.min()) if a.notna().any() else np.nan,
        "prem_med": float(prem.median()) if prem.notna().any() else np.nan,
        "prem_max_abs": float(prem.abs().max()) if prem.notna().any() else np.nan,
        # 折溢价必须连着只数与格子数一起读：`prem_med` 是在这几只的"各自最近可读日"上
        # 取的，格子数 = 这些标的在面板里一共占了几个交易日（09-24：过闸 379 只 = 379
        # 格，即每只基本只有一天）。一比就知道这条腿还短到什么程度
        "n_prem_known": int(prem.notna().sum()),
        "n_prem_cells": n_prem_cells,
        "streak_med": float(streak.median()) if streak.notna().any() else np.nan,
        "streak_max": int(streak.max()) if streak.notna().any() else 0,
    }


def universe_mask(m, min_listed=MIN_LISTED, min_amount=MIN_AMOUNT,
                  low_run_days=AMT_STREAK, min_scale=MIN_SCALE):
    """可投域（宽表布尔，date × code）—— 闸门只有这一处定义，基准与回放共用。

        U_{t,i} = close 非缺失                    （当日真有行情）
                  且 listed_days >= min_listed
                  且 amount20 >= min_amount
                  且 min(amount_{t-low_run_days+1..t}) >= min_amount
                  且 (min_scale <= 0 或 规模 A_{t,i} >= min_scale)
    前四道都是"买不买得到"，不含任何"该不该买"。第一道必须写进来而不是留给调用方
    补：`listed_days` 是 cumsum(skipna)、成交额闸门的 rolling 也跳过 NaN，一只已
    退市/当日没有行情的标的在其余三道上**仍然为真**（实测末日这类有 11 只），
    域统计就会报出"过闸 489 / 有行情 478"这种自相矛盾的数。第三道是连续低量：
    近 low_run_days 天**天天**够量才放行，有一天不够就剔（谷值读法，代价见
    `AMT_STREAK` 那段注释）；`low_run_days <= 0` 即关掉这道闸（`ETF_AMT_STREAK=0`），
    做归因时必须能单独撤掉它，否则新闸门和旧结论之间少了一级台阶。把它单列出来
    是因为基准域与建仓域必须是同一个：分开的两处写法一旦漂移，"超可投域"就变成了
    与一个不存在的域比较。

    第五道（规模闸）**默认开在 5 亿**（`min_scale=MIN_SCALE=5e8`，2026-09-24 定档，
    选档代价实测见 `MIN_SCALE` 那段注释）；`ETF_MIN_SCALE=0` 即关掉它，回到 #14 口径。
    规模读不出来的格子（NaN）**不剔**：份额披露有结构性缺口（沪市只有月末），
    "不知道"不能当"规模小"用。
    """
    thin = (amount_floor(m, low_run_days) < min_amount if low_run_days > 0
            else pd.Series(False, index=m["amount"].columns, dtype=bool))
    ok = (m["close"].notna()
          & (m["listed_days"] >= min_listed)
          & (m["amount20"] >= min_amount)
          & ~thin)
    if min_scale > 0:
        aum = load_scale_matrix(m)
        ok &= aum.isna() | (aum >= min_scale)
    return ok


def tradable_mask(m, s, d1=None, min_listed=MIN_LISTED, min_amount=MIN_AMOUNT,
                  use_limit=True, low_run_days=AMT_STREAK, min_scale=MIN_SCALE):
    """信号日 s 的可交易闸门，返回 (布尔 Series, 被涨停挡掉的只数)。

    五道，全是"能不能成交"而不是"该不该买"，五道直接取 `universe_mask` 在 s 日
    那一行（闸门只有一处定义，基准域与建仓域不分叉）：
      1) 当日有 close（本池无停牌概念，缺行即未上市/已退市）
      2) listed_days >= min_listed（次新不买：建仓期跟踪误差未收敛）
      3) amount20 >= min_amount（容量：日成交 3 千万以下的 ETF 进不得）
      4) 近 low_run_days 日的单日成交额**谷值** >= min_amount（连续低量不买：均值闸门
         看的是"这一个月平均有没有量"，这道看的是"最近是不是天天没人交易"，
         后者才是挂单出不去的形态。取谷值不取峰值：峰值只要一个月里碰巧放过一天量
         就放行，实测与均值闸几乎同义（末日剔 0 只），挡不住"偶尔放量"的僵尸基）
      5) 规模 A_{s,i} >= min_scale（#17 的规模闸，默认 `ETF_MIN_SCALE=5e8` 即 5 亿；
         选档代价与"这一档目前算风险阀、不算 alpha"的定性见 `MIN_SCALE` 注释）
    第六道在 `d1` 给出时叠在本函数里，不在 `universe_mask` 里 —— 它要用**明天**的
    开盘价，而基准域是历史宽表，没有"明天"可言：
      6) 涨停近似闸：O_{d1}/C_s - 1 >= 该标的涨跌停幅 ⇒ 买不进
         （科创板 ETF 段按 ±20%、其余 ±10%；深市创业板系无法从代码区分，
          对它们偏严，挡掉的只数逐次报出，读数的人自己判断影响）
    d1=None 表示不叠第六道（日频给名单时明天的开盘价还不存在），前五道照常生效。
    """
    ok = universe_mask(m, min_listed, min_amount, low_run_days, min_scale).loc[s]
    n_block = 0
    if use_limit and d1 is not None and d1 in m["open"].index:
        gap = m["open"].loc[d1] / m["close"].loc[s] - 1
        lim = pd.Series([limit_of(c) for c in ok.index], index=ok.index)
        blocked = ok & gap.notna() & (gap >= lim)
        n_block = int(blocked.sum())
        ok = ok & ~blocked
    return ok, n_block


def composite_score(facs, weights, ranks=None):
    """按权重把若干因子合成一个打分（分数越大越好）。

        S_{t,i} = Σ_j w_j · sign(IC_j) · rank_pct_i(F_{t,i})      rank_pct ∈ (0,1]

    先做日内百分位秩再加权：本池价格量级横跨 0.3~100 元、量能横跨三个数量级，
    拿原始值加权等于让高价/大成交标的自己说了算，那还是价格水平族的旧病。
    `ranks` 可传入预算好的百分位秩（与 facs 同键），省掉重复 rank。
    """
    acc = None
    used = []
    for name, w in weights.items():
        if name not in facs:
            continue
        r = (ranks or {}).get(name)
        if r is None:
            r = facs[name].rank(axis=1, pct=True)
        acc = r * float(w) if acc is None else acc.add(r * float(w), fill_value=0.0)
        used.append(name)
    return acc, used


def icir_weights(stats, top=COMPOSITE_TOP, key="rank_icir", min_abs=0.05):
    """IC 指标表 → (权重 dict, 选用明细)。

        w_j = |ICIR_j| / Σ_k |ICIR_k|      符号 = sign(IC_j)（负 IC 的因子翻向）

    只取 |ICIR| 最大的前 top 条，且要求 |ICIR| >= min_abs：不设下限的话，
    "最好的 5 条"可能全是噪声（|ICIR|~0.02 也能排进前 5），合成出来的分数只是
    一堆噪声的均值。阈值写在这里并可参数化，是为了让入口能报"为什么只用 3 条"。
    """
    use = [(k, v) for k, v in stats.items()
           if np.isfinite(v.get(key, np.nan)) and abs(v[key]) >= min_abs]
    use.sort(key=lambda kv: -abs(kv[1][key]))
    use = use[:top] if top else use
    tot = sum(abs(v[key]) for _k, v in use) or 1.0
    weights = {k: float(np.sign(v["rank_ic"]) * abs(v[key]) / tot) for k, v in use}
    detail = [{"name": k, "weight": weights[k], "icir": v[key],
               "rank_ic": v["rank_ic"], "expr": v.get("expr", "")}
              for k, v in use]
    return weights, detail


def topk_rebalance(score, m, days, k, hold, cost=None,
                   min_listed=MIN_LISTED, min_amount=MIN_AMOUNT,
                   min_scale=MIN_SCALE):
    """每 hold 个交易日调一次、持有截面 top-k 等权的多头组合回放。

    时序（可执行口径）：信号日 s 收盘算分 → s+1 **开盘**建仓 → 持有 hold 日
    到下一信号日开盘平仓。用开盘价而不是收盘价，是因为本线实盘也是人工下单，
    收盘集合竞价的成交价不可控；开盘到开盘收益记作：
        r^open_{i,d} = O_{i,d}/O_{i,d-1} - 1
    组合日收益（期内不再平衡，忽略权重漂移；hold=10、k>=10 时该项量级远小于费率）：
        p_d = Σ_i A_{i,d}·r^open_{i,d} - Σ_{i∈买入} c_i/k - Σ_{i∈卖出} c_i/k_prev
    c_i 是**该只标的自己的**单边成本（`cost_series`：佣金 + 按成交额分档滑点）。
    `cost` 传 float 则全表压平到同一个 c —— 那是压力测试口径，与旧式
    `2c·φ` 在等规模篮子下逐项相等（|买入|=|卖出|=φk），差别只在首笔：旧式在第 0
    次调仓就按双边 2cφ=2c 计费，本式把最后一篮子的清算成本记到期末最后一天，
    总费用一致、时点不同。`cost=None` 时读 `COST_MODE`（默认 tier）。
    A 为当日生效目标权重（篮子内等权 1/k）。调仓日 d1 的收益仍归旧篮子（它到
    d1 开盘才卖），成本也记在 d1，二者不冲突。

    缺失格按 0 处理（`nan_to_num`）而不是让整行变 NaN：本池**每一天**都有尚未上市
    的标的（池子从 28 只长到 871 只），零权重乘以 NaN 会把整个组合日的收益污染成
    NaN，再被 `portfolio_stats` 的 dropna 悄悄丢掉 —— 那是"每天都算不出来"被伪装成
    "少几天数据"。篮子内标的在持有期内消失（退市）时该只按 0 计，等于把这段的
    涨跌当成平坦，偏保守；这种日子数逐次报在 n_missing 里。
    返回 (净日收益, 毛日收益, 统计 dict)。
    """
    ncols = m["close"].shape[1]
    col_of = {c: i for i, c in enumerate(m["close"].columns)}
    w = np.zeros((len(days), ncols), dtype="float64")
    cost_arr = np.zeros(len(days), dtype="float64")
    prev, phis, n_rebal, limits, sizes, amts = None, [], 0, [], [], []
    slip_rows = []
    n_missing = 0
    for i in range(0, len(days) - hold - 1, hold):
        s, d1 = days[i], days[i + 1]
        ok, n_block = tradable_mask(m, s, d1, min_listed, min_amount,
                                    min_scale=min_scale)
        cand = score.loc[s].where(ok).dropna()
        if len(cand) < max(1, k // 2):
            continue
        basket = list(cand.sort_values(ascending=False).index[:k])
        if not basket:
            continue
        c = cost_series(m, s, cost)
        pset = set(prev or [])
        bset = set(basket)
        buys = [b for b in basket if b not in pset]
        sells = [b for b in prev or [] if b not in bset]
        cost_arr[i + 1] += (float(c[buys].sum()) / max(1, len(basket))
                            + float(c[sells].sum()) / max(1, len(prev or basket)))
        slip_rows.append(float(c[basket].mean()))
        phi = 1.0 if prev is None else 1.0 - len(pset & bset) / k
        lo, hi = i + 1, min(i + 1 + hold, len(days))
        w[lo:hi] = 0.0
        w[lo:hi, [col_of[b] for b in basket]] = 1.0 / len(basket)
        prev, n_rebal = basket, n_rebal + 1
        phis.append(phi)
        limits.append(n_block)
        sizes.append(len(basket))
        amts.append(float(m["amount20"].loc[s, basket].mean()))
        # 持有期内篮子里出现缺失开盘价的天数（退市/长停）
        Rsub = m["ret_open"].iloc[lo:hi]
        n_missing += int(Rsub[basket].isna().to_numpy().sum())
    # 期末清算最后一篮子的卖出腿，记在最后一天。补这一笔是为了与旧式 2c·φ 的
    # **总**费用对齐：旧式在第 0 次调仓按 φ=1 收了两倍单边费，那多出来的一倍其实
    # 就是从未被收的最后一笔清算费（它把首尾配成了对）。本式逐腿实收，所以把
    # 期末清算显式补上，两边总账才差不到一个调仓周期的量。费率用该篮子**建仓时**
    # 的档位（它当时的成交额决定它当时贵不贵），不再回看窗口末尾的行情。
    if slip_rows:
        cost_arr[-1] += slip_rows[-1]
    R = np.nan_to_num(m["ret_open"].reindex(index=days).to_numpy("float64"), nan=0.0)
    gross = pd.Series((w * R).sum(axis=1), index=days)
    net = gross - pd.Series(cost_arr, index=days)
    return net, gross, {"n_rebal": n_rebal,
                        "one_way_turnover": float(np.mean(phis)) if phis else np.nan,
                        "basket_size": float(np.mean(sizes)) if sizes else np.nan,
                        "blocked_limit_up": float(np.mean(limits)) if limits else np.nan,
                        "avg_amount20": float(np.mean(amts)) if amts else np.nan,
                        "avg_cost_one_way": float(np.mean(slip_rows)) if slip_rows else np.nan,
                        "n_missing_in_basket": n_missing}


def portfolio_stats(x, name=""):
    """日收益序列 → 年化收益/波动/夏普/最大回撤。

        ann = 252·mean(r)      vol = √252·std(r)      sharpe = ann/vol（无风险利率按 0）
        mdd = min_t( NAV_t / cummax_t NAV - 1 )，NAV_t = Π(1+r)
    ann/vol 是**夏普**不是 IR；相对基准的信息比率另见 excess_stats。
    """
    p = pd.Series(x).dropna()
    if len(p) < 2:
        return {"label": name, "n_days": int(len(p))}
    ann = float(p.mean() * TRADING_DAYS)
    vol = float(p.std() * np.sqrt(TRADING_DAYS))
    nav = (1 + p).cumprod()
    return {"label": name, "ann_return": ann, "ann_vol": vol,
            "sharpe": ann / vol if vol else np.nan,
            "max_drawdown": float((nav / nav.cummax() - 1).min()),
            "n_days": int(len(p))}


def excess_stats(port, bench, name="bench"):
    """相对基准的年化超额与超额 IR。

        ex_t = r^port_t - r^bench_t
        超额年化 = 252·mean(ex)      超额 IR = 超额年化 / (√252·std(ex))
    不单列 t 值：它与 IR 只差 √年数 一个常数，两列同值的数并排放只会误导读数。
    """
    j = pd.concat([port, bench], axis=1, join="inner").dropna()
    if len(j) < 2:
        return {f"excess_{name}_ann": np.nan, f"excess_{name}_ir": np.nan}
    ex = j.iloc[:, 0] - j.iloc[:, 1]
    ann = float(ex.mean() * TRADING_DAYS)
    sd = float(ex.std() * np.sqrt(TRADING_DAYS))
    return {f"excess_{name}_ann": ann,
            f"excess_{name}_ir": ann / sd if sd else np.nan}


def yearly_table(cols):
    """分年度复利收益表（判"最近还管不管用"：全样本一个数会把 2019 年的事摊进 7 年）。"""
    j = pd.DataFrame(cols).fillna(0.0)
    return j.groupby(j.index.year).apply(
        lambda k: pd.Series({c: float((1 + k[c]).prod() - 1) for c in j.columns}))


def equal_weight_benchmarks(m, universe=None):
    """两条基准的日收益（开盘到开盘口径，与组合层同式）。

      1) `ew_all`      全池等权（不筛、不扣费）—— 回答"跑赢市场平均没有"
      2) `ew_universe` 过完容量/次新/连续低量闸门的可投域等权（同样不扣费）—— 回答
         "跑赢同一段真正可交易的市场没有"。用全池当唯一基准会把大量根本买不到的
         僵尸基算进基准，超额被系统性压低；两条都给，差在哪一目了然。
    `universe=None` 时闸门取自 `universe_mask`（与建仓域同一处定义，不分叉）。
    """
    ret = m["ret_open"]
    full = ret.mean(axis=1).rename("ew_all")
    uni = universe if universe is not None else universe_mask(m)
    dom = ret.where(uni).mean(axis=1).rename("ew_universe")
    return full, dom


# ==================== 外部可覆盖的运行参数（入口脚本用） ====================

def run_params():
    """把本模块的口径常量打包，供入口脚本一次性打印（产物可复现性要求）。"""
    return {"universe_dir": UNIVERSE_DIR, "start": EVAL_START, "end": EVAL_END,
            "min_cs": MIN_CS, "min_bars": MIN_BARS, "min_listed": MIN_LISTED,
            "min_ann_vol": MIN_ANN_VOL, "ret_limit": RET_LIMIT,
            "min_amount": MIN_AMOUNT, "amt_streak": AMT_STREAK,
            "min_scale": MIN_SCALE, "clear_days": CLEAR_DAYS,
            "clear_line": CLEAR_LINE, "scale_ffill": SCALE_FFILL,
            "cost_mode": COST_MODE, "slip_tiers": list(SLIP_TIERS),
            "horizons": list(HORIZONS),
            "quintiles": QUINTILES, "top_k": list(TOP_K), "hold": HOLD,
            "cost_one_way": COST_ONE_WAY, "red_bar": RED_BAR,
            "rdagent_bar": RDAGENT_BAR, "near_dup": NEAR_DUP}
