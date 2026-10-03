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

**09-30 用户裁定：与循环「统一为 `< 0.99`」＝硬闸改看带符号值。** 依据是读 conda 环境
`rdagent`（0.8.0）的源码：`rdagent/scenarios/qlib/developer/factor_runner.py:71` 那句
`IC_max[IC_max < 0.99]` 是**源码字面量**（`scenarios/qlib/` 全仓只有这一处 0.99、没有
env/conf 可喂），而 `IC_max` 全程**不带绝对值**。所以本线的 0.99 那一道现在用
`signed_max`（`summarize()` 里的 `P.loc[name].max()`）——一根跟在库相关 −0.995 的**镜像**
因子，循环会留它、本线也不再报"会被判重复"（那道闸唯一的用途是预测机时白烧）。
|corr| 那把尺**没有删**：它降级成读数列 `pearson_max`，并且继续喂本线**自己**的政策闸
`NEAR_DUP`（同簇即无新信息，反号也算同簇）⇒ 镜像因子仍进不了 CANDIDATE LIST，只是
罪名从"循环会丢"换成"本线政策不认"。三处与循环**仍然**不等价，别再当成一字对齐：
① 求值环境——本线有 `ASHARE_MIN_CS` 截面数闸、且当日任一列是常量就整日丢弃
（`daily_corr()` 循环开头那两条 `continue`），循环没有数闸、常量列由 pandas 给 NaN 再被
`.mean()` 跳过 ⇒ 分母不同，临界读数上判词可翻；② 对象集——本线对 `factors.json` 在库，
循环对它自己那叠 SOTA 实验；③ 本线另有一道循环没有的政策闸（上面那句）。
原始值 Pearson（`Series.corr`，不做秩变换）与 0.99 那条线直接可比。秩相关（`spearman_max`）一并输出，
因为价格/成交量水平型因子的 Pearson 会被极端值抬高，
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

# 默认候选 = 当前 CANDIDATE LIST（2026-09-25 版，按 NEAR_DUP=0.90 重出），表达式按主线
# DSL 写（factor_dsl.FACTOR_DSL 里 max/min 钉死作用于 close，故成交量极值走 Series 方法）
DEFAULT_CANDIDATES = [
    # 名单前四：比值族里最独立的两条 + 跨窗比值 + 唯一活下来的非比值名
    {"name": "20-day STD of Price over 20-day SMA of Price",
     "expr": "std(df,20)/ma(df,20)"},
    {"name": "20-day STD of Volume over 20-day SMA of Volume",
     "expr": "ts_std(volume,20)/ts_mean(volume,20)"},
    {"name": "5-day STD of Volume over 20-day STD of Volume",
     "expr": "ts_std(volume,5)/ts_std(volume,20)"},
    {"name": "10-day MOM of Volume", "expr": "volume/delay(volume,10)-1"},
    # 对照组：09-24 实测落在 0.90~0.99 危险区、被本线政策闸（不是 rdagent 的 0.99 硬闸）
    # 挡掉的三条。留在默认里当**政策回归针**：谁把 NEAR_DUP 拨回 0.95，这里就会从
    # 「危险区(同簇)」翻成「可提名」，一眼看得见翻闸的后果
    {"name": "[对照] 20-day MIN of Volume", "expr": "volume.rolling(20).min()"},
    {"name": "[对照] 60-day SMA of Volume", "expr": "ts_mean(volume,60)"},
    {"name": "[对照] 20-day SMA of Volume（在库 #18，逐字重提）",
     "expr": "ts_mean(volume,20)"},
]

# 危险区下界：0.90~0.99 不触发 rdagent 去重，但与在库因子已是同一簇，留着只是碰运气。
# 09-24 用户裁决从 0.95 收到 0.90，理由是 0.95 那一档**实测挡不住真实同簇**：待买入轴
# `STD(Volume,20)` 的非自身最近邻全是量能族（`SMA(Vol,10)` 0.9266、`SMA(Vol,20)` 0.9262、
# `STD(Vol,10)` 0.9137、`SMA(Vol,5)` 0.9088，见 data/results/
# ashare_redundancy_axis_std20_detail.csv），**四条全在旧闸之下** ⇒ 「同族换一个窗口」
# 这类提名在 0.95 档会顺利过关，而它的 IC 早被库内那几条定了。0.90 是把这些读数
# 全罩住的最紧的一档（最低那条 0.9088 之上），再低就开始误伤真正的比值构造
# （`STD(Vol,5)/STD(Vol,20)` 实测 0.604，是名单里最独立的一条非自身族比值）。
# 09-24 那批（2793 有效日，面板到 2026-09-24）还给了这条政策**唯一一条直接生效证据**：
# `SMA(Volume,60)` 对在库 `SMA(Volume,20)` = **0.9409** ——旧闸 0.95 会放它进 CANDIDATE
# LIST，新闸把它挡在危险区。产物 `data/results/ashare_redundancy_policy090*.csv`。
NEAR_DUP = 0.90


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


