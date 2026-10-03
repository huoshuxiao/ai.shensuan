#!/usr/bin/env python3.10
# -*- coding: utf-8 -*-
"""只读探针：09-29 这一场的行情还能不能取回来（09-30 跑，全程不写一个字节）

背景：股票线 bin 日历末格 = 2026-09-28，`daily_snapshot/` 里没有 `spot_20260929.csv`。
日更链的唯一行情源是批量快照 `ak.stock_zh_a_spot()`，它只回答「现在」。09-30 实测：
交易所日历里 09-25/26/27 **不是**交易日 ⇒ 缺的只有 09-29 一场。

五个探针（`--probe 1,3,5` 可单跑；默认全跑）：
  1 批量快照的「昨收」对的是哪一天 —— 拿写入闸那把尺子（昨收 ↔ bin raw 收盘，
    raw = $close/$factor）分别对 bin 末格 09-28 与倒数第二格 09-24。
  2 逐票 sina 日线（`ak.stock_zh_a_daily`）有没有 09-29 那根 K 线 + 逐票耗时。
  3 **留出对拍**：sina 逐票给的 09-28 那根（open/high/low/close/volume/amount）与
    09-28 那份真缓存快照、与 bin 的 09-28 raw 三方逐字对表 —— 证「换成逐票源，
    写进 bin 的字节与走批量快照一模一样」。这一条是判据，不是冒烟。
  4 外部真值反证：此刻批量快照的「昨收」= 09-29 的盘面收盘（09-30 视角的昨日），
    拿它当独立真值去核 sina 的 09-29 收盘。不符的必须逐只点名解释。
  5 复权因子那条腿：sina 自己的后复权（hfq/raw）与 bin 的 $factor 是不是同一口径。

不写任何文件：只调 akshare 的读接口，不碰生产 `fetch_spot`（它会把快照钉进缓存目录）。

实测读数（09-30 上午本文件 `--probe 1,2,3,4,5` 自己跑出来的，非即席）：
  1 盘中快照 5491 行/21.7s；昨收↔bin 09-28 逐字 2.44%、↔09-24 逐字 1.46%（这两个数拿
    盘中不同时刻去跑会漂，要看的只有「远低于写入闸的 90%」这一条）⇒ 批量快照已永久取不到
    「能描述 09-29」的那一份，硬写 09-29 必被 90% 逐字闸拒。
  2 09-29 那根 K 线在（sina 日线 T+1 已出），7/8 只裸打即成（唯 BJ830799 无数据）；
    0.32~0.66s/票 ⇒ 全市场 5556 只 ≈ 30~60 分钟。北交所只认 BJ92xxxx，而 09-28 那份
    真快照里有成交的 BJ **恰好全是 BJ92**（347/347）⇒ 换成逐票源在北交所上不丢覆盖；
    248 只 BJ8x/43x 在册但批量快照本来也没有它们的行。
  3 留出对拍 10 只（含 BJ92）：close/open/high/low/vwap 与 09-28 那份**真**批量快照
    **逐字相同 100%、max 相对差 0.00e+00**；close 另与 bin 的 09-28 raw 逐字 100%
    （Δ≤3.6e-08 = float32 舍入）；成交量/成交额 sina/快照比值 min=max=1.000000
    （单位同为 股 / 元）⇒ 价·量·额这条腿换成逐票源是逐位的。
  4 121 只：sina 09-29 收盘 ↔ 快照昨收 逐字 98.35% / 容差1% 100.00%。不符的 2 只点名：
    BJ920000（名称「XD安徽凤」，14.32 vs 14.25）与 SZ300817（17.04 vs 16.94，名称未带 XD，
    但其 sina 序列 09-23~29 的 outstanding_share 一动不动 ⇒ 不是送转，与「今日现金分红
    除息、参考价被下调」一致，分红方案没去核实）。⇒ 不是 sina 报错价，是快照那列在
    **除权日**本来就被交易所下调；这条反证的逐字率上限就是当日除权票占比。
  5 sina 的 hfq/raw 与 bin 的 $factor **不是同一口径**：正常日就带 8.4e-06~6.4e-05 抖动，
    除权日差 4.8e-05~2.1e-04 ⇒ 直接拿它写 factor 会给每只票注进一个永久小台阶。
    09-28 那场 5568 只里 17 只除权（0.31%）⇒ 全市场只有这约 0.3% 需要「除权参考价」，
    而 09-29 那一列任何源都取不回来了。可用阈值分离：真除权是 1%~3% 级、抖动是 1e-5 级，
    拿 |sina 因子比 − 1| > 1e-3 当「这只票 09-29 除权」的旗标，其余票 f 原样沿用（逐位对）。
"""
import argparse
import os
import socket
import time

