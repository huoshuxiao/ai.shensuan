# -*- coding: utf-8 -*-
"""丙-2：装载一次面板，把「五分位归因切几格」这根旋钮扫一遍。

旋钮本体：`config/config.py:122` `ASHARE_PORT_QUINTILES`（默认 5，env `STOCK_PORT_QUINTILES`）。
它在全线只有**一个落点**——`run_ashare_portfolio_eval.py:414` 那句
`quintiles=ASHARE_PORT_QUINTILES`，产物就是主表那三列
`excl_worst_ann` / `excl_worst_vs_pool_ann` / `q5_ann`（`:425-428`）。两问：

  问一（决策面）这根旋钮有没有接进任何掏钱的决定？
      读代码是「没有」：`run_signal` 里 qw 只喂返回值 qs，权重 w、成本 cost、
      统计 _stats 一个字都不读它。但读代码不算证 ⇒ **Q3** 用真跑量：同一信号、
      同一网格、同一 top_n，各档（含 q=0＝压根不切桶）的**多头腿日频净值必须逐位
      相同**、统计 dict 必须逐键相等。**Q4** 给它装牙：拿 top_n 50 vs 51 走同一个
      diff 函数，必须翻出非零差——否则「各档全等」可能只是那把尺子瞎了
      （09-29 那条「正对照要选在差异能活到判据那一层」）。
  问二（读数面）档数换了，读数量多少钱？
      ⚠️ 换档改的不是精度，是**定义**：`excl` 取的是桶 0..q-2，末桶就是被「剔掉」
      那一坨 ⇒ 剔掉的比例恒等于 1/q。q=2 踢一半、q=5 踢 20%、q=20 只踢 5%。
      而生产踩的是「四条量能构造并集、命中≥1 就踢」，09-30 戊-B 实测它在刀口 0.80
      下实踢 45.5%（留存 0.545139）⇒ 与生产同剔幅的档是 **q≈2.2**，不是 q=5。
      这句换算由 R3 从戊-B 的落盘现算，不靠嘴说。

档位：q ∈ 0/1/2/3/5/10/20（生产档 5 必须在梯子上）。q=1 是**退化档**：只有一格桶、
一条不剔 ⇒ 按入口自己的式子（qs[:-1] 是空集）「剔除增益」根本无定义 ⇒ Q5 要求那一格
必须是 NaN。它同时是「档数＝定义」这条结论的极限证据。

判据 6 行，任何一行红 ⇒ exit=1：
  Q1  样本对齐：钉到归档那一版（2852 个 live 交易日、末日 2026-09-24），不裁就没法逐位对表
  Q2  锚点：q=5 那一档的五个数（多头腿年化/毛年化/剔除年化/剔除增益/末档年化）对归档 |Δ|<1e-9
  Q3  只喂读数：各档多头腿日频净值与统计 dict 与 ladder[0]（q=0）**逐位相同**（max|Δ|==0.0）
  Q4  牙：同一个 diff 函数比 top_n 50 vs 51 必须 >0（证明 Q3 的零不是瞎出来的）
  Q5  读数有牙：末档年化与剔除增益必须随档变化（全档同值＝本场白跑）；梯子上有 q=1 时
      那一格的剔除增益必须是 NaN（一条不剔 ⇒ 增益无定义）
  Q6  桶构成独立复算：不叫 run_signal，另按它同式（同一道 tradable_mask、同一个
      rank(pct=True)）在 8 个采样调仓日重切桶 ⇒ 每桶非空、末桶占比随档严格单调下降、
      含 q=1 时那一档占比须恰为 1.0、梯子须含 ≥2 个不同档（档数只剩 1 ⇒ 必须报红，
      不许「看不见差异还装绿」）
只报不判的读数 R1~R4：末桶实际占比 vs 1/q（含等号会略多）、pool 腿与「全池等权」的
口径差、与生产并集闸的剔幅等效档换算、本场内存/耗时账单 + 归档是否中途被人覆写。

负对照（真跑，不是设想）：`QL_NEGCTL=1` 七臂全钉成生产那一档 q=5（**臂数不变**，
只是档数只剩一个）⇒ 红集必须**恰好** {Q5, Q6}，Q1~Q4 全绿。这场只动档数、不动样本，
所以锚点仍须逐位对表——Q2 红了就是样本没对齐，不是档数的功劳。

覆写面：只写 `stock/v1/temp/tmp_quintile_ladder_0930{,_negctl}/`。对表基准
`stock/v1/data/results/ashare_portfolio_eval.csv` **只读**（进程开头一次读进内存，
末尾再核 mtime 有没有被人推日更链中途换掉）。两张表都**一行一落盘**：最贵的档
（q=20 要另开 20 张权重矩阵）排在后面，崩在它上面时已算完的行还在盘上。
"""
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, f"{ROOT}/stock/v1/src")
sys.path.insert(0, f"{ROOT}/stock/v1/temp")

