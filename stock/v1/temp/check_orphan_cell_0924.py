#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读复核：01:24 那次误写的 09-23 尾格，与锁定口径重算的结果是否一致

背景：data/update_qlib_bin_daily.py 的第一版在 --dry-run 下也把每只票的 09-23
那一格追加进了 .day.bin，但 calendars/day.txt 没动（仍止于 2026-09-22）。
于是 features 比日历多出一列（下标 6477），qlib 按日历截断 => 这一格现在不可见。

本脚本不写任何字节，只回答一个问题：这一列是**正确**的吗？
  正确 => 修复只需把日历 +1（或回滚后重跑，让同一版代码一次性产出）
  不正确 => 必须回滚砍掉，否则日历一接上就把错数据放进下游

判据：从 spot_20260923.csv 独立重算 10 个字段，与文件末格逐票比相对差。
"""
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
QLIB = os.path.join(ROOT, "stock", "v1", "data", "qlib", "qlib_data", "cn_data")
SNAP = os.path.join(ROOT, "stock", "v1", "data", "daily_snapshot", "spot_20260923.csv")
FIELDS = ["open", "high", "low", "close", "volume", "amount", "factor", "vwap", "adjclose", "change"]

DAYS = [l.strip() for l in open(os.path.join(QLIB, "calendars", "day.txt")) if l.strip()]
NCAL = len(DAYS)
print(f"日历末 {DAYS[-1]} 下标 {NCAL-1}；待查的孤儿格下标 = {NCAL}")


def rd(inst, field):
    p = os.path.join(QLIB, "features", inst.lower(), f"{field}.day.bin")
    if not os.path.exists(p):
        return None
    a = np.fromfile(p, dtype="<f4")
    return int(a[0]), a[1:]


spot = pd.read_csv(SNAP, encoding="utf-8-sig")
spot["inst"] = spot["代码"].str.upper()
spot = spot[spot["inst"].str.match(r"^(SH60|SH68|SZ00|SZ30|BJ43|BJ83|BJ87|BJ88|BJ92)")].set_index("inst")
print(f"快照 {SNAP}：A 股 {len(spot)} 行")

# 孤儿格分布：end 相对 NCAL+1 的位置
tally = {"at_orphan": 0, "at_cal_end": 0, "else": 0, "missing": 0}
diffs = {f: [] for f in FIELDS}
examples = []
no_row = 0

for inst in spot.index:
    got = rd(inst, "close")
    if got is None:
        tally["missing"] += 1
        continue
    s, cl = got
    end = s + cl.size
    if end == NCAL + 1:
        tally["at_orphan"] += 1
    elif end == NCAL:
        tally["at_cal_end"] += 1
        continue          # 没被写过，无需复核
    else:
        tally["else"] += 1
        continue

    _, fa = rd(inst, "factor")
    hit = np.where(np.isfinite(cl) & np.isfinite(fa))[0]
    j = int(hit[-1])                       # 最后一个有效格 = 孤儿格
    if j != cl.size - 1:
        no_row += 1
        continue
    r = spot.loc[inst]
    if isinstance(r, pd.DataFrame):
        r = r.iloc[0]
    raw_T, ref = float(r["最新价"]), float(r["昨收"])
    amount, vol_share = float(r["成交额"]), float(r["成交量"])
    f_prev, c_prev = float(fa[j - 1]), float(cl[j - 1])
    raw_prev = c_prev / f_prev
    f_new = f_prev * (raw_prev / ref)
    _, vw0 = rd(inst, "vwap")
    _, am0 = rd(inst, "amount")
    _, ad0 = rd(inst, "adjclose")
    exp = {
        "open": float(r["今开"]) * f_new,
        "high": float(r["最高"]) * f_new,
        "low": float(r["最低"]) * f_new,
        "close": raw_T * f_new,
        "volume": (vol_share / 100.0) / f_new,
        "amount": amount / 1000.0,
        "factor": f_new,
        "vwap": (amount / vol_share) * f_new,
        "adjclose": raw_T * f_new * (float(ad0[j]) / float(cl[j])),
        "change": raw_T / ref - 1.0,
    }
    bad = []
    for fld in FIELDS:
        v = rd(inst, fld)
        act = float(v[1][-1]) if v is not None else np.nan
        e = exp[fld]
        d = abs(act / e - 1.0) if np.isfinite(act) and np.isfinite(e) and e != 0 else np.nan
        diffs[fld].append(d)
        if np.isfinite(d) and d > 1e-3:
            bad.append(f"{fld} 实={act:.6g} 算={e:.6g}")
    if bad and len(examples) < 12:
        examples.append((inst, str(r["名称"]), bad[:4]))

print(f"\n[孤儿格位置] 恰在下标 {NCAL}: {tally['at_orphan']} | 仍止于日历末: {tally['at_cal_end']} "
      f"| 其它: {tally['else']} | 无 close 文件: {tally['missing']} | 末格非有效值: {no_row}")

print("\n[复核] 实际字节 vs 口径重算 的相对差")
for fld in FIELDS:
    d = np.array(diffs[fld], dtype=float)
    d = d[np.isfinite(d)]
    if d.size:
        print(f"  {fld:9s} n={d.size:5d} 中位={np.median(d):.2e} p95={np.percentile(d,95):.2e} "
              f"max={d.max():.2e} >1e-3 占 {np.mean(d > 1e-3):.2%}")

if examples:
    print("\n出界样本（>0.1% 相对差）")
    for t in examples:
        print("  ", t)