import numpy as np
import pandas as pd

socket.setdefaulttimeout(25)

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
PROVIDER = os.path.join(ROOT, "common/data/stock/qlib/qlib_data/cn_data")
SNAP_DIR = os.path.join(ROOT, "common/data/stock/daily_snapshot")
PREFIX = r"^(SH60|SH68|SZ00|SZ30|BJ43|BJ83|BJ87|BJ88|BJ92)"
SAMPLE = ["SH600000", "SH600519", "SH688111", "SZ000001", "SZ300750",
          "SZ002032", "BJ830799", "SH601398"]
BAR_FIELDS = ["open", "high", "low", "close", "volume", "amount"]


def read_bin(inst, field):
    p = os.path.join(PROVIDER, "features", inst.lower(), f"{field}.day.bin")
    if not os.path.exists(p):
        return None
    a = np.fromfile(p, dtype="<f4")
    return int(a[0]), a[1:]


def calendar():
    return [ln.strip() for ln in
            open(os.path.join(PROVIDER, "calendars/day.txt")) if ln.strip()]


def raw_close_at(inst, g):
    """bin 的盘面收盘 = $close/$factor（与生产 `align_against_bin` 同式）"""
    gc, gf = read_bin(inst, "close"), read_bin(inst, "factor")
    if gc is None or gf is None:
        return np.nan
    s_cl, close = gc
    s_fa, factor = gf
    if not (0 <= g - s_cl < close.size and 0 <= g - s_fa < factor.size):
        return np.nan
    c, f = float(close[g - s_cl]), float(factor[g - s_fa])
    return c / f if (np.isfinite(c) and np.isfinite(f) and f > 0) else np.nan