import math
import os
import resource
import time

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401,E402

from config import (ASHARE_PORT_HOLD, ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED,
                    ASHARE_PORT_QUINTILES, ASHARE_PORT_START, ASHARE_PORT_TOP_N)
from ashare_screen import BUY_EXPR, build_matrices, factor_matrices, gate_desc, load_panel
import run_ashare_portfolio_eval as R

TRADING_DAYS = 252
NEGCTL = os.environ.get("QL_NEGCTL") == "1"
SUF = "_negctl" if NEGCTL else ""
OUT_DIR = f"{ROOT}/stock/v1/temp/tmp_quintile_ladder_0930{SUF}"
ARCH_EVAL = (os.environ.get("QL_ANCHOR")
             or f"{ROOT}/stock/v1/data/results/ashare_portfolio_eval.csv")
# 戊-B（09-30）落盘的「生产并集闸实踢多少」：只读它一个数做等效档换算，不重跑
ARCH_QHOLD = f"{ROOT}/stock/v1/temp/tmp_q_hold_grid_0930/quantile_ladder.csv"

PROD_Q, HOLD = ASHARE_PORT_QUINTILES, ASHARE_PORT_HOLD
if NEGCTL:
    # 档数全钉成生产那一档，但**臂数不变**（七臂）：负对照要的是「差异看不见」，
    # 不是「只跑一臂」——后者会让 Q3 因无从比较而红，那条红是脚本自己的假象
    Q_LADDER = tuple([PROD_Q] * 7)
else:
    Q_LADDER = (0, 1, 2, 3, 5, 10, 20)
N_TOP, N_TOOTH = ASHARE_PORT_TOP_N[0], ASHARE_PORT_TOP_N[0] + 1
SAMPLE_END = os.environ.get("QL_SAMPLE_END", "2026-09-24")
EXPECT_LIVE_DAYS = 2852
PROD_LABEL = {e: l for l, e, _k in R.SIGNALS}.get(BUY_EXPR)
CHECKS = []

# 生产档必须在梯子上，否则「锚点」根本不存在，Q2/Q3 会拿别档冒充生产
assert PROD_Q in Q_LADDER, f"生产档 q={PROD_Q} 不在梯子上 {Q_LADDER} ⇒ 锚点判据无从谈起"
assert PROD_LABEL, f"排序轴 {BUY_EXPR} 不在入口 SIGNALS 里 ⇒ 找不到对表那一行"


