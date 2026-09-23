# -*- coding: utf-8 -*-
"""A 股筛选层（股票线两入口共用）：可交易性闸门 + 量能族剔除规则。

诞生背景：这四道量能剔除与三道闸门原本只长在 run_ashare_portfolio_eval.py 里，
是回测的内部函数。但「今天哪些票不该买」是一个需要独立给出名单的日频决策，
两处各写一份迟早漂开（一次改动只改到一边是这类系统最常见的假结论来源），
所以整段搬到这里，回测入口与本模块的日频入口 import 同一份实现。

量能族只做**剔除**，不做多头腿。依据（09-23 组合层复核，data/results/ashare_portfolio_eval.csv
的护栏后复算版，top200、扣双边 15bp 后相对同域等权池的年化超额）：量能族截面 IC 为负（放量/
爆量是反向指标），于是「做多低分侧」= 买最冷门的那批。它的超额是 -0.3%~+6.4%，看着像有，但
**低价股对照**（MA/VWAP/MAX of Price，与量能毫无关系）同一段给出 +4.7%~+5.6%，而量能侧的换手
是它的 3~12 倍（0.21~0.63 vs 0.05~0.07）；动量与比值两条低分侧更是 -13%~-21%。也就是说低量能
侧赚的是「买冷门/低价一角」的 beta，不是量能自带的信息，多头腿没有独立依据——而且短窗那两条
（STD5 / SMA5）在 top50 上的正超额 +0.15%/+1.01% 加了下面的收益护栏后直接翻负
（-1.13%/-0.58%），说明修复前那点「像有」里有一部分是数据假台阶给的，不是信号。
反过来「把最响的那 20% 从可投池里踢掉」有独立依据：水平与波动两条的剔除增益
+4.0%~+4.5%/年，同一段对照只有 +1.8%~+2.0%，四倍差距，且这个数在成交额口径修复与收益护栏
前后几乎一动不动（0.0439→0.0438 这一类，万分之一量级）。**载荷结论稳、多头腿数字虚**，
正是本模块只输出「剔谁」、绝不输出「买谁」的理由。

四条构造互相独立（判重口径实测，data/results/ashare_redundancy_check.csv 与
ashare_factor_eval.csv）：
    水平   SMA(Volume,N)              与窗口均值同簇，量级最大
    波动   STD(Volume,N)              与水平相关 0.9+，但短窗（5 日）RankIC 最强
    动量   V_t/V_{t-N} - 1            与水平几乎不相关（0.03~0.27）
    比值   SMA(Vol,5)/SMA(Vol,20)     自身归一，把量能水平除掉
动量与比值两条呈**倒 U**（低分端和高分端都不太好），所以只能单边用：只踢高分端。

数据口径（09-23 三个探针核过，见 shell/probe_units_0923.py / probe_units2 / probe_units3，
结论带限定范围，不是「已彻底澄清」）：
    盘面价 = $close / $factor        $volume 单位 = 手，成交额 = 盘面价 × $volume × 100
五只跨板块抽样能把盘面价对回真实报价（茅台 1253.80 / 工行 8.12 / 宁德 304.63 /
中芯 122.41 / 寒武纪 1121.00，全市场中位 14.49 元）；×100 后主板单票日成交额落在真实量级
（茅台 127 亿、工行 31 亿、平安 25 亿、浦发 7 亿），×1 则全体小两个数量级，所以「手」是
唯一站得住的单位。

但**绝对量级不可当市场行情引用**：本面板 68/30 段的 $factor 是失真的 —— 503 只 raw/adj > 50
（寒武纪 142 倍、中芯 83 倍，而这些票上市以来基本不分红，复权因子不可能这么大；中石化
$factor 甚至 > 1，方向就是错的），于是 09-22 全市场成交额算出来是 53.7 万亿，约真实市场 20 倍。
由此五条推论：
    1) ASHARE_PORT_MIN_AMOUNT 的阈值只在**本数据集内**相对成立，别把它当成外部依据；
    2) 量能族四条构造只用 $volume，不含价格也不含 $factor，所以单位与 factor 失真都不影响
       它们的截面分位 —— 上面「只做剔除」那条结论与这场单位风波无关；
    3) amount20 从复权价改成盘面价仍要改：复权价乘出来的成交额连量纲含义都没有（被各自
       失真的 factor 除过），会把分红多的老票系统性看小。实拍 261 只因此被挡在闸门外
       （可投池 5226 → 5484）。信号表里的「成交额中位」一类数字同理，是本数据集内部的
       相对量，不要对外引用成市场事实。
    4) 比水平更要紧的是 $factor 的**台阶**也是假的：全样本 326 个 (票,日) 的 |复权开盘收益|
       > 30%，而同日盘面收益落在 ±30% 以内（2023-10-16 一天 64 只，把全市场等权日收益凭空
       抬了 +20 个百分点）。真实除权必在盘面价上留下同幅缺口，注册制新股首周的真暴涨则
       复权与盘面同时越界 —— 「只有复权侧越界」就等价于「这是数据不是行情」。
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

import time

import numpy as np
import pandas as pd

from config import (ASHARE_DAILY_H5, ASHARE_PORT_START, ASHARE_PORT_END,
                    ASHARE_PORT_WARMUP_DAYS, ASHARE_PORT_MIN_AMOUNT,
                    ASHARE_PORT_MIN_LISTED, ASHARE_PORT_LIMIT_UP,
                    ASHARE_SAMPLE, ASHARE_SCREEN_QUANTILE,
                    ASHARE_SCREEN_RULES)
from factor_dsl import safe_eval

# 送进 DSL 的是前五个；factor 只用来把复权价折回盘面价，不参与任何表达式
RAW_COLS = ["open", "high", "low", "close", "volume", "factor"]
BENCH = "SH000300"
# 单日收益的数据护栏（不是策略参数）：A 股最宽的涨跌停是北交所 ±30%
RET_LIMIT = 0.30

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
    实测本面板有 326 个这样的 (票,日)（2023-10-16 一天 64 只，把全市场等权日收益
    凭空抬了 +20 个百分点；最狠的 SZ300506 复权 +386% 而盘面 +0.3%）。

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
    - amount20 = mean_t(盘面价 · volume · 100)，×100 是因为 $volume 单位是手。
      这条 09-23 才修：原先直接拿复权价乘，成交额被按各自 factor 系统性缩放（老分红股
      factor 低到 0.24，即低估 4 倍），容量闸门 2e7 元在 09-22 当日因此误剔 261/5518 只
      ——全是高分红的老股票，方向单一，不是噪声而是偏。容量是「用真钱买得到吗」，
      必须按真钱计。
      限定：本面板 68/30 段的 $factor 失真（503 只 raw/adj>50），所以 amount20 的
      **绝对量级只在本数据集内成立**，阈值的对外含义不作数；相对排序方向是对的
      （详见模块 docstring 的单位核查）。量能族四条构造只用 volume，不经过这两套价。

    fill_method=None：停牌缺口不前值填充，缺口收益落在复牌首日（对持有人即应得）。
    """
    op, cl, vol = wide["open"], wide["close"], wide["volume"]
    fc = wide["factor"].astype("float64")
    ret = guard_ret(op.astype("float64").pct_change(fill_method=None),
                    (op / fc).pct_change(fill_method=None)).astype("float32")
    # 盘面价与成交额折回 float32 存：这两张是 float64 会各多占 140MB，
    # 而闸门只判量级（2e7 元），float32 的 7 位有效数字足够
    raw = (cl / fc.where(fc > 0)).astype("float32")
    return {"open": op, "close": cl, "volume": vol, "ret_open": ret,
            "ret_open0": ret.fillna(0.0),
            "listed_days": cl.notna().cumsum(),
            "raw_price": raw,
            "amount20": (raw.astype("float64") * vol.astype("float64") * 100.0)
                        .rolling(20).mean().astype("float32")}


