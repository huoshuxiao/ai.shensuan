# -*- coding: utf-8 -*-
"""⑥ 丙：那 7 枚「折内生的键」今天到底占不占席位——纯只读回放，一个字节都不写。

为什么要这把尺子（10-06 用户裁「丙＝先只读量一次，量完再挑甲或乙」）：
`AGENT.md §8.6` 已证 10-06 那场库净增 10 键里 7 枚是**折内**由 `multi_source` 那条腿写进去的
（`multi_source_mining.py:220-226` 的 `batch_upsert` 在折内没有 `fold is not None` 守卫），
它们的 `ic` 是**该折训练段**的 IC，而库里没有任何一列标出这个窗口。
「窗口成绩混进全样本库」要不要治、值多少，取决于这些键今天**有没有被下游读走**：
只有真被读走，处置才有账单；读不到的话，甲（补标签）与乙（折内不写库）花的力气是空的。

三条读席位的腿，各自都从**生产代码的取数口径**反推，不在这里另造一把尺：
  1. 实盘层——逐字复刻 `run_live.py:150-162`（`get_active()` → 要求 `expr` 非空 →
     按 `-abs(ic)` 排序 → `[:10]`）；
  2. 策略权重层——现读 `data/results/optimized_params_daily.json` 的 `factor_weights` 键集
     （环 2 定档后真正发席位的那张表，`apply_optimized_config` 把它喂给信号层）；
  3. 其余读库的开关——现读 `config.GENETIC`／`config.LLM_RESEARCH_PLANNER` 两个开关的真值。

⚠️ 这把尺子的失效方式（先说清楚，免得读数被当成结论）：
  · 它量的是**此刻**的库与此刻的产物表。库每跑一场就长、`factor_weights` 每定档一次就换血，
    所以本文件是**场次快照**，不是恒真命题；隔场复跑必须重新出数。
  · 「跑在折内」这件事是从日志时间戳**推**的（折段起场时刻 ≤ `first_seen` ≤ 该段的 `📚 因子库更新` 行），
    不是库里的字段——库里确实没有窗口列，这正是本条要治的毛病本身。推不出来时必须出声，不许静默折成 0。
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LINE = os.path.dirname(HERE)                       # etf/v1
SRC = os.path.join(LINE, "src")
sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402  裸模块名导入的引导：`from config import X` 要靠它解析到本线那份

LOG = os.path.join(LINE, "log", "research_daily_20261006.log")
LIB = os.path.join(LINE, "data", "library", "factor_library_index.json")
OPT = os.path.join(LINE, "data", "results", "optimized_params_daily.json")


def ts(line):
    """日志行首的 `2026-10-06 08:00:13.679` ⇒ 秒级 float；没有就读不到"""
    m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
    if not m:
        return None
    import datetime
    return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()


def fold_windows(log_path):
    """从日更日志切出「折段」的时间窗 ⇒ [(折号, 起, 止, 报的 +k)]

    止界取该折段之后第一条 `📚 因子库更新` 行（那次写库就发生在折内），再放宽 60 秒
    吃掉同一时刻的并发写；下一折的起界会把这一折的窗收回，窗与窗不重叠。
    """
    if not os.path.exists(log_path):
        return None
    events = []
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.search(r"多源挖掘 折 (\d+)", line)
            if m:
                events.append(("fold", int(m.group(1)), ts(line)))
                continue
            m = re.search(r"因子库更新: \+(\d+) \(来源=([^)]*)\)", line)
            if m:
                events.append(("write", int(m.group(1)), ts(line), m.group(2)))
    folds = []
    for i, ev in enumerate(events):
        if ev[0] != "fold":
            continue
        start = ev[2]
        end, k = None, None
        for nxt in events[i + 1:]:
            if nxt[0] == "fold":
                end = nxt[2]
                break
            if nxt[0] == "write":
                end, k = nxt[2], nxt[1]
                break
        if start is None or end is None:
            continue                      # 切不出右界 ⇒ 这一折不进账，不许猜
        folds.append((ev[1], start, end + 60.0, k))
    folds.sort(key=lambda f: f[1])
    for i in range(len(folds) - 1):
        # 右界不许越过下一折的起界：那 60 秒宽裕是吃「同一时刻的并发写」的，
        # 越过之后两扇窗重叠 ⇒ 一枚键会被**前**一折抢走，归属就成了猜
        folds[i] = (folds[i][0], folds[i][1],
                    min(folds[i][2], folds[i + 1][1]), folds[i][3])
    return folds or None


def main():
    import datetime
    import time as _t

    def fmt(t):
        return _t.strftime("%H:%M:%S", _t.localtime(t))

    print("=== ⑥ 丙：折内生的键 × 三条读席位的腿（只读）===\n")

    # ── 1. 从日志切折窗 ────────────────────────────────────────────────
    folds = fold_windows(LOG)
    if not folds:
        print("❌ 日志里切不出任何折窗 ⇒ 后面的归属全部无从推起，本尺不出结论")
        return 1
    print(f"折窗（现读 {os.path.basename(LOG)}）：")
    for n, s, e, k in folds:
        print(f"  折 {n}：{fmt(s)} → {fmt(e)}（该段报 +{k}）")

    # ── 2. 库里的键按 first_seen 归到折窗 ──────────────────────────────
    idx = json.load(open(LIB, encoding="utf-8"))
    facs = idx["factors"] if "factors" in idx else idx
    print(f"\n库层现读：{len(facs)} 个键（{os.path.relpath(LIB, LINE)}）")

    fold_born, unattributed = [], []
    for name, row in facs.items():
        fs = (row or {}).get("first_seen")
        if not fs:
            unattributed.append((name, "<没有 first_seen>"))
            continue
        try:
            stamp = _t.mktime(_t.strptime(fs, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            unattributed.append((name, f"<first_seen 读不出：{fs}>"))
            continue
        hit = [n for n, s, e, _ in folds if s <= stamp <= e]
        if len(hit) > 1:
            unattributed.append((name, f"<落在两扇折窗的重叠里：{hit}>"))
        elif hit:
            fold_born.append((name, hit[0], fs, row))
        # 不在任何折窗里的键与本题无关，不列

    logged = sum(x[3] for x in folds if x[3])
    print(f"⇒ 落在折窗内新出现的键 **{len(fold_born)} 枚**"
          f"（日志报的折内 +k 合计 {logged}）"
          + ("　⚠️ 两个数不等 ⇒ 日志与库不是同一场，或折内还有第二次写库"
             if logged != len(fold_born) else ""))
    for name, n, fs, row in sorted(fold_born, key=lambda r: r[2]):
        print(f"  折 {n}｜{name:<28} first_seen {fs}  ic={row.get('ic')!r:<22} "
              f"ic_history={len(row.get('ic_history') or [])}  source={row.get('source')}")
    if unattributed:
        print(f"  ⚠️ 归属不了的有 {len(unattributed)} 枚：{unattributed[:5]}")

    if not fold_born:
        print("\n❌ 一枚都没归进折窗 ⇒ 要么日志与库不是同一场（本尺过期），要么折内写库那条路没走通"
              "；两种都不许把「0 席位」当结论念")
        return 1

    names = {n for n, *_ in fold_born}

    # ── 3. 腿一：实盘层 top-10（逐字复刻 run_live.py 的取数）────────────
    from config import GENETIC, LLM_RESEARCH_PLANNER
    from factor_library import FactorLibrary
    # 走真类的 `get_active()`，不在这儿抄一份状态判断：它对 `status` 缺省的处理与
    # 手写 `.get("status", "active")` **不一样**（真类要求字面上等于 "active"，
    # 缺 status 的键会被挡在外面），抄错了这条腿的名单就和生产不是一个口径。
    lib = FactorLibrary()
    active_names = [n for n in lib.get_active()
                    if (lib.factors.get(n) or {}).get("expr")]
    facs = lib.factors
    ranked = sorted(active_names,
                    key=lambda n: -abs(facs[n].get("ic", 0.0) or 0.0))
    top10 = ranked[:10]
    print(f"\n腿一｜实盘打分层（复刻 `run_live.py:150-162`：`get_active()` 且 expr 非空 ⇒ 按 |ic| 取前 10）")
    print(f"  候选 {len(active_names)} 个（全库 {len(facs)}，被这两道过滤挡掉 "
          f"{len(facs) - len(active_names)} 个）")
    hit_live = 0
    for name in sorted(names):
        if name in top10:
            hit_live += 1
            r = facs[name]
            print(f"  ✅ {name} 占第 {ranked.index(name) + 1} 席"
                  f"（|ic| 排序，ic={r.get('ic')!r}＝**折窗里算出来的那个数**）")
        else:
            print(f"  ·  {name} 排第 {ranked.index(name) + 1 if name in ranked else '—'}"
                  f"，进不了前 10")
    print(f"  ⇒ 进实盘打分的：**{hit_live}／{len(names)}**")
    print(f"  ⚠️ 但 `run_live.py` 没有接进日更链路（链路只跑 ①②③④），影子盘至今零成交"
          " ⇒ 这一席是**名义席**，不是今天真会下的单")

    # ── 4. 腿二：策略权重层 ────────────────────────────────────────────
    weights = {}
    if os.path.exists(OPT):
        weights = json.load(open(OPT, encoding="utf-8")).get("factor_weights") or {}
    hit_seat = sorted(set(weights) & names)
    print(f"\n腿二｜策略权重层（现读 `optimized_params_daily.json` 的 factor_weights，"
          f"{len(weights)} 个名字）")
    print(f"  名单：{sorted(weights)}")
    print(f"  ⇒ 折内生的键占席位：**{len(hit_seat)}／{len(names)}**"
          + (f"（{hit_seat}）" if hit_seat else " ⇒ 环 2 一张都没选上"))

    # ── 5. 腿三：其它读库的开关 ────────────────────────────────────────
    print(f"\n腿三｜其它两个会读因子库的入口，开关现值：")
    print(f"  GENETIC.enabled = {GENETIC.get('enabled')}")
    print(f"  LLM_RESEARCH_PLANNER.enabled = {LLM_RESEARCH_PLANNER.get('enabled')}")
    plan = os.path.join(LINE, "data", "results", "research_plan.json")
    if os.path.exists(plan):
        print(f"  research_plan.json mtime = "
              f"{_t.strftime('%m-%d %H:%M', _t.localtime(os.path.getmtime(plan)))}"
              f"（开关关着 ⇒ 这文件是历史产物，不是本场读数）")

    print(f"\n时间基准：本尺读数取自 {datetime.datetime.now():%m-%d %H:%M:%S}，"
          f"库文件 mtime {os.path.getmtime(LIB):.0f}")
    print("✅ 账量完，处置（甲补标签／乙折内不写库）仍待用户挑；本脚本一个字节都没写。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
