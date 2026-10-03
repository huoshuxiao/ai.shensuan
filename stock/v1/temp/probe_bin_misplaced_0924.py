# -*- coding: utf-8 -*-
"""事故范围的第二刀核对：越界那一格之外，还有没有「写对了字节但落错日期」的格子。

第一版 append 不加 NaN 占位，所以停牌复牌票的 09-23 那格会落在它自己的下一格
（下标 < 6477），从「越过日历末格」这个判据完全看不出来。这里改用记账表点名：
拿 append_20260923.csv 里每只待写票的 09-23 盘面收盘，去比它 close 数组末格的
raw = close/factor —— 相等就说明末格是这次写的（无论下标对不对），要砍。
"""
import os
import numpy as np
import pandas as pd

PROVIDER = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
            "qlib/qlib_data/cn_data")
SNAP = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
        "daily_snapshot")
FIELDS = ["open", "high", "low", "close", "volume", "amount",
          "factor", "vwap", "adjclose", "change"]

cal = [ln.strip() for ln in open(os.path.join(PROVIDER, "calendars", "day.txt")) if ln.strip()]
keep, last_idx = len(cal), len(cal) - 1
rep = pd.read_csv(os.path.join(SNAP, "append_20260923.csv"), encoding="utf-8-sig")
todo = rep[rep["盘面收盘"] > 1e-6]
print(f"记账表 {len(rep)} 行，其中待写（有有效行情）{len(todo)} 只")


def cells_of(inst, field):
    p = os.path.join(PROVIDER, "features", inst.lower(), f"{field}.day.bin")
    if not os.path.exists(p):
        return None, None
    a = np.fromfile(p, dtype="<f4")
    return int(a[0]), a[1:]


ends = {}
misplaced = []
want_close = dict(zip(todo["inst"], todo["盘面收盘"]))
for inst in todo["inst"]:
    st, cl = cells_of(inst, "close")
    if cl is None:
        ends.setdefault("缺close文件", []).append(inst)
        continue
    valid = np.where(np.isfinite(cl))[0]
    end = st + len(cl) - 1
    ends.setdefault(end, []).append(inst)
    # end <= last_idx：末格落在日历之内却可能是这次写的（落错日期的格子，
    # 下标判据看不见，只能靠内容对）；end == last_idx+1 才是「越过日历末格」
    if end <= last_idx and len(valid):
        _, fa = cells_of(inst, "factor")
        j = valid[-1]
        raw = cl[j] / fa[j] if fa is not None and j < len(fa) else np.nan
        want = want_close[inst]
        if np.isfinite(raw) and abs(raw / want - 1) < 1e-4:
            misplaced.append((inst, cal[st + j], raw, want))

for k in sorted(ends, key=lambda x: (isinstance(x, str), x)):
    v = ends[k]
    d = "?" if isinstance(k, str) else (cal[k] if k < keep else f"越界#{k}")
    print(f"  末格下标 {k}（{d}）：{len(v)} 只  例 {v[:3]}")
print(f"\n末格落在过去、且内容正是 09-23 行情（= 落错日期）：{len(misplaced)} 只")
for m in misplaced[:10]:
    print(f"  {m[0]} 末格日期 {m[1]} raw={m[2]:.4f} vs 快照 {m[3]:.4f}")