def check(cid, label, ok, detail):
    CHECKS.append((cid, label, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} [{cid}] {label}｜{detail}", flush=True)
    p = os.path.join(OUT_DIR, "checks.csv")
    pd.DataFrame([{"cid": cid, "label": label, "ok": bool(ok), "detail": detail}]).to_csv(
        p, mode="a", header=not os.path.exists(p), index=False)


def readout(label, detail):
    """只报不判的读数：它可以是 0、可以没有差异，那种结果本身是答案，不该让整场作废"""
    print(f"  ·  [{label}·读数] {detail}", flush=True)


def fmt(x):
    return "nan" if (isinstance(x, float) and math.isnan(x)) else f"{x:+.6f}"


def diff(a, b):
    """两条日频净值序列的逐格最大绝对差。Q3 用它判「零」，Q4 用它判「非零」——
    两处必须是同一个函数，否则 Q3 那个零就没有对照"""
    j = pd.concat([a, b], axis=1, join="outer").fillna(0.0)
    return float(np.abs(j.iloc[:, 0] - j.iloc[:, 1]).max())


def st_equal(sa, sb):
    """两个统计 dict 逐键比（不许四舍五入，Q3 要的是「逐位」）。返回 (全等?, 不等的键)"""
    bad = []
    for k in sorted(set(sa) | set(sb)):
        x, y = sa.get(k), sb.get(k)
        both_nan = (isinstance(x, float) and isinstance(y, float)
                    and math.isnan(x) and math.isnan(y))
        if not both_nan and x != y:
            bad.append(f"{k}({x} vs {y})")
    return not bad, bad


def leg_readings(qs):
    """主表三列的现算式子，逐字照 run_ashare_portfolio_eval.py:422-428 抄。

    ⚠️ 这是对入口那四行的**复刻**，因此它本身可能被抄错 ⇒ 由 **Q2** 拿生产那一档
    对归档逐位对表来钉住它：抄错就不可能同时复现多头腿之外的三个读数。
    """
    if not qs:
        return {}
    a = [float(q.mean() * TRADING_DAYS) for q in qs]
    out = {"n_legs": len(qs), "q末腿年化": a[-1]}
    if len(qs) > 1:
        excl = pd.concat(qs[:-1], axis=1).mean(axis=1)
        pool = pd.concat(qs, axis=1).mean(axis=1)
    else:
        # 只有一格桶＝一条不剔 ⇒ 入口的 qs[:-1] 是空集，增益无定义。留 NaN 不编数
        excl = pd.Series(dtype="float64")
        pool = qs[0]
    out["excl_worst_ann"] = float(excl.mean() * TRADING_DAYS) if len(excl) else np.nan
    out["excl_worst_vs_pool_ann"] = (float((excl.mean() - pool.mean()) * TRADING_DAYS)
                                     if len(excl) else np.nan)
    out["pool腿"] = pool
    out["各腿年化"] = a
    return out


def bucket_mix(f, mtx, days, hold, qs, n_sample=8):
    """独立复算桶构成：不叫 run_signal，另按它同式（同一道 tradable_mask、同一个
    rank(pct=True)、同一句 np.minimum((r*q).astype(int), q-1)）在采样调仓日重切一次。

    为什么要有第二条：问二的答案是「剔掉比例 = 1/q」，那是**桶边界算术**，不该由
    被测的那段代码自己报数。这里只数只数、不回放收益，所以它不进决策路径，
    也就不违反「多头腿不许有第二套」。
    """
    grid = list(range(0, len(days) - hold - 1, hold))
    step = max(1, len(grid) // n_sample)
    rows = []
    for i in grid[::step][:n_sample]:
        s, d1 = days[i], days[i + 1]
        ok, _ = R.tradable_mask(s, d1, f, mtx)
        cand = f.loc[s].where(ok).dropna()
        r = cand.rank(pct=True).to_numpy()
        for q in sorted({x for x in qs if x >= 1}):
            g = np.minimum((r * q).astype(int), q - 1)
            cnt = np.bincount(g, minlength=q)
            rows.append({"date": str(s.date()), "q": q, "n_cand": int(len(cand)),
                         "末桶只数": int(cnt[-1]), "末桶占比": float(cnt[-1] / len(cand)),
                         "最小桶只数": int(cnt.min()), "非空桶数": int((cnt > 0).sum()),
                         "1/q": 1.0 / q})
    return pd.DataFrame(rows)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for name in ("quintile_ladder.csv", "bucket_mix.csv", "checks.csv"):
        p = os.path.join(OUT_DIR, name)
        if os.path.exists(p):
            os.remove(p)
    t0 = time.time()
    arch = pd.read_csv(ARCH_EVAL)
    ar = arch[(arch.signal == PROD_LABEL) & (arch.top_n == N_TOP)]
    if len(ar) != 1:
        raise SystemExit(f"[准入] 归档里 {PROD_LABEL} top_n={N_TOP} 命中 {len(ar)} 行"
                         f"（应为 1 行）⇒ 对表基准不唯一，整场作废")
    ar = ar.iloc[0]
    anchor = {k: float(ar[k]) for k in
              ("ann_return", "ann_return_gross", "excl_worst_ann",
               "excl_worst_vs_pool_ann", "q5_ann")}
    arch_mt = os.path.getmtime(ARCH_EVAL)
    print(f"[口径] 涨停闸 {gate_desc()}｜信号 {PROD_LABEL}（{BUY_EXPR}）｜top_n {N_TOP}"
          f"｜hold {HOLD}｜生产档 q={PROD_Q}｜梯子 {Q_LADDER}｜NEGCTL={int(NEGCTL)}",
          flush=True)
    print(f"[锚点] 归档 {os.path.basename(ARCH_EVAL)} 那行："
          + "｜".join(f"{k}={v:+.9f}" for k, v in anchor.items()), flush=True)

    wide, _ = load_panel()
    mtx = build_matrices(wide)
    fms = factor_matrices([BUY_EXPR], mtx)      # allow=None ⇒ 只需要排序轴那一张矩阵
    full_idx = mtx["ret_open"].index
    from_start = full_idx >= pd.Timestamp(ASHARE_PORT_START)
    n_full = int(from_start.sum())
    live = from_start & (full_idx <= pd.Timestamp(SAMPLE_END))
    n_used = int(live.sum())
    print(f"[样本] {ASHARE_PORT_START} 起 {n_full} 个交易日，钉到 {SAMPLE_END} 用 {n_used} 个"
          f"（多的 {n_full - n_used} 天已裁，不裁就没法逐位对表）", flush=True)
    mtx = {k: v.loc[full_idx[live]] for k, v in mtx.items()}
    f = fms[BUY_EXPR].loc[fms[BUY_EXPR].index[live]]
    del fms
    days = mtx["ret_open"].index
    col_of = {c: i for i, c in enumerate(mtx["close"].columns)}
    universe = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
                & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    bench = mtx["ret_open"].where(universe).mean(axis=1)
    print(f"[准备] {time.time() - t0:.0f}s｜{len(days)} 个交易日｜峰值内存 "
          f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f}MB", flush=True)

    print("\n===== 判据 Q1 样本对齐 =====", flush=True)
    check("Q1", "样本末已裁到归档那一版",
          n_used == EXPECT_LIVE_DAYS and days[-1] == pd.Timestamp(SAMPLE_END),
          f"用 {n_used} 天（应 {EXPECT_LIVE_DAYS}）｜末日 {days[-1].date()}"
          f"｜面板现有 {n_full} 天")

    print("\n===== 逐档回放（按档从小到大；最贵的 q=20 要另开 20 张权重矩阵，排最后）=====",
          flush=True)
    arms, rows = {}, []
    OUT = os.path.join(OUT_DIR, "quintile_ladder.csv")
    # 牙臂（同档、不同 top_n）单列一个任务，跟梯子共用一次面板装载
    jobs = [(q, N_TOP) for q in Q_LADDER] + [(PROD_Q, N_TOOTH)]
    for idx, (q, n) in enumerate(jobs):
        t1 = time.time()
        port, st, qs = R.run_signal(f, col_of, days, mtx, n, hold=HOLD,
                                    universe=universe, quintiles=q)
        rd = leg_readings(qs)
        del qs                                   # q=20 那档 20 张权重矩阵 ≈1.2GB，用完就放
        arms[idx] = (port, st, rd, q, n)
        row = {"arm": idx, "q": q, "top_n": n, "prod": q == PROD_Q and n == N_TOP,
               "tooth": n == N_TOOTH, "gate": R.ASHARE_TRADABLE_GATE,
               "n_rebal": st["n_rebal"],
               "n_rebal_expect": len(range(0, len(days) - HOLD - 1, HOLD)),
               "多头腿年化": st["ann_return"], "多头腿毛年化": st["ann_return_gross"],
               "夏普": st["sharpe"], "回撤": st["max_drawdown"],
               "换手": st["one_way_turnover"], "名单没凑满次数": st["n_list_short"],
               "腿数": rd.get("n_legs", 0),
               "剔除年化": rd.get("excl_worst_ann", np.nan),
               "剔除增益": rd.get("excl_worst_vs_pool_ann", np.nan),
               "末档年化": rd.get("q末腿年化", np.nan),
               "各腿年化": "|".join(f"{x:+.6f}" for x in rd.get("各腿年化", [])),
               "峰值内存MB": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
               "秒": round(time.time() - t1, 1)}
        rows.append(row)
        pd.DataFrame([row]).to_csv(OUT, mode="a", header=not os.path.exists(OUT), index=False)
        print(f"  臂{idx} q={q:>2} n={n}｜多头腿 {fmt(st['ann_return'])}｜剔除增益 "
              f"{fmt(row['剔除增益'])}｜末档 {fmt(row['末档年化'])}｜腿 {row['腿数']}"
              f"｜n={row['n_rebal']}｜{row['峰值内存MB']}MB｜{row['秒']}s", flush=True)

    base = 0                                     # 梯子第一臂（正常场是 q=0＝压根不切桶）
    prod_j = next(i for i, (_p, _s, _r, q, n) in arms.items() if q == PROD_Q and n == N_TOP)
    tooth_j = next(i for i, (_p, _s, _r, _q, n) in arms.items() if n == N_TOOTH)
    port0, st0, _rd0 = arms[base][:3]
    prod_port, _prod_st, prod_rd = arms[prod_j][:3]

    print("\n===== 判据 Q2 生产档对归档 =====")
    mine = {"ann_return": arms[prod_j][1]["ann_return"],
            "ann_return_gross": arms[prod_j][1]["ann_return_gross"],
            "excl_worst_ann": prod_rd["excl_worst_ann"],
            "excl_worst_vs_pool_ann": prod_rd["excl_worst_vs_pool_ann"],
            "q5_ann": prod_rd["q末腿年化"]}
    worst, bad = 0.0, []
    for k in anchor:
        d = abs(mine[k] - anchor[k])
        worst = max(worst, d)
        tag = f"{k} 本跑 {mine[k]:+.9f} vs 归档 {anchor[k]:+.9f} 差 {d:.2e}"
        print(f"    · {tag}")
        if d >= 1e-9:
            bad.append(tag)
    check("Q2", "锚点：q=5 那一档五个数对归档 |Δ|<1e-9",
          not bad, f"最坏差 {worst:.2e}｜不符的有 {'；'.join(bad) if bad else '无'}"
                  f"（这条同时钉住 leg_readings 那段复刻——抄错就复现不出多头腿之外的三个读数）")

    print("\n===== 判据 Q3/Q4 这根旋钮接没接进决策 =====")
    dd = {i: diff(arms[i][0], port0) for i in arms if i != base and arms[i][4] == N_TOP}
    stbad = []
    for i, (_p, s, _r, _q, n) in arms.items():
        if n != N_TOP:
            continue
        okk, badkeys = st_equal(s, st0)
        if not okk:
            stbad.append(f"臂{i}: {'、'.join(badkeys)}")
    check("Q3", f"只喂读数：各臂（n={N_TOP}）多头腿与臂{base}（q={Q_LADDER[base]}）"
                f"**逐位相同**（max|Δ|==0.0）且统计 dict 逐键相等",
          len(dd) >= 2 and all(v == 0.0 for v in dd.values()) and not stbad,
          f"比了 {len(dd)} 臂｜最大差 {(max(dd.values()) if dd else float('nan')):.1e}"
          f"｜dict 不等的有 {stbad if stbad else '无'}")
    tooth = diff(prod_port, arms[tooth_j][0])
    check("Q4", "牙：同一个 diff 函数比 top_n 50 vs 51 必须 >0",
          tooth > 0.0, f"max|Δ|={tooth:.3e}（>0 ⇒ Q3 那个零是量出来的，不是尺子瞎了）")

    print("\n===== 判据 Q5 读数换档变不变 =====")
    lad = pd.DataFrame([r for r in rows if r["top_n"] == N_TOP])

    def spread(x):
        return float(np.abs(np.diff(x)).max()) if len(x) >= 2 else 0.0

    qe = lad[lad["q"] >= 2]
    sp_end, sp_gain = spread(qe["末档年化"].to_numpy()), spread(qe["剔除增益"].to_numpy())
    vary = sp_end > 1e-9 and sp_gain > 1e-9
    r1 = lad[lad["q"] == 1]
    one_nan = bool(math.isnan(float(r1["剔除增益"].iloc[0]))) if len(r1) else None
    check("Q5", "读数有牙：末档年化与剔除增益必须随档变化；q=1 那一格增益须为 NaN"
                "（一条不剔＝增益无定义）",
          vary and (one_nan is not False),
          f"q≥2 档末档年化相邻档最大差 {sp_end:.6f}｜剔除增益 {sp_gain:.6f}"
          f"｜q=1 增益 NaN={one_nan}｜梯子上不同档数 {int(lad['q'].nunique())}")

    print("\n===== 判据 Q6 桶构成独立复算（不叫 run_signal）=====")
    mix = bucket_mix(f, mtx, days, HOLD, Q_LADDER)
    mix.to_csv(os.path.join(OUT_DIR, "bucket_mix.csv"), index=False)
    nq = sorted(mix.q.unique())
    share = mix.groupby("q")["末桶占比"].mean()
    nonempty = bool((mix["非空桶数"] == mix["q"]).all())
    mono = all(float(share[a]) - float(share[b]) > 1e-9 for a, b in zip(nq[:-1], nq[1:]))
    unit = (1 not in share.index) or abs(float(share.loc[1]) - 1.0) < 1e-12
    check("Q6", "桶构成复算：每桶非空 + 末桶占比随档严格单调下降 + 梯子须含 ≥2 个不同档"
                "（含 q=1 时那一档末桶占比须恰为 1.0）",
          nonempty and mono and len(nq) >= 2 and unit,
          f"采样 {mix['date'].nunique()} 场×{len(nq)} 档｜非空桶 {'全过' if nonempty else '有空桶'}"
          f"｜占比 {'→'.join(f'q{int(q)}:{share[q]:.4f}' for q in nq)}"
          f"｜单调 {'成立' if mono else '不成立'}｜q=1 恰为 1.0：{unit}｜不同档数 {len(nq)}")

    print("\n===== 读数 R1~R4 =====")
    if len(mix):
        dev = (mix["末桶占比"] - mix["1/q"]).abs()
        readout("R1 末桶实际占比 vs 1/q",
                f"平均偏差 {dev.mean():.5f}｜最大 {dev.max():.5f}（含等号 ⇒ 末桶略多于 1/q）"
                f"｜采样场候选只数 {int(mix['n_cand'].min())}~{int(mix['n_cand'].max())}"
                f"｜最小桶 {int(mix['最小桶只数'].min())} 只")
    first_by_q = {}
    for i, (_p, _s, _r, q, n) in arms.items():
        if n == N_TOP:
            first_by_q.setdefault(q, i)          # 负对照里同档多臂，取第一臂就够
    if 1 in first_by_q:
        q1leg = arms[first_by_q[1]][2]["pool腿"]  # q=1 的单桶＝过闸候选全池等权
        gaps = [f"q{q} 日均|Δ| {float((arms[first_by_q[q]][2]['pool腿'] - q1leg).abs().mean()):.6f}"
                for q in sorted({x for x in first_by_q if x >= 2})]
        readout("R2 pool 腿 vs 全池等权（q=1 那一格）",
                "｜".join(gaps)
                + f"｜q=1 单桶 vs 域等权基准 日均|Δ| {float((q1leg - bench).abs().mean()):.6f}"
                  "（剔除增益的减数是「各桶等权的平均」，不是全池等权 ⇒ 这部分是口径差不是信号）")
    if os.path.exists(ARCH_QHOLD):
        qh = pd.read_csv(ARCH_QHOLD)
        pr = qh[qh["prod"]]
        if len(pr):
            keep = float(pr["L1_并集1_keep_ratio"].iloc[0])
            qe_q = 1.0 / (1.0 - keep)
            near = min([q for q in Q_LADDER if q >= 1], key=lambda x: abs(x - qe_q))
            readout("R3 与生产并集闸的剔幅等效档",
                    f"戊-B 落盘：刀口 0.80 下并集(≥1) 留存 {keep:.4f} ⇒ 实踢 {1 - keep:.4f}"
                    f"｜同剔幅等效 q={qe_q:.2f}｜梯子上最近档 q={near}"
                    f"｜生产档 {PROD_Q} 的剔幅是 {1 / PROD_Q:.4f}，与并集闸不是同一件事")
        else:
            readout("R3 与生产并集闸的剔幅等效档", "戊-B 落盘里没有 prod 行，跳过")
    else:
        readout("R3 与生产并集闸的剔幅等效档", f"未读到 {ARCH_QHOLD}（戊-B 那场的产物），跳过")
    moved = os.path.getmtime(ARCH_EVAL) != arch_mt
    readout("R4 本场账单", f"整场 {time.time() - t0:.0f}s｜峰值内存 "
            f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f}MB"
            f"｜回放 {len(arms)} 臂｜归档 mtime 中途{'被改过 ⚠️' if moved else '未变'}"
            f"（改过则锚点用的是开跑前读进内存的那一版）")

    red = sorted(c[0] for c in CHECKS if not c[2])
    print(f"\n[落盘] {OUT}｜{OUT_DIR}/bucket_mix.csv｜{OUT_DIR}/checks.csv")
    print(f"===== 汇总：{len(CHECKS) - len(red)}/{len(CHECKS)} 通过 =====", flush=True)
    for cid, label, _ok, d in CHECKS:
        if not _ok:
            print(f"  ❌ [{cid}] {label}｜{d}", flush=True)
    print(f"[RED_IDS] {'、'.join(red) if red else '无'}", flush=True)
    sys.exit(1 if red else 0)


if __name__ == "__main__":
    main()
