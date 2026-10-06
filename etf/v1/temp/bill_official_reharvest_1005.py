# -*- coding: utf-8 -*-
"""乙（回收别捞旧档）量账 v2——只读，产物只写 /tmp

口径先说清，三层各自独立，不合并成一个「总偏差」：
  ① 机制层：日志逐场——official 从 factors.json 捞回几条、几条进了 merged
  ② 库层：ic_history 每条时间戳 = 一次 upsert；同名「第二次起」＝同一表达式
     被重新试一遍。一场里 official 会被 upsert 两次（多源段 batch_upsert +
     main.py:332 stage_library_and_clustering），所以先按 300s 聚成「场」，
     一场只算一次试验——否则数字翻倍。
  ③ 判据层：把 ② 的重复条数从 n_trials 里摘掉，DSR 差多少钱。
     两个臂用**同一份 returns 向量**，差值只来自 n_trials；
     归档 dsr.csv 只当锚点，并报出实测的复刻残差（不假装逐位相等）。
  ④ 决策层：official 那几条按生产入口（run_live.py:161-162）的 |IC| 排序排在第几席、
     有没有进实盘取的前 10 个——即"这笔重复到底买走了多少实盘权重"。
"""
import glob
import json
import os
import re
import sys

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
for _p in (os.path.join(REPO, "etf", "v1", "src", "config"),
           os.path.join(REPO, "etf", "v1", "src", "core"),
           os.path.join(REPO, "etf", "v1", "src", "backtest"),
           os.path.join(REPO, "common", "src", "core"),
           os.path.join(REPO, "common", "src")):
    sys.path.insert(0, _p)

LOG_DIR = os.path.join(REPO, "etf", "v1", "log")
LIB_JSON = os.path.join(REPO, "etf", "v1", "data", "library",
                        "factor_library_index.json")
DSR_CSV = os.path.join(REPO, "etf", "v1", "data", "results", "dsr.csv")
EQ_CSV = os.path.join(REPO, "etf", "v1", "data", "results", "equity_daily.csv")
GAP_SEC = 300

TS = "%Y-%m-%d %H:%M:%S"
OFFICIAL_RE = re.compile(r"^(\S+ \S+) \|   ✅ official   产出 (\d+) 个因子")
HARVEST_RE = re.compile(r"^(\S+ \S+) \|   (?:✅|⚠️) 官方产出 (\d+) 个因子"
                        r"（.*?，落盘于 (\S+ \S+)）")
UPSERT_RE = re.compile(r"^(\S+ \S+) \|   📚 因子库更新: \+(\d+) \(来源=([^)]*)\)")
APPEND_RE = re.compile(r"^(\S+ \S+) \|   多源挖掘追加: (\d+)")
from datetime import datetime  # noqa: E402


def pt(s):
    return datetime.strptime(s.replace(".", " ")[:19], "%Y-%m-%d %H:%M:%S")


# ---------- ① 机制层 ----------
events = []
for path in sorted(glob.glob(os.path.join(LOG_DIR, "research_daily_*.log"))):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    cur = None
    for ln in lines:
        m = HARVEST_RE.match(ln)
        if m:
            if cur:
                events.append(cur)
            cur = {"log": re.search(r"(\d{8})", os.path.basename(path)).group(1),
                   "ts": m.group(1),
                   "n_file": int(m.group(2)), "file_mtime": m.group(3),
                   "n_source": None, "official_up": None, "merged": None}
            continue
        if cur is None:
            continue
        m = OFFICIAL_RE.match(ln)
        if m and cur["n_source"] is None:
            cur["n_source"] = int(m.group(2))
            continue
        m = UPSERT_RE.match(ln)
        if m and cur["official_up"] is None and m.group(3) == "official":
            cur["official_up"] = int(m.group(2))
            continue
        m = APPEND_RE.match(ln)
        if m:
            cur["merged"] = int(m.group(2))
            events.append(cur)
            cur = None
    if cur:
        events.append(cur)

print("=== ① 机制层：每场 official 捞回 vs 进账（来源见日志原文）===")
print(f"{'日志日期':8s} {'本场时刻':19s} {'factors.json 落盘于':19s} "
      f"{'捞回':4s} {'过闸写库':4s} {'整场 merged':4s}")
for e in events:
    stale = ""
    if e["file_mtime"] < e["ts"][:16]:
        stale = " ←旧档"
    print(f"{e['log']:8s} {e['ts']:19s} {e['file_mtime']:19s} "
          f"{e['n_file']:4d} {str(e['official_up']):4s} "
          f"{str(e['merged']):4s}{stale}")
print(f"合计：捞回 {sum(e['n_file'] for e in events)} 条次、"
      f"过闸写库 {sum(e['official_up'] or 0 for e in events)} 条次、"
      f"整场进账 {sum(e['merged'] or 0 for e in events)} 条次"
      f"（{sum(1 for e in events if e['merged'] is None)} 场没打出 merged 行）")

# ---------- ② 库层：按「场」聚合 ic_history ----------
lib = json.load(open(LIB_JSON, encoding="utf-8"))
entries = list(lib.values()) if isinstance(lib, dict) else lib
official = [e for e in entries if e.get("source") == "official"]


def fields_of(hist):
    ts = sorted(pt(h["time"]) for h in hist if h.get("time"))
    out = []
    for t in ts:
        if not out or (t - out[-1]).total_seconds() > GAP_SEC:
            out.append(t)
    return out


