# -*- coding: utf-8 -*-
"""A 股筛选层（股票线两入口共用）：可交易性闸门 + 量能族剔除规则。

诞生背景：这四道量能剔除与三道闸门原本只长在 run_ashare_portfolio_eval.py 里，
是回测的内部函数。但「今天哪些票不该买」是一个需要独立给出名单的日频决策，
两处各写一份迟早漂开（一次改动只改到一边是这类系统最常见的假结论来源），
所以整段搬到这里，回测入口与本模块的日频入口 import 同一份实现。

量能族只做**剔除**，不做多头腿。依据（data/results/ashare_portfolio_eval.csv，
**涨停闸 `board` 档 = 现在的默认档**；39 行 = 13 构造 × top50/100/200，扣双边 15bp
后相对同域等权池的年化超额）：量能族截面 IC 为负（放量/爆量是反向指标），
于是「做多低分侧」= 买最冷门的那批。全表中位只有 **-0.98%**（20 行为负），落在
量能族自己那 24 行上更低（-3.57%，17 行为负）；而**低价股对照**（MA/VWAP/MAX of
Price，与量能毫无关系）同表四组中位 +2.3%~+3.1% 且换手只有 0.08~0.10，量能侧换手
0.16~1.00；动量与比值两条低分侧更是 -14%~-30%。也就是说低量能侧那点
收益是「买冷门/低价一角」的 beta，不是量能自带的信息，多头腿没有独立依据。
反过来「把最响的那 20% 从可投池里踢掉」有独立依据，而且**扛住了两次口径修正**：

| 判据（年化超额，top50/100/200 相同） | 面板量 `$volume` | 真手数 `$volume×$factor` |
|---|---|---|
| 剔除增益 量能波动 STD(Volume,5) | +0.0436 | +0.0418 |
| 剔除增益 量能水平 SMA(Volume,5/10) | +0.0406 / +0.0389 | +0.0388 / +0.0370 |
| 剔除增益 量能动量 / 量能比 | +0.0231 / +0.0234 | +0.0233 / +0.0238 |
| 剔除增益 价格水平族对照 | +0.0153~+0.0172 | 同（不含 volume，逐字不变） |

即：把 `$volume` 换成语义正确的真手数，剔除增益只让掉 0.2pp（4%），Q5 仍是最差组
（STD5 -0.0405 → -0.0333）；而多头腿在闸门那次口径修正后从中位 +0.0223 掉到
**-0.0140**（当时表是 36 行 = 12 构造 × 3 档，flat 档；现表 39 行、`board` 档 -0.0098，
20 行为负没变）。所以本模块的输出分两条，各自踩着不同强度的证据，
**连「剔谁」这一环在两条上用的强度都不一样**（09-24 的 ⑮P0 实测 + ⑰P3 落地）：

* **剔谁（`volume_exclusion`）**：载荷结论。四条构造取并集踢掉最响的 20%，
  已扛住护栏 / 闸门 / 成交量基准三次口径变动（最佳单条增益依次 0.0450 → 0.0436 → 0.0418；
  09-24 ⑲ 又加了第四次变动 = 涨停闸 flat→board→dated，这一格 0.0436 → 0.0437 → 0.0437，
  等于没动）。并集比最佳单条强 **+6.46% vs +4.37%/年**（`board` 档，即现在的默认档），
  所以这张「不该买」的域保持 ≥1 条即剔。
  函数同时给出 `n_hit`（当日几条构造一致判响）——它是下一条的入口判据。
* **买谁的顺序（`buy_candidates`）**：只有一根排序轴，做多 SMA(Volume,20) 低分侧，
  top50/100/200 年化超额 +1.39%/+1.59%/+1.58%（`board` 档，即现在的归档基线）。
  **09-24 换轴**：这根轴此前是 STD(Volume,20)（+0.95%/+2.31%/+3.70%），换过来的理由是
  生产名单那一档（top50）三项全胜、且换手 0.186 vs 0.311（费用直接省一半），
  再加 2021 之后六年 4/6 为正 vs 旧轴 2/6 —— 换轴的完整账单与代价见
  `buy_candidates` docstring 与 CHANGELOG ㉑。它在「多头腿中位为负」那张表里
  属于**为正的少数**（量能族那 24 行里只有 7 行为正：这同簇两条构造各三档 = 6 行，
  截面 spearman 0.93~0.95，剩 1 行是 `SMA(Volume,10)`@top200 的 +0.26%），
  别写成「唯一为正」。代价也说清：全窗口 top100/200
  两档仍是旧轴更高（+2.31%/+3.70%），所以**放大持仓数要重量这条**；而「低价股」
  对照 MA(Price,5) 换轴之后在**三档全部**盖过排序轴（+3.56%/+2.73%/+2.18%、
  换手只有 0.08~0.10），本轴没走准入链，与低价/冷门那角 beta 分不开。
  所以交出来的叫**待买入短名单**（人工先核这一批），不叫买入信号。
  措辞按 09-24 的指示改：本线此前定的是「只出剔除、不出买入」，现改为「剔除 +
  一根实测为正的轴排出短名单」，判定依据与限制一律写在看板「待买入名单」页上。
  **同一张并集掩码直接压在名单上是净负的，而且换轴之后这笔钱大了 4 倍**（现轴
  `ts_mean(volume,20)`：参照 +1.39% → 并集 ≥1 **-3.98%**、单程换手 0.186 → 0.433，
  差 5.4pp/年；换轴前的旧轴是 +0.95% → -0.39%、换手 0.31→0.48，只差 1.3pp。
  账单 `ashare_portfolio_buylist.csv`，旧轴那份 `_std20axis.csv`），所以名单入口只挡
  `n_hit >= ASHARE_BUY_MIN_HITS`（默认 3 条一致判响的共识爆量）；这一档与「完全不挡」
  差在年化的小数点第 6 位 —— 换轴前是**逐字相同**，换轴后不再是，但仍是道几乎
  没咬过票的保险。**机制也换了**：旧轴那句「与量能水平 spearman 0.93~0.95 所以两条
  不咬名单」已作废（排序轴现在就**是**水平那条），新账单给的是恒等式：「单条·量能水平」
  与不剔除逐字相同 ⇒ 一根轴的低分端不可能同时是自己的高分端，这条在名单上恒空操作；
  名单上的伤害 100% 来自与水平近乎正交的**量能动量（-2.03%）与量能比（-1.89%）**。

对照产物：`data/results/ashare_portfolio_eval.csv`（面板量，生产口径，涨停闸 `board`）
与 `ashare_portfolio_eval_realvol.csv`（真手数，复核口径），开关 `ASHARE_VOL_BASIS`；
同一批表另存两份闸门口径 —— `*_flat.csv`（⑯⑰ 那批结论当时的 flat 档，无 `gate` 列）
与 `*_dated.csv`（回测专用的按日期分档），换闸的钱见 `shell/probe_gate_dates_0924.py`。

四条构造互相独立（判重口径实测，data/results/ashare_redundancy_check.csv 与
ashare_factor_eval.csv）：
    水平   SMA(Volume,N)              与窗口均值同簇，量级最大
    波动   STD(Volume,N)              与水平相关 0.9+，但短窗（5 日）RankIC 最强
    动量   V_t/V_{t-N} - 1            与水平几乎不相关（0.03~0.27）
    比值   SMA(Vol,5)/SMA(Vol,20)     自身归一，把量能水平除掉
动量与比值两条呈**倒 U**（低分端和高分端都不太好），所以只能单边用：只踢高分端。

数据口径（09-23 两批探针。**第二批把第一批推翻了一半**，因为第一批只能靠量级印象，
第二批拿外部成交额做绝对判据）：

    盘面价   = $close / $factor              逐票对回新浪不复权收盘，859 个 (票,日)
                                             相对差 max 9.4e-08 —— 这条第一批就是对的
    $volume  = 真实手数 / $factor            即**复权成交量**，不是「手」
    真成交额 = $close × $volume × 100        = 盘面价 × 真实手数 × 100

判据是绝对的、不是相对的：新浪自报成交额 A（元）÷（复权价×$volume×100）在 54 只票上逐票
中位 0.99~1.01（抽样按 $factor 十分位分层，覆盖 0.0070~1.2402 两头），而 ÷（盘面价×$volume×100）
那版逐票散布 **177 倍、0/54 命中**；等价地 V×$factor/L = 1.0000 逐票精确、
corr(log(V/L), log $factor) = **-1.000**。按此口径 09-22 全市场成交额合计 **2.14 万亿**。
来路：shell/probe_units_0923.py（第一批，单位结论已被修正）、probe_live_sources4_0923.py
（第二批，定性）、probe_factor_steps / probe_return_outliers（$factor 台阶）。由此五条推论：
    1) 容量闸门 ASHARE_PORT_MIN_AMOUNT 现在按**真钱**计，阈值的对外含义成立。上一版写的是
       「只在本数据集内成立」，因为那时用 盘面价×$volume，那版把 09-22 全市场算成 53.7 万亿
       （虚高 25 倍；票级误差中位 8.4×、p99 143×、最大 1152×）—— 那是本模块的公式错，
       不是面板的脏。
    2) 但**成交额修对不等于量能构造干净**：$volume 内含 1/$factor，所以「放量」里混着复权
       基准 —— $factor 小（涨幅大或分红多）的票被系统性看成高量能。本模块原话「量能族四条
       构造只用 volume，不经过这两套价，所以不受失真影响」是**错的，已作废**；真手数口径
       （volume_real = $volume×$factor）的复核走 ASHARE_VOL_BASIS=real。
    3) amount20 的正确形式是 复权价 × $volume × 100。09-23 曾把它「修」成盘面价那一版，方向
       反了：那等于给成交额再乘 1/$factor，闸门对老票放水。两笔都实跑过（同一套数据，只是
       单日 vs 20 日均额之差）：按**闸门真正用的 amount20**，09-22 有 **261 只**真成交额不足
       2e7 元的票被错放行、反向只错挡 3 只 —— 这与日频名单文件的逐行差完全对得上
       （5484 → 5226 行，261 出 / 3 进）；按**单日成交额**则是 359 放行 / 1 挡。
    4) 比水平更要紧的是 $factor 的**台阶**也是假的：生产切片（2014-09-03 起 2930 日 × 6072 票）
       里有 **90 个 (票,日)** 的 |复权开盘收益| > 30%，而同日盘面收益落在 ±30% 以内
       （**2023-10-16 一天 64 只**，把全市场等权日收益凭空抬了 **+19.89 个百分点**，
       未裁剪 +19.82% vs 当日中位 -0.52%；全历史没有第二天抬过 0.5pp）。板块分布
       BJ 69 / SZ 15 / SH 6 —— 九成在北交所。不切日期的全 h5 口径是 92 个。
       （09-23 曾记成 326，那是把「|复权开盘收益|>30%」的 3906 个格子与另一套判据混
       起来的错数，09-24 用 shell/count_fake_steps_0924.py 按生产代码重算定在 90。）
       判据：真实除权必在盘面价上留下同幅缺口，注册制新股首周的真暴涨则复权与盘面同时越界
       —— 「只有复权侧越界」就等价于「这是数据不是行情」。
       build_matrices 据此加了 RET_LIMIT 裁剪，上面第 2 段的组合层判据一律用裁剪后的数。
       来路：shell/probe_units_0923.py、probe_units2/3、probe_factor_steps_0923.py、
       probe_return_outliers_0923.py。
    5) 同一道护栏已抽成 guard_ret 供截面评估共用，但**它对截面 IC 几乎没有影响，这本身是
       一条判据**：21 因子全市场实跑，cs_rank_ic 位移 ≤7e-9、cs_ic ≤6.1e-5、ts_ic ≤1.4e-7
       （这个量级等同 float32→float64 的存储与累加顺序差，即护栏在这条路上没有可分辨的
       作用），符号零翻转、名次 spearman 1.000。原因是两者对「一天脏」的处理
       方式不同：IC 是对 ~4057 个交易日取**均值**，2023-10-16 那种脏日摊到分母里只剩千分之一；
       年化收益是对日收益取**累加**，一天 +20 个百分点会永久留在总收益里 —— 所以组合层最大
       动了 1.9pp 而截面几乎不动。含义：**要看数据脏不脏，去看收益类指标，别拿 IC 当体检表。**
"""

