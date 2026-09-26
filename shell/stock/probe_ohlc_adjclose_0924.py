#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日更 append 判据 · 最后一环：open/high/low/adjclose 是复权价还是盘面价？

只用本地 bin，不联网。判据两条：

[1] adjclose 锚点：若 adjclose_t = close_t / f_first（后复权、锚在上市首日），
    则逐日比值 adjclose/close 应为**常数**。用 nanstd/nanmedian 看离散度。
[2] open/high/low 基准：在**除权日**（|Δf/f| > 1%）比较 OHLC 与 close 的比值。
    若 OHLC 与 close 同基准（都是复权），除权日的 open/close 分布应与非除权日一致；
    若 OHLC 是盘面价而 close 是复权价，除权日该比值会跳出 f 的跳变倍数。

注意：每个字段有自己的 start_idx，必须各自对齐到日历下标再比较。
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QLIB = os.path.join(ROOT, "stock", "v1", "data", "qlib", "qlib_data", "cn_data")
FIELDS = ["open", "high", "low", "close", "volume", "vwap", "amount", "adjclose", "factor", "change"]


def cal_index():
    days = [l.strip() for l in open(os.path.join(QLIB, "calendars", "day.txt")) if l.strip()]
    return {d: i for i, d in enumerate(days)}, days


CAL, DAYS = cal_index()
NCAL = len(DAYS)


def load(code, field):
    """返回 (start_idx, values, ends_at) —— values 从 start_idx 逐日对齐到日历末尾"""
    p = os.path.join(QLIB, "features", code.lower(), f"{field}.day.bin")
    if not os.path.exists(p):
        return None
    a = np.fromfile(p, dtype="<f4")
    if a.size < 2:
        return None
    s = int(a[0])
    v = a[1:]
    return s, v, s + v.size


def align(code, field, width):
    r = load(code, field)
    if r is None:
        return None, None
    s, v, end = r
    out = np.full(width, np.nan, dtype=np.float64)
    if end > width:  # 尾部超出日历（不该发生，先记录）
        return None, None
    out[s:end] = v
    return out, end


def first_valid(arr):
    idx = np.flatnonzero(~np.isnan(arr))
    return (idx[0], arr[idx[0]]) if idx.size else (None, None)


def main():
    codes = sorted(
        l.split("\t")[0].strip().lower()
        for l in open(os.path.join(QLIB, "instruments", "all.txt"))
        if l.strip()
    )
    # 抽样：每个板块前若干只 + 长度靠前的，保证有老票
    sample = codes[::37]
    print(f"[样本] {len(sample)} 只 / 全库 {len(codes)} 只，日历长度 {NCAL}")

    ratio_rows = []       # adjclose/close 的常数性
    exr_stats = []        # (is_exrights, open/close 偏离)
    tail_rows = []
    start_mismatch = []

    for c in sample:
        cl, e_cl = align(c, "close", NCAL)
        ad, e_ad = align(c, "adjclose", NCAL)
        f, e_f = align(c, "factor", NCAL)
        op, e_op = align(c, "open", NCAL)
        if cl is None or ad is None or f is None:
            continue
        ends = {e_cl, e_ad, e_f, e_op}
        if len(ends) > 1:
            start_mismatch.append((c, {"close": e_cl, "adjclose": e_ad, "factor": e_f, "open": e_op}))

        i0, _ = first_valid(f)
        if i0 is None:
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            r = ad / cl
        m, sd = np.nanmedian(r), np.nanstd(r)
        if np.isfinite(m) and m > 0:
            ratio_rows.append((c, m, sd / m, 1.0 / f[i0], ad[i0] / cl[i0]))

        # 除权日：f 的相对跳变 > 1%
        df = f[1:] / f[:-1]
        exr = np.abs(df - 1) > 0.01
        ok = ~np.isnan(cl[:-1]) & ~np.isnan(cl[1:]) & ~np.isnan(op[:-1]) & ~np.isnan(op[1:])
        for mask, tag in ((exr & ok, True), ((~exr) & ok, False)):
            if not mask.any():
                continue
            q = op[1:][mask] / cl[1:][mask]
            exr_stats.append((tag, q, np.where(mask)[0]))

        if cl is not None:
            iL = np.flatnonzero(~np.isnan(cl))[-1]
            tail_rows.append(dict(
                code=c, date=DAYS[iL], close=cl[iL], raw=cl[iL] / f[iL], adj=ad[iL], f=f[iL],
                open_ratio=(op[iL] / cl[iL]) if op is not None else np.nan,
            ))

    print("\n[1] adjclose / close 是否为常数（离散度 = nanstd/nanmedian）")
    if ratio_rows:
        df = pd.DataFrame(ratio_rows, columns=["code", "median", "rel_std", "1_over_f_first", "at_first"])
        print(f"    相对离散度中位数 = {df['rel_std'].median():.3e}，p95 = {df['rel_std'].quantile(.95):.3e}")
        print(f"    |median - 1/f_first| 中位数 = {np.nanmedian(np.abs(df['median'] - df['1_over_f_first'])):.4e}")
        print(f"    上市首日 adjclose/close 中位数 = {df['at_first'].median():.6f}")
        print(df.head(8).to_string(index=False))

    print("\n[2] open/close 在除权日 vs 非除权日的分布（同基准则两者接近）")
    for tag in (True, False):
        qs = np.concatenate([q for t, q, _ in exr_stats if t is tag]) if any(t is tag for t, _, _ in exr_stats) else np.array([])
        if qs.size:
            print(f"    {'除权日' if tag else '正常日'}: n={qs.size} 中位数={np.nanmedian(qs):.4f} "
                  f"|偏离|>5% 占比={np.nanmean(np.abs(qs-1)>0.05):.4f} max={np.nanmax(qs):.3f} min={np.nanmin(qs):.3f}")

    print("\n[3] 各字段尾部是否对齐（end 不一致 = 有字段少写/多写）")
    print(f"    不一致票数: {len(start_mismatch)} / {len(sample)}")
    for r in start_mismatch[:10]:
        print("    ", r)

    print("\n[4] 尾部实例（close=复权 / raw=盘面 / adjclose）")
    for r in tail_rows[:6]:
        print("    " + " ".join(f"{k}={v:.6g}" if isinstance(v, float) else f"{k}={v}" for k, v in r.items()))


if __name__ == "__main__":
    sys.exit(main())
