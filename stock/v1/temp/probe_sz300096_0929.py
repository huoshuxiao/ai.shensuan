#!/usr/bin/env python3.10
"""只读探针：解释 sz300096 的 change 末格为何 生产 NaN / 重跑 +2.33%

不写任何文件。对同一个字段分别读两份 provider（生产 bin 备份 vs 重跑后的 bin），
打印起始下标、格数、末尾若干格的值，并把日历下标对上日期。
"""
import os
import numpy as np

A = "stock/v1/temp/rerun_backups/rerun_20260928_0929_0119/cn_data"   # 生产 09-28 20:59
B = "common/data/stock/qlib/qlib_data/cn_data"                          # 重跑后
FIELDS = ["close", "factor", "change", "adjclose", "volume"]
TAIL = 8


def cal(p):
    return [ln.strip() for ln in open(os.path.join(p, "calendars/day.txt")) if ln.strip()]


def read(p, inst, field):
    f = os.path.join(p, "features", inst, f"{field}.day.bin")
    a = np.fromfile(f, dtype="<f4")
    return int(a[0]), a[1:]


for label, p in (("生产", A), ("重跑", B)):
    c = cal(p)
    print(f"\n=== {label}: {p}")
    print(f"  日历 {len(c)} 天，末三场 {c[-3:]}")
    for field in FIELDS:
        start, arr = read(p, "sz300096", field)
        cells = "; ".join(f"{c[start + i] if start + i < len(c) else f'#{start + i}'}="
                          f"{'NaN' if not np.isfinite(v) else format(float(v), '.6g')}"
                          for i, v in enumerate(arr[-TAIL:]))
        last_finite = np.flatnonzero(np.isfinite(arr))
        lf = f"{c[start + last_finite[-1]]}(下标 {start + last_finite[-1]})" if last_finite.size else "无"
        print(f"  {field:9s} start={start} n={arr.size} 末有效格={lf}\n            {cells}")