import _bootstrap  # noqa: F401  (裸模块名导入的 sys.path 引导)

import json
import os
import re
import time

import numpy as np
import pandas as pd

from config import (ASHARE_DAILY_H5, ASHARE_FACTORS_JSON, ASHARE_PORT_START,
                    ASHARE_PORT_END, ASHARE_PORT_WARMUP_DAYS,
                    ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED,
                    ASHARE_PORT_LIMIT_UP, ASHARE_SAMPLE,
                    ASHARE_SCREEN_QUANTILE, ASHARE_SCREEN_RULES,
                    ASHARE_VOL_BASIS, ASHARE_BUY_TOP_N, ASHARE_BUY_MIN_HITS,
                    ASHARE_BOARD_LIMIT_UP, ASHARE_BOARD_LIMIT_SINCE, ASHARE_LIMIT_NEAR,
                    ASHARE_TRADABLE_GATE,
                    ASHARE_INDUSTRY_CSV, ASHARE_INDUSTRY_MAX_AGE,
                    ASHARE_ORDER_TOP_N, ASHARE_ORDER_MAX_PER_INDUSTRY,
                    ASHARE_ORDER_MAX_PER_BOARD)
from factor_dsl import safe_eval

# 送进 DSL 的是前五个；factor 只用来把复权价折回盘面价，不参与任何表达式
RAW_COLS = ["open", "high", "low", "close", "volume", "factor"]
BENCH = "SH000300"
# 单日收益的数据护栏（不是策略参数）：A 股最宽的涨跌停是北交所 ±30%
RET_LIMIT = 0.30
INDUSTRY_UNKNOWN = "未知"


def board_of(code):
    """面板 instrument（SZ/SH/BJ 前缀）-> 板块名，键与 ASHARE_BOARD_LIMIT_UP 一对一

        BJ*            北交所      ±30%
        SH688* / 689*  科创板      ±20%   （689 是科创板 CDR，别漏）
        SZ30* / 301*   创业板      ±20%
        其余            主板        ±10%

    判板块只用代码前缀，不看价格也不看名称：面板没有名称字段，而代码段是数据里
    自带且永不变的那个量。三段都有权限（09-24 用户确认），所以本函数只用于
    「按各自的限幅判能不能追」与「别全挤在一段」，**不用于排除任何一段**。
    """
    s = str(code).upper()
    if s.startswith("BJ"):
        return "北交所"
    if s.startswith("SH"):
        return "科创板" if s[2:5] in ("688", "689") else "主板"
    return "创业板" if len(s) > 2 and s[2] == "3" else "主板"


def limit_up_of(code):
    """该票「贴涨停」阈值 = 所属板块限幅 × ASHARE_LIMIT_NEAR（主板且未配表时回落旧值）

        阈值_i = limit(板块_i) × 0.95        主板 0.095 / 双创 0.19 / 北交 0.285

    乘 0.95 是沿用旧阈值的余量口径（0.095 本来就是 0.10 的 95%）：贴板与准贴板
    在次日开盘的买不进概率上是一回事，不必卡到分毫。
    """
    b = board_of(code)
    lim = ASHARE_BOARD_LIMIT_UP.get(b)
    if not lim:
        return ASHARE_PORT_LIMIT_UP
    return lim * ASHARE_LIMIT_NEAR


