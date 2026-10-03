# -*- coding: utf-8 -*-
"""丁2 刀口的现场重测（只读，一个字节都不改库）。

为什么要重测：上一次量账单时库是 35 条 active，09-28 22:04 有一场管线跑完把它涨到
36 条、并把所有条目的 ic 刷新了一遍 —— 而「重复对里留谁」正是拿库内自带 |ic| 挑的。
刀口（|corr| >= RDAGENT_BAR=0.99）没变，名单可能变了。

做的事
    ① 用环 3 同一套函数重算「在库 × 在库」对角线（判据单点：直接 import 环 3 的
       library_internal，不自己另写一份相关）
    ② 按 |corr| >= 0.99 并簇，每簇留 |ic| 最大者（同分按 first_seen 早者）
    ③ 量波及面：run_live 的实盘 top10、genetic 源的 seeds 前 10
    ④ 负对照：剔掉的那几条必须在库里留着一个 >=0.99 的活口，否则"清重复"变成"清信息"
"""
import os
import sys
import json

BASE = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(BASE, "etf/v1/src"))
sys.path.insert(0, os.path.join(BASE, "common/src"))
sys.path.insert(0, os.path.join(BASE, "common/src/core"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import etf_admission as EA  # noqa: E402
from run_etf_redundancy_check import library_internal  # noqa: E402


def main():
    # load_active_library 已经把 status!=active 和 expr 为空的都滤掉了，
    # 所以「库到底几条」要从导出的 csv 本体数，别拿它的返回长度当库大小
    LIB_DIR = os.path.join(BASE, "etf/v1/data/library")
    act = pd.read_csv(os.path.join(LIB_DIR, "factor_library.csv"),
                      keep_default_na=False).to_dict("records")
    act = [r for r in act if str(r.get("status", "")).lower() == "active"]
    blank = [r.get("name") for r in act if not str(r.get("expr", "")).strip()]
    lib = EA.load_active_library()
    print(f"[库] csv 里 active {len(act)} 条，其中 expr 为空、判重看不见的 "
          f"{len(blank)} 条: {blank}")
    print(f"[库] 可译（进得了判重）{len(lib)} 条")

    specs = EA.specs_from_rows(lib, family="在库")
    pool = EA.load_pool()
    facs = EA.evaluate_factors(pool, specs)
    facs = {k: EA.slice_to_start(v, EA.EVAL_START) for k, v in facs.items()}
    names = list(facs)
    ic_of = {(r.get("name") or (r.get("expr") or "")[:24]): abs(float(r.get("ic") or 0.0))
             for r in lib}
    print(f"[求值] 可译 {len(names)} 条，评估窗 "
          f"{next(iter(facs.values())).index[0].date()} ~ "
          f"{next(iter(facs.values())).index[-1].date()}")

    LP, LS = EA.cs_corr_mean(facs, min_cs=EA.MIN_CS)
    det, n_unique_all = library_internal(LS, LP, names, ic_of)
    print(f"\n[对角线] 可译 {len(names)} 条 ⇒ 按 |corr|>={EA.RED_BAR} 并簇只剩 "
          f"{n_unique_all} 条唯一；|corr|>={EA.NEAR_DUP} 的对 {len(det)} 张")

    # ---- 只在 RDAGENT_BAR 这一档并簇 ----
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    bars = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            v = abs(float(LS.loc[a, b]))
            if not np.isfinite(v):
                continue
            key = (a, b)
            bars[key] = v
            if v >= EA.RDAGENT_BAR:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

    clusters = {}
    for n in names:
        clusters.setdefault(find(n), []).append(n)
    seen = json.load(open(os.path.join(BASE, "etf/v1/data/library/factor_library_index.json")))
    first_seen = {k: v.get("first_seen", "") for k, v in seen.items()}

    keep, drop, rows = [], [], []
    for root, members in clusters.items():
        if len(members) == 1:
            keep.append(members[0])
            continue
        members = sorted(members, key=lambda n: (-ic_of.get(n, 0.0), first_seen.get(n, "")))
        k = members[0]
        keep.append(k)
        drop += members[1:]
        rows.append({"簇": len(members), "留": k, "留|ic|": round(ic_of.get(k, 0), 5),
                     "剔": ",".join(members[1:]),
                     "剔|ic|": ",".join(str(round(ic_of.get(m, 0), 5)) for m in members[1:]),
                     "簇内最大|corr|": round(max(bars[t] for t in bars
                                            if t[0] in members and t[1] in members), 4)})
    print(f"\n===== |corr| >= {EA.RDAGENT_BAR}（RD-Agent 自己那道线）并出的簇 =====")
    print(pd.DataFrame(rows).to_string(index=False) if rows else "（无簇）")
    print(f"\n[刀口] 可译 {len(names)} 条 ⇒ 簇 {len(rows)} 个、拟剔 {len(drop)} 条")
    print(f"       active {len(act)} → {len(act) - len(drop)}")
    print(f"       拟剔名单: {sorted(drop)}")

    # ---- 负对照：每条被剔的都要有一个 ≥0.99 的活口留在库里 ----
    keepset = set(keep)
    orphans = [d for d in drop
               if not any(abs(float(LS.loc[d, k])) >= EA.RDAGENT_BAR for k in keepset)]
    print(f"\n[负对照] 被剔却没有任何活口可代表它的条目 {len(orphans)} 条"
          f"（应为 0）: {orphans}")
    assert not orphans, "有条目被剔后该信息在库里彻底消失，这不是清重复"
    # 正对照：留下的集合里不该再有 ≥0.99 的对
    remain_bad = [(a, b) for a in keep for b in keep
                  if a < b and abs(float(LS.loc[a, b])) >= EA.RDAGENT_BAR]
    print(f"[正对照] 清理后仍留在库里的 ≥{EA.RDAGENT_BAR} 对 {len(remain_bad)} 张（应为 0）"
          f": {remain_bad}")
    assert not remain_bad

    # ---- 波及面：两个消费口子（都按 factor_library.py 的真实取法复刻）----
    # run_live.py:155 先滤掉无 expr 的，再按 |ic| 降序取 10
    def top10_live(drop_set):
        cand = [n for n, f in seen.items()
                if f.get("status") == "active" and (f.get("expr") or "").strip()
                and n not in drop_set]
        return sorted(cand, key=lambda n: -abs(float(seen[n].get("ic") or 0.0)))[:10]

    # multi_source_mining.py:62 _run_genetic 不滤 expr，直接 get_active() 的前 10
    # （get_active 的顺序 = _load_index 按 first_seen 排过的顺序）
    def seeds10(drop_set):
        order = [n for n, f in sorted(seen.items(), key=lambda x: x[1].get("first_seen", ""))
                 if f.get("status") == "active" and n not in drop_set]
        return order[:10]

    nowtop, aftertop = top10_live(set()), top10_live(set(drop))
    print(f"\n[run_live 实盘信号 top10]\n  现状: {nowtop}\n  清理后: {aftertop}")
    print(f"  出: {sorted(set(nowtop) - set(aftertop))} 进: {sorted(set(aftertop) - set(nowtop))}")

    nowseed, afterseed = seeds10(set()), seeds10(set(drop))
    print(f"\n[genetic 源 seeds 前 10（按 first_seen）]\n  现状: {nowseed}\n  清理后: {afterseed}")
    print(f"  出: {sorted(set(nowseed) - set(afterseed))} 进: {sorted(set(afterseed) - set(nowseed))}")

    # ---- 顺带把 0.85 那一档的量读出来，方便对比刀口深浅 ----
    n85 = sum(1 for v in bars.values() if v >= EA.RED_BAR)
    print(f"\n[另一档] |corr| >= {EA.RED_BAR} 的对 {n85} 张、"
          f"|corr| >= {EA.RDAGENT_BAR} 的对 {sum(1 for v in bars.values() if v >= EA.RDAGENT_BAR)} 张")
    det.to_csv(os.path.join(BASE, "etf/v1/temp/snap_ding2_0929/diagonal_remeasure_36.csv"), index=False)
    print(f"\n[输出] etf/v1/temp/snap_ding2_0929/diagonal_remeasure_36.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
