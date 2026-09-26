#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日更 append 判据 · 结构篇：OHLC 基准 + 每个 .day.bin 的尾部形状

三条判据：
[a] low <= vwap <= high 的违反率。vwap 已定为复权均价。若 high/low 是盘面价而复权
    价差 f 倍（f 常为 0.05~0.4），违反率会接近 100%；同基准则应 <1%。
[b] 除权日 high/close、low/close 的分布 vs 正常日（同 [a] 的交叉验证）。
[c] 尾部形状：每个 .day.bin 的 start_idx+len 是否等于日历长度 NCAL。
    等于 -> append 只需在文件尾追加 4 字节；小于 -> 需要先补 NaN 再写值。
    这决定了日更脚本要不要处理"数组比日历短"这一类票。
"""
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QLIB = os.path.join(ROOT, "stock", "v1", "data", "qlib", "qlib_data", "cn_data")
DAYS = [l.strip() for l in open(os.path.join(QLIB, "calendars", "day.txt")) if l.strip()]
NCAL = len(DAYS)
FIELDS = ["open", "high", "low", "close", "volume", "vwap", "amount", "adjclose", "factor", "change"]


def load(code, field):
    p = os.path.join(QLIB, "features", code.lower(), f"{field}.day.bin")
    if not os.path.exists(p):
        return None
    a = np.fromfile(p, dtype="<f4")
    if a.size < 2:
        return None
    return int(a[0]), a[1:]


def align(s, v, width=NCAL):
    out = np.full(width, np.nan, dtype=np.float64)
    end = min(s + v.size, width)
    out[s:end] = v[:end - s]
    return out


codes = sorted(l.split("\t")[0].strip().lower()
               for l in open(os.path.join(QLIB, "instruments", "all.txt")) if l.strip())
sample = codes[::61]

viol_复权 = [0, 0]
viol_盘面 = [0, 0]
exr = {"hi_exr": [], "lo_exr": [], "hi_nrm": [], "lo_nrm": []}
shape = {"reach_end": 0, "short": 0, "short_by_days": [], "no_file": [], "tail_nonnan_end": 0, "mismatch": []}

for c in sample:
    cols = {}
    ok = True
    for fld in FIELDS:
        r = load(c, fld)
        if r is None:
            if fld in ("open", "high", "low", "close", "vwap", "factor"):
                ok = False
                shape["no_file"].append((c, fld))
            continue
        cols[fld] = align(*r)

    # [c] 尾部形状：逐字段统计 end 相对 NCAL 的位置
    rc = load(c, "close")
    if rc is not None:
        end = rc[0] + rc[1].size
        if end == NCAL:
            shape["reach_end"] += 1
        else:
            shape["short"] += 1
            shape["short_by_days"].append(NCAL - end)
        last = np.flatnonzero(~np.isnan(cols["close"]))
        if last.size and last[-1] == end - 1:
            shape["tail_nonnan_end"] += 1
    for fld in FIELDS:
        r = load(c, fld)
        if r is None:
            continue
        e = r[0] + r[1].size
        if e != NCAL:
            shape["mismatch"].append((c, fld, r[0], r[1].size, e - NCAL))

    if not ok:
        continue

    lo, hi, vw, cl, f = cols["low"], cols["high"], cols["vwap"], cols["close"], cols["factor"]
    m = np.isfinite(lo) & np.isfinite(hi) & np.isfinite(vw) & (vw > 0)
    viol_复权[0] += int(np.sum((lo[m] * 0.999 > vw[m]) | (vw[m] > hi[m] * 1.001)))
    viol_复权[1] += int(m.sum())
    # 若 low/high 是盘面价：raw = cl/f，则 low_raw=lo, high_raw=hi 而 vw 需除以 f 才可比
    fw = np.where(np.abs(f) > 1e-12, f, np.nan)
    m2 = m & np.isfinite(fw)
    vw_raw = vw[m2] / fw[m2]
    viol_盘面[0] += int(np.sum((lo[m2] * 0.999 > vw_raw) | (vw_raw > hi[m2] * 1.001)))
    viol_盘面[1] += int(m2.sum())

    df = f[1:] / f[:-1]
    isx = np.abs(df - 1) > 0.01
    good = np.isfinite(cl[:-1]) & np.isfinite(cl[1:]) & np.isfinite(hi[1:]) & np.isfinite(lo[1:])
    for tag, mask in (("exr", isx & good), ("nrm", (~isx) & good)):
        exr["hi_" + tag].append(np.nanmedian(hi[1:][mask] / cl[1:][mask]) if mask.any() else np.nan)
        exr["lo_" + tag].append(np.nanmedian(lo[1:][mask] / cl[1:][mask]) if mask.any() else np.nan)

print(f"[样本] {len(sample)} 只")
print("\n[a] vwap 是否落在 [low, high] 内（复权基准 vs 把 high/low 当盘面价）")
print(f"    同基准假设: 违反 {viol_复权[0]}/{viol_复权[1]} = {viol_复权[0]/max(viol_复权[1],1):.4%}")
print(f"    盘面假设  : 违反 {viol_盘面[0]}/{viol_盘面[1]} = {viol_盘面[0]/max(viol_盘面[1],1):.4%}")

print("\n[b] 除权日 vs 正常日的 high/close、low/close 中位数")
for k in ("hi", "lo"):
    a = np.array([v for v in exr[k + "_exr"] if np.isfinite(v)])
    b = np.array([v for v in exr[k + "_nrm"] if np.isfinite(v)])
    print(f"    {k}/close 除权日 n={a.size} 中位={np.median(a) if a.size else float('nan'):.4f} "
          f"范围=[{a.min() if a.size else float('nan'):.3f},{a.max() if a.size else float('nan'):.3f}] | "
          f"正常日 中位={np.median(b):.4f} 范围=[{b.min():.3f},{b.max():.3f}]")

print("\n[c] 尾部形状（以 close 计，end = start_idx + len）")
print(f"    恰好到日历末尾 {NCAL}: {shape['reach_end']} 只 | 不等于 NCAL: {shape['short']} 只")
if shape["short_by_days"]:
    s = np.array(shape["short_by_days"])
    print(f"    NCAL-end: 中位={np.median(s):.0f} min={s.min()} max={s.max()} | 负数=已越过日历末尾")
print(f"    最后一个非 NaN 恰好落在数组末尾（无 NaN 尾巴）: {shape['tail_nonnan_end']} 只")
mm = shape["mismatch"]
print(f"    end != NCAL 的(票,字段)单元: {len(mm)} / 抽样 {len(sample)} 只")
import collections
cnt = collections.Counter(t for _, t, _, _, _ in mm)
print(f"    按字段分布: {dict(cnt)}")
over = [d for *_, d in mm if d > 0]
under = [d for *_, d in mm if d < 0]
print(f"    越过日历末尾: {len(over)} 个, 天数分布 {collections.Counter(over).most_common(5)}")
print(f"    短于日历末尾: {len(under)} 个, 天数分布 {collections.Counter(under).most_common(5)}")
for t in mm[:8]:
    print("      ", t)
print(f"    缺文件字段: {len(shape['no_file'])}")
for t in shape["no_file"][:5]:
    print("      ", t)
