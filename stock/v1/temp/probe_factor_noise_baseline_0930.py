#!/usr/bin/python3.10
# -*- coding: utf-8 -*-
"""历史标定：我们**自己在用**的这份 bin，因子（$factor）本来就每天跳多少只

为什么要这一份（09-30，arbitrate_pkg_0929_with_sina_0930.log + 逐票 raw 缺口核对）
    原本想说「社区包每天自造 30~34 只假跳」。逐只看下来不成立，得拆成三类：
      ① 真除权除息：交易所记账表认、包认、sina 也认（09-28 三只对照 1.02744/1.01378/1.01330
         三方差 ≤2.2e-4）。这类该跳，且 09-29 一大半是它（9 月是 A 股中期分红除息高峰）。
      ② 生成器噪声：像 SH601828 那样**每天** ±0.1~0.3% 地抖（09-17→09-29 抖了 7 次）。
         关键事实：这种抖**在我们这份 bin 的 ≤09-22 历史里一模一样地存在**（两边逐字节相同），
         不是这个包新带来的。⇒ 说它「污染 09-29」之前必须先量：历史本来就跳多少。
      ③ 算术上不可能的跳：SZ002713 包把因子抬 ×1.2866，而它的盘面价 10.88→10.73（−1.4%）
         没有任何缺口 ⇒ 主板票要自洽得涨 +26.9%，超涨跌停 ⇒ 这条是坏的。
         判据能抓它，靠的是「接缝涨幅必须落在涨跌停带内」，与任何外部源无关。

本脚本只干一件事：把 ② 的刻度量出来（过去 60 个交易日，逐日、逐只），
好让 09-29 那 63 只（占全市场 1.02%）有个「正常 / 异常」的可比基线。

只读，不写任何数据。生产 bin 的日历字节起终都核一遍，防别的会话在写。
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
PROD = os.path.join(ROOT, "common/data/stock/qlib/qlib_data/cn_data")
CAL = os.path.join(PROD, "calendars/day.txt")
NDAYS = 60
THS = (1e-4, 5e-4, 1e-3, 5e-3)      # |因子日跳| 的分档


def main():
    md5a = os.popen(f"md5sum {CAL}").read().split()[0]
    cal = [ln.strip() for ln in open(CAL) if ln.strip()]
    g1 = len(cal) - 1
    g0 = max(1, g1 - NDAYS + 1)
    print(f"生产日历 {len(cal)} 格（末 {cal[g1]}）｜窗口 {cal[g0]}…{cal[g1]} = {g1-g0+1} 个交易日")

    feats = os.path.join(PROD, "features")
    insts = sorted(os.listdir(feats))
    print(f"扫 {len(insts)} 只的 $factor.day.bin（只读）", flush=True)

    per_day = {g: {"live": 0} | {f"t{t}": 0 for t in THS} | {f"mx{t}": 0.0 for t in THS}
               for g in range(g0, g1 + 1)}
    big = []
    for n, inst in enumerate(insts):
        if n and n % 1500 == 0:
            print(f"  …{n}/{len(insts)}", flush=True)
        f = os.path.join(feats, inst, "factor.day.bin")
        if not os.path.exists(f):
            continue
        a = np.fromfile(f, dtype="<f4")
        s, v = int(a[0]), a[1:]
        lo, hi = max(s, g0), min(s + v.size, g1 + 1)
        if hi <= lo + 1:
            continue
        seg = v[lo - s:hi - s].astype(np.float64)
        ok = np.isfinite(seg) & (seg > 0)
        r = np.full(seg.size, np.nan)
        good = ok[1:] & ok[:-1]
        r[1:][good] = seg[1:][good] / seg[:-1][good] - 1.0
        for i in range(1, seg.size):
            g = lo + i
            if not np.isfinite(r[i]):
                continue
            d = per_day[g]
            d["live"] += 1
            ar = abs(r[i])
            for t in THS:
                if ar > t:
                    d[f"t{t}"] += 1
                    d[f"mx{t}"] = max(d[f"mx{t}"], ar)
            if ar > 5e-2:
                big.append((cal[g], inst.upper(), r[i], seg[i], seg[i - 1]))

    rows = []
    for g in range(g0, g1 + 1):
        d = per_day[g]
        if not d["live"]:
            continue
        rows.append({"日": cal[g], "有因子格": d["live"]} |
                    {f">{t:g}": d[f"t{t}"] for t in THS} |
                    {f">{t:g} 占比": d[f"t{t}"] / d["live"] for t in THS} |
                    {f">{t:g} 最大": d[f"mx{t}"] for t in THS})
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)

    def make_fmt(col):
        if "占比" in col:
            return lambda x: f"{x:.2%}"
        if "最大" in col:
            return lambda x: f"{x:.4%}"
        return lambda x: f"{x:,.0f}"

    print("\n逐日因子跳变计数（本 bin 自身，与包无关）")
    print(df.tail(12).to_string(index=False,
          formatters={c: make_fmt(c) for c in df.columns if c != "日"}))

    print("\n这 %d 个交易日的汇总" % len(df))
    for t in THS:
        col, mcol = f">{t:g} 占比", f">{t:g} 最大"
        print(f"  |Δf| > {t:<8g} 日均 {df[col].mean():.2%} 只｜中位 {df[col].median():.2%}｜"
              f"最多的一天 {df[col].max():.2%}（{df.loc[df[col].idxmax(), '日']}）｜"
              f"单日最大跳 {df[mcol].max():.4%}")
    last = df.iloc[-1]
    print(f"\n  末日 {last['日']}：>1e-3 的 {last['>0.001']:.0f} 只 = {last['>0.001 占比']:.2%}")
    hist, ours = df[df["日"] <= "2026-09-22"], df[df["日"] >= "2026-09-23"]
    print(f"  分段（两种写法必然不同）：")
    for lab, seg in (("包 lineage 历史 ≤09-22", hist), ("我们自己写的 09-23/24/28", ours)):
        print(f"    {lab:<26} {len(seg):>2} 天｜>1e-4 日均 {seg['>0.0001 占比'].mean():.2%}｜"
              f">1e-3 日均 {seg['>0.001 占比'].mean():.2%}｜>5e-3 日均 {seg['>0.005 占比'].mean():.2%}")
    print("  ⇒ 和包的 09-29（63 只 = 1.02%）比：同一量级就是正常，翻数倍才是这个包带进来的新东西")

    if big:
        print(f"\n  历史里 |Δf|>5% 的 {len(big)} 格（真除权/送转，非噪声）：")
        for d, inst, r, v1, v0 in sorted(big)[-12:]:
            print(f"    {d} {inst:9s} f {v0:.5f}→{v1:.5f} = {r:+.4%}")
    md5b = os.popen(f"md5sum {CAL}").read().split()[0]
    print(f"\n扫描期间日历字节：{'未被改动 ✅' if md5a == md5b else '被别的会话改动 ❌ 本场作废'}")
    return 0 if md5a == md5b else 1


if __name__ == "__main__":
    sys.exit(main())
