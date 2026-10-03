# -*- coding: utf-8 -*-
"""M7（市场宽度）那一眼好得不像话 ⇒ 拆四问。只读缓存的日频表，不再碰面板。

输入：`tmp_dd_exposure_0927/exp_daily.csv.gz`（满仓腿净日收益 + 域等权日收益 + 市场宽度）
      `tmp_dd_exposure_0927/exp_nav.csv.gz`、`exp_exposure.csv.gz`（主脚本落的日频净值与 E）

① **制度分解**：按 M7-45 的在场/空仓把满仓腿 M1 的日收益分两组 —— 日均、中位、合计、最惨 5 天。
   读法：M1 的亏损若几乎全落在空仓组、赚钱全落在在场组 ⇒ 这根判据确实摸到了东西；
   两组差不多的话，那个 +18.4pp 就是别的东西（要先查实现）。
② **肥尾程度**：M7-45 相对 M1 的日差，最好/最差 5 天各占累计多少、逐年怎么分布、前后半年各多少。
   读法：一笔「样本内好到离谱」的账，最怕只靠一年或几天。㉟㊱ 那道闸就是死在「全部来自 2015」。
③ **阈值扫描 0.20~0.60**：平滑 = 不是挑出来的孤点；有悬崖 = 那个 x 本身是噪音。
④ **择时还是择股**：同一根 E 分别叠在「5 席篮子」与「域等权」上。
   读法：域等权自己もし大幅变好 ⇒ 主要是在择市（跟选股无关，谁用都一样）；
   篮子变好而域等权不变 ⇒ 是「这批票只在宽市里赚钱」，那才轮到名单层记账。

不改任何生产代码；产物 `tmp_dd_exposure_0927/m7_probe_*.csv`。
"""

import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tmp_dd_exposure_0927")
TRADING_DAYS = 252
COST = 0.0015          # ASHARE_PORT_COST_ONE_WAY，与主脚本同值（这里只做二次分析，不改判据）


def ann(s):
    p = s.dropna()
    return float(p.mean() * TRADING_DAYS)


def overlay(r, breadth, x):
    """与主脚本同一个动作：E 收盘定、次日生效、|ΔE| 收一边费"""
    e = (breadth >= x).astype(float).shift(1).fillna(1.0)
    tax = COST * e.diff().abs().fillna(0.0)
    return r * e - tax, e


def dd_min(net):
    nav = (1.0 + net).cumprod()
    return float((nav / nav.cummax() - 1.0).min())


def main():
    t0 = time.time()
    daily = pd.read_csv(os.path.join(OUT, "exp_daily.csv.gz"), index_col=0, parse_dates=True)
    r = daily["M1净日收益"].dropna()
    b = daily["域等权日收益"].reindex(r.index).fillna(0.0)
    breadth = daily["市场宽度"].reindex(r.index)
    if breadth.isna().mean() > 0.001:
        raise SystemExit(f"⇒ 宽度序列缺值 {(breadth.isna().mean()):.1%} ⇒ 缓存与主脚本不同批，别看")
    idx = r.index
    expo = pd.read_csv(os.path.join(OUT, "exp_exposure.csv.gz"), index_col=0, parse_dates=True)
    e45 = expo["M7-45"].reindex(idx)

    # ---------- ① 制度分解 ----------
    rows = []
    for lab, mask in (("在场 E=1", e45 > 0.5), ("空仓 E=0", e45 <= 0.5)):
        s, sb = r[mask], b[mask]
        rows.append({"组": lab, "天数": int(mask.sum()), "占比": float(mask.mean()),
                     "篮子日均": float(s.mean()), "篮子日中位": float(s.median()),
                     "篮子年化": float(s.mean() * TRADING_DAYS),
                     "篮子最惨5天合计": float(s.nsmallest(5).sum()),
                     "域等权日均": float(sb.mean()), "域等权年化": float(sb.mean() * TRADING_DAYS)})
    reg = pd.DataFrame(rows).set_index("组")
    with pd.option_context("display.width", 240):
        print("\n===== ① 满仓腿的日收益，按 M7-45 在场/空仓分组 =====")
        print(reg.to_string(float_format=lambda v: f"{v:+.5f}"))
    reg.to_csv(os.path.join(OUT, "m7_probe_regime.csv"))

    # ---------- ② 肥尾 / 逐年 / 前后半 ----------
    d = (overlay(r, breadth, 0.45)[0] - r).dropna()
    tot = float(d.sum())
    half = idx[len(idx) // 2]
    yr = d.groupby(d.index.year).sum()
    h1, h2 = float(d[d.index < half].sum()), float(d[d.index >= half].sum())
    print(f"\n===== ② M7-45 比满仓每天多赚/少赚（日差合计 {tot:+.4f}）=====")
    print(f"  最好 5 天 {float(d.nlargest(5).sum()):+.4f}（占 {float(d.nlargest(5).sum()) / tot:+.1%}）"
          f"｜最差 5 天 {float(d.nsmallest(5).sum()):+.4f}（占 {float(d.nsmallest(5).sum()) / tot:+.1%}）"
          f"｜为正的日占比 {(d > 0).mean():.1%}")
    print(f"  前后半年（切点 {half.date()}）：前半 {h1:+.4f}｜后半 {h2:+.4f}"
          f"⇒ {'两半同号' if h1 * h2 > 0 else '★翻号：正的部分只在一边'}")
    print("  逐年：" + "、".join(f"{y}{v:+.3f}" for y, v in yr.items())
          + f"｜为正 {(yr > 0).sum()}/{len(yr)} 年")
    pd.DataFrame({"日差": d}).to_csv(os.path.join(OUT, "m7_probe_dailydiff.csv"))

    # ---------- ③ 阈值扫描 ----------
    rows3 = []
    for x in (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
        net, e = overlay(r, breadth, x)
        rows3.append({"x": x, "年化": ann(net), "Δ年化 vs M1": ann(net) - ann(r),
                      "最深回撤": dd_min(net), "Δ回撤 vs M1": dd_min(net) - dd_min(r),
                      "空仓占比": float((e <= 0.5).mean()),
                      "调仓次数": int((e.diff().abs() > 1e-9).sum())})
    grid = pd.DataFrame(rows3).set_index("x")
    print("\n===== ③ 阈值扫描（宽度全期均值 "
          f"{float(breadth.mean()):.3f}、中位 {float(breadth.median()):.3f}）=====")
    print(grid.to_string(float_format=lambda v: f"{v:+.4f}"))
    grid.to_csv(os.path.join(OUT, "m7_probe_grid.csv"))

    # ---------- ④ 择时 vs 择股 ----------
    rows4 = []
    for lab, s in (("5 席篮子（M1）", r), ("域等权（可投域基准）", b)):
        for x in (0.30, 0.45):
            net, e = overlay(s, breadth, x)
            rows4.append({"对象": lab, "x": x, "满仓年化": ann(s), "择时后年化": ann(net),
                          "Δ年化": ann(net) - ann(s), "满仓最深回撤": dd_min(s),
                          "择时后回撤": dd_min(net), "Δ回撤": dd_min(net) - dd_min(s)})
    tt = pd.DataFrame(rows4).set_index(["对象", "x"])
    print("\n===== ④ 同一根 E 叠在篮子上 vs 叠在域等权上 =====")
    print(tt.to_string(float_format=lambda v: f"{v:+.4f}"))
    tt.to_csv(os.path.join(OUT, "m7_probe_timing_vs_basket.csv"))
    print(f"\n[输出] {OUT}/m7_probe_*.csv　{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
