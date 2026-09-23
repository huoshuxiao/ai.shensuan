# -*- coding: utf-8 -*-
"""A 股全市场日线口径的因子评估入口（只读，不写因子库、不触发 git 提交）。

被评对象：RD-Agent(Q) factor 循环回收下来的 factors.json（12 个 OHLCV 因子）。
数据源：RD-Agent 循环自带的 qlib 源码数据 daily_pv.h5
    （2008-12-29 ~ 今，SH/SZ/BJ 全市场个股 + 少量指数，指数在此按代码规则剔除）。

两套 IC 口径并列输出，因为它们回答的不是同一个问题：
1) 主线时序口径  mean_t IC(F_t, r_{t+1})，逐只标的各算一条再取均值
   （common/src/core/factor_dsl.compute_ic，Spearman）。与遗传编程 / LLM / 官方源在
   _attach_impl 里的算法完全一致，可直接与 ETF 管线的在库因子横向比。
2) 截面口径      逐日截面上的 IC = corr_cs(F_{t,i}, r_{t+1,i})：
   - Pearson 版 = qlib 报表里的 IC，用于对齐 RD-Agent 沙箱报出的 0.029；
   - Spearman 版 = qlib 的 Rank IC，抗离群值。
   ICIR = mean(每日 IC) / std(每日 IC)。

注意：F 与 r 都取同一标的的下一交易日，故时序口径与截面口径都不含跨标的
信息泄漏；但两者对「价格水平型」因子（ma(df,5) 本质是收盘价水平）都会掺入
低价股效应，解释结论时需记住这点。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import json
import os
import sys
import time

import numpy as np
import pandas as pd

from config import (RDAGENT_OUTPUT_DIR, ASHARE_DAILY_H5,
                    ASHARE_EVAL_START, ASHARE_EVAL_END, ASHARE_MIN_OBS,
                    ASHARE_MIN_CS, ASHARE_SAMPLE, ASHARE_EVAL_OUT)
from factor_dsl import safe_eval, compute_ic
from ashare_screen import guard_ret

# 参数全部走股票线 config（STOCK_* 环境变量可覆盖，见 stock/v1/src/config/config.py）
H5_PATH = ASHARE_DAILY_H5
FACTORS_JSON = os.environ.get(
    "STOCK_FACTORS", os.path.join(RDAGENT_OUTPUT_DIR, "factors.json"))
START, END = ASHARE_EVAL_START, ASHARE_EVAL_END
MIN_OBS, MIN_CS, SAMPLE = ASHARE_MIN_OBS, ASHARE_MIN_CS, ASHARE_SAMPLE
OUT_CSV = ASHARE_EVAL_OUT

# factor 只为收益护栏服务（盘面收益 = 复权收益 ÷ factor 的对看基准），不进任何表达式
RAW_COLS = ["open", "high", "low", "close", "volume", "factor"]


def _is_index(code: str) -> bool:
    """按 qlib 代码规则识别指数：沪市 000xxx、深市 399xxx 为指数。

    个股与之会撞前缀（深市主板也是 000xxx），但本 bundle 里深市个股形如
    SZ000001（平安银行），与沪市指数 SH000300 只能靠交易所前缀区分，
    故判据是 (前缀, 数字段) 的组合而非单纯数字段。
    """
    ex, num = code[:2], code[2:]
    if ex == "SH":
        return num.startswith("000")
    if ex == "SZ":
        return num.startswith("399")
    return False


def load_panel():
    """读入 qlib 日线数据并按标的分组，返回 {code: 单标的 DataFrame}"""
    print(f"[数据] 读取 {H5_PATH}（约 0.8GB）...")
    t0 = time.time()
    raw = pd.read_hdf(H5_PATH, key="data")
    print(f"[数据] 原始 shape={raw.shape}，耗时 {time.time() - t0:.0f}s")

    cols = {c.lstrip("$"): c for c in raw.columns}
    need = [cols[c] for c in RAW_COLS if c in cols]
    df = raw[need]
    df.columns = RAW_COLS
    del raw

    dt = df.index.get_level_values("datetime")
    sl = (dt >= pd.Timestamp(START)) if START else np.ones(len(df), bool)
    if END:
        sl &= dt <= pd.Timestamp(END)
    df = df.loc[sl]

    inst = df.index.get_level_values("instrument")
    stocks = sorted({i for i in inst.unique() if not _is_index(i)})
    # groupby 切片前先按代码过滤，避免把指数留在内存里陪跑
    keep = inst.isin(set(stocks))
    df = df.loc[keep]

    pool = {}
    for code, sub in df.groupby(level="instrument", sort=False):
        sub = sub.droplevel("instrument").sort_index()
        if len(sub) >= MIN_OBS and sub["close"].notna().sum() >= MIN_OBS:
            pool[code] = sub.astype("float32")
    del df
    if SAMPLE > 0:
        pool = dict(list(pool.items())[:SAMPLE])
    span = (min(p.index[0] for p in pool.values()),
            max(p.index[-1] for p in pool.values()))
    print(f"[数据] 有效个股 {len(pool)} 只（已剔指数），区间 {span[0].date()} ~ {span[1].date()}")
    return pool


def daily_cross_ic(factor_long, fwd_long, min_cs):
    """逐日截面 IC：返回 (Pearson 序列, Spearman 序列)。

    Pearson:  IC_t = corr_i(F_{t,i}, r_{t+1,i})   —— qlib 报表口径
    Spearman: RankIC_t = corr_i(rank(F_{t,i}), rank(r_{t+1,i}))
    先把两个输入并成一张表按日分组，保证成对样本一致（缺一侧即整行丢掉）。
    """
    panel = pd.DataFrame({"f": factor_long, "r": fwd_long}).dropna()
    out_p, out_s = [], []
    for d, k in panel.groupby(level="datetime", sort=True):
        if len(k) < min_cs:
            continue
        x, y = k["f"].to_numpy("float64"), k["r"].to_numpy("float64")
        if np.std(x) == 0 or np.std(y) == 0:
            continue
        out_p.append((d, np.corrcoef(x, y)[0, 1]))
        rx = pd.Series(x).rank().to_numpy()
        ry = pd.Series(y).rank().to_numpy()
        out_s.append((d, np.corrcoef(rx, ry)[0, 1]
                      if np.std(rx) and np.std(ry) else np.nan))
    if not out_p:
        return pd.Series(dtype="float64"), pd.Series(dtype="float64")
    return (pd.Series(dict(out_p)).sort_index(),
            pd.Series(dict(out_s)).sort_index())


def guarded_ret(p):
    """单只票的复权日收益，过 $factor 假台阶护栏（判据与实测来路见 ashare_screen.guard_ret）"""
    cl = p["close"].astype("float64")
    return guard_ret(cl.pct_change(fill_method=None),
                     (cl / p["factor"].astype("float64")).pct_change(fill_method=None))


def evaluate(pool, tasks):
    """对每个因子表达式做两套口径的评估，返回指标行列表"""
    # 前向收益 = 次日收盘收益 r_{t+1} = P_{t+1}/P_t - 1，与 _attach_impl 同式
    # （fill_method=None 关掉 pad 填充，否则停牌缺口会变成假收益）。
    # 每只票只算一次：截面标签与逐票时序 IC 用同一条收益，且省下按表达式重复
    # pct_change 的 21 倍开销。
    ret_by_code = {c: guarded_ret(p).shift(-1) for c, p in pool.items()}
    fwd_long = pd.concat(ret_by_code, names=["instrument", "datetime"]) \
        .swaplevel().sort_index()

    # 逐标的求值：一次遍历标的、内部跑完全部表达式（标的数远小于表达式数，
    # 这样 groupby 切片只做一遍）
    acc = {t["name"]: {} for t in tasks}
    ts_ic = {t["name"]: [] for t in tasks}
    for code, df in pool.items():
        for t in tasks:
            try:
                f = safe_eval(t["expr"], df)
            except Exception as e:
                if code == next(iter(pool)):
                    print(f"    ⚠️ 表达式不可求值 {t['expr']}: {e}")
                continue
            if f.isna().all():
                continue
            acc[t["name"]][code] = f
            ic = compute_ic(f, ret_by_code[code])
            if not np.isnan(ic):
                ts_ic[t["name"]].append(ic)

    rows = []
    for t in tasks:
        name = t["name"]
        if not acc[name]:
            rows.append({"name": name, "expr": t["expr"], "status": "不可求值/全 NaN"})
            continue
        long = pd.concat(acc[name], names=["instrument", "datetime"]) \
            .swaplevel().sort_index().astype("float64")
        del acc[name]
        cs_p, cs_s = daily_cross_ic(long, fwd_long, MIN_CS)
        ics = np.array(ts_ic[name], dtype="float64")
        rows.append({
            "name": name, "expr": t["expr"], "status": "ok",
            "n_stocks": len(ics),
            # 主线口径：逐标的时序 Spearman IC 的均值 / （均值/标准差）
            "ts_ic_mean": float(np.mean(ics)),
            "ts_icir": float(np.mean(ics) / (np.std(ics) + 1e-9)),
            "ts_abs_ic_gt_0.02": float(np.mean(np.abs(ics) > 0.02)),
            # 截面口径：逐日 IC
            "cs_ic_mean": float(cs_p.mean()) if len(cs_p) else np.nan,
            "cs_icir": float(cs_p.mean() / (cs_p.std() + 1e-9)) if len(cs_p) else np.nan,
            "cs_rank_ic_mean": float(cs_s.mean()) if len(cs_s) else np.nan,
            "cs_rank_icir": float(cs_s.mean() / (cs_s.std() + 1e-9)) if len(cs_s) else np.nan,
            "cs_ic_win_rate": float((cs_p > 0).mean()) if len(cs_p) else np.nan,
            "n_days": len(cs_p),
        })
    return rows


def main():
    with open(FACTORS_JSON, encoding="utf-8") as f:
        items = json.load(f)
    tasks = [{"name": it.get("name", ""), "expr": it.get("expr", "")}
             for it in items if it.get("expr")]
    print(f"[因子] {FACTORS_JSON} 共 {len(items)} 条，可翻译进 DSL 的 {len(tasks)} 条")

    pool = load_panel()
    t0 = time.time()
    rows = evaluate(pool, tasks)
    print(f"[评估] 完成，耗时 {time.time() - t0:.0f}s")

    res = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 50)
    print("\n===== A 股全市场日线口径 =====")
    print(res.drop(columns=["expr"]).to_string(index=False))
    res.to_csv(OUT_CSV, index=False)
    print(f"\n[输出] {OUT_CSV}")


if __name__ == "__main__":
    sys.exit(main())
