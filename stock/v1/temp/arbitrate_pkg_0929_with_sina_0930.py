#!/usr/bin/python3.10
# -*- coding: utf-8 -*-
"""用 sina 后复权因子给社区包的 09-29 跳变候选当第二源：哪些是真除权、哪些是包自己造的

为什么要第二源（probe_pkg_factor_gap_0930.log 全市场读数，09-30）
    三场有交易所记账表的日子：包的因子跳变**一次不漏**认下真除权（甲 41/47/17，漏 0），
    但每天另外自造 30~34 只无中生有的跳（乙，占全市场 0.5%）。最狠一条 SZ002713：
    包因子 ×1.2866 而交易所说那天不除权 ⇒ 复权收盘凭空 +28.27%。
    09-29 没有记账表（批量快照那天取不回来了）⇒ 包给 63 只候选，分不清真假。

先给这把尺子装牙（探针 A/B），牙没装上就不许引用第二天的裁决
    A 正对照 = 09-28 交易所认定的真除权 3 只（|f倍数−1| 最大的）：sina 也得跳，
      且跳的幅度与包一致（|sina/包 − 1| < 5e-4）。
    B 负对照 = 09-28 交易所说没除权、包却跳了 3 只（|r_f−1| 最大的）：sina 恒 1（±1e-3）。
    两组任一只不合 ⇒ 打印 ❌ 并 exit 1，**不做 09-29 裁决**。
    （实测口径：正常日 sina 的 hfq/raw 与 bin $factor 有 1e-5 级抖动，见
      probe_0929_recover_0930.py 探针5 ⇒ 所以判「跳不跳」用 1e-3，判「跳得对不对」用 5e-4。）

只读：拉 sina 日线，不写 bin、不写面板。裁决名单落到 temp/ 下的 csv（我的草稿产物）。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
PROD = os.path.join(ROOT, "common/data/stock/qlib/qlib_data/cn_data")
PKG = "/home/sunwenkun/Developer/agent-workspace/qlib_pkg_20260929/qlib_bin"
SNAP = os.path.join(ROOT, "common/data/stock/daily_snapshot")
OUT = os.path.join(ROOT, "stock/v1/temp/pkg_0929_factor_arbitration_0930.csv")
TOL = 1e-3          # 因子比偏离 1 多远算「一次跳变」（与记账表判据同值）
JITTER = 5e-4       # 两源跳「同一个事件」的容差（正常日抖动 1e-5，留 50 倍余量）
DAY, PREV = "2026-09-29", "2026-09-28"


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


def fmt(v, spec=".5f"):
    return "取不到" if not np.isfinite(v) else format(v, spec)


def fmt_pct(v):
    return "取不到" if not np.isfinite(v) else f"{v:+.4%}"


def load_book(day_compact):
    p = os.path.join(SNAP, f"append_{day_compact}.csv")
    df = pd.read_csv(p, encoding="utf-8-sig", dtype={"inst": str})
    df = df[df["f倍数"].notna() & df["inst"].notna()]
    return {i.lower(): float(v) for i, v in zip(df["inst"], df["f倍数"])}


def sina_pair(inst, start, end, tries=3):
    """取同一段日线的 raw 与 hfq 两版；行数/日期不齐就判不可用（宁可少一只不误判一只）"""
    import akshare as ak
    for t in range(tries):
        try:
            raw = ak.stock_zh_a_daily(symbol=inst.lower(), start_date=start, end_date=end)
            hfq = ak.stock_zh_a_daily(symbol=inst.lower(), start_date=start, end_date=end,
                                      adjust="hfq")
            if len(raw) != len(hfq):
                return None, None
            k = pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d").tolist()
            if k != pd.to_datetime(hfq["date"]).dt.strftime("%Y-%m-%d").tolist():
                return None, None
            return raw, hfq
        except Exception:
            if t == tries - 1:
                return None, None
            time.sleep(1.5)


def sina_ratio(inst, prev_day, day, start, end):
    """sina 口径的因子比 = (hfq/raw)_day ÷ (hfq/raw)_prev。返回 (倍数, 该票当日盘面涨幅)"""
    raw, hfq = sina_pair(inst, start, end)
    if raw is None:
        return float("nan"), float("nan")
    k = {d: i for i, d in
         enumerate(pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d").tolist())}
    if prev_day not in k or day not in k:
        return float("nan"), float("nan")
    i0, i1 = k[prev_day], k[day]
    f0 = float(hfq["close"].iloc[i0]) / float(raw["close"].iloc[i0])
    f1 = float(hfq["close"].iloc[i1]) / float(raw["close"].iloc[i1])
    r_raw = float(raw["close"].iloc[i1]) / float(raw["close"].iloc[i0]) - 1.0
    return f1 / f0, r_raw


def main():
    cal = [ln.strip() for ln in open(os.path.join(PKG, "calendars/day.txt")) if ln.strip()]
    g29, g28 = cal.index(DAY), cal.index(PREV)
    g24 = cal.index("2026-09-24")
    book28 = load_book("20260928")
    insts = sorted(os.listdir(os.path.join(PKG, "features")))
    print(f"包日历 {len(cal)} 格｜09-24={g24} 09-28={g28} 09-29={g29}｜记账表 09-28 {len(book28)} 行",
          flush=True)

    cand = {}
    for inst in insts:
        qf, qc = read_bin(PKG, inst, "factor"), read_bin(PKG, inst, "close")
        for tag, g, gp in (("29", g29, g28), ("28", g28, g24)):
            f1, f0 = at(qf, g), at(qf, gp)
            c1, c0 = at(qc, g), at(qc, gp)
            if not np.isfinite([f1, f0, c1, c0]).all() or f0 == 0 or c0 == 0:
                continue
            r = f1 / f0
            if abs(r - 1.0) <= TOL:
                continue
            cand.setdefault(tag, []).append(
                {"inst": inst, "包因子比": r, "盘面涨幅": (c1 / f1) / (c0 / f0) - 1.0})
    print(f"包跳变候选：09-28 {len(cand.get('28', []))} 只｜09-29 {len(cand.get('29', []))} 只\n",
          flush=True)

    # ── 装牙：09-28 的正/负对照 ──────────────────────────────────────────
    c28 = pd.DataFrame(cand["28"])
    c28["交易所"] = [book28.get(i, 1.0) for i in c28["inst"]]
    c28["k"] = (c28["包因子比"] - 1).abs()
    jia = c28[c28["交易所"].apply(lambda v: abs(v - 1.0) > TOL)].nlargest(3, "k")
    yi = c28[c28["交易所"].apply(lambda v: abs(v - 1.0) <= TOL)].nlargest(3, "k")
    print(f"  记账表匹配上 {int((c28['交易所'] != 1.0).sum())}/{len(c28)} 只"
          f"（09-28 交易所说除权 17 只 ⇒ 这里应当接近 17；匹配 0 只 = 键名没对上，尺子空转）")
    if len(jia) < 3 or len(yi) < 3 or int((c28["交易所"] != 1.0).sum()) == 0:
        print(f"\n❌ 对照组本身没凑齐（甲 {len(jia)} 只 / 乙 {len(yi)} 只）⇒ 判据没跑起来，"
              f"不许把后面的绿灯当结论")
        return 1

    teeth = True
    print("=" * 88)
    print("A 正对照：交易所说 09-28 真除权的 3 只 —— sina 也得跳，且跳量和包对得上")
    print("=" * 88)
    for _, r in jia.iterrows():
        sr, rr = sina_ratio(r["inst"], "2026-09-24", "2026-09-28", "20260922", "20260929")
        dev = abs(sr / r["包因子比"] - 1) if np.isfinite(sr) else float("nan")
        ok = np.isfinite(sr) and abs(sr - 1) > TOL and dev < JITTER
        teeth &= ok
        print(f"  {'✅' if ok else '❌'} {r['inst']:9s} 交易所={r['交易所']:.5f} "
              f"包={r['包因子比']:.5f} sina={fmt(sr)} ⇒ sina 与包差 {fmt(dev, '.1e')}")
    print("=" * 88)
    print("B 负对照：交易所说 09-28 没除权、包却跳了的 3 只 —— sina 恒 1")
    print("=" * 88)
    for _, r in yi.iterrows():
        sr, rr = sina_ratio(r["inst"], "2026-09-24", "2026-09-28", "20260922", "20260929")
        ok = np.isfinite(sr) and abs(sr - 1) <= TOL
        teeth &= ok
        print(f"  {'✅' if ok else '❌'} {r['inst']:9s} 交易所={r['交易所']:.5f} "
              f"包={r['包因子比']:.5f} sina={fmt(sr)}（盘面 {fmt_pct(rr)}）")

    if not teeth:
        print("\n❌ 尺子没装上牙：对照组与交易所答案不符 ⇒ 09-29 的裁决不做，别引用包")
        return 1
    print("\n✅ 两组对照都与交易所记账一致 ⇒ sina 这一源可以用，往下裁 09-29\n")

    # ── 裁决 09-29 ──────────────────────────────────────────────────────
    c29 = pd.DataFrame(cand["29"]).sort_values("inst")
    t0 = time.time()
    rows = []
    for n, (_, r) in enumerate(c29.iterrows(), 1):
        if n % 15 == 0:
            print(f"  …{n}/{len(c29)}  {time.time() - t0:.0f}s", flush=True)
        sr, rr = sina_ratio(r["inst"], PREV, DAY, "20260924", "20260929")
        if not np.isfinite(sr):
            v = "sina 取不到"
        elif abs(sr - 1) > TOL:
            v = "甲 真除权（该跳）" if abs(sr / r["包因子比"] - 1) < JITTER else "乙ʹ sina 也跳但对不上量"
        else:
            v = "乙 包自造（不该跳）"
        rows.append({"inst": r["inst"], "包因子比": r["包因子比"], "sina因子比": sr,
                     "sina盘面涨幅": rr, "包盘面涨幅": r["盘面涨幅"], "裁决": v,
                     "接包原样会凭空多出": r["包因子比"] - (sr if np.isfinite(sr) else 1.0)})
    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n{len(out)} 只裁完 / {time.time() - t0:.0f}s → {OUT}")
    for v, m in out["裁决"].value_counts().items():
        s = out[out["裁决"] == v]
        print(f"  {v:<24} {m:>3} 只｜|包因子比−1| 中位 {(s['包因子比'] - 1).abs().median():.4%} "
              f"max {(s['包因子比'] - 1).abs().max():.4%}")
    bad = out[out["裁决"] == "乙 包自造（不该跳）"].assign(
        k=lambda x: x["接包原样会凭空多出"].abs()).nlargest(8, "k")
    if len(bad):
        print("\n  乙类（接包原样＝面板里种一根假台阶）最大的 8 只：")
        for _, r in bad.iterrows():
            print(f"    {r['inst']:9s} 包={r['包因子比']:.5f} sina={r['sina因子比']:.5f} "
                  f"⇒ 凭空 {r['接包原样会凭空多出']:+.4%}（当日盘面真涨 {r['sina盘面涨幅']:+.4%}）")
    ga = out[out["裁决"] == "甲 真除权（该跳）"]
    if len(ga):
        print(f"\n  甲类（真除权、包给对了）{len(ga)} 只：{', '.join(ga['inst'].head(20))}"
              f"{' …' if len(ga) > 20 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