def load_industry_map(path=None, max_age=ASHARE_INDUSTRY_MAX_AGE):
    """外部行业映射 -> (代码→行业 Series, 元信息 dict)；缺表/过期**不报错**，交调用方降级

    行业不是判据、是**执行层的分散约束输入**，所以它缺席时正确行为是「少一条约束、
    照样出名单」，而不是整条日频链路挂掉 —— 与 ST 那道闸同一处理（meta 里如实记
    checked=False，看板显示未知，不假装过滤发生过）。

    口径取舍（09-24）：落盘表的 `行业` 列是「新浪 49 板块为主、缺的用证监会一级兜底」
    的**合并列**。集中度**分析**必须两套分开数（分类粒度不同会把一个实体行业拆成两个
    名字，见 shell/industry_concentration_0924.py 的 --mixed），但**去重约束**用合并列
    反而更保守：证监会那 18 类更粗 ⇒ 更容易判成同行业 ⇒ 只会多拆散，不会漏拆。
    """
    p = path or os.environ.get("STOCK_INDUSTRY_CSV") or ASHARE_INDUSTRY_CSV
    meta = {"path": p, "loaded": False, "n": 0, "age_days": None, "stale": False,
            "fetched": None}
    if not os.path.exists(p):
        meta["reason"] = "文件不存在"
        return pd.Series(dtype=object), meta
    age = (time.time() - os.path.getmtime(p)) / 86400.0
    meta["age_days"] = round(age, 1)
    meta["stale"] = bool(age > max_age)
    d = pd.read_csv(p, dtype=str).fillna("")
    s = pd.Series(d["行业"].to_numpy(), index=d["代码"].to_numpy())
    s = s[s.str.strip() != ""]                      # 空串 = 该票两源都没行业
    meta.update(loaded=True, n=int(len(s)),
                fetched=str(d["抓取日"].max()) if "抓取日" in d else None)
    if meta["stale"]:
        meta["reason"] = f"映射已 {age:.0f} 天没更新（阈值 {max_age} 天），行业去重仍用旧表"
    return s, meta

# (键, 中文名, 表达式, 说明)。表达式一律走主线 DSL，与截面评估/组合层同一条
# 求值路径，不在此另写一套 pandas 版本，否则三处口径迟早分叉。
VOLUME_RULES = [
    ("level", "量能水平", "ts_mean(volume,20)",
     "20 日均量：绝对量能水平，冷门/热门的直接代理"),
    ("volatility", "量能波动", "ts_std(volume,5)",
     "5 日量标准差：短窗波动最强的一条（RankIC -0.0570 / RankICIR -0.599）"),
    ("momentum", "量能动量", "volume/delay(volume,5)-1",
     "5 日量能变化率：与水平几乎不相关（0.03~0.27），倒 U 故只踢高分端"),
    ("ratio", "量能比", "(ts_mean(volume,5))/(ts_mean(volume,20))",
     "短长均量比：自身归一除掉量能水平，倒 U 同上"),
]
RULE_EXPRS = [e for _k, _n, e, _d in VOLUME_RULES]

# 待买入名单的排序轴。**09-24 换轴**（用户裁决）：从 `ts_std(volume,20)`（量能波动）
# 换成 `ts_mean(volume,20)`（量能水平）—— 也就是与上面 `level` 那条**同一个表达式**，
# 方向相反：高分端（热门）踢掉，低分端（冷门）买。这不是笔误，是「一根轴两头用」，
# 换轴的账单与三条反方理由见 CHANGELOG ㉑ 与 buy_candidates 的 docstring。
# 为什么换：组合层 top50（= 生产名单那一档）三项全胜现用旧轴（超额 +1.39% vs +0.95%、
# IR +0.110 vs +0.080、单程换手 0.186 vs 0.311），且分年度在 2021 之后是 4/6 年为正、
# 六年平均 +3.1%/年，而旧轴同段 2/6、平均 -0.7%/年 —— 旧轴的超额集中在 2015~2020。
# 中文名仍叫「安静度」：语义从「波动小所以安静」变成「没人交易所以安静」，
# 每日 CSV 的列名与 meta 的 `expr` 字段一起构成口径凭据（跨日比列前先核对 expr）。
BUY_EXPR = "ts_mean(volume,20)"
BUY_NAME = "安静度"


# ---------- 因子库 ↔ 盘前判据 的接点 ----------
# 为什么要这一节：盘前判据（上面 5 条）与 RD-Agent 回收的因子库是两拨人写的，
# 长在同一套 DSL 上却没有对照表，于是「因子库跑了一晚上、盘前名单一条都没变」
# 这件事没人看得见。接点必须**按表达式比**而不是按名字比 —— 回收来的 name 与
# expr 会错标（实测 factors.json 里名为 "5-day Simple Moving Average of Price"
# 的那条，expr 是 ts_mean(volume,5)/ts_mean(volume,20)，即量能的短长均量比），
# 按 name 匹配会把一条量能构造认成价格构造。
def normalize_expr(e) -> str:
    """表达式折成可比对形状：去掉全部空白。DSL 是白名单求值，不加括号不改序，
    所以只有「空白差异」这一种等价的写法不同需要归一。"""
    return re.sub(r"\s+", "", str(e or ""))


def screen_benchmarks():
    """主线 5 条判据 → [(规范 expr, 角色, 键, 中文名)]，供与因子库对照

    09-24 换轴后排序轴与 `level` 是同一条表达式，所以这里**按表达式合并角色**，
    而不是让 `next()` 只报出先出现的那重身份 —— 否则看板接点表会说这条只是剔除构造，
    而它同时也是买入排序轴（一根轴两头用正是当前口径，不能藏）。
    """
    merged = {}
    for k, n, e, _d in VOLUME_RULES:
        merged.setdefault(normalize_expr(e), (["剔除构造"], [k], [n]))
    key = normalize_expr(BUY_EXPR)
    if key in merged:
        roles, keys, names = merged[key]
        roles.append("排序轴")
        keys.append("buy")
        names.append(BUY_NAME)
    else:
        merged[key] = (["排序轴"], ["buy"], [BUY_NAME])
    return [(e, "+".join(r), "+".join(k), "+".join(n))
            for e, (r, k, n) in merged.items()]


def _dsl_evaluable(expr) -> bool:
    """这条表达式能否在本主线 DSL 里求值（3 行合成数据上试一次）

    factor_matrices 求值失败时是静默 pass 的（不让一条坏表达式拖垮整批），
    所以提升进剔除并集之前必须自己探一次 —— 否则那条构造会悄悄变成空矩阵。
    ma/std/max/min 这四个算子按 FACTOR_DSL 钉死作用于 close，`df` 是求值环境里
    的真实 DataFrame，故 `max(df,20)` 这类模板产物是**可求值**的，不是坏表达式。
    """
    d = pd.DataFrame({"open": [1.0, 2.0, 3.0], "high": [1.0, 2.0, 3.0],
                      "low": [1.0, 2.0, 3.0], "close": [1.0, 2.0, 3.0],
                      "volume": [10.0, 20.0, 30.0]})
    try:
        v = safe_eval(str(expr), d)
        return bool(len(v))
    except Exception:
        return False


def library_factors(path=None):
    """读 RD-Agent 回收的因子库；缺文件返回 []（盘前入口要能脱离因子库独立跑）"""
    path = path or ASHARE_FACTORS_JSON
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)
    return [it for it in items if isinstance(it, dict) and it.get("expr")]


def library_screen_link(path=None):
    """因子库每一条 → 盘前判据对照表（这就是看板缺的那块接点）

    role：剔除构造 / 排序轴 / 未启用；命中判据 = 规范化表达式相等。
    注意 factors.json 里的 mean_ic / rank_ic 是**沙箱里模型预测的 IC**，不是
    这条因子在全市场日线截面上的 IC（后者在 ashare_factor_eval.csv），两者不能
    混用 —— 本函数刻意不携带前者。
    """
    bench = screen_benchmarks()
    rows = []
    for it in library_factors(path):
        key = normalize_expr(it["expr"])
        hit = next(((role, k, n) for e, role, k, n in bench if e == key), None)
        rows.append({"name": it.get("name", ""), "expr": it["expr"],
                     "role": hit[0] if hit else "未启用",
                     "rule_key": hit[1] if hit else "",
                     "rule_name": hit[2] if hit else "",
                     "dsl_ok": _dsl_evaluable(it["expr"])})
    return rows



