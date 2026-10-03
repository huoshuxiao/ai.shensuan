# -*- coding: utf-8 -*-
"""「稳不稳」秤：把在库因子的逐日截面 IC 序列按时间窗口切开读数（只读）。

为什么要有这台秤
---------------
环1（run_ashare_factor_eval.py）已经算过每个因子的**逐日**截面 IC，但返回时
只留了全样本均值/ICIR 就把序列丢掉。一个因子全样本 Rank IC = +0.015 有两种
完全不同的来路：① 十六年里每年都稳定在 +0.015 上下；② 全靠 2019~2021 那两年
撑，近三个月已经翻负。只看均值分不出这两种，而「低相关」「IC 高」的判据都
不含这个维度 —— 本文件就是补这个维度，把「稳不稳」变成一个可比的数字。

尺子从哪来（关键是**不抄第二份**）
---------------------------------
面板加载、前向收益、表达式求值、逐日截面 IC 全部 import 环1 的原函数；环1 的
evaluate() 多了一个可选的 series_sink：传一个空 dict 进去，它就把本来要丢弃的
逐日序列顺手交出来。所以本文件的 rank_ic_full 与环1 指标行的 cs_rank_ic_mean 是同一次
计算的两份读数（落盘前当场对一次，见下面的 A 判据），不是两处各算一遍。

四个读数（都是**读数**，不是判据）
---------------------------------
主线用 Spearman 版逐日截面 Rank IC（秩相关，抗个股涨跌停造成的离群值）：

1) 滚动一年 IC     roll = mean_{252 个交易日}(IC_t)
   → roll_last / roll_min / roll_max：这一条曲线现在在哪、历史上最低与最高到过哪；
     roll_same_pct：全部窗口里与全样本同正负号的比例（1.0 = 任何一个「年」都不打脸）。
2) 分年 IC         IC_y = mean_{自然年 y}(IC_t)
   → year_same_pct：多少个自然年同号；lo_ic_year/hi_ic_year 给逐年 IC 最低与最高的那两年
     及其值（**只说高低不说好坏**：这批因子 IC 为负，hi 那年是反转最不打用的年份）。
     （注：t 日的 IC 配的是 t+1 日的收益，跨年那一天的收益算进后一年，边界误差 1 天。）
3) 等分折 IC       把整段按时间顺序等分 F 段，各算一次 mean(IC)
   → fold_same_pct / fold_min / fold_last / fold_n：fold_last 是最新那一段，最接近「将来」；fold_n 是**归档那一次真的切出了几段**（另两根腿早有 roll_n / n_years，折腿 10-01 才补上）⇒ 拿它对当前配置，不同版当场看出来。
4) 衰减            对滚动一年曲线做最小二乘（斜率手写 Σ(x-x̄)(y-ȳ)/Σ(x-x̄)²，不用 polyfit）
   → slope_per_yr：每年 IC 水平挪动多少；drift_ratio = (斜率 × 样本年数) / 全样本 IC，
     读法是「整段里走过的漂移相当于自身水平的几倍」：|比值| > 1 ⇒ 末期符号已由趋势决定，
     ≈0 ⇒ 平的。近 R 年单独一档：recent_ic / recent_sign_ok。

两条对表，各管各的问题（09-26 首次全市场跑把它逼清楚的）
--------------------------------------------------------
A) **判据（承重）**：同一次运行内，本秤存下来的逐日序列求平均，必须逐位等于环1 指标行的
   `cs_rank_ic_mean` / `cs_ic_mean`，天数必须逐条等于它的 `n_days`（|差| ≤ 1e-15）。
   两边是同一次 `evaluate()` 的两份输出，所以这条只可能因**本秤**弄坏序列而失败。
B) **读数（不是判据）**：与上一次环1 归档 csv 比最大差。它回答的是「那份归档表还代不代表
   现在这版面板」—— 09-26 撞上的实情：归档落盘 09-24 02:12，面板 09-25 23:55 被日更 ②
   全量重生成过 ⇒ 差 6.4e-5 = 一天的 IC 权重，是「数据往前走了」而不是「口径分叉」，判据 A
   不受影响；打印 ⚠️ 提醒「环1 表及引用它的看板数字对应上一版面板」。只有当差 ≥ 5e-4
   （一天数据推不出这个量级）才硬失败，因为那种量级意味着两条路真的分叉了
   （有人动了护栏/起点/求值口径）。

边界与不办的事
--------------
- 只写 ashare_rolling_ic.csv / ashare_ic_daily.csv / ashare_ic_yearly.csv 三个
  **新**文件；⑳ 的归档基线一个字节不动。判据（红条、ASHARE_RED_BAR、准入线）
  一律不改 —— 这些数先进来看，要不要升成判据由用户拍。
- 这不是真·样本外：在库这 21 条是 RD-Agent 在**全样本**上选出来的，本文件量的
  是「历史分段稳不稳 + 近期衰减没衰减」，属代理指标；真样本外只能等 forward
  running（每天日更之后新落的日子往后再看）。这一点在汇报里必须念出来。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import json
import os
import sys
import time

import numpy as np
import pandas as pd

from config import (ASHARE_FACTORS_JSON, ASHARE_DAILY_H5, ASHARE_ROLLING_OUT,
                    ASHARE_ROLLING_DAILY, ASHARE_ROLLING_YEARLY,
                    ASHARE_ROLL_WINDOW, ASHARE_ROLL_FOLDS,
                    ASHARE_ROLL_RECENT_YEARS, ASHARE_EVAL_OUT)
from run_ashare_factor_eval import load_panel, evaluate

FACTORS_JSON = ASHARE_FACTORS_JSON
WINDOW, FOLDS, RECENT_YEARS = ASHARE_ROLL_WINDOW, ASHARE_ROLL_FOLDS, ASHARE_ROLL_RECENT_YEARS
# 判据 A：同一次运行内「序列求平均」与「环1 指标行」是同一个 .mean() 的两次读数，
# 只允许 float64 末位噪声（实测 0），1e-15 只可能抓出真错（丢一天 / 口径串列 / 索引错位）。
SAME_RULER_TOL = 1e-15
# 读数 B 的硬失败线：跨运行差值超过这个量级就不是「面板往前走了一天」能解释的。
# 一天能带来的位移上界 = |单日 IC 极值 - 均值| / 天数 ≈ 0.25 / 4058 ≈ 6e-5（09-26 实测
# 6.357e-5 正好落在这里）；5e-4 留 8 倍余量，又比这张表里相邻因子的 IC 间距（~1e-3）
# 小一个量级 —— 越线就说明两条路真分叉了，不是数据在往前走。
ARCHIVE_DRIFT_MAX = 5e-4
YEARS_IN_DAYS = 365.25


def _sign(x):
    """符号函数：0 就是 0，不参与同号统计（不会因为 sign(0)==sign(0) 白送一个 1）"""
    return float(np.sign(x))


def _mean_by_year(s):
    """按自然年切：返回 Series(index=年份, value=该年均值)"""
    return s.groupby(s.index.year).mean().sort_index()


def _folds(s, n_folds):
    """按时间顺序等分成 n_folds 段，返回各段 mean(IC) 组成的 list（每段等长，不重叠）"""
    idx = np.array_split(np.arange(len(s)), n_folds)
    vals = s.to_numpy()
    return [float(vals[i].mean()) for i in idx if len(i)]


def _rolling(s, window):
    """滚动一年 IC 曲线：mean 窗口固定 window 个交易日，不满窗的位置留 NaN（不造数）"""
    return s.rolling(window, min_periods=window).mean().dropna()


def _decay(roll, full_ic):
    """衰减读数：对滚动曲线做 OLS，返回 (每年斜率, 整段漂移 / 全样本 IC)。

    斜率 = Σ(x-x̄)(y-ȳ) / Σ(x-x̄)²，x 为距序列起点的年数。手写而不用 polyfit /
    np.cov 比值：numpy 2.x 的 polyfit 不吃 axis=0，而 np.cov 是 ddof=1、np.var 是
    ddof=0，两个混着除会把斜率系统性偏掉 (n-1)/n —— 斜率是要和 IC 绝对值比的量，
    不能带这种系数。
    drift_ratio 只在 |full_ic| 有意义时才算：全样本 IC ≈ 0 时比值会炸到天际，
    那种因子本来就不是「衰减」问题，直接给 NaN 让人去看绝对值。

    一个口径细节：滚动窗按**条数**（交易日）算，x 轴按**日历**时间算，两者不同源
    是刻意的 —— 读数要念成「这一年」就得按条数取 252 格，而斜率要可比就得摊到
    真实年数上。代价是周末/长假的间距不齐会给曲线带上轻微非线性（合成数据实测
    在 1e-9 量级，真 IC 序列的噪声比它大七个数量级，不影响读数）。
    """
    if len(roll) < 2 or abs(full_ic) < 1e-6:
        return np.nan, np.nan
    x = (roll.index - roll.index[0]).days.to_numpy() / YEARS_IN_DAYS
    y = roll.to_numpy(dtype="float64")
    xc = x - x.mean()
    vx = float((xc ** 2).sum())
    if vx <= 0:
        return np.nan, np.nan
    slope = float((xc * (y - y.mean())).sum() / vx)
    return slope, float(slope * x.max() / full_ic)


def stability_stats(name, rank_ic, pearson_ic):
    """一个因子的全部时间窗口读数；rank_ic 为空序列时返回不可评的一行"""
    s = rank_ic.dropna()
    if len(s) < WINDOW:
        return {"name": name, "status": "样本不足（逐日 IC 天数 < 一个滚动窗）",
                "n_days": int(len(s))}
    full = float(s.mean())
    sg = _sign(full)
    roll = _rolling(s, WINDOW)
    yearly = _mean_by_year(s)
    fold_means = _folds(s, FOLDS)
    # 近 R 年 = 序列末尾往回 R 个自然年（日历年，不是交易日）
    cut = s.index.max() - pd.DateOffset(years=RECENT_YEARS)
    recent = float(s[s.index > cut].mean())
    slope, drift_ratio = _decay(roll, full)
    lo_y, hi_y = yearly.idxmin(), yearly.idxmax()
    return {
        "name": name, "status": "ok",
        "n_days": int(len(s)),
        "span": f"{s.index.min():%Y-%m-%d}~{s.index.max():%Y-%m-%d}",
        # —— 全样本（应与环1 逐位相同，末尾对表会验）——
        "rank_ic_full": full,
        # +1e-9 地板沿用环1 写法：退化到常数 IC 的序列会顶到 1e7，真逐日 IC 不会
        "rank_icir_full": full / (float(s.std()) + 1e-9),
        "pearson_ic_full": float(pearson_ic.dropna().mean()),
        # —— 读数 1：滚动一年 ——
        "roll_last": float(roll.iloc[-1]),
        "roll_min": float(roll.min()),
        "roll_max": float(roll.max()),
        "roll_same_pct": float((np.sign(roll.to_numpy()) == sg).mean()),
        "roll_n": int(len(roll)),
        # —— 读数 2：分年 ——
        "n_years": int(len(yearly)),
        "year_same_pct": float((np.sign(yearly.to_numpy()) == sg).mean()),
        # 名字只说数值高低，不说好坏：这批因子 IC 全为负，「best」会被读成「好用的那年」，
        # 而逐年 IC **最高**那一年恰恰是反转方向最不打用的年份（实测集中在 2020）。
        "lo_ic_year": int(lo_y), "lo_ic": float(yearly.min()),
        "hi_ic_year": int(hi_y), "hi_ic": float(yearly.max()),
        # —— 读数 3：等分折 ——  ⚠️ `fold_n` 记的是**归档那一次实际用的**折数，不是当前配置值：这三列是
        #     跑出来的、折数却是配置项 ⇒ 表里不留这个戳就判断不出 `fold_*` 与配置同不同版（09-30 拨 5→25 后差了一版三天）
        "fold_same_pct": float(np.mean([_sign(v) == sg for v in fold_means])),
        "fold_min": float(np.min(fold_means)), "fold_last": float(fold_means[-1]), "fold_n": int(len(fold_means)),
        # —— 读数 4：衰减 / 近期 ——
        "slope_per_yr": slope,
        "drift_ratio": drift_ratio,
        "recent_ic": recent,
        "recent_sign_ok": _sign(recent) == sg,
    }


def yearly_table(all_stats, rank_series, pearson_series):
    """因子 × 自然年 明细长表：分年那档的完整形状留在这里，汇总文件只留极值"""
    rows = []
    for st in all_stats:
        if st.get("status") != "ok":
            continue
        name = st["name"]
        r, p = rank_series[name].dropna(), pearson_series[name].dropna()
        pv = _mean_by_year(p)
        for y, v in _mean_by_year(r).items():
            rows.append({"name": name, "year": int(y),
                         "n_days": int((r.index.year == y).sum()),
                         "rank_ic": float(v),
                         "pearson_ic": float(pv.loc[y]) if y in pv.index else np.nan})
    return pd.DataFrame(rows)


def _mtime(path):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path))) \
        if os.path.exists(path) else "<不存在>"


def check_same_ruler(summary, rows, sink):
    """判据 A（承重那条）：**同一次运行内**，序列求平均必须逐位等于环1 当场算出的字段。

    比的是同一个 `evaluate()` 的两份输出：它自己返回的指标行（`cs_rank_ic_mean` /
    `cs_ic_mean` / `n_days`）与它经 `series_sink` 交出来的原始逐日序列。因此这条
    **只可能因本秤出错而失败** —— 求均值时多 dropna 了一天、把 Pearson 当成 Rank 存、
    concat 索引错位，都会在这里被抓住；它不管「尺子有没有被抄第二份」，那是 import
    本身保证的（面板、前向收益、daily_cross_ic 全是环1 的原函数）。
    容差 1e-15：两边是同一个 pandas `.mean()` 的结果，理论上应当逐位相同。
    """
    by_name = {r["name"]: r for r in rows}
    bad = []
    for rec in summary.itertuples(index=False):
        if getattr(rec, "status", "") != "ok":
            continue
        r = by_name[rec.name]
        s = sink[rec.name]
        checks = [("汇总列 rank_ic_full", rec.rank_ic_full, r["cs_rank_ic_mean"]),
                  ("汇总列 pearson_ic_full", rec.pearson_ic_full, r["cs_ic_mean"]),
                  ("序列本身 rank 均值", float(s["rank"].dropna().mean()), r["cs_rank_ic_mean"]),
                  ("序列本身 pearson 均值", float(s["pearson"].dropna().mean()), r["cs_ic_mean"])]
        for what, got, want in checks:
            if abs(got - want) > SAME_RULER_TOL:
                bad.append(f"{rec.name}: {what} {got!r} vs 环1 指标行 {want!r}")
        if len(s["pearson"]) != int(r["n_days"]):
            bad.append(f"{rec.name}: 序列天数 {len(s['pearson'])} "
                       f"!= 环1 n_days {int(r['n_days'])}")
    if bad:
        raise SystemExit("[判据 A 不过] 本秤存下来的序列与环1 当场算的指标行不是同一份：\n  "
                         + "\n  ".join(bad[:8]) + ("…" if len(bad) > 8 else ""))
    n = int((summary["status"] == "ok").sum())
    print(f"[判据 A] {n} 条：序列求平均 == 环1 指标行（|差| ≤ {SAME_RULER_TOL:.0e}）、"
          "天数逐条相等 ✅")


def report_archive_drift(summary):
    """读数 B（**不是判据，是过期表读数**）：与上一次环1 归档比，最大差是多少。

    A 已经锁住「本跑自洽」，B 回答的是另一个问题：**屏上/文档里那份
    `ashare_factor_eval.csv` 还代不代表现在这版面板**。09-26 首次全市场跑就撞上它：
    归档表落盘 09-24 02:12，而面板昨晚 23:55 被日更 ② 全量重生成过 ⇒ 环1 表比面板
    旧一天以上，rank 最大差 6.4e-5（= 一天 IC 权重 ~0.25/4058，正是「多贴一天」的量级）。

    只保留一条硬失败：|差| >= ARCHIVE_DRIFT_MAX 不是「数据往前走了一天」能解释的，
    那种量级只可能是两条路真的分叉了（有人改了护栏/起点/求值口径）⇒ 报错退出。
    """
    if not os.path.exists(ASHARE_EVAL_OUT):
        print(f"[读数 B] 没有环1 归档（{ASHARE_EVAL_OUT}）可比 ⇒ 首次跑，跳过过期表读数")
        return
    ref = pd.read_csv(ASHARE_EVAL_OUT)
    ok = summary[summary["status"] == "ok"]
    both = ok[ok["name"].isin(set(ref["name"]))]
    if both.empty:
        print(f"[读数 B] ⚠️ 归档表里一条花名都对不上（因子库换过？）⇒ 本读数无法给出，"
              "判据 A 不受影响")
        return
    ref = ref.set_index("name")
    dr = (both.set_index("name")["rank_ic_full"]
          - ref.loc[both["name"], "cs_rank_ic_mean"]).abs().max()
    dp = (both.set_index("name")["pearson_ic_full"]
          - ref.loc[both["name"], "cs_ic_mean"]).abs().max()
    if max(dr, dp) >= ARCHIVE_DRIFT_MAX:
        raise SystemExit(f"[判据失败] 与环1 归档差 {max(dr, dp):.3e} ≥ {ARCHIVE_DRIFT_MAX:.0e}"
                         f"（rank {dr:.2e} / pearson {dp:.2e}）⇒ 这不是「面板往前走了一天」"
                         "能解释的量级，先查是不是有人动了护栏/起点/求值口径，别用这张表")
    print(f"[读数 B] 可比 {len(both)}/{len(ok)} 条：rank 最大差 {dr:.1e}、pearson 最大差 "
          f"{dp:.1e}（都在阈值 {ARCHIVE_DRIFT_MAX:.0e} 以下 ⇒ 是数据往前走了，不是口径分叉）\n"
          f"         环1 归档落盘 {_mtime(ASHARE_EVAL_OUT)}　面板落盘 {_mtime(ASHARE_DAILY_H5)}"
          + ("\n         ⚠️ 归档比面板旧 ⇒ 上面那张环1 表（以及看板/文档里引用它的数字）"
             "对应的是**上一版面板**；要刷新就重跑 run_ashare_factor_eval.py（约 14 分钟，"
             "会覆写权威 csv，先备份）"
             if os.path.getmtime(ASHARE_EVAL_OUT) < os.path.getmtime(ASHARE_DAILY_H5) else ""))


def main():
    with open(FACTORS_JSON, encoding="utf-8") as f:
        items = json.load(f)
    tasks = [{"name": it.get("name", ""), "expr": it.get("expr", "")}
             for it in items if it.get("expr")]
    print(f"[因子] {FACTORS_JSON} 共 {len(items)} 条，可翻译进 DSL 的 {len(tasks)} 条")
    print(f"[参数] 滚动窗={WINDOW} 交易日；等分折={FOLDS}；近期窗={RECENT_YEARS} 自然年")

    pool = load_panel()
    sink = {}
    t0 = time.time()
    rows = evaluate(pool, tasks, series_sink=sink)
    print(f"[扫面板] 完成，耗时 {time.time() - t0:.0f}s（窗口统计在下面，毫秒级）")

    rank_series = {k: v["rank"] for k, v in sink.items()}
    pearson_series = {k: v["pearson"] for k, v in sink.items()}
    expr_by_name = {t["name"]: t["expr"] for t in tasks}
    stats = []
    for r in rows:
        name = r["name"]
        if name not in rank_series:
            stats.append({"name": name, "status": r.get("status", "无逐日序列"),
                          "n_days": 0})
            continue
        st = stability_stats(name, rank_series[name], pearson_series[name])
        st["expr"] = expr_by_name.get(name, "")
        stats.append(st)
    summary = pd.DataFrame(stats)

    check_same_ruler(summary, rows, sink)
    report_archive_drift(summary)

    # 逐日宽表：两个口径都存，换窗口/换口径时不必重扫 11 分钟面板
    daily = pd.DataFrame({**{f"rank|{k}": v for k, v in rank_series.items()},
                          **{f"pearson|{k}": v for k, v in pearson_series.items()}}
                         ).sort_index()
    daily.index.name = "date"
    yr = yearly_table(stats, rank_series, pearson_series)

    for path, df in [(ASHARE_ROLLING_OUT, summary), (ASHARE_ROLLING_DAILY, daily),
                     (ASHARE_ROLLING_YEARLY, yr)]:
        df.to_csv(path, index=(df.index.name is not None))
        print(f"[输出] {path}（{df.shape[0]} 行 × {df.shape[1]} 列）")

    cols = ["name", "rank_ic_full", "roll_last", "roll_same_pct", "year_same_pct",
            "lo_ic_year", "lo_ic", "fold_same_pct", "fold_last", "recent_ic",
            "recent_sign_ok", "drift_ratio"]
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print("\n===== 「稳不稳」读数（rank IC 主线；同号占比 1.0 = 从不打脸）=====")
    print(summary[summary["status"] == "ok"]
          [[c for c in cols if c in summary.columns]].to_string(index=False))
    bad = summary[summary["status"] != "ok"]
    if len(bad):
        print("\n===== 没能出读数的 =====")
        print(bad[["name", "status"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
