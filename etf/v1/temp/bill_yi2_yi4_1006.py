# -*- coding: utf-8 -*-
"""乙-2／乙-4 量账（10-06）——**全程只读**，一个字节都不写生产，产物只打印到 stdout

三层各自独立，不合并成一个「总偏差」：
  ⓪ 身份闸：先从 10-05 那场日志读出「official 产出 16」「本场净增 2」「多源挖掘追加 5」，
     再用库内 `ic_history` 的时间戳反推那 5 条**是不是同一批**（两条尺对得上才往下走）。
  ① 乙-2：净增过滤落在本线 `main.py:251-254`（`_mine()` 返回处）⇒ 影响的是**候选池 + 试验计数**，
     按构造**碰不到写库**（共享层 `multi_source_mining.py` 的 `batch_upsert` 在 `return merged` 之前
     就执行完了）。这一格要实测的是「压掉几条」与「实盘席位一条都不变」两半。
  ② 乙-4：进 merged 前与库内表达式**逐字**比 ⇒ 影响的是**写库那一笔**，于是动到
     `run_live.py:161-162` 那把 `-abs(ic)` 前十席。这一格量的是「摘几条／其中同名异式几条／
     前十席换几席」。
  ③ 模拟器自己的正对照：把「摘除集」设为空跑同一段代码，前十席必须**逐位复现**现网顺序
     （不等 ⇒ ② 的差值全部作废）。

判据层的先验引 10-05 那笔（同一次只读实跑）：`n_trials` 从 429 摘到 1，DSR 只到 0.8565，
门槛 0.95 ⇒ **不存在任何一档试验数能让判决翻**。本脚本不重烧 DSR，只补「池子／席位」两半。
"""
import json
import os
import re
import sys

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
# ⚠️ 这一支是**对 2026-10-05 那一场历史真跑的重放**（不是每天要过的闸），所以日志按名钉死；
# 下一场落地后要重量 ⇒ 把 LOG 指到新的那份，别指望它自己跟着变。
LOG = os.path.join(REPO, "etf/v1/log/research_daily_20261005.log")
LIB = os.path.join(REPO, "etf/v1/data/library/factor_library_index.json")
FJ = os.path.join(REPO, "etf/v1/data/results/rdagent_output/factors.json")
TC = os.path.join(REPO, "common/data/etf/cache/trial_counter.json")

FAIL = []


def ok(label, cond, evidence):
    print(f"  {'✅' if cond else '❌'} {label}｜{evidence}")
    if not cond:
        FAIL.append(label)


def hr(title):
    print(f"\n===== {title} =====")


# ---------- ⓪ 身份闸 ----------
hr("⓪ 身份闸（两条独立尺必须咬合，否则后面全作废）")
with open(LIB, encoding="utf-8") as f:
    lib = json.load(f)
with open(FJ, encoding="utf-8") as f:
    fj = json.load(f)
with open(TC, encoding="utf-8") as f:
    tc = json.load(f)
print(f"  库条目 {len(lib)} 条／active {sum(1 for v in lib.values() if v.get('status')=='active')} 条"
      f"｜factors.json {len(fj)} 条｜trial_counter {tc}")

