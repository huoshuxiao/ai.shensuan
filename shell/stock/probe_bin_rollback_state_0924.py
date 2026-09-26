# -*- coding: utf-8 -*-
"""回滚前的现状核对：日历停在 09-22，但 --dry-run 的第一版把 09-23 那格真写进了 features。

要数清楚三件事才敢砍字节：
  1. 有多少 (票,字段) 文件的数组越过了日历末格下标 6476，各超出几格；
  2. 越界的格数是不是整齐地等于 1（多出来的就不是这次事故，得单独看）；
  3. 有没有事故新建出来的整只票目录（start 直接 = 6477，砍完只剩表头）。
"""
import os
import numpy as np

PROVIDER = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
            "qlib/qlib_data/cn_data")
FIELDS = ["open", "high", "low", "close", "volume", "amount",
          "factor", "vwap", "adjclose", "change"]

cal = [ln.strip() for ln in open(os.path.join(PROVIDER, "calendars", "day.txt")) if ln.strip()]
keep = len(cal)
last_idx = keep - 1
print(f"日历 {keep} 格，末日 {cal[-1]} ⇒ 合法的最大格下标 {last_idx}")

feat = os.path.join(PROVIDER, "features")
insts = sorted(os.listdir(feat))
print(f"features 下 {len(insts)} 个目录")

over_by = {}          # 超出格数 -> 文件数
over_files = 0
orphan_insts = {}     # inst -> {field: (start, ncells)}
header_only = []
bad_start = []
for inst in insts:
    d = os.path.join(feat, inst)
    if not os.path.isdir(d):
        print(f"  ⚠️ {inst} 不是目录")
        continue
    for field in FIELDS:
        p = os.path.join(d, f"{field}.day.bin")
        if not os.path.exists(p):
            continue
        a = np.fromfile(p, dtype="<f4")
        if not len(a):
            header_only.append((inst, field, "空文件"))
            continue
        start, n = int(a[0]), len(a) - 1
        if start >= keep:
            orphan_insts.setdefault(inst, {})[field] = (start, n)
        if start + n - 1 > last_idx:
            over = start + n - last_idx
            over_by[over] = over_by.get(over, 0) + 1
            over_files += 1
        if start + n - 1 > len(cal) + 40:      # 离谱到像结构坏了
            bad_start.append((inst, field, start, n))

print(f"\n越界文件 {over_files} 个；超出格数分布：{dict(sorted(over_by.items()))}")
print(f"start 已在日历之外的（事故新建）：{len(orphan_insts)} 只票")
for inst, fs in list(orphan_insts.items())[:10]:
    print(f"  {inst}: {fs}")
print(f"空文件/只有表头：{header_only[:10]}")
print(f"start+len 离谱：{bad_start[:10]}")

# 抽两只票看末三格，确认越界那一格就是 09-23 的行情
for inst in ("sh600519", "sz000001"):
    for field in ("close", "factor", "volume"):
        p = os.path.join(feat, inst, f"{field}.day.bin")
        if not os.path.exists(p):
            print(f"{inst}/{field}: 缺文件")
            continue
        a = np.fromfile(p, dtype="<f4")
        start, cells = int(a[0]), a[1:]
        tail = cells[-3:]
        idxs = [start + len(cells) - 3 + i for i in range(3)]
        tag = ["越界" if i > last_idx else "日历内" for i in idxs]
        print(f"{inst}/{field}: start={start} 格数={len(cells)} 末3格 " +
              " ".join(f"[{i}]{cal[i] if i < keep else '?'}={v:.6g}({t})"
                       for i, v, t in zip(idxs, tail, tag)))
