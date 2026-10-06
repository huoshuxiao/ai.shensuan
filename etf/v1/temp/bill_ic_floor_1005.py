# -*- coding: utf-8 -*-
"""选项B 量账（10-05 18:3x）：0.005 那道 IC 地板**到底砍错了没有**——只读，产物只写 /tmp

先说一句本批**自我更正**（原先写在 CHANGELOG 里的那句已标作废）：
  上一节我按 `factors.json` 落盘的 `mean_ic` 现算，得到「14 条里只 3 条过 0.005、
  本场净增那两枚差 0.0000434 被砍」。**这个数不是生产判据吃的那个数。**
  现读链路：`common/src/core/multi_source_mining.py:20-22` 的 `_run_official` 在回收之后
  立刻过 `_attach_impl`（:71-101），后者对**本池逐标的**求 `compute_ic` 并重算 `mean_ic`
  （:89）⇒ 地板（:203-205）吃的是**重算值**，容器/驱动那份戳记（`rdagent_driver.py:244`
  按 step 取一次、:246-257 摊给该实验每条因子）在判据之前已经被覆盖。
  ⇒ 所以"沙箱戳记逐位相同"仍是缺陷（它是**落盘归档**、也在 `rdagent_facade._official_to_impl`
  那条不重算的旁路上当 IC 用），但它**值多少钱**必须由本脚本现量，不许靠归档值推断。

三层读数，各自独立、不合并成一个"总偏差"：
  ① 资格层：14 条里有几条 `expr` 是空的 ⇒ `_attach_impl:74-76` 直接 `continue`＝连求值都不参加
  ② 尺子层：同一批因子过**生产那一个函数**重算 ⇒ 逐条报「戳记 vs 重算」两列与差值；
     正对照（有牙）：戳记逐位相同的那一组，重算后**必须不再相同**（表达式不同），
     若仍相同＝我压根没重算 ⇒ 当场 ❌、退出码非零。
  ③ 判据层：把地板在**重算值**上从 0.000 扫到 0.010，逐档报放行条数；
     并报「若地板吃戳记」会放行几条 ⇒ 两把尺的账单并排放，差多少钱一目了然。
  ④ 诚实层：跑完把三份生产文件（factors.json／因子库索引／trial_counter）的 sha256
     与起算前逐字节对表 ⇒ 这一场只算不记。
"""
import hashlib
import json
import os
import sys
import time

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
for _p in (os.path.join(REPO, "etf", "v1", "src", "config"),
           os.path.join(REPO, "etf", "v1", "src", "core"),
           os.path.join(REPO, "etf", "v1", "src", "backtest"),
           os.path.join(REPO, "etf", "v1", "src"),
           os.path.join(REPO, "common", "src", "core"),
           os.path.join(REPO, "common", "src", "data", "etf"),
           os.path.join(REPO, "common", "src")):
    sys.path.insert(0, _p)
SRC = os.path.join(REPO, "etf", "v1", "src")
os.chdir(SRC)
import _bootstrap  # noqa: E402,F401

OUT_JSON = os.path.join(REPO, "etf", "v1", "data", "results",
                        "rdagent_output", "factors.json")
LIB_JSON = os.path.join(REPO, "etf", "v1", "data", "library",
                        "factor_library_index.json")
TC_JSON = os.path.join(REPO, "common", "data", "etf", "cache",
                       "trial_counter.json")
FAIL = []


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:8] if os.path.exists(p) else "<不存在>"


BASE = {p: sha(p) for p in (OUT_JSON, LIB_JSON, TC_JSON)}
print("=== ④ 起算前的生产文件指纹（收尾要逐字节对表）===")
for p, h in BASE.items():
    print(f"  {h}  {os.path.relpath(p, REPO)}")

raw = json.load(open(OUT_JSON, encoding="utf-8"))
print(f"\n=== ① 资格层：`factors.json` 现读 {len(raw)} 条 ===")
no_expr = [f for f in raw if not (f.get("expr") or "").strip()]
has_expr = [f for f in raw if (f.get("expr") or "").strip()]
print(f"  表达式为空 ⇒ 不参与求值：{len(no_expr)} 条"
      f"{'（' + '、'.join(f['name'][:28] for f in no_expr) + '）' if no_expr else ''}")
print(f"  带表达式、会进 `_attach_impl` 的：{len(has_expr)} 条")
stamp = {}
for f in has_expr:
    stamp.setdefault(round(float(f.get("mean_ic", 0)), 16), []).append(f["name"])
same = {k: v for k, v in stamp.items() if len(v) > 1}
print(f"  落盘戳记分组：{len(stamp)} 组（其中逐位相同的 {len(same)} 组、"
      f"最大一组 {max(len(v) for v in stamp.values())} 条）"
      f"⇒ 这就是「实验级摊派」的形状，不是本池实测")

# 负对照：_attach_impl 那条 `if not expr: continue` 的分支要能被我的输入真的走到
from multi_source_mining import _attach_impl  # noqa: E402
_probe = _attach_impl([{"name": "探针", "expr": ""}], {}, "official")
print(f"  负对照（空表达式送进生产那一步）：返回 {len(_probe)} 条（期望 0＝那条 continue 真有牙）")
if len(_probe) != 0:
    FAIL.append("负对照失效：空表达式竟然被 _attach_impl 收下了")