def load_panel():
    """读源数据转宽表，返回 (个股 open/close/volume 宽表, 基准开盘价序列)"""
    print(f"[数据] 读取 {ASHARE_DAILY_H5}（约 0.8GB）...")
    t0 = time.time()
    raw = pd.read_hdf(ASHARE_DAILY_H5, key="data")
    cols = {c.lstrip("$"): c for c in raw.columns}
    df = raw[[cols[c] for c in RAW_COLS]]
    df.columns = RAW_COLS
    del raw

    dt = df.index.get_level_values("datetime")
    # 暖机窗口切在 START 之前，保证 START 首日就有满窗因子与 20 日均额
    lo = pd.Timestamp(ASHARE_PORT_START) - pd.Timedelta(days=ASHARE_PORT_WARMUP_DAYS)
    sl = dt >= lo
    if ASHARE_PORT_END:
        sl &= dt <= pd.Timestamp(ASHARE_PORT_END)
    df = df.loc[sl]

    inst = np.asarray(df.index.get_level_values("instrument"))
    # 指数按 qlib 代码规则识别：沪市 SH000xxx、深市 SZ399xxx（与截面评估同一判据）
    is_idx = np.array([(s[:2] == "SH" and s[2:].startswith("000"))
                       or (s[:2] == "SZ" and s[2:].startswith("399")) for s in inst])
    stocks = sorted(set(inst[~is_idx]))
    if ASHARE_SAMPLE > 0:
        # 等间隔抽样而非「前 N 只」：代码排序下前 300 只全是北交所（BJ4xxxxx），
        # 拿它冒烟会让流动性闸门整段清零，误判成脚本没跑起来
        step = max(1, len(stocks) // ASHARE_SAMPLE)
        stocks = stocks[::step][:ASHARE_SAMPLE]
    # 基准要在剔指数之前取：SH000300 本身就落在被剔的那一批里
    bench_open = (df.loc[inst == BENCH, "open"].droplevel("instrument").sort_index()
                  if (inst == BENCH).any() else pd.Series(dtype="float32"))
    df = df.loc[~is_idx]
    print(f"[数据] {df.shape[0]:,} 行 / {time.time() - t0:.0f}s，个股 {len(stocks)} 只，"
          f"基准 {BENCH} {'有' if len(bench_open) else '无'}行情")

    wide = {}
    # factor 也一起转宽表：面板价是**复权价**（SH600519 面板 304.93 vs 盘面 1253.79），
    # 人工下单要看的是真实报价，raw = 复权价 / $factor
    for f in ("open", "close", "volume", "factor"):
        m = df[f].unstack("instrument")
        wide[f] = m.reindex(columns=stocks).astype("float32")
        del m
    del df
    return wide, bench_open


def guard_ret(ret, raw_ret, limit=RET_LIMIT):
    """复权收益的假台阶护栏：只裁「复权侧越界而盘面侧正常」那批 (票,日)。

        裁剪集  fake = { |r_adj| > limit  ∧  |r_raw| <= limit }
        结果    r' = r_adj                     （非 fake）
                 clip(r_adj, ±limit)           （fake）

    为什么这个判据成立：真实除权必在盘面价上留下同幅缺口（r_raw 同向大跌），
    注册制新股首周的真暴涨则复权与盘面同时越界；而 A 股最宽涨跌停是北交所 ±30%，
    |r_adj| > 30% 且盘面没动 = 只可能是 $factor 跳了，不是行情。
    实测本面板生产切片有 90 个这样的 (票,日)（2023-10-16 一天 64 只，把全市场等权日收益
    凭空抬了 +19.89 个百分点；最狠的 BJ838227 复权 +91557% 而盘面 -2.3%）。

    裁回 ±limit 而不是置 NaN：置空会改变当天持仓只数与截面样本数，毁掉与旧基线的
    可比性。Series / DataFrame 皆可（逐元素），故组合层与截面评估两条路共用这一份。
    """
    fake = ret.abs().gt(limit) & raw_ret.abs().le(limit)
    return ret.where(~fake, ret.clip(-limit, limit))


def build_matrices(wide):
    """派生宽表：开盘到开盘收益（含 0 填充版）、已有行情天数、20 日均成交额（元）

    面板价是**复权价**，$factor 是复权因子，盘面价 = 复权价 / factor（09-22 茅台
    面板 304.93 / 0.243208 = 1253.80 元，与券商行情一致）。两类派生量因此走两套价：

    - ret_open 用**复权**开盘：pct_change 必须无除息跳空，用盘面价会在每个除息日
      凭空亏一次分红，等于给每条策略都叠加一笔不存在的损失；
      但复权序列里有**假台阶**，见下面 RET_LIMIT 那段，收益先越界裁剪再用。
    - amount20 = mean_t($close · $volume · 100)，即**复权价乘面板量**。
      这条 09-23 折过一次返：先误改成「盘面价 × $volume × 100」（理由是"容量要用真钱计"，
      方向对、公式错），但 $volume 本身已经是复权成交量（= 真实手数/$factor，探针逐票
      V·f/L=1.0000），所以真成交额 = 盘面价×真实手数×100 = 复权价×$volume×100，
      盘面价那版等于再乘一次 1/$factor：09-22 全市场虚高到 53.7 万亿（真值 2.14 万亿），
      闸门对老票放水，359 只真成交额 < 2e7 元的票被错放进可投池。
      限定仍然成立的一句：成交额这个**数**现在是外部可引用的真钱，但量能族构造吃的是
      $volume 而不是真实手数，内含 1/$factor —— 见模块 docstring 数据口径第 2 条，
      真手数口径走 ASHARE_VOL_BASIS=real。

    fill_method=None：停牌缺口不前值填充，缺口收益落在复牌首日（对持有人即应得）。
    """
    op, cl, vol = wide["open"], wide["close"], wide["volume"]
    fc = wide["factor"].astype("float64")
    ret = guard_ret(op.astype("float64").pct_change(fill_method=None),
                    (op / fc).pct_change(fill_method=None)).astype("float32")
    # 盘面价折回 float32 存：这张是 float64 会多占 140MB，而它只用来给人照着下单，
    # float32 的 7 位有效数字对 3 位小数的报价足够
    raw = (cl / fc.where(fc > 0)).astype("float32")
    return {"open": op, "close": cl, "volume": vol, "ret_open": ret,
            "ret_open0": ret.fillna(0.0),
            "listed_days": cl.notna().cumsum(),
            "raw_price": raw,
            # 真实手数 = $volume × $factor（面板量是复权成交量，判据见上面 amount20 那段）
            "volume_real": (vol.astype("float64") * fc).astype("float32"),
            "amount20": (cl.astype("float64") * vol.astype("float64") * 100.0)
                        .rolling(20).mean().astype("float32")}


def factor_matrices(exprs, mtx):
    """按主线 DSL 逐标的求值因子（与截面评估同一条求值路径，不另写一套）

    safe_eval 的求值环境会绑定 open/high/low/close/volume 五个裸名列，缺任一列
    即整式抛错，故本批表达式虽不用 high/low，仍按「等于 close」补齐占位。

    DSL 里 `volume` 绑哪一张表由 ASHARE_VOL_BASIS 决定：adj = 面板 $volume（复权成交
    量，与历史基线、RD-Agent 沙箱同口径），real = volume_real（真实手数）。换 basis
    等于换因子定义本身，只在口径复核对照跑时动它。"""
    vol = mtx["volume_real"] if ASHARE_VOL_BASIS == "real" else mtx["volume"]
    out = {e: {} for e in exprs}
    for code in mtx["close"].columns:
        cl = mtx["close"][code]
        df = pd.DataFrame({"open": mtx["open"][code], "high": cl, "low": cl,
                           "close": cl, "volume": vol[code]})
        for e in exprs:
            try:
                out[e][code] = safe_eval(e, df)
            except Exception:
                pass
    return {e: pd.DataFrame(v).reindex(index=mtx["close"].index,
                                       columns=mtx["close"].columns)
            .astype("float32") for e, v in out.items()}


_GATE_VEC = {}
_GATE_DATED = {}
# 生效日转 Timestamp 只做一次（config 不引 pandas，见 ASHARE_BOARD_LIMIT_SINCE 那段注释）
_SINCE_TS = {k: pd.Timestamp(v[0]) for k, v in ASHARE_BOARD_LIMIT_SINCE.items()}


def limit_up_on(code, on):
    """该票在**日期 on 当天**「贴涨停」阈值 = 当天生效的限幅 × ASHARE_LIMIT_NEAR

        阈值_i(on) = limit(板块_i, on) × 0.95

    限幅是历史事件不是常量（创业板 ±20% 从 2020-08-24、科创板随 2019-07-22 首批上市、
    北交所继承精选层 2020-07-27 的 ±30%），生效日之前用 ASHARE_BOARD_LIMIT_SINCE 里
    那个「之前」值。`on` 传**买入日**（回测里是次日开盘那天），不是信号日。
    """
    b = board_of(code)
    since, pre = _SINCE_TS[b], ASHARE_BOARD_LIMIT_SINCE[b][1]
    lim = ASHARE_BOARD_LIMIT_UP.get(b) or pre
    return (lim if pd.Timestamp(on) >= since else pre) * ASHARE_LIMIT_NEAR


def gate_desc():
    """当前闸门口径的一句话描述，**三档共用这一个构造函数**

    日频入口、回测横幅与看板口径表都调它。分开写三份措辞迟早出现「文档说 board、
    代码是 flat」那种事（09-24 真的发生过：config 里同名开关被定义两次）。
    """
    if ASHARE_TRADABLE_GATE == "flat":
        return f"flat（全线 {ASHARE_PORT_LIMIT_UP:.1%}）"
    per = " / ".join(f"{b} {v * ASHARE_LIMIT_NEAR:.1%}"
                     for b, v in ASHARE_BOARD_LIMIT_UP.items())
    if ASHARE_TRADABLE_GATE == "board":
        return f"board（板块限幅 × {ASHARE_LIMIT_NEAR}：{per}）"
    since = " / ".join(f"{b} {d}" for b, (d, _p) in
                       sorted(ASHARE_BOARD_LIMIT_SINCE.items(), key=lambda kv: kv[1][0]))
    return (f"dated（按买入日生效的板块限幅 × {ASHARE_LIMIT_NEAR}：{per}；"
            f"生效日 {since}）")


def gate_vector(columns, on=None):
    """第 4 道闸的阈值：flat 档是标量，board/dated 档是**与 columns 同序**的阈值向量

    board 档每个调仓日都要对 6000+ 列算一遍 board_of，570 次调仓下来重算是可省的，
    而 pandas 2.2 的 Index 不可哈希（`lru_cache` 直接吃 columns 会 TypeError），
    所以按 columns 对象的身份缓存一份。回测里同一进程的 columns 自始至终是同一个
    对象；真换了对象就重算，不返回错序的向量。

    dated 档多一个日期轴，但阈值只在各段生效日之间跳变 ⇒ 缓存键用「哪些板块已经过了
    生效日」这个组合签名（整段回测里最多出现 4 种），不按日期堆 569 份向量。
    """
    if ASHARE_TRADABLE_GATE == "flat":
        return ASHARE_PORT_LIMIT_UP
    if ASHARE_TRADABLE_GATE == "board":
        if _GATE_VEC.get("columns") is not columns:
            _GATE_VEC["columns"] = columns
            _GATE_VEC["vector"] = pd.Series([limit_up_of(c) for c in columns],
                                            index=columns, dtype="float64")
        return _GATE_VEC["vector"]
    if on is None:
        raise SystemExit("[筛选] dated 档必须给日期 on（判的是买入日那天生效的限幅）——"
                         "宁可报错，不静默按今天那档跑历史")
    ts = pd.Timestamp(on)
    key = tuple(sorted(b for b, d in _SINCE_TS.items() if ts >= d))
    slot = _GATE_DATED.get(key)
    if slot is None or slot["columns"] is not columns:
        slot = {"columns": columns,
                "vector": pd.Series([limit_up_on(c, ts) for c in columns],
                                    index=columns, dtype="float64")}
        _GATE_DATED[key] = slot
    return slot["vector"]


def tradable_mask(s, d1, f, mtx):
    """信号日 s 上每只标的是否可交易；返回 (可选池布尔, 涨停剔除数)

    四道闸门，全是「能不能成交」而不是「该不该买」：
    1) 因子当日有值；2) 已有行情天数 >= ASHARE_PORT_MIN_LISTED（次新不买）；
    3) 20 日均成交额 >= ASHARE_PORT_MIN_AMOUNT（容量）；
    4) 次日开盘相对今收的跳升 < 涨停闸阈值。阈值口径由 `ASHARE_TRADABLE_GATE` 决定：
       `flat` = 全线 ASHARE_PORT_LIMIT_UP=0.095（主板 ±10% 近似，**历史基线那批数
       字用的是这一档**）；`board` = 板块限幅 × ASHARE_LIMIT_NEAR（主板 9.5% /
       科创创业 19% / 北交 28.5%）；`dated` = 同 board，但**按买入日 d1 那天生效的
       限幅**取档（创业板 2020-08-24 前 ±10%，见 ASHARE_BOARD_LIMIT_SINCE）。
       三档在主板上逐字相同，差异全在三段尾板块。

    d1=None 表示 s 是面板最后一天：明天的开盘价还不存在，第 4 道闸**没法前瞻**，
    只能返回前三道的结果并在 n_limit 里报 0。日频入口走这条路，涨停这件事留到
    第二天开盘前人工确认（本系统只出信号、人工下单，正好接得住这个时序）。
    """
    ok = (f.loc[s].notna()
          & (mtx["listed_days"].loc[s] >= ASHARE_PORT_MIN_LISTED)
          & (mtx["amount20"].loc[s] >= ASHARE_PORT_MIN_AMOUNT))
    if d1 is None:
        return ok, 0
    gap = mtx["open"].loc[d1] / mtx["close"].loc[s] - 1
    # dated 档吃 d1：能不能买是**买入那天**的规则说了算，不是信号日
    lim = gate_vector(mtx["open"].columns, d1)
    blocked = gap.notna() & (gap >= lim)
    n_limit = int((ok & blocked).sum())
    return ok & gap.notna() & (gap < lim), n_limit


def active_rules(names=ASHARE_SCREEN_RULES, path=None):
    """启用哪几条剔除构造（默认 = STOCK_SCREEN_RULES，四条量能构造全开）

    额外支持 `lib:<因子库 name>` 前缀：把 RD-Agent 因子库里的某条表达式提成新的
    一条剔除构造。这是**有后果的动作** —— 它会进剔除并集、改变保留池和待买入
    名单，所以默认不开；且必须该条在本主线 DSL 里可求值才允许提名（否则它会静默
    变成空矩阵，见 _dsl_evaluable 的注释）。按 name 指认是人的显式动作，故不做
    表达式归一化匹配，但同名多条时一律报错让人改选，不猜。
    """
    want = [x.strip() for x in str(names).split(",") if x.strip()]
    known = {k for k, _n, _e, _d in VOLUME_RULES}
    unknown = {x for x in want if x not in known and not x.startswith("lib:")}
    if unknown:
        raise SystemExit(f"[筛选] STOCK_SCREEN_RULES 含未知构造 {sorted(unknown)}，"
                         f"可选 {sorted(known)} 或 lib:<因子库 name>")
    rules = [r for r in VOLUME_RULES if r[0] in want]
    for nm in sorted({x[4:] for x in want if x.startswith("lib:")}):
        hits = [it for it in library_factors(path) if it.get("name") == nm]
        if len(hits) != 1:
            raise SystemExit(f"[筛选] 因子库里 name={nm} 匹配到 {len(hits)} 条，"
                             f"要么不存在要么不唯一，不代为挑选")
        it = hits[0]
        if not _dsl_evaluable(it["expr"]):
            raise SystemExit(f"[筛选] 因子库 {nm} 在主线 DSL 里不可求值：{it['expr']}")
        # 库里不少条目与主线构造是**同一条表达式**（只是名字不同）。再提名一次不会
        # 带来新判据，却会让同一条规则在明细表里出现两列、剔除计数翻倍
        dup = [k for k, _n, e, _d in rules if normalize_expr(e) == normalize_expr(it["expr"])]
        if dup:
            raise SystemExit(f"[筛选] 因子库 {nm} 的表达式 ({it['expr']}) 与已启用构造 "
                             f"{dup} 是同一条，无需提名")
        rules.append((f"lib:{nm}", nm, it["expr"],
                      "来自 RD-Agent 因子库，经 STOCK_SCREEN_RULES 人工提名，"
                      "未过组合层回放"))
    return rules


def volume_exclusion(rule_mats, s, pool, quantile=ASHARE_SCREEN_QUANTILE):
    """信号日 s 的量能族剔除：返回 (保留布尔 Series, 明细表)

    逐条构造在**已过闸门的池内**算截面分位 pct = rank/N，pct >= quantile 判剔
    （默认 0.8，即踢掉最响的那 20%）；四条取并集。分位在池内算而不是全市场，
    是为了让「爆量」相对于当天真正可投的那批票来判——否则冷门池会被整体误伤。

        pct_i   = rank_j∈pool(F_j) / count(pool)
        n_hit_i = Σ_rule 1[pct_i >= 0.8]           当日对该构造有值的才计入
        剔除    ⇔ n_hit_i >= 1                     （本函数的 keep，并集口径）

    明细表列 = 各构造的 pct + excluded_by（命中的构造名，分号连接）+ n_hit。
    n_hit 是给**待买入名单**那道独立阈值用的（`ASHARE_BUY_MIN_HITS`，见
    screen_on_date）：并集在这条腿上实测是净负的，故两处踩不同强度，而差距只能
    由「命中几条」这个数说话。一条构造当日无值（不可求值/整列缺数据）时它的 pct
    全 NaN，于是**不计入 n_hit** —— 这会放宽共识判据，日频入口在 meta 里报
    当日实际有几条构造在跑，别看漏。
    """
    rules = active_rules()
    detail = pd.DataFrame(index=pool.index, dtype="float32")
    hits = pd.Series("", index=pool.index, dtype=object)
    n_hit = pd.Series(0, index=pool.index, dtype="int16")
    keep = pool.copy()
    for _key, cn, expr, _doc in rules:
        f = rule_mats.get(expr)
        if f is None or s not in f.index:
            detail[cn] = np.nan
            continue
        v = f.loc[s].reindex(pool.index)
        pct = v.where(pool).rank(pct=True)
        detail[cn] = pct
        fired = pct.notna() & (pct >= quantile)
        keep &= ~fired
        n_hit += fired.astype("int16")
        hits = hits.where(~fired, hits + np.where(hits == "", "", ";") + cn)
    detail["excluded_by"] = hits
    detail["n_hit"] = n_hit
    detail["keep"] = keep
    return keep, detail


def buy_candidates(s, mtx, rule_mats, pool, keep,
                   top_n=ASHARE_BUY_TOP_N, st_codes=None):
    """保留池内的待买入短名单：按「安静度」= SMA($volume,20) 升序取前 top_n。

    四步判据，全是已验证过的东西或纯粹的「能不能成交」，没有新造的 alpha：

        候选  C_i = 闸门(i) ∧ n_hit(i) < ASHARE_BUY_MIN_HITS ∧ 当日有成交(i)
                    ∧ 当日收盘涨幅(i) < 涨停闸阈值(i, s) ∧ i ∉ ST 名单
        排序键 q_i = SMA($volume, 20)_i(s)        升序，安静者在前
        名单  head(q|C, top_n)，等权 1/top_n

    q_i 与剔除用的 `level` 那条是**同一个表达式**（一根轴两头用：高分端踢出域、
    低分端排前面），这是 09-24 用户裁决换轴的结果，不是笔误。

    涨停闸阈值 = `gate_vector(columns, s)`，与回测第 4 道闸**同一个函数**，三档口径：
    `flat` 全线 0.095；`board` 限幅(板块_i)×ASHARE_LIMIT_NEAR；`dated` 再乘上
    「s 那天生效的那一档限幅」（创业板 2020-08-24 前只算 ±10%）。日频只在 s 一天上判，
    所以三档在这里的区别只是常数取哪个；回测侧 `dated` 会逐调仓日换档。

    `keep` 参数传的是**待买入那道闸**（命中 ≥ ASHARE_BUY_MIN_HITS 条构造才挡），
    不是「不该买」页那条并集（≥1）—— 两处阈值不同是有意的，由 screen_on_date 负责
    分开算，判据与实测代价写在 config 的 ASHARE_BUY_MIN_HITS 那段。

    第三道闸为什么按板块分档而不是一个阈值通吃（09-24，用户确认个人户三段均有权限）：
    原先固定 `ASHARE_PORT_LIMIT_UP=0.095` 是主板 ±10% 的近似，对科创板/创业板（±20%）
    和北交所（±30%）就是误伤 —— 全面板实测「涨幅过 0.095」的格子里 81%~85% 离自己的
    涨停还远（`shell/probe_board_limits_0924.py`）。这笔误伤**当天就显形过**：09-23 那道
    追涨停闸在 flat 档挡下 40 只、board 档只挡 29 只（`n_chase`，同一天同一份快照），
    不是「保留池最高涨幅 7.25% 所以一只没咬到」——那句已按实测作废。

    为什么排序轴是「量能水平」而不是「量能波动」（09-24 用户裁决换轴）：组合层
    （`ashare_portfolio_eval.csv`，涨停闸 `board` 档 = 现在的归档基线；做多低分侧、
    扣双边 15bp、超额相对同段同域等权池、top50/100/200 三档年化）里量能族八条构造
    × 3 档只有 7 行为正，其中 6 行就是下面那两条同簇构造（各三档），
    第 7 行是 `SMA(Volume,10)`@200 那格 +0.26%：

        SMA(Volume,20)   +1.39% / +1.59% / +1.58%   IR +0.11/+0.14/+0.17  换手 0.186/0.176/0.162  ← 现用轴
        STD(Volume,20)   +0.95% / +2.31% / +3.70%   IR +0.08/+0.22/+0.41  换手 0.311/0.282/0.257  ← 换轴前
        STD(Volume,5)    -6.86% / -4.51% / -3.04%
        SMA(Volume,5)    -4.09% / -3.05% / -1.60%
        SMA(Volume,10)   -1.40% / -0.98% / +0.26%
        MOM(Volume,5)   -30.00% / -26.31% / -22.48%
        MOM(Volume,20)  -16.29% / -15.44% / -14.17%
        量能比          -21.90% / -19.58% / -17.61%

    两条为正的构造是**同一簇**（截面 spearman 0.93~0.95），所以换轴不增信息，换的是
    「在同一个信号上取哪个代理」。取水平而不是波动的实测理由有三条：① 生产名单那一档
    （top50）三项全胜（+1.39% vs +0.95%、IR +0.11 vs +0.08、换手 0.186 vs 0.311，
    换手少 40% 直接是省下的双边 15bp）；② 分年度（`ashare_portfolio_eval_yearly.csv`，
    top100 档、相对同域等权池）现用轴 8/12 为正、中位 +3.25%，旧轴 7/12、中位 +5.56%
    —— 旧轴赢在幅度、输在命中，而**分段一拆就反过来了**：2021 起六年现用轴 4/6 为正、
    六年均值 +3.1%/年，旧轴 2/6、六年均值 **-0.7%/年**，旧轴那 +5.6% 的中位几乎全来自
    2015~2020（+5.8%~+20.5%）；③ 2025 那一年现用轴 +3.9% 为正、旧轴 -2.3% 为负，
    即「最近还管不管用」这一问上只有现用轴答得出正面。
    （flat 档那一份另存 `*_flat.csv`，同一格是 +0.99%/+2.34%/+3.74%；换闸只让掉 0.04pp，
    下面这些结论没有一条因为档位而翻符号。）

    换轴带来的三件必须知道的事，都不构成否决但都要盯：
    ① **与剔除侧同表达式**：`level` 那条踢高分端 20%、本轴买低分端，一根轴两头用。
       它过不了因子库「不是换皮」那道关（判重实测与在库量能族 0.909~0.927），
       但作为**已启用判据的内部复用**没有新信息风险 —— 代价是「量能水平」这一根轴
       在系统里的权重变高了，它一旦失效，域和名单同时坏。
       09-24 重跑的名单腿账单（`ashare_portfolio_buylist.csv`，13 行 × top50，
       `board` 档）顺手把这条推到一个极端：`level` 那条剔除**对本名单是恒空操作**
       ——「单条·量能水平」与「参照·不剔除」在年化/IR/换手/均额四列上**逐字相同**
       （一根轴的低分端不可能同时是自己的高分端）。所以换轴后并集压在名单上的
       全部伤害（+1.39% → -3.98%）来自**量能动量 -2.03% 与量能比 -1.89%** 这两条
       近乎正交的构造；「留一·去掉量能水平」与「留一·去掉量能波动」两行则与
       「并集≥1」逐字相同，等于承认这两条在名单腿上一格未贡献。
    ② **可执行性不能拿全窗均值预判**：12 年平均这 50 只的 20 日均额从 1.051 亿降到
       0.947 亿（`avg_amount_20d`），但 09-23 那个截面**方向相反** —— 均额中位从
       0.467 亿升到 0.748 亿、一手金额中位从 1,234 元升到 1,663 元（`$volume` 是
       **手数**，按均量排序挑的是「成交笔数少」那一角，不等于「成交金额小」）。
       所以每一日都要按当日名单的 20 日均额与一手金额逐只核，别看年化摘要。
    ③ **幅度不如旧轴**：全窗口 top100/200 两档仍是旧轴更高（+2.31%/+3.70% vs
       +1.59%/+1.58%）。本名单固定 top50，所以取现用轴；要放大持仓数得重新量这条。

    还有一条老边界没变：与「低价股」对照**分不开** —— 同一张表里 MA(Price,5) 低分侧
    三档给 +3.56% / +2.73% / +2.18%、单程换手只有 0.08~0.10，换轴之后它在**三档全部**
    盖过排序轴（换轴前是 top50/top100 盖过、top200 被本轴反超）。分年度它是 9/12 为正
    但中位只 +1.81%（现用轴 8/12、+3.25%）。也就是说这份超额里有多少来自「买低价/冷门
    那角」、有多少来自量能本身，现有证据判不了 —— 这根轴够格决定「先看谁」，不够格
    承诺收益，这也是名单上还要再叠三道执行性硬闸的原因。

    ST 判据的来路：面板没有股票名称字段，ST/*ST 只能从**当日收盘快照**的「名称」列
    拿（09-24 实测当日 204 只 = 110 ST + 94 *ST）。历史日没有快照就没跑这道闸，
    stats 里如实记 st_checked=False —— 不假装一个没发生过的过滤发生过。
    退市整理期那类「名称不带退字」的风险不在本判据能力内，见 CHANGELOG 遗留 #80。
    """
    cl, raw, vol = mtx["close"], mtx["raw_price"], mtx["volume"]
    idx = cl.index
    i = idx.get_loc(s)
    if i < 1:
        raise SystemExit(f"[待买入] 信号日 {s.date()} 是面板首日，没有昨收可比")
    prev = idx[i - 1]

    q = rule_mats.get(BUY_EXPR)
    if q is None or s not in q.index:
        raise SystemExit(f"[待买入] 因子矩阵里没有 {BUY_EXPR}，排不了序："
                         f"入口需把 BUY_EXPR 一起送进 factor_matrices")
    v = q.loc[s].reindex(cl.columns).astype("float32")

    # 第一道：闸门 + 那道**较松**的量能剔除（keep 由调用方给定判据，此处不重算）
    step1 = pool & keep & v.notna()
    # 第二道：当日得真有成交。面板里停牌是 NaN，但 $volume=0 的格子也存在（数据源
    # 挂了根零成交的假线），两种都买不到。**不**要求昨天也有行情：停牌一天后复牌的票
    # 照样能买，只是下一道的昨收取不到，涨幅判不了而已
    traded = step1 & cl.loc[s].notna() & raw.loc[s].notna() & vol.loc[s].gt(0)
    # 第三道：当日收盘已贴着涨停的不追。用**复权**收盘算日涨幅，并过同一道假台阶
    # 护栏（否则 2023-10-16 那批北交所脏格会被当成「涨停」误剔）；昨收取不到的缺口日
    # 涨幅为 NaN，pandas 的阈值比较对 NaN 一律 False，即「判不了就不剔」，另计数报出
    chg = guard_ret(cl.loc[s] / cl.loc[prev] - 1.0,
                    raw.loc[s] / raw.loc[prev] - 1.0)
    # 阈值走 gate_vector —— 与回测第 4 道闸**同一个函数**，两边不许各写一份判据。
    # 日期传 s：这一道判的是「s 日收盘是否已贴板」（涨幅也是 s 日的），而回测那道判的是
    # d1 开盘能不能买到，所以它传 d1 —— dated 档下两者吃**各自那天生效**的限幅，不是笔误。
    lim = gate_vector(cl.columns, s)
    chase = traded & chg.ge(lim)
    # 第四道：ST/*ST（仅当日有快照时生效）
    is_st = pd.Series(cl.columns.isin(st_codes or set()), index=cl.columns)
    cand = traded & ~chase & ~(is_st if st_codes else pd.Series(False, index=cl.columns))

    ok_v = v.where(cand)
    pct = ok_v.rank(pct=True)                      # 候选池内分位，人读用
    order = ok_v.dropna().sort_values()
    head = order.index[:top_n]

    # 与组合层回放的那一步对账：那边只过闸门就取低分侧 top_n，本名单多了三道闸。
    # 差异只报「换掉几只」，不改判据本身
    plain = v.where(step1).dropna().sort_values().index[:top_n]
    stats = {
        "n_step1": int(step1.sum()), "n_traded": int(traded.sum()),
        "n_chase": int(chase.sum()), "n_st": int((traded & is_st).sum()) if st_codes else 0,
        "st_checked": bool(st_codes), "n_cand": int(cand.sum()),
        "n_chg_unknown": int((traded & chg.isna()).sum()),
        # 第三道闸按板块拆的计数：老口径下这些数会全砸在「主板」那一档，拆开才看得见
        # 「双创/北交所被误当涨停」每天到底发不发生（09-23 当日四档全 0）
        "n_chase_by_board": {k: int(x) for k, x in
                             pd.Series([board_of(c) for c in cl.columns[chase.to_numpy()]]
                                       ).value_counts().items()},
        "top_n": len(head),
        "n_diff_vs_backtest": len(set(plain) - set(head)),
        # 第 top_n 名的安静度值 = 本名单的入线阈值，次日对比可用
        "quiet_cut": float(order.iloc[len(head) - 1]) if len(head) else float("nan"),
    }
    out = pd.DataFrame({
        "rank": np.arange(1, len(head) + 1, dtype="int32"),
        "板块": [board_of(c) for c in head],
        BUY_NAME: order.loc[head].to_numpy(),
        "安静度分位": pct.reindex(head).to_numpy(),
        "当日涨幅": chg.reindex(head).to_numpy(),
        "listed_days": mtx["listed_days"].loc[s].reindex(head).to_numpy(),
        "weight": np.full(len(head), 1.0 / max(top_n, 1), dtype="float32"),
    }, index=pd.Index(head, name="code"))
    return out, stats


def screen_on_date(s, d1, mtx, rule_mats, gate_mat,
                   quantile=ASHARE_SCREEN_QUANTILE,
                   top_n=ASHARE_BUY_TOP_N, st_codes=None, with_buy=True,
                   buy_min_hits=ASHARE_BUY_MIN_HITS):
    """一个信号日的完整筛选：闸门 → 量能剔除 →（保留池内）待买入排序

    返回 dict(universe=过闸池, keep=保留, dropped=被剔, detail=分位明细,
    n_limit_up=涨停剔除数, buy=待买入名单, buy_stats=该名单各环节计数)。
    gate_mat 是算闸门时用来判「因子有值」的那张矩阵，日频入口传量能水平那条即可。
    with_buy=False 时不排待买入名单（rule_mats 里没有 BUY_EXPR 的旧调用方用这条）。

    **两套剔除强度，一张明细表**（P3，09-24 用户选，判据见 config 里
    ASHARE_BUY_MIN_HITS 那段）：

        keep      = pool ∧ n_hit >= 1        域/展示口径，「不该买」名单走这条
        keep_buy  = pool ∧ n_hit <  buy_min_hits      只用来挡待买入名单的入口

    为什么两处不同：并集在减法腿（把爆量的从池里踢掉）值 +6.46%/年，压在待买入
    名单上却从 +0.95% 掉到 -0.39%（换手 0.31→0.48，费用 2.49pp > 毛收益 1.15pp）。
    两个数踩的是同一条判据、不同的**用途**，所以各自按各自实测的最优强度取值，
    而不是强行统一成一个。buy_min_hits=1 时 keep_buy 与 keep 等价（旧口径）。
    """
    ok, n_limit = tradable_mask(s, d1, gate_mat, mtx)
    pool = pd.Series(ok, index=gate_mat.columns)
    keep, detail = volume_exclusion(rule_mats, s, pool, quantile)
    r = {"universe": pool, "keep": keep, "dropped": pool & ~keep,
         "detail": detail[pool], "n_limit_up": n_limit}
    if with_buy and BUY_EXPR in rule_mats:
        n_hit = detail["n_hit"].reindex(pool.index).fillna(0)
        keep_buy = pool & (n_hit < buy_min_hits)
        r["buy"], r["buy_stats"] = buy_candidates(
            s, mtx, rule_mats, pool, keep_buy, top_n, st_codes)
        r["buy_stats"]["buy_min_hits"] = int(buy_min_hits)
        # 共识爆量：≥buy_min_hits 条构造一致判响，只被这道闸挡掉的只数
        r["buy_stats"]["n_consensus"] = int((pool & ~keep_buy).sum())
        # 名单与「不该买」域之间今日的实际差：并集剔了、但待买入闸放行的只数
        r["buy_stats"]["n_lenient_vs_union"] = int((pool & ~keep & keep_buy).sum())
    return r


def order_candidates(buy, industry=None, top_n=ASHARE_ORDER_TOP_N,
                     max_per_industry=ASHARE_ORDER_MAX_PER_INDUSTRY,
                     max_per_board=ASHARE_ORDER_MAX_PER_BOARD):
    """下单层：从观察名单（buy_*.csv 那 50 只）里按名次挑 top_n 只真下单（B 路，09-24）

    这一层**不新造任何 alpha**，也不动排序轴：它只把「先人工核这一批」的排队顺序
    裁成一个装得下的仓位表，代价是两条**分散约束**。观察名单已按安静度升序排好，
    本函数按名次自上而下走一遍，贪心取够即止：

        取_i = 名次序扫描 i∈buy，取当且仅当
                 板块数[板块_i] < max_per_board
                 ∧ (行业_i = 未知 ∨ 行业数[行业_i] < max_per_industry)
        停止：取满 top_n 只，或名单走完
        权重：每只 = 1/top_n（**按槽位给权**，凑不满时余量自动是现金，
              不按实际只数归一 —— 否则「今天只挑到 3 只」会被悄悄放大成满仓 3 只）

    三条口径为什么长这样（都有实测来源，见 CHANGELOG ⑬⑭⑮）：
    * **约束是分散度，不是白名单**：用户个人户科创板/创业板/北交所都有权限，
      所以任何一段都不拉黑；`max_per_board` 只防「5 只全在同一段」。
      这条尤其要紧：09-24 实测这根轴在 2024~2025 会把 5 个席位里的 2~3 个交给北交所。
    * **行业未知不参与去重**：外部映射对北交所缺 85%、科创板缺 96%，把「未知」当
      一个行业去重等于变相拉黑那两段 —— 与上一条直接冲突。代价如实说：未知票的
      行业约束是**没生效**的，所以 stats 里单独报 n_unknown，看板把那几行标出来。
    * **不足不放宽**：放宽（比如行业上限临时提到 2）就是在本层改判据，而本层的
      全部依据是「执行性」。凑不满就少买几只、把 shortfall 报出来给人看。

    行业去重用的是**合并列**（新浪 49 板块为主 + 证监会一级兜底，见 load_industry_map），
    分类粒度比集中度分析用的口径粗 ⇒ 约束偏保守，只会多拆散不会漏拆散。
    """
    ind = industry if industry is not None else pd.Series(dtype=object)
    picked, skipped = [], []
    n_ind, n_brd = {}, {}
    for code, row in buy.iterrows():
        if len(picked) >= top_n:
            break
        b = str(row.get("板块") or board_of(code))
        g = str(ind.get(code, "")).strip() or INDUSTRY_UNKNOWN
        if n_brd.get(b, 0) >= max_per_board:
            skipped.append({"code": code, "rank": int(row["rank"]), "reason": f"板块已满:{b}"})
            continue
        if g != INDUSTRY_UNKNOWN and n_ind.get(g, 0) >= max_per_industry:
            skipped.append({"code": code, "rank": int(row["rank"]), "reason": f"行业已满:{g}"})
            continue
        n_brd[b] = n_brd.get(b, 0) + 1
        if g != INDUSTRY_UNKNOWN:
            n_ind[g] = n_ind.get(g, 0) + 1
        picked.append({"code": code, "slot": len(picked) + 1,
                       "obs_rank": int(row["rank"]), "板块": b, "行业": g,
                       "weight": 1.0 / top_n})
    out = pd.DataFrame(picked).set_index("code") if picked else pd.DataFrame(
        columns=["slot", "obs_rank", "板块", "行业", "weight"], dtype=object)
    out.index.name = "code"
    stats = {"top_n": top_n, "n_picked": len(picked),
             "shortfall": top_n - len(picked),
             "max_per_industry": max_per_industry, "max_per_board": max_per_board,
             # 观察名单自己的板块构成：shortfall 到底是「约束太严」还是「名单太偏」，
             # 看这个数就能分开 —— cap 定得比名单里最大板块数还小，就永远凑不满
             "n_board_in_list": {k: int(x) for k, x in pd.Series(
                 [str(r.get("板块") or board_of(c)) for c, r in buy.iterrows()]
             ).value_counts().items()},
             "n_industry_used": len(n_ind),
             "n_unknown": int((out["行业"] == INDUSTRY_UNKNOWN).sum()) if len(out) else 0,
             "industry_available": bool(len(ind)),
             "n_skipped": len(skipped), "skipped": skipped[:12],
             "weight_sum": float(out["weight"].sum()) if len(out) else 0.0}
    return out, stats
