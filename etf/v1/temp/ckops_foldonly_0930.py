# -*- coding: utf-8 -*-
"""一次性缓存手术（09-30，用户裁「甲-A」）：只作废旧折条目，把主线那条认领到新指纹

为什么要有这一步：丙-2 那个折内开关进了作废指纹（`run_checkpoint.py:59`），而指纹是**整场一份**
⇒ 一 `arm()` 就把 4 条条目（主线 + 三折）一起判死 ⇒ 想「只重建折表」会变成「主线也重挖一遍」，
顺带 upsert 因子库、把对照弄脏（主线也换了人，折表差异就说不清归谁）。

它到底改什么：
1. 删掉 `折 1/2/3 因子` 三条 ⇒ 这一折重挖（试验数照旧只增不减，方向保守）。
2. 给存量指纹补上 `official_in_fold` 这一个键，取值与本场现算的指纹一致 ⇒ 主线条目续传命中。

**硬门（不满足就一字节不写、退出码非 0）**：除这一个键之外，存量指纹必须与本场现算的指纹
**逐键相同**。数据一推进（ts_last/bars/pool 任一变化）这道门就挡住 ⇒ 手术不会把"昨天的因子"
冒充成"今天挖的"，那条整批作废的闸语义一行没动。

写法沿用面板那套原子落盘：`.part` → 回读确认解得开 → `os.replace` 顶上。

用法：`/usr/bin/python3.10 etf/v1/temp/ckops_foldonly_0930.py`（只预演）
      `... ckops_foldonly_0930.py --apply`（真写）
"""

import os
import pickle
import sys

os.environ.setdefault("ETF_FREQ", "daily")

HERE = os.path.dirname(os.path.abspath(__file__))
BYPASS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(BYPASS, "src"))
import _bootstrap  # noqa: E402,F401

from config import CACHE_DIR, FREQ  # noqa: E402
from data_loader import DataLoader  # noqa: E402
from etf_universe import get_universe  # noqa: E402
from run_checkpoint import RunCheckpoint, make_fingerprint  # noqa: E402

PATH = os.path.join(CACHE_DIR, f"run_checkpoint_{FREQ}.pkl")
FOLD_KEYS = ["折 1 因子", "折 2 因子", "折 3 因子"]
MAIN_KEY = "主线多源因子"
NEW_KEY = "official_in_fold"
APPLY = "--apply" in sys.argv


def die(msg):
    print(f"❌ {msg}\n⇒ 一字节未写。")
    sys.exit(1)


print(f"缓存文件 {PATH}  size={os.path.getsize(PATH):,}  APPLY={APPLY}")
with open(PATH, "rb") as f:
    blob = pickle.load(f)
stored_fp = blob.get("fingerprint") or {}
entries = blob.get("entries") or {}

print("\n[1/4] 存量状态")
print(f"  条目键：{sorted(entries)}")
for k in sorted(entries):
    v = entries[k]
    print(f"    {k!r}: 因子 {len(v.get('value') or [])} 个, "
          f"tc_after={v.get('tc_after')}, written={v.get('written')}")
if sorted(entries) != sorted(FOLD_KEYS + [MAIN_KEY]):
    die(f"条目键与预想的不符（应为 {sorted(FOLD_KEYS + [MAIN_KEY])}）")
if NEW_KEY in stored_fp:
    die("存量指纹已含 official_in_fold ⇒ 这场缓存本来就是新指纹写的，手术没有必要")

print("\n[2/4] 现算本场指纹（要真读一遍池子与时间轴）")
universe = get_universe()
codes = universe.universe["code"].tolist()
pool = DataLoader(freq=FREQ).load_pool(codes)
if not pool:
    die("池子加载失败，拿不到时间轴")
ref_code = max(pool, key=lambda c: len(pool[c]))
all_ts = pool[ref_code].index
current_fp = make_fingerprint(list(pool), all_ts)
print(f"  时间轴参考 {ref_code}: {all_ts[0]:%Y-%m-%d} ~ {all_ts[-1]:%Y-%m-%d} ({len(all_ts)} bars)")

print("\n[3/4] 硬门：除那一个键之外，数据必须一个格都没动")
diff = {}
for k in sorted(set(stored_fp) | set(current_fp)):
    old, new = stored_fp.get(k, "（无此键）"), current_fp.get(k, "（无此键）")
    if old != new:
        diff[k] = (old, new)
    flag = "=" if old == new else "≠"
    print(f"  {flag} {k:<17} 存量={old}  现算={new}")
if set(diff) != {NEW_KEY}:
    die(f"差异键 = {sorted(diff)}，必须**只有** {NEW_KEY} ⇒ 数据也变了，"
        f"这道手术该作废（走整链重挖那条路）")
if current_fp[NEW_KEY] is not False:
    die(f"现算指纹里 {NEW_KEY}={current_fp[NEW_KEY]}，本线配置应为 False")

if not APPLY:
    print("\n[4/4] 预演结束（没带 --apply ⇒ 一个字节没写）")
    print(f"  计划：删 {FOLD_KEYS}；存量指纹补 {NEW_KEY}=False；保留 {MAIN_KEY}")
    sys.exit(0)

print("\n[4/4] 落盘（.part → 回读 → os.replace）")
new_blob = dict(blob)
new_blob["fingerprint"] = dict(stored_fp, **{NEW_KEY: False})
new_entries = {MAIN_KEY: entries[MAIN_KEY]}
new_blob["entries"] = new_entries
tmp = PATH + ".part"
with open(tmp, "wb") as f:
    pickle.dump(new_blob, f)
    f.flush()
    os.fsync(f.fileno())
with open(tmp, "rb") as f:
    back = pickle.load(f)
if back["fingerprint"] != new_blob["fingerprint"] or sorted(back["entries"]) != [MAIN_KEY]:
    die(".part 回读对不上，已放弃 replace")
os.replace(tmp, PATH)
print(f"  已顶上：{PATH}（{os.path.getsize(PATH):,} 字节）")

print("\n[复核] 拿真闸再走一遍（RunCheckpoint.arm）")
ck = RunCheckpoint(enabled=True).arm(make_fingerprint(list(pool), all_ts))
print(f"  arm 后 entries={sorted(ck.entries)}  discarded={ck.discarded}")
if sorted(ck.entries) != [MAIN_KEY] or ck.discarded != 0:
    die("复核没通过：arm 仍把主线条目作废了")
print("✅ 手术完成：主线会续传命中，三折会重挖")