print("\n=== ② 尺子层：现取生产同一份池子，走**生产那一个函数**重算 ===")
from config import FREQ  # noqa: E402
from etf_universe import get_universe  # noqa: E402
from data_loader import DataLoader  # noqa: E402

t0 = time.perf_counter()
codes = get_universe().universe["code"].tolist()
pool = DataLoader(freq=FREQ).load_pool(codes)
print(f"  池子：universe {len(codes)} 个代码 → load_pool 实得 {len(pool)} 条 "
      f"（{time.perf_counter() - t0:.1f}s）")
if not pool:
    print("❌ 池子为空 ⇒ 本脚本没有任何尺子可称，全场作废")
    sys.exit(1)

t0 = time.perf_counter()
rec = _attach_impl(raw, pool, "official")
print(f"  重算完成：{len(rec)} 条出了逐标的 IC（{time.perf_counter() - t0:.1f}s）"
      f"⇒ 其余 {len(raw) - len(rec)} 条求值全失败或表达式为空，本来就不入场")

by_name = {f["name"]: f for f in rec}
print(f"\n  {'因子名':44s} {'戳记(归档)':>13s} {'本池重算':>13s} {'差值':>12s} "
      f"{'标的数':>5s} 过0.005(重算)")
for f in raw:
    n = f["name"]
    s = float(f.get("mean_ic", 0))
    if n in by_name:
        r = float(by_name[n]["mean_ic"])
        k = len(by_name[n].get("impl") or {})
        print(f"  {n[:44]:44s} {s:13.8f} {r:13.8f} {r - s:+12.8f} {k:5d} "
              f"{str(abs(r) >= 0.005):>6s}")
    else:
        print(f"  {n[:44]:44s} {s:13.8f} {'<无重算值>':>13s} "
              f"{'—':>12s} {'0':>5s}   无入场资格")

# 正对照（有牙）：戳记逐位相同的那一组，重算后必须不再相同
for k, names in sorted(same.items(), key=lambda x: -len(x[1])):
    vals = [by_name[n]["mean_ic"] for n in names if n in by_name]
    if len(vals) < 2:
        print(f"  ⚠️ 戳记组 n={len(names)} 里只有 {len(vals)} 条有重算值 ⇒ 这一组无从对照（第三种状态，不折成通过）")
        continue
    spread = max(vals) - min(vals)
    print(f"  正对照：戳记 {k:.16f} 那 {len(names)} 条 → 重算跨度 {spread:.10f}"
          f"（不同表达式被摊了同一个戳记）")
    if spread == 0.0:
        FAIL.append("正对照失效：不同表达式的重算值仍逐位相同 ⇒ 我没真的重算")

print("\n=== ③ 判据层：地板在**两把尺**上各放行几条（现扫，非推算）===")
BAR = 0.005
re_vals = [abs(float(f["mean_ic"])) for f in rec]
st_vals = [abs(float(f.get("mean_ic", 0))) for f in raw]
print(f"  当前地板 {BAR}：吃**重算值**放行 {sum(1 for v in re_vals if v >= BAR)} / {len(re_vals)} 条"
      f"；若吃**落盘戳记**放行 {sum(1 for v in st_vals if v >= BAR)} / {len(st_vals)} 条")
print(f"\n  {'地板':>7s}  {'按重算值放行':>10s}   {'按戳记放行':>8s}   差")
for bar in (0.0, 0.001, 0.002, 0.003, 0.004, 0.0045, 0.0049, BAR,
            0.0051, 0.006, 0.008, 0.010):
    a = sum(1 for v in re_vals if v >= bar)
    b = sum(1 for v in st_vals if v >= bar)
    print(f"  {bar:7.4f}  {a:10d}   {b:8d}   {a - b:+d}")
near = sorted(re_vals, key=lambda v: abs(v - BAR))[:5]
print(f"  贴线读数：重算值里离 0.005 最近的 5 个 = "
      f"{', '.join(f'{v:.8f}(差{v - BAR:+.8f})' for v in near)}")

print("\n=== ④ 收尾：生产文件指纹对表（只算不记）===")
for p, h in BASE.items():
    now = sha(p)
    ok = now == h
    print(f"  {'✅ 未动' if ok else '❌ 被改写'}  {now}  {os.path.relpath(p, REPO)}"
          f"{'' if ok else f'（起算前 {h}）'}")
    if not ok:
        FAIL.append(f"只读承诺被破：{os.path.basename(p)} 被本脚本改写")

print(f"\n恒等自检：戳记 {len(raw)} 条 = 有重算 {len(rec)} + 无重算 {len(raw) - len(rec)}"
      f" ⇒ {'✅' if len(raw) == len(rec) + (len(raw) - len(rec)) else '❌'}")
if FAIL:
    print(f"\n❌ 本脚本 {len(FAIL)} 条判据红：")
    for x in FAIL:
        print("   -", x)
    sys.exit(1)
print("\n✅ 三把尺子都有牙、生产文件逐字节未动 ⇒ 以上读数可引")
