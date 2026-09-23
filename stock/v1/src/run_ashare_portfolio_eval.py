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

from config import (RDAGENT_OUTPUT_DIR, ASHARE_DAILY_H5,
                    ASHARE_PORT_START, ASHARE_PORT_END,
                    ASHARE_PORT_WARMUP_DAYS, ASHARE_PORT_TOP_N,
                    ASHARE_PORT_HOLD, ASHARE_PORT_MIN_AMOUNT,
                    ASHARE_PORT_MIN_LISTED, ASHARE_PORT_COST_ONE_WAY,
                    ASHARE_PORT_LIMIT_UP, ASHARE_PORT_QUINTILES, ASHARE_SAMPLE,
                    ASHARE_PORT_OUT)
from factor_dsl import safe_eval

FACTORS_JSON = os.environ.get(
    "STOCK_FACTORS", os.path.join(RDAGENT_OUTPUT_DIR, "factors.json"))
RAW_COLS = ["open", "high", "low", "close", "volume"]
BENCH = "SH000300"
TRADING_DAYS = 252

# (标签, 表达式, 说明)。全部做多低分侧（对应截面 IC<0）。
# 前四条是被验对象；后两条是**对照**，用来判超额是真信号还是规模/价格暴露：
#   ma(df,5)                             —— 价格水平，其负 IC 很可能只是低价股效应
#   ts_std(volume,5)/ts_mean(volume,20)  —— 量能波动除以自身均量，消掉「成交量水平」
#     这个规模代理；若它还有超额，信号才不是「买最冷门的票」的换皮
SIGNALS = [
    ("量能波动 STD(Volume,5)", "ts_std(volume,5)", "被验"),
    ("量能波动 STD(Volume,20)", "ts_std(volume,20)", "被验"),
    ("量能水平 SMA(Volume,5)", "ts_mean(volume,5)", "被验"),
    ("量能水平 SMA(Volume,10)", "ts_mean(volume,10)", "被验"),
    ("对照·价格水平 MA(Price,5)", "ma(df,5)", "对照"),
    ("对照·相对量能波动", "ts_std(volume,5)/ts_mean(volume,20)", "对照"),
]


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
    bench_open = (df.loc[inst == BENCH, "open"].droplevel("instrument").sort_index()
                  if (inst == BENCH).any() else pd.Series(dtype="float32"))
    df = df.loc[~is_idx]
    print(f"[数据] {df.shape[0]:,} 行 / {time.time() - t0:.0f}s，个股 {len(stocks)} 只，"
          f"基准 {BENCH} {'有' if len(bench_open) else '无'}行情")

    wide = {}
    for f in ("open", "close", "volume"):
        m = df[f].unstack("instrument")
        wide[f] = m.reindex(columns=stocks).astype("float32")
        del m
    del df
    return wide, bench_open


def build_matrices(wide):
    """派生宽表：开盘到开盘收益（含 0 填充版）、已有行情天数、20 日均成交额（元）

    qlib cn_data 的 $volume 单位是**手**（1 手 = 100 股），故 close·volume 要 ×100
    才是人民币成交额。已用两只票核过单位：茅台 2026-09-22 close 304.93 × vol 1.01e5
    ×100 = 30.8 亿元、浦发 6.14 × 7.84e5 ×100 = 4.8 亿元，均与实际日成交额同级；
    不乘 100 会把全市场成交额压低两个数量级、流动性闸门形同虚设。"""
    op, cl, vol = wide["open"], wide["close"], wide["volume"]
    # fill_method=None：停牌缺口不前值填充，缺口收益落在复牌首日（对持有人即应得）
    ret = op.astype("float64").pct_change(fill_method=None).astype("float32")
    return {"open": op, "close": cl, "volume": vol, "ret_open": ret,
            "ret_open0": ret.fillna(0.0),
            "listed_days": cl.notna().cumsum(),
            "amount20": (cl * vol * 100.0).rolling(20).mean()}


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


