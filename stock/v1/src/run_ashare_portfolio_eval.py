# -*- coding: utf-8 -*-
"""A 股**组合层**验证：把截面 IC 已确认的负向因子做成可交易多头组合，看扣费后还有没有超额。

被验对象来自 run_ashare_factor_eval.py 的结论：12 个回收因子在 A 股全市场
ts_/cs_ 两套口径下方向一致为负（放量/追涨是反向指标），其中量能波动族最强且
窗口越短越强（`STD(Volume,5)` RankIC -0.0570 / RankICIR -0.599）。截面 IC 只回答
「排序对不对」，不回答「能不能赚」，故此处按可执行口径重做一遍：

    信号日 s 收盘后算 F_s  →  取 F 最小的一批（IC<0 ⇒ 低分侧才是好侧）
    →  s 的下一交易日 d1 以**开盘价**建仓（收盘竞价实际买不到，故不用收盘价）
    →  持有 H 个交易日，至 d1+H 开盘价平仓

持仓期收益按开盘到开盘计：
    r^open_{i,d} = O_{i,d} / O_{i,d-1} - 1
组合日收益（期内不再平衡，忽略权重漂移；H=5、N≥50 时该项量级远小于费率）：
    p_d = Σ_i A_{i,d} · r^open_{i,d} - 2·c·φ_d
A 为当日生效目标权重（篮子内等权 1/N），φ_d 为调仓日新买成分占比（等权下卖出
占比相同，故双边成本 2cφ），c = ASHARE_PORT_COST_ONE_WAY。调仓日 d1 的收益仍归
旧篮子（它到 d1 开盘才卖出），成本也记在 d1，二者不冲突。

三道可交易性闸门（截面 IC 里没有、组合层才暴露）：
1. 容量：20 日均成交额 mean(close·volume) ≥ ASHARE_PORT_MIN_AMOUNT。低量能因子
   天然指向冷门小票，不设这道闸门等于自欺；
2. 次新：已有行情天数 cumsum(close 非空) ≥ ASHARE_PORT_MIN_LISTED；
3. 涨停买不进：O_{i,d1}/C_{i,s} - 1 ≥ 9.5% 者剔除（主板涨跌停 ±10%；未区分 ST 的
   ±5% 与创业板/科创板 ±20%，故这是一个偏松的近似闸门）。

基准两条：同一可交易池的等权组合（同 open-to-open 口径、不扣费）与 SH000300。
另按因子全市场五等分印出各组年化，用来判「单调性」而非只看多头腿。
**幸存者偏差提醒**：面板含已退市个股（5677 只 > 今日上市数），退市前的最后一段
行情在数据里通常被截断而非归零，故绝对收益水平整体偏乐观。本脚本的判据是
「相对基准的超额」与「五分位单调性」，不是绝对收益。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import json
import os
import sys
import time

import numpy as np
import pandas as pd

from config import (RDAGENT_OUTPUT_DIR, ASHARE_PORT_START,
                    ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED,
                    ASHARE_PORT_COST_ONE_WAY,
                    ASHARE_PORT_QUINTILES, ASHARE_PORT_TOP_N,
                    ASHARE_PORT_HOLD, ASHARE_PORT_OUT,
                    ASHARE_EXCL_OUT, ASHARE_BUYLIST_OUT,
                    ASHARE_SCREEN_QUANTILE, ASHARE_BUY_TOP_N,
                    ASHARE_BUY_MIN_HITS,
                    ASHARE_TRADABLE_GATE)
# 数据装载 / 派生宽表 / 因子求值 / 可交易闸门整段搬到 strategy/ashare_screen.py，
# 与日频信号入口共用同一份实现：闸门口径出现第二种写法，回测结论就作废了
# VOLUME_RULES 同样只认这一份 —— 本脚本要量的是**生产在用的那四条**剔除构造，
# 在这里另抄一遍窗口就等于量了一个不存在的东西（09-24 P0 就是这么发现的：
# 生产「量能水平」是 SMA(Volume,20)，而主表历史行只跑过 5 日与 10 日窗）
from ashare_screen import (BENCH, BUY_EXPR, RULE_EXPRS, VOLUME_RULES,
                           build_matrices, factor_matrices, gate_desc,
                           load_panel, tradable_mask)

FACTORS_JSON = os.environ.get(
    "STOCK_FACTORS", os.path.join(RDAGENT_OUTPUT_DIR, "factors.json"))
TRADING_DAYS = 252

# (标签, 表达式, 说明)。全部做多低分侧（对应截面 IC<0）。
# 前四条是被验对象；后两条是**对照**，用来判超额是真信号还是规模/价格暴露：
#   ma(df,5)                             —— 价格水平，其负 IC 很可能只是低价股效应
#   ts_std(volume,5)/ts_mean(volume,20)  —— 量能波动除以自身均量，消掉「成交量水平」
#     这个规模代理；若它还有超额，信号才不是「买最冷门的票」的换皮
SIGNALS = [
    # 「被验」= 送进多头腿（做多低分侧）的候选；「生产」= 盘前剔除判据本身，
    # 只进因子矩阵供并集实测，不做多头腿（量能族只做剔除这条结论见模块 docstring）
    ("量能波动 STD(Volume,5)", "ts_std(volume,5)", "被验"),
    ("量能波动 STD(Volume,20)", "ts_std(volume,20)", "被验"),
    ("量能水平 SMA(Volume,5)", "ts_mean(volume,5)", "被验"),
    ("量能水平 SMA(Volume,10)", "ts_mean(volume,10)", "被验"),
    # 09-23 加入：量能族的另两种构造（动量、短长均量比）。它们在 factors.json 里
    # 是 RD-Agent 回收来的新构造，同日全市场截面才第一次给出可比读数 ——
    # RankIC -0.0430/-0.0472/-0.0378、RankICIR -0.46/-0.47/-0.36，量级已接近
    # STD(Volume,5)（-0.0570/-0.599）但与之不是同一件事（一个是波动、一个是变化率、
    # 一个是自身比值）。是否值得并进取决于本层的超额与单调性，不看截面 IC。
    ("量能动量 MOM(Volume,5)", "volume/delay(volume,5)-1", "被验"),
    ("量能动量 MOM(Volume,20)", "volume/delay(volume,20)-1", "被验"),
    ("量能比 SMA(Vol,5)/SMA(Vol,20)",
     "(ts_mean(volume,5))/(ts_mean(volume,20))", "被验"),
    # 09-24 P0 补的生产缺口：盘前判据「量能水平」用的是 **20 日窗**
    # （ashare_screen.VOLUME_RULES[0]），而这张表历史上只有 5 日 / 10 日两条。
    # 也就是说「生产并集的四条」从未整体过过组合层，本次才第一次有读数。
    ("量能水平 SMA(Volume,20)", "ts_mean(volume,20)", "被验"),
    ("对照·价格水平 MA(Price,5)", "ma(df,5)", "对照"),
    ("对照·相对量能波动", "ts_std(volume,5)/ts_mean(volume,20)", "对照"),
    # 09-23 新回收的三个价格族因子**不送多头**：全市场截面 RankIC 只有
    # -0.0146 ~ -0.0159，与对照 ma(df,5) 的 -0.0153 几乎同值同号，即它们带来的
    # 只是同一个「低价股效应」的又一层皮（VWAP ≈ 均价、MAX ≈ 区间高点，都是价格
    # 水平）。列为对照留在此处，是为了让「不进取向」这个结论有可查的证据。
    ("对照·VWAP(Price,5)", "ts_sum(volume*close,5)/ts_sum(volume,5)", "对照"),
    ("对照·MAX(Price,5)", "max(df,5)", "对照"),
    ("对照·MAX(Price,20)", "max(df,20)", "对照"),
]


def run_signal(f, col_of, days, mtx, n, hold, universe, quintiles=0, allow=None):
    """单信号 × 单规模回放。返回 (扣费后日收益, 统计 dict, 五分位日收益 list)

    `allow` 是「剔除形态」的额外可用性布尔矩阵（行=信号日，见 exclusion_mask）：
    在闸门之上再与一道，就把候选集从「过闸池」换成「该形态的保留池」。多头腿与
    减法腿因此吃同一套闸门、同一个调仓网格，两表数字可直接对照。
    """
    w = np.zeros((len(days), f.shape[1]), dtype="float32")
    cost = np.zeros(len(days), dtype="float32")
    qw = [np.zeros((len(days), f.shape[1]), dtype="float32")
          for _ in range(quintiles)] if quintiles else []
    prev, phis, n_rebal, amounts, limits = None, [], 0, [], []
    for i in range(0, len(days) - hold - 1, hold):
        s, d1, j = days[i], days[i + 1], i + 1
        ok, n_limit = tradable_mask(s, d1, f, mtx)
        if allow is not None and s in allow.index:
            ok = ok & allow.loc[s]
        cand = f.loc[s].where(ok).dropna()
        if len(cand) < n:
            continue
        basket = list(cand.sort_values().index[:n])       # 低分侧 = 做多侧
        phi = 1.0 if prev is None else 1.0 - len(set(prev) & set(basket)) / n
        cost[j] += 2 * ASHARE_PORT_COST_ONE_WAY * phi     # 双边：买新 + 卖旧
        lo, hi = j + 1, min(j + 1 + hold, len(days))
        w[lo:hi] = 0.0
        w[lo:hi, [col_of[c] for c in basket]] = 1.0 / n
        prev, phis, n_rebal = basket, phis + [phi], n_rebal + 1
        amounts.append(float(mtx["amount20"].loc[s, basket].mean()))
        limits.append(n_limit)
        if quintiles:
            r = cand.rank(pct=True).to_numpy()            # 全市场（过闸门）五等分
            g = np.minimum((r * quintiles).astype(int), quintiles - 1)
            idx = np.array([col_of[c] for c in cand.index])
            for b in range(quintiles):
                sel = idx[g == b]
                qw[b][lo:hi] = 0.0
                if len(sel):
                    qw[b][lo:hi, sel] = 1.0 / len(sel)
    gross = pd.Series((w * mtx["ret_open0"].values).sum(axis=1), index=days)
    port = gross - cost
    st = _stats(f, days, n_rebal, port, amounts, phis, limits, universe, gross)
    qs = [pd.Series((m * mtx["ret_open0"].values).sum(axis=1), index=days)
          for m in qw]
    return port, st, qs


def _stats(f, days, n_rebal, port, amounts, phis, limits, universe, gross):
    p = port.dropna()
    ann = float(p.mean() * TRADING_DAYS)
    vol = float(p.std() * np.sqrt(TRADING_DAYS))
    nav = (1 + p).cumprod()
    return {"n_rebal": n_rebal,
            "factor_coverage": float(np.mean(
                [int(f.loc[d].notna().sum()) for d in days[::20]])),
            "universe": float(np.mean([int(universe.loc[d].sum())
                                       for d in days[::20]])),
            "ann_return": ann,
            # 毛收益单列：与净收益之差即费率吃掉多少，用来分「信号弱」还是「费重」
            "ann_return_gross": float(gross.mean() * TRADING_DAYS),
            "ann_vol": vol,
            # ann/vol 是**夏普**（无风险利率按 0），不是 IR；相对基准的 IR 另列
            "sharpe": ann / vol if vol else np.nan,
            "max_drawdown": float((nav / nav.cummax() - 1).min()),
            "one_way_turnover": float(np.mean(phis)) if phis else np.nan,
            "avg_amount_20d": float(np.mean(amounts)) if amounts else np.nan,
            "avg_blocked_limit_up": float(np.mean(limits)) if limits else np.nan}


def _excess(port, bench, name):
    """相对基准的超额年化与超额 IR。

    超额 t 值与 IR 只差一个 √年数 的常数（两者都由 mean/std 日频超额算出），
    故只留 IR，不再重复列一个数值相同的 t 误导读数。

    `excess_{name}_days` 不是装饰：inner join 的宽度由基准序列走到哪天决定，而指数
    那 6 只**不参与日更**（akshare 收盘快照只有个股），bin 里停在 2026-09-21 ⇒
    个股每天往前走、这列的天数只会往下缩。同一行里 `ann_return` 是全区间、
    `excess_sh000300_ann` 是交集区间，不写出天数就会被当成同一段比较。"""
    j = pd.concat([port, bench], axis=1, join="inner").dropna()
    ex = j.iloc[:, 0] - j.iloc[:, 1]
    ann = float(ex.mean() * TRADING_DAYS)
    sd = float(ex.std() * np.sqrt(TRADING_DAYS))
    return {f"excess_{name}_ann": ann,
            f"excess_{name}_ir": ann / sd if sd else np.nan,
            f"excess_{name}_days": int(len(ex))}


def _yearly(port, bench):
    """分年度净收益 / 基准 / 超额（判「最近还管不管用」而非只看全样本）"""
    j = pd.concat([port, bench], axis=1, join="inner").fillna(0.0)
    j.columns = ["port", "bench"]
    g = j.groupby(j.index.year)
    out = g.apply(lambda k: pd.Series({
        "port": (1 + k["port"]).prod() - 1,
        "bench": (1 + k["bench"]).prod() - 1}))
    out["excess"] = out["port"] - out["bench"]
    return out


# ==================== P0（09-24）：并集到底比单条多剔掉了什么 ====================
# 上面那张表量的全是**多头腿**（做多低分侧），而生产用法方向相反：四条量能构造
# 各踢掉池内最响的 20%，取并集。剔除是减法，不产生新买入、费率近零，所以它的
# 价值只能这样量：
#     增益 = 年化(剩余池等权) - 年化(不剔除等权)      同一批调仓日、同一套闸门
# 现有列里最接近的 `excl_worst_vs_pool_ann` 是**单因子五等分**口径，它既没量过
# 并集、也没量过「命中几条才剔」，于是「四条并集值不值」「量能波动 09-23 只独有
# 38 只该不该留」这两件事一直没有数。本段就是把这三套形态一次跑齐：
#     单条×4 / 并集(命中≥1，即「不该买」那张域的口径) / 命中≥2,3,4 / 留一×4
RULE_NAMES = [nm for _k, nm, _e, _d in VOLUME_RULES]
_ALL_R = tuple(range(len(VOLUME_RULES)))
# (标签, 类别, 参与构造下标, 最小命中数)。参照行给 min_hits=9，即命中数永远达不到
# 阈值 ⇒ 一只都不剔，天然退化成「不剔除」，不必为它另开分支
EXCL_VARIANTS = (
    [("参照·不剔除", "参照", _ALL_R, 9)]
    + [(f"单条·{nm}", "单条", (r,), 1) for r, nm in enumerate(RULE_NAMES)]
    + [(f"并集(命中>={k})", "并集", _ALL_R, k) for k in (1, 2, 3, 4)]
    + [(f"留一·去掉{nm}", "留一", tuple(x for x in _ALL_R if x != r), 1)
       for r, nm in enumerate(RULE_NAMES)]
)
# ⑰P3 之后生产踩的是**两个强度**，不是一个：域 = 并集(≥1)、待买入名单 =
# 并集(≥ASHARE_BUY_MIN_HITS)。本表把 1..4 都跑了，两档天然都在。这条守卫防的是
# 以后有人把 ASHARE_BUY_MIN_HITS 拨到 5、或把上面的档位收窄，于是复跑出来的表
# 读不出生产形态而没人报错 —— 报错比事后发现两张表对不上便宜
if not 1 <= ASHARE_BUY_MIN_HITS <= 4:
    raise SystemExit(f"[准入] ASHARE_BUY_MIN_HITS={ASHARE_BUY_MIN_HITS} 不在本表覆盖的"
                     f" 1..4 档内 ⇒ 需同步扩展 EXCL_VARIANTS 的并集档位，"
                     f"否则名单腿读不出生产强度")


def exclusion_hits(mtx, days, fms, hold, quantile):
    """在调仓日网格上逐日复现生产剔除判定 → (过闸池矩阵, {构造名: 命中矩阵})

    与 strategy/ashare_screen.volume_exclusion **同式**，不在此另立判据：
        pool_s = tradable_mask(s, d1)                      容量 / 次新 / 涨停买不进
        pct_i  = rank_{j∈pool_s}(F_{j,s}) / |pool_s|       池内截面分位
        hit_i  = pct_i >= quantile                         默认 0.8 ⇒ 每条约踢 20%
    闸门里「因子有值」那道用的是量能水平矩阵（与日频入口同一条），所以命中矩阵
    的行只到「有下一交易日可判」为止；面板末日没有 d1，涨停闸前瞻不了，不进回放。
    """
    gate = fms[RULE_EXPRS[0]]
    grid, pool_rows, hit_rows = [], [], {nm: [] for nm in RULE_NAMES}
    for i in range(0, len(days) - hold - 1, hold):
        s, d1 = days[i], days[i + 1]
        ok, _ = tradable_mask(s, d1, gate, mtx)
        fired = {}
        for _k, nm, e, _d in VOLUME_RULES:
            pct = fms[e].loc[s].reindex(ok.index).where(ok).rank(pct=True)
            fired[nm] = pct.notna() & (pct >= quantile)
        grid.append(s)
        pool_rows.append(ok)
        for nm in RULE_NAMES:
            hit_rows[nm].append(fired[nm])
    cols = mtx["close"].columns
    grid = pd.DatetimeIndex(grid)
    pack = lambda rows: pd.DataFrame(rows, index=grid, columns=cols)  # noqa: E731
    return pack(pool_rows), {nm: pack(v) for nm, v in hit_rows.items()}


def exclusion_mask(variant, pool, hit):
    """某一形态的「可用」矩阵：True = 当天留在池里可以买
        allowed_i = pool_i ∧ ( Σ_{r∈参与构造} hit_{r,i} ) < 最小命中数
    留一 = 参与构造去掉那一条；单条 = 只留那一条；命中≥k 剔的是同时被 k 条判响的票。
    """
    _label, _kind, subs, k = variant
    h = sum(hit[RULE_NAMES[r]].astype("int16") for r in subs)
    return pool & (h < k)


def run_exclusion(mtx, days, hold, pool, hit, ref_label="参照·不剔除"):
    """13 种剔除形态共用一次遍历 → (对照行 list, {形态标签: 可用矩阵})

    日收益按开盘到开盘、等权、**不扣费**（与主表 `excl_worst_*` 同一立场：减法不
    产生新买入）。调仓网格与多头腿严格对齐：信号日 i、建仓日 j=i+1，持仓段
    [i+2, i+2+hold)（j 当日开盘归旧篮子）。未覆盖的边界日不参与统计（cov 掩码），
    所以增益是纯同窗口之差，不含「空仓摊平」的水分。
    """
    allows = {v[0]: exclusion_mask(v, pool, hit) for v in EXCL_VARIANTS}
    ret = mtx["ret_open0"].to_numpy(dtype="float32", copy=False)
    legs = {lab: np.zeros(len(days), dtype="float64") for lab in allows}
    kept_n = {lab: [] for lab in allows}
    cov = np.zeros(len(days), dtype=bool)
    n_cand = []
    arr_pool = pool.to_numpy()
    # allow 已按构造与 pool 取交，所以 True 的列必然在当天候选里，不必再求交
    arr_allow = {v[0]: allows[v[0]].to_numpy() for v in EXCL_VARIANTS}
    for pos in range(len(pool)):
        i = pos * hold
        lo, hi = i + 2, min(i + 2 + hold, len(days))
        n_cand.append(int(arr_pool[pos].sum()))
        A = ret[lo:hi]
        for lab in arr_allow:
            kp = arr_allow[lab][pos]
            n = int(kp.sum())
            if not n:
                continue
            legs[lab][lo:hi] = A[:, kp].mean(axis=1)
            kept_n[lab].append(n)
        cov[lo:hi] = True

    base = legs[ref_label]
    idx_cov = pd.DatetimeIndex(days)[cov]
    yrs = idx_cov.year.to_numpy()
    rows = []
    for label, kind, subs, k in EXCL_VARIANTS:
        g = (legs[label] - base)[cov]
        sd = float(g.std() * np.sqrt(TRADING_DAYS))
        ann = float(g.mean() * TRADING_DAYS)
        nk = float(np.mean(kept_n[label])) if kept_n[label] else np.nan
        rows.append({
            "variant": label, "kind": kind,
            "rules": "、".join(RULE_NAMES[r] for r in subs),
            # min_hits 如实写：参照行是 9（命中数永远达不到 ⇒ 一只都不剔），
            # 填 1 会被读成「参照 = 并集」，两行就分不开
            "min_hits": k,
            "n_rebal": len(n_cand),
            "avg_candidates": float(np.mean(n_cand)),
            "avg_kept": nk,
            "avg_excluded": float(np.mean(n_cand)) - nk,
            "keep_ratio": nk / float(np.mean(n_cand)),
            # 两条腿各自的年化（毛）：gain 就是二者之差，单列出来免得读的人再减
            "ann_return": float(legs[label][cov].mean() * TRADING_DAYS),
            "ann_return_pool": float(base[cov].mean() * TRADING_DAYS),
            "gain_ann": ann,
            "gain_ir": ann / sd if sd else np.nan,
            "gain_days": int(cov.sum()),
            # 分段看增益：全样本为正但增益全在 2015~2020 的话，并集就是历史 beta
            "gain_2015_2020": float(g[yrs <= 2020].mean() * TRADING_DAYS),
            "gain_2021_2026": float(g[yrs >= 2021].mean() * TRADING_DAYS)})
    return rows, allows


def buylist_rows(fms, col_of, days, mtx, universe, bench, allows, top_n):
    """短名单行：同一根「安静度」轴，候选集分别按 13 种剔除形态裁剪，扣费回放

    回答的是另一半问题——并集改变的是**最终真买的那 50 只**，而不只是剩余池的
    平均收益。判据与主表多头腿逐字相同（open→open、持有 5 日、双边 15bp），只多
    一道 allow；所以「参照·不剔除」这一行应当与主表**排序轴那一根**（现 = SMA(Volume,20)，
    09-24 换轴前是 STD(Volume,20)）top50 那一行几乎重合，两表对不上就是本段有 bug，
    这是内置的回归自检。
    """
    rows = []
    for label, kind, _subs, _k in EXCL_VARIANTS:
        port, st, _qs = run_signal(fms[BUY_EXPR], col_of, days, mtx, top_n,
                                   hold=ASHARE_PORT_HOLD, universe=universe,
                                   quintiles=0, allow=allows[label])
        row = {"variant": label, "kind": kind, "top_n": top_n,
               "sort_axis": BUY_EXPR, **st}
        for bn, bs in bench.items():
            row.update(_excess(port, bs, bn))
        rows.append(row)
    return rows


def main():
    with open(FACTORS_JSON, encoding="utf-8") as fh:
        have = {it.get("expr") for it in json.load(fh) if it.get("expr")}
    # 送验集合 = 多头腿候选（要在因子库里）+ 生产的四条剔除构造与排序轴（不看因子库，
    # 它们本来就是判据，不是候选；少一条并集就拼不出来）
    sig = [(l, e, k) for l, e, k in SIGNALS if e in have or k == "对照"]
    exprs = sorted({e for _, e, _ in sig} | set(RULE_EXPRS) | {BUY_EXPR})
    print(f"[因子] {FACTORS_JSON} 可翻译 {len(have)} 条，本次送验 {len(sig)} 条："
          f"{'、'.join(l for l, _, _ in sig)}")
    print(f"[因子] 求值表达式 {len(exprs)} 条（生产判据 {len(RULE_EXPRS)} 条 + 排序轴"
          f" {BUY_EXPR}"
          f"{'' if BUY_EXPR not in RULE_EXPRS else '，与剔除侧同表达式、已去重'}）")

    wide, bench_open = load_panel()
    mtx = build_matrices(wide)
    t0 = time.time()
    fms = factor_matrices(exprs, mtx)
    print(f"[因子] 表达式求值完成 {time.time() - t0:.0f}s")

    # 暖机段只用于算因子，回放从 START 起
    live = mtx["ret_open"].index >= pd.Timestamp(ASHARE_PORT_START)
    mtx = {k: v.loc[mtx["ret_open"].index[live]] for k, v in mtx.items()}
    fms = {e: m.loc[m.index[live]] for e, m in fms.items()}
    days = mtx["ret_open"].index
    col_of = {c: i for i, c in enumerate(mtx["close"].columns)}
    # 可投资域（只过容量/次新两道闸门，涨停闸留到建仓那天）：
    # 基准取它的等权，才是「同一段市场」的比较对象 —— 用全面板等权会把大量
    # 根本买不到的冷门票算进基准，超额被系统性压低
    universe = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
                & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    pool_ret = mtx["ret_open"].where(universe).mean(axis=1)

    bench = {"univ_ew": pool_ret}
    if len(bench_open):
        bench["sh000300"] = bench_open.reindex(days).astype("float64") \
            .pct_change(fill_method=None)

    rows, yearly = [], {}
    for label, expr, kind in sig:
        f = fms[expr]
        for k, n in enumerate(ASHARE_PORT_TOP_N):
            port, st, qs = run_signal(f, col_of, days, mtx, n,
                                      hold=ASHARE_PORT_HOLD, universe=universe,
                                      quintiles=ASHARE_PORT_QUINTILES)
            row = {"signal": label, "expr": expr, "kind": kind, "top_n": n, **st}
            for bn, bs in bench.items():
                row.update(_excess(port, bs, bn))
            if qs:
                # 「剔除最差五分位」这条用法单独算：量能族的负 IC 很可能主要来自
                # 差票（放量/追涨者跑输），那么多头腿赚不到钱不代表剔除规则没用 ——
                # 剔除是**减法等权**，不产生新买入，费率近零，是另一件事
                a = [float(q.mean() * TRADING_DAYS) for q in qs]
                excl = pd.concat(qs[:-1], axis=1).mean(axis=1)
                pool = pd.concat(qs, axis=1).mean(axis=1)
                row["excl_worst_ann"] = float(excl.mean() * TRADING_DAYS)
                row["excl_worst_vs_pool_ann"] = float(
                    (excl.mean() - pool.mean()) * TRADING_DAYS)
                row["q5_ann"] = a[-1]
            rows.append(row)
            if k == 0 and qs:
                anns = [float(q.mean() * TRADING_DAYS) for q in qs]
                print(f"  五分位年化 {label}: "
                      + " → ".join(f"Q{i + 1} {a:+.4f}" for i, a in enumerate(anns))
                      + f" ｜ Q1-Q{len(anns)} {anns[0] - anns[-1]:+.4f}")
            if n == ASHARE_PORT_TOP_N[len(ASHARE_PORT_TOP_N) // 2]:
                yearly[label] = _yearly(port, bench["univ_ew"])

    # ---------- P0（09-24）：减法腿，并集 / 命中≥k / 留一 ----------
    t1 = time.time()
    pool_df, hit_df = exclusion_hits(mtx, days, fms, ASHARE_PORT_HOLD,
                                     ASHARE_SCREEN_QUANTILE)
    ex_rows, allows = run_exclusion(mtx, days, ASHARE_PORT_HOLD, pool_df, hit_df)
    print(f"[剔除] {len(EXCL_VARIANTS)} 种形态 × {len(pool_df)} 个调仓日 "
          f"{time.time() - t1:.0f}s")
    ex = pd.DataFrame(ex_rows)
    ex.insert(0, "gate", ASHARE_TRADABLE_GATE)      # 同上：口径随行自报
    pd.set_option("display.unicode.east_asian_width", True)
    print("\n===== P0 剔除增益（剩余池等权 − 不剔除等权；毛收益、同一调仓网格）=====")
    print(ex[["variant", "kind", "min_hits", "avg_candidates", "avg_kept",
              "keep_ratio", "ann_return", "ann_return_pool", "gain_ann",
              "gain_ir", "gain_2015_2020", "gain_2021_2026"]]
          .to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    ex.to_csv(ASHARE_EXCL_OUT, index=False)

    bl = pd.DataFrame(buylist_rows(fms, col_of, days, mtx, universe, bench,
                                          allows, ASHARE_BUY_TOP_N))
    bl.insert(0, "gate", ASHARE_TRADABLE_GATE)      # 同上：口径随行自报
    print(f"\n===== P0 短名单在不同剔除形态下（{BUY_EXPR} 升序取前 "
          f"{ASHARE_BUY_TOP_N}，扣双边 {ASHARE_PORT_COST_ONE_WAY:.4f}）=====")
    print(bl[["variant", "kind", "top_n", "n_rebal", "universe", "ann_return",
              "ann_return_gross", "sharpe", "one_way_turnover",
              "avg_amount_20d", "excess_univ_ew_ann", "excess_univ_ew_ir",
              "excess_sh000300_ann"]]
          .to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    bl.to_csv(ASHARE_BUYLIST_OUT, index=False)

    res = pd.DataFrame(rows)
    # 涨停闸吃哪一档是**运行级**属性，写在每一行上：同一张表里混了 flat 与 board
    # 两种口径的数，事后没人看得出来（09-24 把 board 设成默认之后的防线）
    res.insert(0, "gate", ASHARE_TRADABLE_GATE)
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 60)
    show = ["signal", "kind", "top_n", "n_rebal", "universe", "ann_return",
            "ann_return_gross", "ann_vol", "sharpe", "max_drawdown",
            "one_way_turnover", "excess_univ_ew_ann", "excess_univ_ew_ir",
            "excess_sh000300_ann", "q5_ann", "excl_worst_vs_pool_ann",
            "avg_amount_20d", "avg_blocked_limit_up"]
    show = [c for c in show if c in res.columns]
    # 口径描述来自 ashare_screen.gate_desc()：三档共用一个构造函数，别在这儿写第二份
    print(f"\n===== A 股组合层验证（{ASHARE_PORT_START} 起 · 持有 {ASHARE_PORT_HOLD} 日"
          f" · 单边费率 {ASHARE_PORT_COST_ONE_WAY:.4f} · 做多低分侧 · "
          f"基准=可投资域等权 · 涨停闸={gate_desc()}）=====")
    print(res[show].to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    yr = pd.concat({k: v for k, v in yearly.items()})
    print("\n===== 分年度（净收益 vs 可投资域等权；中间档规模）=====")
    print(yr.unstack(0)[["port", "excess"]].to_string(
        float_format=lambda v: f"{v:.4f}"))
    res.to_csv(ASHARE_PORT_OUT, index=False)
    yr.to_csv(ASHARE_PORT_OUT.replace(".csv", "_yearly.csv"))
    print(f"\n[输出] {ASHARE_PORT_OUT}"
          f"（分年度见 {ASHARE_PORT_OUT.replace('.csv', '_yearly.csv')}）\n"
          f"[输出] {ASHARE_EXCL_OUT}（减法腿增益）\n"
          f"[输出] {ASHARE_BUYLIST_OUT}（短名单×剔除形态）")


if __name__ == "__main__":
    sys.exit(main())