def verdict(signed_max, abs_max):
    """两道闸各答一个问题，09-30 起**分开**判（用户裁「统一为 `< 0.99`」）。

      硬闸 `signed_max >= ASHARE_RED_BAR` —— **逐字复刻循环会丢谁**：rdagent 在
      `factor_runner.py:71` 保留 `IC_max < 0.99` 的列，`IC_max` 是**带符号**值
      （对在库伙伴取最大）。一根跟在库相关 −0.995 的镜像因子，循环**留**，
      所以这里也不许说"会被判重复"——那一格判词的唯一用途是预测白烧的机时。
      政策闸 `abs_max >= NEAR_DUP` —— **这条值不值得提名**：反号的两条是同一条
      信息的两个符号，对本线库仍是同簇 ⇒ 仍挡在 CANDIDATE LIST 之外，只是罪名
      从"循环会丢它"改成"同簇"。
    """
    if signed_max >= ASHARE_RED_BAR:
        return "会被判重复"
    if abs_max >= NEAR_DUP:
        return "危险区(同簇)"
    return "可提名"


def summarize(P, S, expr_of=lambda name: ""):
    """候选×在库 两张均值矩阵 → 判重表（判据单点，夹具直接喂合成矩阵复跑这里）。

    `pearson_signed_max` 是**硬闸**吃的量（`P.loc[name].max()`，带符号，= rdagent 的
    `IC_max`）；`pearson_max` 是 |corr| 读数、喂**政策闸**。拆成函数而不是埋在 `main()`
    里，是为了让"镜像因子该翻判词"这件事能用合成夹具真跑一遍（见
    `stock/v1/temp/check_signed_bar_0930.py`），而不是只靠读代码。
    """
    rows = []
    for name in P.index:
        row_p = P.loc[name]
        ap = row_p.abs()
        asn = S.loc[name].abs()
        worst = ap.idxmax()                # |corr| 口径的最近邻——政策闸认的是它
        second = float(ap.sort_values(ascending=False).iloc[1])
        rows.append({
            "name": name,
            "expr": expr_of(name),
            # ★ 硬闸判据：带符号、对在库伙伴取最大＝rdagent `IC_max` 同一个量
            "pearson_signed_max": float(row_p.max()),
            "signed_nearest_lib": row_p.idxmax(),
            # |corr| 那把尺降级为读数（同簇识别与政策闸仍用它），没有删
            "pearson_max": float(ap.max()),
            "nearest_lib": worst,
            "pearson_signed": float(P.loc[name, worst]),
            "pearson_second": second,
            "spearman_max": float(asn.max()),
            "bar": ASHARE_RED_BAR,
            "verdict": verdict(float(row_p.max()), float(ap.max())),
        })
    return pd.DataFrame(rows).sort_values("pearson_signed_max", ascending=False)


def main():
    t0 = time.time()
    lib = load_library()
    cands = load_candidates()
    pool = ashare_eval.load_panel()
    wide_lib = build_wide(pool, lib, "在库")
    wide_cand = build_wide(pool, cands, "候选")

    P, S = daily_corr(wide_lib, wide_cand, ASHARE_MIN_CS)

    by_name = {c["name"]: c["expr"] for c in cands}
    res = summarize(P, S, expr_of=lambda name: by_name.get(name, ""))

    detail = P.stack().rename("pearson").to_frame()
    detail["spearman"] = S.stack()
    detail.index.names = ["candidate", "in_library"]
    detail = detail.reset_index()
    detail.to_csv(ASHARE_RED_DETAIL, index=False)
    res.to_csv(ASHARE_RED_OUT, index=False)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 60)
    print("\n===== 判重预检（硬闸 = 逐日截面原始值 Pearson 均值的**带符号**最大值，"
          "与 rdagent `IC_max < 0.99` 同量；政策闸仍看 |corr| ≥ 0.90） =====")
    print(res.drop(columns=["expr", "bar"]).to_string(index=False))
    # 这一场两道尺子差几条：>0 就是说「旧口径会把循环其实会留的因子报成重复」
    flip = int(((res["pearson_max"] >= ASHARE_RED_BAR)
                & (res["pearson_signed_max"] < ASHARE_RED_BAR)).sum())
    print(f"\n[两道尺子之差·只报不判] |corr|≥{ASHARE_RED_BAR} 而带符号<{ASHARE_RED_BAR} 的候选 "
          f"{flip}/{len(res)} 条（这些是**镜像同簇**：循环会留、本线政策不提名）")
    print(f"\n[输出] {ASHARE_RED_OUT}")
    print(f"[输出] {ASHARE_RED_DETAIL}（候选×在库 全矩阵）")
    print(f"[耗时] {time.time() - t0:.0f}s")
    bad = res[res["verdict"] != "可提名"]
    if len(bad):
        print(f"\n⚠️ {len(bad)} 条候选不该进 CANDIDATE LIST：{', '.join(bad['name'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