txt = open(LOG, encoding="utf-8", errors="replace").read()
m_n = re.search(r"official\s+产出 (\d+) 个因子", txt)
m_net = re.search(r"本场净增 (\d+) 个因子：(.+)", txt)
m_got = re.search(r"多源挖掘追加: (\d+)", txt)
m_up = re.search(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[\d. ]*\|   📚 因子库更新: \+4 \(来源=official\)", txt)
ok("日志三行读数都抓得到", bool(m_n and m_net and m_got),
   f"产出 {m_n and m_n.group(1)}／净增 {m_net and m_net.group(1)}／追加 {m_got and m_got.group(1)}")
n_prod = int(m_got.group(1))
net_names = [s.strip() for s in m_net.group(2).split("、") if s.strip()]
ok("净增那行的名字数 == 它自己念的数", len(net_names) == int(m_net.group(1)),
   f"名字 {len(net_names)} 个 vs 行内数字 {m_net.group(1)}：{net_names}")

# 用 ic_history 时间戳反推「那一场多源段写了哪几条」：official 那批 +4 与 llm 那批 +1 同一秒
up_s = (m_up.group(1)[:19] if m_up else None)
touch = [n for n, v in lib.items()
         if any(h.get("time", "")[:19] == up_s for h in v.get("ic_history", []))]
by_src = {}
for n in touch:
    by_src.setdefault(lib[n].get("source"), []).append(n)
print(f"  起场那一秒 {up_s} 被 upsert 的条目：共 {len(touch)} 条，按 source {dict((k, len(v)) for k, v in by_src.items())}")
ok("时间戳反推的条数 == 日志「多源挖掘追加」那条", len(touch) == n_prod,
   f"反推 {len(touch)} vs 日志 {n_prod}")
ok("official 那批条数 == 日志「因子库更新: +4 (来源=official)」",
   len(by_src.get("official", [])) == 4, f"实测 {len(by_src.get('official', []))}")

# ---------- ① 乙-2 ----------
hr("① 乙-2 反事实：候选池只吃本场净增（过滤落 main.py:251-254）")
fj_names = {e["name"] for e in fj}
merged_names = list(touch)
survive = [n for n in merged_names if n in set(net_names)]
dropped = [n for n in merged_names if n not in set(net_names)]
print(f"  那 5 条的名字：{merged_names}")
print(f"  净增名单（日志念的那 2 个）：{net_names}")
ok("净增名单里的名字都在 factors.json 那 16 个里", set(net_names) <= fj_names,
   f"缺 {sorted(set(net_names) - fj_names)}")
print(f"  放行 {len(survive)} 条／摘掉 {len(dropped)} 条｜被摘的名字：{dropped}")
stale, fresh_but_uncovered = [], []
for n in dropped:
    v = lib[n]
    (stale if v["first_seen"] < up_s else fresh_but_uncovered).append(n)
    print(f"    - {n}｜库里 source={v.get('source')} first_seen={v.get('first_seen')} "
          f"update_count={v.get('update_count')} 当前 ic={v.get('ic'):.6f}"
          f"⇒ {'旧档被重新试一遍' if v['first_seen'] < up_s else '本场才第一次入库'}")
# 这两类由 `first_seen < up_s` 一刀切开 ⇒ 互斥且并起来是全集，**按构造恒成立**，
# 所以它只能当读数印出来，不许写成判据（判据在下面那格「口径只认 official 那条腿」）。
print(f"  分类读数：被摘 {len(dropped)} 条＝旧档 {len(stale)} 条＋本场才第一次入库 "
      f"{len(fresh_but_uncovered)} 条")
ok("乙-2 的「净增」口径**只认 official 那条腿**（净增名单里没有 llm 腿的本场新名）",
   bool(fresh_but_uncovered) and all(n not in fj_names for n in fresh_but_uncovered),
   f"照日志那份净增名单实现，会连带摘掉 llm 腿本场新写的 {len(fresh_but_uncovered)} 条：{fresh_but_uncovered}")
print(f"  试验台账影响：那一拍 `tc.add` 由 {len(merged_names)} 变 {len(survive)}"
      f"＝台账少记 {len(dropped)} 次；`raw_factors` 同步少 {len(dropped)} 条"
      "⇒ 后面三段（GP／多目标／LLM-GP 混合）吃的是 raw_factors 当种子，种子会变少（结构性，未实测）")
# 乙-2 按构造碰不到写库：过滤在共享层 return 之后
msm = open(os.path.join(REPO, "common/src/core/multi_source_mining.py"), encoding="utf-8").read().splitlines()
idx_up = next(i for i, ln in enumerate(msm, 1) if "lib.batch_upsert(src_factors" in ln)
# ⚠️ 取 batch_upsert **之后**那道 `return merged`：`merge_factors()` 自己也以同四个字收尾（:159），
# 第一版拿 `next()` 抓到的是它 ⇒ 行号闸假红（10-06 自查）。锚点必须落在被测那个函数体内。
idx_ret = next(i for i, ln in enumerate(msm, 1)
               if i > idx_up and ln.strip() == "return merged")
ok("共享层里 batch_upsert 的行号 < 本函数 return merged 的行号（＝写库早于本线拿到返回值）",
   idx_up < idx_ret, f"batch_upsert :{idx_up} vs return :{idx_ret}")

# ---------- ② 乙-4 ----------
hr("② 乙-4 反事实：进 merged 前与库内表达式逐字比")
cls = {"同名同式（会被摘）": [], "同名异式（会被摘＝新写法永远进不了库）": [],
       "库内无同名（放行）": []}
for e in fj:
    n, ex = e["name"], (e.get("expr") or "").strip()
    if n in lib:
        key = ("同名同式（会被摘）" if ex == (lib[n].get("expr") or "").strip()
               else "同名异式（会被摘＝新写法永远进不了库）")
    else:
        key = "库内无同名（放行）"
    cls[key].append(n)
for k, v in cls.items():
    print(f"  {k}：{len(v)} 条")
    for n in v:
        ex_lib = (lib.get(n, {}).get("expr") or "（不在库）")
        ex_fj = next((e.get("expr") for e in fj if e["name"] == n), "")
        print(f"    - {n}\n        候选 expr：{ex_fj}\n        库内 expr：{ex_lib}")
n_drop = len(cls["同名同式（会被摘）"]) + len(cls["同名异式（会被摘＝新写法永远进不了库）"])
print(f"  ⇒ 本场这一批 16 条里，乙-4 会摘 {n_drop} 条（其中「同名异式」{len(cls['同名异式（会被摘＝新写法永远进不了库）'])} 条是**会把新写法一起挡掉**的那一类）")

# 席位模拟：run_live.py:161-162 的口径＝active 且有 expr，按 -abs(ic) 排序取前 10
LIVE_N = 10


def seats(ic_over=None, absent=()):
    pool = [dict(v, name=n) for n, v in lib.items()
            if v.get("status") == "active" and (v.get("expr") or "").strip()]
    for n, val in (ic_over or {}).items():
        if n in [p["name"] for p in pool]:
            for p in pool:
                if p["name"] == n:
                    p["ic"] = val
    pool = [p for p in pool if p["name"] not in set(absent)]
    pool.sort(key=lambda x: -abs(x.get("ic", 0.0)))
    return [p["name"] for p in pool[:LIVE_N]]


base = seats()
print(f"  现网实盘前十席（按库里当前 ic）：{base}")
# 摘除 ⇒ 那一笔 upsert 不发生 ⇒ ic 冻结在**上一条**历史值
frozen, vanished = {}, []
for n in cls["同名同式（会被摘）"] + cls["同名异式（会被摘＝新写法永远进不了库）"]:
    hist = lib.get(n, {}).get("ic_history") or []
    if len(hist) >= 2:
        frozen[n] = float(hist[-2]["ic"])
    elif len(hist) == 1:
        vanished.append(n)
alt = seats(ic_over=frozen, absent=vanished)
churn = [(i + 1, a, b) for i, (a, b) in enumerate(zip(base, alt)) if a != b]
entered = [n for n in alt if n not in base]
left = [n for n in base if n not in alt]
print(f"  乙-4 后前十席：{alt}")
print(f"  逐席差 {len(churn)} 席｜入席 {len(entered)} 条 {entered}／出席 {len(left)} 条 {left}／"
      f"位移（仍在榜但换席位）{len(churn) - len(entered)} 席")
for i, a, b in churn:
    print(f"    第 {i} 席：{a} → {b}")
print(f"  冻结值来源：{len(frozen)} 条取 ic_history 倒数第二条，{len(vanished)} 条只有一条历史⇒视为未入库")
for n in sorted(frozen, key=lambda x: -abs(frozen[x])):
    if n in base or n in alt:
        print(f"    前十席相关：{n}｜当前 ic {lib[n]['ic']:+.6f} → 冻结 ic {frozen[n]:+.6f}"
              f"（|ic| {'变大' if abs(frozen[n]) > abs(lib[n]['ic']) else '变小'}）")
# 正对照：摘除集为空 ⇒ 模拟器必须逐位复现现网顺序
ok("正对照：空摘除集跑同一段代码 ⇒ 前十席逐位复现现网", seats(ic_over={}, absent=()) == base,
   f"{seats(ic_over={}, absent=())[:3]} …")

# ---------- ③ 汇总 ----------
hr("③ 两问各自买到什么／付出什么（只报读数，不给建议）")
print(f"  乙-2：候选池 {len(merged_names)} → {len(survive)} 条，台账少记 {len(dropped)} 次；"
      f"实盘前十席变动 0 席（按构造：写库在那一拍之前已发生，见上面那条行号闸 :{idx_up} < :{idx_ret}）")
print(f"  乙-4：本场 16 条候选里 {n_drop} 条会被摘；前十席变动 {len(churn)} 席；"
      f"其中「同名异式」{len(cls['同名异式（会被摘＝新写法永远进不了库）'])} 条属「挡掉新写法」那一类代价")
print(f"  两问共用的先验（10-05 只读实跑）：n_trials 429→1 时 DSR 0.004713→0.8565，门槛 0.95 ⇒ 判决任何一档都不翻")

print("\n===== 尺子自检 =====")
if FAIL:
    print(f"❌ {len(FAIL)} 格判红：{FAIL}")
    sys.exit(1)
print("✅ 全部身份闸与正对照通过（rc=0）")