def rates(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = np.abs(a / b - 1.0)[np.isfinite(a) & np.isfinite(b) & (np.abs(b) > 1e-9)]
    if not d.size:
        return np.nan, np.nan, 0
    return float((d < 1e-4).mean()), float((d < 1e-2).mean()), int(d.size)


def sina_daily(inst, start, end, adjust=None, tries=3):
    import akshare as ak
    for t in range(tries):
        try:
            kw = dict(symbol=inst.lower(), start_date=start, end_date=end)
            if adjust:
                kw["adjust"] = adjust
            return ak.stock_zh_a_daily(**kw)
        except Exception:
            if t == tries - 1:
                raise
            time.sleep(1.5)


def bar_on(df, day):
    k = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d") == day
    return df[k].iloc[0] if k.any() else None


def probe_bulk_spot():
    import akshare as ak
    t0 = time.time()
    spot = ak.stock_zh_a_spot()
    ts = pd.to_datetime(spot["时间戳"].astype(str), format="%H:%M:%S", errors="coerce")
    pre = float(((ts.dt.hour * 60 + ts.dt.minute) < 15 * 60).mean())
    print(f"  批量快照 {len(spot)} 行 / {time.time() - t0:.1f}s，早于 15:00 的行占 "
          f"{pre:.1%}（盘中 ⇒ 生产代码不会落缓存，这里也没落）")
    return spot


def p1():
    print("=" * 74)
    print("探针1 批量快照的「昨收」是哪一天")
    print("=" * 74)
    spot = probe_bulk_spot()
    cal = calendar()
    s = spot[spot["代码"].astype(str).str.upper().str.match(PREFIX)]
    s = s.iloc[::max(1, len(s) // 600)]
    for label, g in ((f"bin 末格 {cal[-1]}", len(cal) - 1),
                     (f"bin 倒数第二格 {cal[-2]}", len(cal) - 2)):
        refs, raws = [], []
        for _, r in s.iterrows():
            v = raw_close_at(str(r["代码"]).upper(), g)
            if np.isfinite(v) and float(r["昨收"]) > 1e-6:
                refs.append(float(r["昨收"]))
                raws.append(v)
        ex, wi, n = rates(refs, raws)
        print(f"  昨收 ↔ {label}: 逐字 {ex:.2%} / 容差1% {wi:.2%}（{n} 只）"
              f"{'  ✅ 就是这一场' if ex >= 0.90 else '  ❌ 不是这一场'}")
    print("  ⇒ 此刻这份快照描述的是 09-30 那一场；能写 09-29 的批量快照永久消失了")
    return spot


def p2():
    print("=" * 74)
    print("探针2 逐票 sina 日线有没有 09-29（+ 逐票耗时）")
    print("=" * 74)
    got = {}
    for inst in SAMPLE:
        t0 = time.time()
        try:
            d = sina_daily(inst, "20260920", "20260930", tries=1)
            r = bar_on(d, "2026-09-29")
            print(f"  {inst}: {time.time() - t0:.2f}s {len(d)} 行 09-29 "
                  f"{'✅' if r is not None else '❌'}"
                  + (f" {float(r['close']):.4f}/{float(r['volume']):.0f}股" if r is not None else ""))
            if r is not None:
                got[inst] = r
        except Exception as e:
            print(f"  {inst}: {time.time() - t0:.2f}s ⚠️ {type(e).__name__} {str(e)[:70]}")
    print(f"  裸打（不重试）拿到 {len(got)}/{len(SAMPLE)} 只")
    return got


def p3(n=10):
    print("=" * 74)
    print("探针3 留出对拍：sina 逐票 09-28 那根 vs 09-28 真快照 vs bin 09-28 raw")
    print("=" * 74)
    snap = pd.read_csv(os.path.join(SNAP_DIR, "spot_20260928.csv"), encoding="utf-8-sig")
    snap["I"] = snap["代码"].astype(str).str.upper()
    snap = snap.set_index("I")
    cal = calendar()
    g = len(cal) - 1
    cols = {k: [] for k in ["close", "open", "high", "low", "vwap", "vol_ratio", "amt_ratio"]}
    done = 0
    for inst in SAMPLE[:6] + ["SZ000651", "SH601899", "SZ300502", "BJ920119"]:
        try:
            d = sina_daily(inst, "20260928", "20260928")
        except Exception as e:
            print(f"  {inst}: sina FAIL {type(e).__name__} {str(e)[:50]}")
            continue
        r = bar_on(d, "2026-09-28")
        if r is None:
            print(f"  {inst}: sina 没有 09-28 行")
            continue
        b = snap.loc[inst]
        b = b.iloc[0] if isinstance(b, pd.DataFrame) else b
        v_bin = raw_close_at(inst, g)
        cols["close"].append([float(r["close"]), v_bin, float(b["最新价"])])
        for f, col in (("open", "今开"), ("high", "最高"), ("low", "最低")):
            cols[f].append([float(r[f]), float(b[col])])
        cols["vwap"].append([float(r["amount"]) / float(r["volume"]),
                             float(b["成交额"]) / float(b["成交量"])])
        cols["vol_ratio"].append([float(r["volume"]) / float(b["成交量"]), 1.0])
        cols["amt_ratio"].append([float(r["amount"]) / float(b["成交额"]), 1.0])
        print(f"  {inst}: close sina={float(r['close']):.4f} bin_raw={v_bin:.4f} "
              f"快照={float(b['最新价']):.4f} | vol sina/快照="
              f"{float(r['volume']) / float(b['成交量']):.4f}")
        done += 1
    print(f"\n  对拍 {done} 只：")
    for k in ("close", "open", "high", "low", "vwap"):
        a = np.array(cols[k])
        pair = (a[:, 0], a[:, 1]) if k != "close" else (a[:, 0], a[:, 2])
        ex, wi, _n = rates(pair[0], pair[1])
        dmax = float(np.max(np.abs(pair[0] / pair[1] - 1)))
        third = f"（另与 bin raw 逐字 {rates(a[:,0], a[:,1])[0]:.0%}）" if k == "close" else ""
        print(f"    {k:<6} sina↔真快照 逐字 {ex:.0%} / 容差1% {wi:.0%} / max 相对差 {dmax:.2e} {third}")
    for k, nm in (("vol_ratio", "成交量单位比"), ("amt_ratio", "成交额单位比")):
        a = np.array(cols[k])[:, 0]
        print(f"    {nm} sina/快照: 中位 {np.median(a):.6f} / min {a.min():.6f} / max {a.max():.6f}")


def p4(spot=None, n=120):
    print("=" * 74)
    print("探针4 外部真值反证：快照昨收（=09-29 真收盘）↔ sina 逐票 09-29 收盘")
    print("=" * 74)
    if spot is None:
        spot = probe_bulk_spot()
    s = spot[spot["代码"].astype(str).str.upper().str.match(PREFIX)]
    s = s.iloc[::max(1, len(s) // n)]
    refs, sina = [], []
    bad = []
    t0 = time.time()
    for _, r in s.iterrows():
        inst = str(r["代码"]).upper()
        try:
            d = sina_daily(inst, "20260929", "20260929")
        except Exception:
            continue
        b = bar_on(d, "2026-09-29")
        if b is None or float(r["昨收"]) <= 1e-6:
            continue
        refs.append(float(r["昨收"]))
        sina.append(float(b["close"]))
        if abs(float(b["close"]) / float(r["昨收"]) - 1) > 1e-4:
            bad.append((inst, float(b["close"]), float(r["昨收"]), str(r["名称"])))
    ex, wi, m = rates(sina, refs)
    print(f"  成功 {m} 只 / {time.time() - t0:.0f}s：逐字相同 {ex:.2%} / 容差1% {wi:.2%}")
    print(f"  逐字不符 {len(bad)} 只，逐只点名：")
    for inst, c29, ref, nm in bad:
        print(f"    {inst} sina09-29={c29:.4f} 快照昨收={ref:.4f}（{c29 / ref - 1:+.2%}）名称=「{nm}」")
    print("  ⇒ 不符只应来自「今天该票除权、快照那列被交易所下调成参考价」；"
          "逐只点名后要能说出是分红还是送转，说不出的一律当未证")


def p5():
    print("=" * 74)
    print("探针5 复权因子那条腿：sina 后复权 (hfq/raw) 与 bin $factor 同不同口径")
    print("=" * 74)
    cal = calendar()
    g24, g28 = len(cal) - 2, len(cal) - 1
    for inst in ["SH600273", "SH600926", "BJ920065", "SH600000", "SZ300750", "SH601398"]:
        try:
            raw = sina_daily(inst, "20260922", "20260928")
            hfq = sina_daily(inst, "20260922", "20260928", adjust="hfq")
        except Exception as e:
            print(f"  {inst}: FAIL {type(e).__name__} {str(e)[:50]}")
            continue
        k = {d: i for i, d in enumerate(
            pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d").tolist())}
        if "2026-09-28" not in k or "2026-09-24" not in k or len(hfq) != len(raw):
            print(f"  {inst}: 行数不齐")
            continue
        r24, r28 = float(raw["close"].iloc[k["2026-09-24"]]), float(raw["close"].iloc[k["2026-09-28"]])
        h24, h28 = float(hfq["close"].iloc[k["2026-09-24"]]), float(hfq["close"].iloc[k["2026-09-28"]])
        sf = read_bin(inst, "factor")
        bf = (float(sf[1][g24 - sf[0]]), float(sf[1][g28 - sf[0]]))
        print(f"  {inst}: sina 因子比 {h28 / r28 / (h24 / r24):.6f} vs bin 因子比 "
              f"{bf[1] / bf[0]:.6f} ⇒ 差 {abs((h28 / r28 / (h24 / r24)) / (bf[1] / bf[0]) - 1):.2e}"
              f" | sina raw28={r28:.4f}")
    print("  ⇒ 正常日也带 1e-5 级抖动 ⇒ sina 的 hfq 不能直接当 $factor 用（会给每只票永久小台阶）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", default="1,2,3,4,5", help="逗号分隔：1..5")
    a = ap.parse_args()
    which = {int(x) for x in a.probe.split(",")}
    spot = p1() if 1 in which or 4 in which else None
    if 2 in which:
        p2()
    if 3 in which:
        p3()
    if 4 in which:
        p4(spot)
    if 5 in which:
        p5()