def _gates(s, d1, f, mtx):
    """信号日 s 上每只标的是否可交易；返回 (可选池布尔, 涨停剔除数)"""
    ok = (f.loc[s].notna()
          & (mtx["listed_days"].loc[s] >= ASHARE_PORT_MIN_LISTED)
          & (mtx["amount20"].loc[s] >= ASHARE_PORT_MIN_AMOUNT))
    gap = mtx["open"].loc[d1] / mtx["close"].loc[s] - 1
    n_limit = int((ok & gap.notna() & (gap >= ASHARE_PORT_LIMIT_UP)).sum())
    return ok & gap.notna() & (gap < ASHARE_PORT_LIMIT_UP), n_limit


def run_signal(f, col_of, days, mtx, n, hold, universe, quintiles=0):
    """单信号 × 单规模回放。返回 (扣费后日收益, 统计 dict, 五分位日收益 list)"""
    w = np.zeros((len(days), f.shape[1]), dtype="float32")
    cost = np.zeros(len(days), dtype="float32")
    qw = [np.zeros((len(days), f.shape[1]), dtype="float32")
          for _ in range(quintiles)] if quintiles else []
    prev, phis, n_rebal, amounts, limits = None, [], 0, [], []
    for i in range(0, len(days) - hold - 1, hold):
        s, d1, j = days[i], days[i + 1], i + 1
        ok, n_limit = _gates(s, d1, f, mtx)
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
    故只留 IR，不再重复列一个数值相同的 t 误导读数。"""
    j = pd.concat([port, bench], axis=1, join="inner").dropna()
    ex = j.iloc[:, 0] - j.iloc[:, 1]
    ann = float(ex.mean() * TRADING_DAYS)
    sd = float(ex.std() * np.sqrt(TRADING_DAYS))
    return {f"excess_{name}_ann": ann,
            f"excess_{name}_ir": ann / sd if sd else np.nan}


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


def main():
    with open(FACTORS_JSON, encoding="utf-8") as fh:
        have = {it.get("expr") for it in json.load(fh) if it.get("expr")}
    sig = [(l, e, k) for l, e, k in SIGNALS if e in have or k == "对照"]
    print(f"[因子] {FACTORS_JSON} 可翻译 {len(have)} 条，本次送验 {len(sig)} 条："
          f"{'、'.join(l for l, _, _ in sig)}")

    wide, bench_open = load_panel()
    mtx = build_matrices(wide)
    t0 = time.time()
    fms = factor_matrices([e for _, e, _ in sig], mtx)
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

    res = pd.DataFrame(rows)
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 60)
    show = ["signal", "kind", "top_n", "n_rebal", "universe", "ann_return",
            "ann_return_gross", "ann_vol", "sharpe", "max_drawdown",
            "one_way_turnover", "excess_univ_ew_ann", "excess_univ_ew_ir",
            "excess_sh000300_ann", "q5_ann", "excl_worst_vs_pool_ann",
            "avg_amount_20d", "avg_blocked_limit_up"]
    show = [c for c in show if c in res.columns]
    print(f"\n===== A 股组合层验证（{ASHARE_PORT_START} 起 · 持有 {ASHARE_PORT_HOLD} 日"
          f" · 单边费率 {ASHARE_PORT_COST_ONE_WAY:.4f} · 做多低分侧 · "
          f"基准=可投资域等权）=====")
    print(res[show].to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    yr = pd.concat({k: v for k, v in yearly.items()})
    print("\n===== 分年度（净收益 vs 可投资域等权；中间档规模）=====")
    print(yr.unstack(0)[["port", "excess"]].to_string(
        float_format=lambda v: f"{v:.4f}"))
    res.to_csv(ASHARE_PORT_OUT, index=False)
    yr.to_csv(ASHARE_PORT_OUT.replace(".csv", "_yearly.csv"))
    print(f"\n[输出] {ASHARE_PORT_OUT}"
          f"（分年度见 {ASHARE_PORT_OUT.replace('.csv', '_yearly.csv')}）")


if __name__ == "__main__":
    sys.exit(main())
