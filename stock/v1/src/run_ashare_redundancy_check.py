# -*- coding: utf-8 -*-
"""因子**判重预检**：一条候选在进 RD-Agent 循环之前，会不会被 SOTA 去重直接丢掉。

为什么这是准入链的第三环（前两环是 run_ashare_factor_eval.py 的截面 IC 与
run_ashare_portfolio_eval.py 的组合层复核）：
rdagent/scenarios/qlib/developer/factor_runner.py:56 `deduplicate_new_factors` 把新
因子与每一条 SOTA 因子逐日截面相关，取「与最近的那条」的相关，

    corr_t(F_new, F_sota) = corr_{i ∈ 当日截面}(F_{t,i}^new, F_{t,i}^sota)
    redundancy(F_new)     = max_{F_sota ∈ SOTA}  mean_t corr_t

    mean_t corr_t >= 0.99  →  该列被丢弃；全部被丢 → FactorEmptyError → Skip loop

被丢弃是**在 running 步之前**发生的，也就是说这一轮的墙钟（股票线 33 分钟起）全
部白烧，而且日志里只留一句 Skip loop，看不出是「因子不好」还是「因子重复」。
这两种失败在主线看来必须分开：前者是研究失败（有价值的负结果），后者是候选管理
失败（一点信息都没产生）。所以候选名单在写进 prompts.yaml 的 CANDIDATE LIST 之前
先跑这里，>= 0.99 的直接不提名下。

口径与判据逐字对齐 rdagent：它用的是**原始值 Pearson**（`Series.corr`，不做秩变换），
所以主判据列 `pearson_max` 也是原始值 Pearson，才能与 0.99 那条线直接比。秩相关
（`spearman_max`）一并输出，因为价格/成交量水平型因子的 Pearson 会被极端值抬高，
两个口径的差本身就是「这条因子有多被少数尾部样本主导」的读数。

面板起点取 ASHARE_RED_START（默认 2015）而不是截面评估的 2010：判重要的是
「在 SOTA 面板上会不会被判重复」，2010-2014 未上市标的多、逐日截面稀，相关性
会虚高，那样算出来的「重复」不能代表循环里真实发生的事。

只读：不写因子库、不改 factors.json、不触发 git 提交。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import json
import os
import sys
import time

import numpy as np
import pandas as pd

from config import (RDAGENT_OUTPUT_DIR, ASHARE_RED_BAR, ASHARE_RED_START,
                    ASHARE_RED_END, ASHARE_RED_OUT, ASHARE_RED_DETAIL,
                    ASHARE_MIN_CS, ASHARE_SAMPLE)
# 复用截面评估的数据装载：剔指数、MIN_OBS 过滤、float32 化这些口径必须与 IC 评估
# 逐字一致，抄一份迟早漂。它的 START/END 是模块级常量，这里按本脚本的起点覆写。
import run_ashare_factor_eval as ashare_eval
from factor_dsl import safe_eval

ashare_eval.START, ashare_eval.END = ASHARE_RED_START, ASHARE_RED_END

FACTORS_JSON = os.environ.get(
    "STOCK_FACTORS", os.path.join(RDAGENT_OUTPUT_DIR, "factors.json"))
# 候选名单：不传就用下面这份「写进 prompts.yaml CANDIDATE LIST 的当前内容 + 两个
# 已知重复的对照」。正常用法是每改一次 CANDIDATE LIST 就带
# STOCK_RED_CANDIDATES=<json> 跑一遍，把 >=0.99 的从名单里划掉。
CANDIDATES_JSON = os.environ.get("STOCK_RED_CANDIDATES", "")

# 默认候选 = 当前 CANDIDATE LIST（2026-09-23 版），表达式按主线 DSL 写
# （factor_dsl.FACTOR_DSL 里 max/min 钉死作用于 close，故成交量极值走 Series 方法）
DEFAULT_CANDIDATES = [
    {"name": "20-day MIN of Volume", "expr": "volume.rolling(20).min()"},
    {"name": "5-day MIN of Volume", "expr": "volume.rolling(5).min()"},
    {"name": "5-day STD of Volume over 20-day STD of Volume",
     "expr": "ts_std(volume,5)/ts_std(volume,20)"},
    {"name": "5-day SMA of Price over 20-day SMA of Price",
     "expr": "ma(df,5)/ma(df,20)"},
    {"name": "20-day STD of Price over 20-day SMA of Price",
     "expr": "std(df,20)/ma(df,20)"},
    # 对照组：09-23 曾被当作「新信息」提进清单、实测是重复的两个，留在默认里当回归
    {"name": "[对照] 20-day MAX of Volume", "expr": "volume.rolling(20).max()"},
    {"name": "[对照] 5-day MAX of Volume", "expr": "volume.rolling(5).max()"},
]

# 危险区下界：0.95~0.99 不触发 rdagent 去重，但与在库因子已是同一簇，留着只是碰运气
NEAR_DUP = 0.95


def load_library():
    with open(FACTORS_JSON, encoding="utf-8") as f:
        items = json.load(f)
    lib = [{"name": it.get("name", ""), "expr": it.get("expr", "")}
           for it in items if it.get("expr")]
    print(f"[在库] {FACTORS_JSON}：{len(items)} 条，可进 DSL 求值的 {len(lib)} 条")
    return lib


def load_candidates():
    if CANDIDATES_JSON:
        with open(CANDIDATES_JSON, encoding="utf-8") as f:
            cands = [{"name": it.get("name", ""), "expr": it.get("expr", "")}
                     for it in json.load(f) if it.get("expr")]
        print(f"[候选] {CANDIDATES_JSON}：{len(cands)} 条")
    else:
        cands = DEFAULT_CANDIDATES
        print(f"[候选] 默认 CANDIDATE LIST：{len(cands)} 条")
    return cands


def build_wide(pool, tasks, label):
    """逐标的求值，堆成宽表 (datetime, instrument) × 构造名

    与截面评估一样一次遍历标的、内层跑完全部表达式；不可求值/全 NaN 的列直接丢，
    因为 rdagent 那边这类因子根本进不了 SOTA 面板。
    """
    acc = {t["name"]: {} for t in tasks}
    dropped = []
    for code, df in pool.items():
        for t in tasks:
            try:
                s = safe_eval(t["expr"], df)
            except Exception:
                dropped.append((t["name"], code))
                continue
            if s.isna().all():
                continue
            acc[t["name"]][code] = s.astype("float32")
    cols = {}
    for name, d in acc.items():
        if d:
            cols[name] = pd.concat(d, names=["instrument", "datetime"]) \
                .swaplevel().sort_index().astype("float32")
    wide = pd.DataFrame(cols)
    print(f"[{label}] 成表 {wide.shape[1]} 列 × {len(wide):,} 行"
          + (f"（{len(dropped)} 次逐标的求值失败已忽略）" if dropped else ""))
    return wide


def daily_corr(wide_lib, wide_cand, min_cs):
    """逐日截面相关，再对日取均值 —— 与 deduplicate_new_factors 同构

    返回 (pearson, spearman) 两张 候选×在库 的均值矩阵。
    pearson  用原始值，rdagent 判据即此： mean_t corr_t(F_new, F_sota)
    spearman 用日内秩（等价于 qlib 的 Rank IC 口径），抗极端值
    """
    joined = pd.concat([wide_lib, wide_cand], axis=1).dropna()
    n_lib, n_cand = wide_lib.shape[1], wide_cand.shape[1]
    print(f"[对齐] 成对样本 {len(joined):,} 行（要求当日全部构造非缺失）")
    idx_lib = list(range(n_lib))
    idx_cand = list(range(n_lib, n_lib + n_cand))
    p_sum = np.zeros((n_cand, n_lib))
    s_sum = np.zeros((n_cand, n_lib))
    n_day = 0
    vals = joined.to_numpy("float64")
    day = joined.index.get_level_values("datetime")
    # 按日切片（面板已按 datetime 排序，同一天的行连续），比 groupby 少一层对象开销
    bounds = np.flatnonzero(np.r_[True, day[1:] != day[:-1], True])
    for a, b in zip(bounds[:-1], bounds[1:]):
        x = vals[a:b]
        if len(x) < min_cs:
            continue
        sd = x.std(axis=0)
        if (sd[:n_lib] == 0).any() or (sd[n_lib:] == 0).any():
            continue                      # 常量列：corr 无定义，与 rdagent 给 nan 同等处理
        rx = pd.DataFrame(x).rank().to_numpy()
        cp = np.corrcoef(x.T)[np.ix_(idx_cand, idx_lib)]
        cs = np.corrcoef(rx.T)[np.ix_(idx_cand, idx_lib)]
        p_sum += cp
        s_sum += cs
        n_day += 1
    if not n_day:
        raise SystemExit("[判重] 没有任何一天满足截面样本数要求，检查 STOCK_MIN_CS/STOCK_SAMPLE")
    cols = list(wide_lib)
    index = list(wide_cand)
    print(f"[判重] 有效交易日 {n_day} 天")
    return (pd.DataFrame(p_sum / n_day, index=index, columns=cols),
            pd.DataFrame(s_sum / n_day, index=index, columns=cols))


def verdict(pear_max):
    if pear_max >= ASHARE_RED_BAR:
        return "会被判重复"
    if pear_max >= NEAR_DUP:
        return "危险区(同簇)"
    return "可提名"


def main():
    t0 = time.time()
    lib = load_library()
    cands = load_candidates()
    pool = ashare_eval.load_panel()
    wide_lib = build_wide(pool, lib, "在库")
    wide_cand = build_wide(pool, cands, "候选")

    P, S = daily_corr(wide_lib, wide_cand, ASHARE_MIN_CS)

    rows = []
    for name in P.index:
        ap = P.loc[name].abs()
        asn = S.loc[name].abs()
        worst = ap.idxmax()
        second = float(ap.sort_values(ascending=False).iloc[1])
        rows.append({
            "name": name,
            "expr": next((c["expr"] for c in cands if c["name"] == name), ""),
            "pearson_max": float(ap.max()),
            "nearest_lib": worst,
            "pearson_signed": float(P.loc[name, worst]),
            "pearson_second": second,
            "spearman_max": float(asn.max()),
            "bar": ASHARE_RED_BAR,
            "verdict": verdict(float(ap.max())),
        })
    res = pd.DataFrame(rows).sort_values("pearson_max", ascending=False)

    detail = P.stack().rename("pearson").to_frame()
    detail["spearman"] = S.stack()
    detail.index.names = ["candidate", "in_library"]
    detail = detail.reset_index()
    detail.to_csv(ASHARE_RED_DETAIL, index=False)
    res.to_csv(ASHARE_RED_OUT, index=False)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 60)
    print("\n===== 判重预检（主判据 = 逐日截面原始值 Pearson 均值，对齐 rdagent 的 0.99） =====")
    print(res.drop(columns=["expr", "bar"]).to_string(index=False))
    print(f"\n[输出] {ASHARE_RED_OUT}")
    print(f"[输出] {ASHARE_RED_DETAIL}（候选×在库 全矩阵）")
    print(f"[耗时] {time.time() - t0:.0f}s")
    bad = res[res["verdict"] != "可提名"]
    if len(bad):
        print(f"\n⚠️ {len(bad)} 条候选不该进 CANDIDATE LIST：{', '.join(bad['name'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