def factor_matrices(exprs, mtx):
    """按主线 DSL 逐标的求值因子（与截面评估同一条求值路径，不另写一套）

    safe_eval 的求值环境会绑定 open/high/low/close/volume 五个裸名列，缺任一列
    即整式抛错，故本批表达式虽不用 high/low，仍按「等于 close」补齐占位。"""
    out = {e: {} for e in exprs}
    for code in mtx["close"].columns:
        cl = mtx["close"][code]
        df = pd.DataFrame({"open": mtx["open"][code], "high": cl, "low": cl,
                           "close": cl, "volume": mtx["volume"][code]})
        for e in exprs:
            try:
                out[e][code] = safe_eval(e, df)
            except Exception:
                pass
    return {e: pd.DataFrame(v).reindex(index=mtx["close"].index,
                                       columns=mtx["close"].columns)
            .astype("float32") for e, v in out.items()}


def tradable_mask(s, d1, f, mtx):
    """信号日 s 上每只标的是否可交易；返回 (可选池布尔, 涨停剔除数)

    四道闸门，全是「能不能成交」而不是「该不该买」：
    1) 因子当日有值；2) 已有行情天数 >= ASHARE_PORT_MIN_LISTED（次新不买）；
    3) 20 日均成交额 >= ASHARE_PORT_MIN_AMOUNT（容量）；
    4) 次日开盘相对今收的跳升 < ASHARE_PORT_LIMIT_UP（涨停买不进，主板 ±10% 近似）。

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
    n_limit = int((ok & gap.notna() & (gap >= ASHARE_PORT_LIMIT_UP)).sum())
    return ok & gap.notna() & (gap < ASHARE_PORT_LIMIT_UP), n_limit


def active_rules(names=ASHARE_SCREEN_RULES):
    """按 STOCK_SCREEN_RULES 逗号串挑启用哪几条构造（默认全开）"""
    want = {x.strip() for x in str(names).split(",") if x.strip()}
    known = {k for k, _n, _e, _d in VOLUME_RULES}
    if not want or not want <= known:
        raise SystemExit(f"[筛选] STOCK_SCREEN_RULES 含未知构造 {sorted(want - known)}，"
                         f"可选 {sorted(known)}")
    return [r for r in VOLUME_RULES if r[0] in want]


def volume_exclusion(rule_mats, s, pool, quantile=ASHARE_SCREEN_QUANTILE):
    """信号日 s 的量能族剔除：返回 (保留布尔 Series, 明细表)

    逐条构造在**已过闸门的池内**算截面分位 pct = rank/N，pct >= quantile 判剔
    （默认 0.8，即踢掉最响的那 20%）；四条取并集。分位在池内算而不是全市场，
    是为了让「爆量」相对于当天真正可投的那批票来判——否则冷门池会被整体误伤。

        pct_i = rank_j∈pool(F_j) / count(pool)      剔除 ⇔ ∃rule: pct >= 0.8

    明细表列 = 各构造的 pct + excluded_by（哪些构造命中的，分号连接）。
    """
    rules = active_rules()
    detail = pd.DataFrame(index=pool.index, dtype="float32")
    hits = pd.Series("", index=pool.index, dtype=object)
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
        hits = hits.where(~fired, hits + np.where(hits == "", "", ";") + cn)
    detail["excluded_by"] = hits
    detail["keep"] = keep
    return keep, detail


def screen_on_date(s, d1, mtx, rule_mats, gate_mat,
                   quantile=ASHARE_SCREEN_QUANTILE):
    """一个信号日的完整筛选：闸门 → 量能剔除 → 给出名单

    返回 dict(universe=过闸池, keep=保留, dropped=被剔, detail=分位明细,
    n_limit_up=涨停剔除数)。gate_mat 是算闸门时用来判「因子有值」的那张矩阵，
    日频入口传量能水平那条即可。
    """
    ok, n_limit = tradable_mask(s, d1, gate_mat, mtx)
    pool = pd.Series(ok, index=gate_mat.columns)
    keep, detail = volume_exclusion(rule_mats, s, pool, quantile)
    return {"universe": pool, "keep": keep, "dropped": pool & ~keep,
            "detail": detail[pool], "n_limit_up": n_limit}
