#!/usr/bin/python3.10
# -*- coding: utf-8 -*-
"""社区包的 $factor 那条腿每天自己造多少「无中生有的跳变」，以及这些跳变接进面板值多少钱

来历（diff_pkg_vs_prod_bin_0930.py 全市场读数，09-30）
    同源前缀 ≤09-22：17,949,319 格 × 10 字段，值差 **0**。⇒ 包和我们的 bin 是同一份底。
    我们写的三天：价格类字段 96~98% 的格两边不等，但中位相对差 5.9e-05，根子全在 $factor。
    最大一条：SZ002713 东易日盛，包把 09-23 的因子抬 ×1.2866 ⇒ 它的复权收盘从 1.41931
    跳到 1.80093（+26.9%），而我们 append_20260923.csv 记的 昨收(除权参考价)=10.88=前收
    ⇒ 交易所说那天 002713 **不除权**。⇒ 包那一步是无中生有。

两件事先分清楚（第一版这个脚本把第二件当判据，量出来全是假红，09-30 已推翻）
    ① 包的 $change 口径和我们的不一样：包写的是**盘面不含权涨幅**（丹娜生 09-23 +0.711%），
       我们按 #77 锁定的口径写**复权含权涨幅**（同一格 +1.723%）。
       但 $change **压根不进面板**（pregen 只取六列，run_ashare_rerun_chain.py:43 已记），
       下游的涨幅是拿 $close/$open 自己 pct_change 出来的 ⇒ 这条口径差传不到任何判据，
       不用管它。
    ② 包的 $factor 跳变会**顺着 $close 灌进面板**：接进来的那一格，
       r_adj = 包close_29/我们close_28 − 1 = (行情涨幅) × (包因子比) − 1
       ⇒ 包因子比偏离 1 多少，面板那天就多偏多少。**这才是要量的东西。**

护栏救不了这一档（ashare_screen.py:425 guard_ret）
    它只裁「|r_adj| > RET_LIMIT(=±30%) 而盘面正常」那批。而包自造的跳变里最狠的一条是
    +28.66% —— **正好在 30% 以内，护栏放行**。所以接包之前必须先数清楚：09-29 那一场
    包自己造了几只、最大的有多大。

判据（三场有交易所答案，可以考这把尺子准不准）
    跳变候选 = |包 $factor 今/昨 − 1| > 1e-3
      甲 真除权：交易所 f倍数 也 >1+1e-3      → 该跳，比的是「跳得准不准」
      乙 无中生有：交易所 f倍数 ≡ 1           → 不该跳，接进来就是假台阶
      丙 包漏了：交易所说除权、包没跳          → 反方向，接进来会少一根权益
    09-29 没有记账表（那天的批量快照取不回来了）⇒ 只报候选数与幅度分布，等第二源

只读，一个字节都不写。
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
PROD = os.path.join(ROOT, "common/data/stock/qlib/qlib_data/cn_data")
PKG = "/home/sunwenkun/Developer/agent-workspace/qlib_pkg_20260929/qlib_bin"
SNAP = os.path.join(ROOT, "common/data/stock/daily_snapshot")
TOL = 1e-3            # 因子比偏离 1 多远算「一次跳变」


def read_bin(p, inst, field):
    f = os.path.join(p, "features", inst, f"{field}.day.bin")
    if not os.path.exists(f):
        return None
    a = np.fromfile(f, dtype="<f4")
    return int(a[0]), a[1:]


def at(b, g):
    if b is None:
        return float("nan")
    j = g - b[0]
    return float(b[1][j]) if 0 <= j < b[1].size else float("nan")


def load_book(day_compact):
    """append_<场次>.csv → {inst 小写: f倍数}；f倍数 = 前收_bin ÷ 昨收(除权参考价)
    = 交易所那天该给因子的倍数（我们日更用的就是这个数，#77）"""
    p = os.path.join(SNAP, f"append_{day_compact}.csv")
    if not os.path.exists(p):
        return {}
    df = pd.read_csv(p, encoding="utf-8-sig", dtype={"inst": str})
    df = df[df["f倍数"].notna() & df["inst"].notna()]
    return {i.lower(): float(v) for i, v in zip(df["inst"], df["f倍数"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", type=int, default=0, help="等距抽样 N 只（0=全市场）")
    a = ap.parse_args()

    cal = [ln.strip() for ln in open(os.path.join(PKG, "calendars/day.txt")) if ln.strip()]
    days = {d: cal.index("2026-" + d) for d in ("09-23", "09-24", "09-28", "09-29")}
    books = {d: load_book("2026" + d.replace("-", "")) for d in ("09-23", "09-24", "09-28")}
    print(f"包日历 {len(cal)} 格（末 {cal[-1]}）")
    print("交易所答案在场的手： " + ", ".join(f"{d} {len(b)} 行" for d, b in books.items()) + "\n",
          flush=True)

    insts = sorted(os.listdir(os.path.join(PKG, "features")))
    if a.codes and a.codes < len(insts):
        insts = insts[::int(np.ceil(len(insts) / a.codes))]
    print(f"扫 {len(insts)} 只（包侧 factor/close + 生产侧 factor/close/change）", flush=True)

    rows = []
    for n, inst in enumerate(insts):
        if n and n % 1500 == 0:
            print(f"  …{n}/{len(insts)}", flush=True)
        qf, qc = read_bin(PKG, inst, "factor"), read_bin(PKG, inst, "close")
        pf, pc, pch = (read_bin(PROD, inst, f) for f in ("factor", "close", "change"))
        if qf is None or qc is None:
            continue
        for d, g in days.items():
            f1, f0 = at(qf, g), at(qf, g - 1)
            c1, c0 = at(qc, g), at(qc, g - 1)
            if not np.isfinite([f1, f0, c1, c0]).all() or f0 == 0 or c0 == 0:
                continue
            r_f = f1 / f0
            if abs(r_f - 1.0) <= TOL:
                continue
            ex = books.get(d, {}).get(inst, float("nan"))
            our_f0, our_f1 = at(pf, g), at(pf, g - 1)
            r_our = our_f1 / our_f0 if np.isfinite(our_f0) and np.isfinite(our_f1) and our_f0 else float("nan")
            kind = ("待第二源" if d == "09-29" else
                    ("甲 真除权" if np.isfinite(ex) and abs(ex - 1.0) > TOL else "乙 无中生有"))
            # 接缝涨幅：假设只把**包的这一格**写进 bin，面板那天自己 pct_change 出来的涨幅
            # = 包 close(g) ÷ 我们 close(g−1) − 1（g−1 两源逐位相同，所以分母用谁的一致）
            ours_prev = at(pc, g - 1)
            seam = (at(qc, g) / ours_prev - 1.0) if np.isfinite(ours_prev) else float("nan")
            rows.append({"日": d, "票": inst.upper(), "包比": r_f, "我们": r_our,
                         "交易所": ex, "类": kind,
                         "接缝": seam,
                         "盘面": (c1 / f1) / (c0 / f0) - 1.0,
                         "毒": seam - ((c1 / f1) / (c0 / f0) - 1.0)
                         if np.isfinite(seam) else float("nan"),
                         "我们change": at(pch, g)})
    df = pd.DataFrame(rows)

    print("\n" + "=" * 90)
    print("① 三场有答案的：包自己造了几只假跳（乙），真跳（甲）跳得准不准")
    print("=" * 90)
    for d in ("09-23", "09-24", "09-28"):
        gg = df[df["日"] == d]
        jia, yi = gg[gg["类"] == "甲 真除权"], gg[gg["类"] == "乙 无中生有"]
        book_all = {k: v for k, v in books[d].items() if abs(v - 1.0) > TOL}
        missed = [k for k in book_all if abs(at(read_bin(PKG, k, "factor"), days[d]) /
                                           at(read_bin(PKG, k, "factor"), days[d] - 1) - 1) <= TOL] \
            if len(book_all) < 400 else ["（除权票太多，跳过逐只反查）"]
        print(f"  ── {d} ──")
        print(f"     交易所记账说除权 {len(book_all)} 只｜包认账（甲）{len(jia)} 只｜"
              f"包不认账（丙 漏）{len(missed)} 只{'' if len(missed)<20 else '（名单见末）'}")
        print(f"     包自造的假跳（乙）{len(yi)} 只，占全市场 {len(yi)/len(insts):.2%}"
              f"｜最大 |r_f−1| = {0 if not len(yi) else (yi['包比']-1).abs().max():.4%}")
        if len(jia):
            dev = (jia["包比"] - jia["交易所"]).abs()
            print(f"     甲类的因子比 vs 交易所：中位差 {dev.median():.2e}、最大 {dev.max():.2e}"
                  f"（>1e-4 的 {int((dev>1e-4).sum())}/{len(jia)} 只）")
        if len(yi):
            print("     乙类最大的 6 只（接包原样 = 面板里 6 根假台阶）：")
            for _, r in yi.nlargest(6, "包比", keep="all").pipe(
                    lambda x: x.assign(k=(x["包比"] - 1).abs()).nlargest(6, "k")).iterrows():
                print(f"       {r['票']:9s} 包因子比={r['包比']:.5f} 我们={r['我们']:.5f} "
                      f"交易所={r['交易所']:.5f} ⇒ 接缝 {r['接缝']:+.4%}"
                      f"（盘面真涨 {r['盘面']:+.4%}，凭空多 {r['毒']:+.4%}）")
        if len(missed) >= 20:
            print(f"     丙（包漏的除权）{len(missed)} 只：{', '.join(m.upper() for m in missed[:12])} …")
    print("\n" + "=" * 90)
    print("② 09-29 那一场（没有交易所答案）：包准备灌进来的跳变清单")
    print("=" * 90)
    q = df[df["日"] == "09-29"].assign(k=lambda x: (x["包比"] - 1).abs())
    print(f"  跳变候选 {len(q)} 只（占 {len(insts)} 只的 {len(q)/len(insts):.2%}）")
    for lab, m in (("|r_f−1| ≤0.2%", q["k"] <= 2e-3), ("0.2~1%", q["k"].between(2e-3, 1e-2)),
                   ("1~5%", q["k"].between(1e-2, 5e-2)), (">5%", q["k"] > 5e-2)):
        s = q[m]
        if len(s):
            print(f"    {lab:<14} {len(s):>3} 只｜接缝涨幅 mean {s['接缝'].mean():+.4%}"
                  f" / max|{s['接缝'].abs().max():.4%}|"
                  f"｜凭空多 max {s['毒'].abs().max():.4%}")
    print("  最大的 12 只（待第二源判真伪）：")
    for _, r in q.nlargest(12, "k").iterrows():
        print(f"    {r['票']:9s} 包因子比={r['包比']:.5f} 我们={r['我们']:.5f} "
              f"⇒ 接缝 {r['接缝']:+.4%}（盘面 {r['盘面']:+.4%}，"
              f"凭空 {r['毒']:+.4%}）")
    over = q["接缝"].abs() > 0.30
    print(f"\n  护栏（guard_ret ±30%）救得回的只有 {int(over.sum())} 只 ⇒ "
          f"其余 {len(q) - int(over.sum())} 只的接缝涨幅会原样进面板")
    return 0


if __name__ == "__main__":
    sys.exit(main())