print("\n=== ② 库层：official 六个名字被「场」数到几次 ===")
D = 0
dup_field_times = []
for e in sorted(official, key=lambda x: x.get("first_seen", "")):
    fl = fields_of(e.get("ic_history") or [])
    d = max(0, len(fl) - 1)
    D += d
    dup_field_times += [f.isoformat(sep=" ") for f in fl[1:]]
    print(f"  {e['name']:58s} first={e['first_seen']} "
          f"upsert行={len(e.get('ic_history') or []):3d} "
          f"聚成场={len(fl):3d} → 重复试验 {d:3d}")
print(f"库层重复试验 D = {D}（= 同名第二次起、每次一场一枚）")

allsrc = {}
for e in entries:
    allsrc[e.get("source")] = allsrc.get(e.get("source"), 0) + \
        max(0, len(fields_of(e.get("ic_history") or [])) - 1)
print(f"全库各来源「同名第二次起」的场次数（含 official 之外的换皮重复）："
      f"{json.dumps(allsrc, ensure_ascii=False)}")

# ---------- ③ 判据层 ----------
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from dsr import deflated_sharpe_ratio, annualize_sharpe  # noqa: E402

rets = pd.read_csv(EQ_CSV, parse_dates=["date"])["equity"] \
        .pct_change().dropna().values
counter = json.load(open(os.path.join(REPO, "common", "data", "etf",
                                      "cache", "trial_counter.json")))["count"]
arch = pd.read_csv(DSR_CSV).iloc[-1]
now = deflated_sharpe_ratio(rets, n_trials=counter)
resid = abs(now["dsr"] - float(arch["dsr"]))
print("\n=== ③ 判据层 ===")
print(f"复刻臂 n_trials={counter}: dsr={now['dsr']!r} sr0_ann="
      f"{annualize_sharpe(now['sr0_expected_max'], 252):.4f} T={now['n_samples']}")
print(f"归档锚 dsr.csv      : dsr={float(arch['dsr'])!r} sr0_ann="
      f"{arch['sr0_annual']} N={arch['n_trials']} T={arch['n_samples']}")
print(f"复刻残差 |Δdsr|={resid:.2e}（相对 {resid/float(arch['dsr']):.1e}）"
      f"——sr/n_samples 逐位相同，skew/kurt 差在第 14 位，"
      f"来自「归档那场的 returns 在内存里、我这里从 CSV 重读」，"
      f"量级远小于本节要比的差，故只当锚点、不声称逐位相等")

print(f"\n{'n_trials':>10s} {'dsr':>12s} {'sr0_annual':>11s} "
      f"{'过0.95?':>8s}  距现值")
# 低档那一排（1/10/50/100）＝反向扫到极限：就算把台账清空到"只试过 1 次"，
# 这一腿的 DSR 也过不了 0.95 ⇒ 判决不翻不是因为重复多，是这一腿本身太弱。
for n in sorted({counter, counter - D, 429 - 26, 403, 380, 350, 300, 200,
                 100, 50, 10, 1}):
    r = deflated_sharpe_ratio(rets, n_trials=n)
    print(f"{n:10d} {r['dsr']:12.8f} "
          f"{annualize_sharpe(r['sr0_expected_max'], 252):11.4f} "
          f"{str(r['passed']):>8s}  dsr {r['dsr']-now['dsr']:+.8f}")

need = None
for n in range(5000, 0, -1):
    if deflated_sharpe_ratio(rets, n_trials=n)["passed"]:
        need = n
        break
sr_bar = now["sr_observed"]
print(f"\n门槛侧：SR̂_bar={sr_bar:.8f}（年化 "
      f"{annualize_sharpe(sr_bar, 252):.4f}），"
      f"要 dsr>0.95 需要 n_trials ≤ {need}（当前 {counter}，"
      f"摘掉 D={D} 只到 {counter-D}）")
print(f"⚠️ 这格只说「摘掉重复之后门槛松了多少」，"
      f"不说「因子变好了」——表达式一条都没换")

# ---------- ④ 决策层 ----------
# 走**生产同一条路**（`run_live.py:153-162`：get_library → get_active →
# 按 -abs(ic) 排序 → 取前 10），不自己复刻排序，否则这一格量的是我的写法。
# get_library 只读索引文件（common/src/core/factor_library.py:17 起，__init__ 无写盘）。
print("\n=== ④ 决策层：official 那几条在实盘取的前 10 席里排第几 ===")
from factor_library import get_library  # noqa: E402

_lib = get_library()
_active = [dict(_lib.factors[n], name=n) for n in _lib.get_active()
           if _lib.factors.get(n, {}).get("expr")]
_active.sort(key=lambda f: -abs(f.get("ic", 0.0)))
_top = _active[:10]
off_names = {e["name"] for e in official}
print(f"  活跃因子 {len(_active)} 个（实盘取前 {len(_top)}），"
      f"official 来源 {len(off_names)} 个")
for i, f in enumerate(_active, 1):
    if f["name"] in off_names:
        print(f"  第 {i:2d} 席  {f['name']:56s} "
              f"|ic|={abs(f.get('ic', 0.0)):.4f}"
              f"{'  ✅ 进前 10' if i <= 10 else ''}")
print(f"  ⇒ official 进实盘前 10 的条数："
      f"{len([f for f in _top if f['name'] in off_names])}")
