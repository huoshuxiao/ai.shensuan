# -*- coding: utf-8 -*-
"""10-06 丁场只读对表：罩子那行为什么整场 0 次（不猜，逐层现读）。

只读。一行生产代码都不改，一个产物都不写。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
FJ = os.path.join(ROOT, "etf/v1/data/results/rdagent_output/factors.json")
SNAP_FJ = os.path.join(ROOT, "etf/v1/temp/snapshot_ding_1005/rdagent/factors.json")
TC = os.path.join(ROOT, "common/data/etf/cache/trial_counter.json")
SNAP_TC = os.path.join(ROOT, "etf/v1/temp/snapshot_ding_1005/cache/trial_counter.json")
LIB = os.path.join(ROOT, "etf/v1/data/library/factor_library_index.json")
LOG = os.path.join(ROOT, "etf/v1/temp/chain_ding_1005.log")

NEW = ["5-day SMA of Price over 20-day MIN of Price",
       "1-day SMA of Price over 5-day MAX of Price"]


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


print("=== 1) 归档 factors.json：本场 vs 快照（起场前）")
cur, old = load(FJ), load(SNAP_FJ)
names_cur = [x.get("name") for x in cur]
names_old = [x.get("name") for x in old]
print(f"  条目数 {len(names_old)} → {len(names_cur)}")
print(f"  新名字 {sorted(set(names_cur) - set(names_old))}")
for n in NEW:
    e = [x for x in cur if x.get("name") == n]
    if e:
        e = e[0]
        print(f"  · 归档戳记 {n[:46]:46s} mean_ic={e.get('mean_ic')}")

print("\n=== 2) 因子库里同一批名字落的是谁的数（重算值 or 戳记）")
lib = load(LIB)
fac = lib.get("factors", lib)
for n in NEW:
    hit = None
    for k, v in (fac.items() if isinstance(fac, dict) else []):
        if k == n or (isinstance(v, dict) and v.get("name") == n):
            hit = v
            break
    if hit is None:
        print(f"  · 库里查无此名：{n[:46]}")
        continue
    ics = []
    impl = hit.get("impl") or {}
    if isinstance(impl, dict):
        ics = [x.get("ic") for x in impl.values()
               if isinstance(x, dict) and x.get("ic") is not None]
    uniq = len(set(ics))
    print(f"  · 库 {n[:44]:44s} mean_ic={hit.get('mean_ic')} "
          f"source={hit.get('source')} 逐标的 IC 共 {len(ics)} 个、不同值 {uniq} 个"
          f"{'（⇒ 全等＝戳记形状）' if ics and uniq == 1 else '（⇒ 有离散＝本池重算形状）'}")

print("\n=== 3) 试验账本（E 格那条）")
print(f"  快照 {load(SNAP_TC)}  →  现值 {load(TC)}")

print("\n=== 4) 本场准入的两道地板吃了什么（从日志现读）")
txt = open(LOG, encoding="utf-8", errors="replace").read()
print(f"  「外来 IC 重算」出现次数 = {txt.count('外来 IC 重算')}")
for key in ("入池因子", "official   产出", "本场净增", "多源挖掘追加",
            "候选 ", "过 IC 门槛"):
    hits = [ln.strip() for ln in txt.splitlines() if key in ln][:4]
    for h in hits:
        print(f"  · {h[:150]}")

print("\n=== 5) 八格之外的归档字节级变化（快照 vs 现值）")
import hashlib


def sha(p):
    if not os.path.exists(p):
        return "（缺）"
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:12]


for rel in ("results/signals_daily.csv", "results/equity_daily.csv",
            "results/trades_daily.csv", "results/dsr_daily.csv",
            "library/factor_library_index.json"):
    sp = os.path.join(ROOT, "etf/v1/temp/snapshot_ding_1005", rel)
    np_ = os.path.join(ROOT, "etf/v1/data", rel)
    sr = sum(1 for _ in open(sp, encoding="utf-8-sig", errors="replace")) - 1
    nr = sum(1 for _ in open(np_, encoding="utf-8-sig", errors="replace")) - 1
    print(f"  · {rel.split('/')[-1]:28s} {sr:>7,} → {nr:>7,} 行"
          f"  sha {sha(sp)} → {sha(np_)}  {'变了' if sha(sp) != sha(np_) else '逐字节相同'}")
