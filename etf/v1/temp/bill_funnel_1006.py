# -*- coding: utf-8 -*-
"""官方腿因子在链路里的**落点追账**（10-06 用户裁令：④⑤⑦ 同批、升格 P0；只查不改）。

要回答的一句话：那 17 个官方名字，逐个说清它死在哪一道闸上。

⚠️⚠️ **本脚本的「甲/乙」分级结论已作废（10-06 10:2x），不许再当现状引用。**
作废原因：本脚本 `:73` 的 `by_floor` 拿的是**产物层 `factors.json` 的 `mean_ic`** 来判地板甲，
而真跑的那道地板（`multi_source_mining._attach_impl` `:71-101` → `:203-205`）
吃的是**在本池逐标的重算后被就地覆盖的 `mean_ic`**——两者不是同一个数。
`factors.json` 那 17 条只带 7 个不同的自报值（同组逐字相同），它是容器按**实验**摊派出去的，
**从来没进过任何判据**。⇒ 本脚本「甲死几 / 乙死几」这个分布是错的树给的，逐条「死因」不能引用。
正确读数在 `temp/replay_official_funnel_1006.py`（复刻生产函数本身；闸一身份 max|Δ|＝0.000e+00、
闸二计数 11→7、闸三集合逐字相同）：**甲死 4 → 判重死 5 → 聚类死 4 → 交回 4，库层新键 0 条。**
仍然成立、可以引用的只有与复刻不冲突的那几笔：
① 库层本场 10 个新键 `source` 全是 `llm`、official 源 0 条（「H 绿 ≠ 官方腿进库」）；
② 那枚净增名字在 `factors.json` 查得到、在 `factor_library_index.json` 里不是键；
③ 最后一节「⑦ 同名 `ic_history` 间隔 <= 6 秒」的**尺子自证**（错误键名 0／正确键名 390）仍然有效，
   但**按 ≤6 秒数双写这个口径本身是错的**——本场两处写库点间隔 24 秒、按秒数出 0 对，
   差点把「每场都双写」读成「本场没双写」。正确口径见 AGENT.md §8.6：
   **按「同一场同名 ≥2 行」数，不许按秒数。**

三道口径互不兑现的树（AGENT.md §8.6）：
  回收层 = 日志「official 产出 17 / 本场净增 1」
  产物层 = rdagent_output/factors.json（9b 落的档，本场 17 条）
  库层   = factor_library_index.json 的键（本场收在 88）

链路里 official 那条腿实际要过的闸（本脚本按代码逐道复刻，全部只读）：
  甲  common/src/core/multi_source_mining.py:204-205  |mean_ic| >= MULTI_SOURCE.min_ic_per_source
  并  :207 merge_factors（**按名字**在本批内归并，同名只活一条，赢者 = |mean_ic| 最大）
  乙  :209 dedup_factors + :212-214 cluster_factors/dedup_by_cluster（本批内按截面相关）
  库  :220-224 lib.batch_upsert（内部还有静态体检 check_expr，挡下的不念进 +N）
  回  main.py:253 recount_foreign_ic → :312 select_factors（地板乙 0.005）
  再库 main.py:315 stage_library_and_clustering（[4/9] 又 upsert 一遍 + 再聚类一次）

字节证据取自 `run_checkpoint_daily.pkl` 的 `主线多源因子` 条目（`value` = 多源挖掘交回那 7 条本体），
它是生产自己落的盘，不是本脚本重算的。
"""
import json
import os
import pickle
import sys
from datetime import datetime

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
F_JSON = os.path.join(REPO, "etf/v1/data/results/rdagent_output/factors.json")
LIB = os.path.join(REPO, "etf/v1/data/library/factor_library_index.json")
CK = os.path.join(REPO, "common/data/etf/cache/run_checkpoint_daily.pkl")
LOG = os.path.join(REPO, "etf/v1/temp/chain_I_prod_1006.log")
FLOOR = 0.005          # 地板甲 = 地板乙，同值两道（main.py:84 注释里那条性质）
SESSION_DAY = "2026-10-06"


def _sha(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def load():
    official = json.load(open(F_JSON))
    lib = json.load(open(LIB))
    with open(CK, "rb") as f:
        ck = pickle.load(f)["entries"]
    return official, lib, ck


def funnel(official, ck):
    """逐道复刻：返回 {name: 判决}，判决含死因。"""
    names = [f["name"] for f in official]
    by_floor = {f["name"]: abs(f.get("mean_ic", 0.0)) >= FLOOR for f in official}
    passed = [f for f in official if by_floor[f["name"]]]
    got = ck.get("主线多源因子", {}).get("value") or []
    survived = {f["name"] for f in got}
    src_of = {f["name"]: f.get("source") for f in got}
    verdict = {}
    for f in official:
        n = f["name"]
        if not by_floor[n]:
            cause = "甲：|mean_ic| < 0.005（多源内部地板）"
        elif n in survived:
            cause = "活到多源交回（进 batch_upsert）"
        else:
            cause = "乙：本批内判重/聚类被摘（dedup_factors 或 dedup_by_cluster）"
        verdict[n] = dict(mean_ic=f.get("mean_ic"), expr=f.get("expr"),
                          cause=cause, survived=by_floor[n] and n in survived,
                          src_after=src_of.get(n))
    return names, passed, got, survived, verdict


def library_side(lib, survived, names):
    keys = set(lib)
    rows = []
    for n in names:
        rec = lib.get(n, {})
        hist = rec.get("ic_history") or []
        rows.append(dict(
            name=n, in_lib=n in keys,
            src=rec.get("source") or rec.get("extra", {}).get("source"),
            ic=rec.get("ic"), first=rec.get("first_seen"),
            upd=rec.get("update_count"), n_hist=len(hist)))
    new_keys = [k for k, v in lib.items()
                if str(v.get("first_seen", ""))[:10] == SESSION_DAY]
    return rows, new_keys


def ic_history_dups(lib, max_gap=6.0):
    """⑦：同名两条 ic_history 间隔 <= 6 秒的，全部点名 + 时间戳。

    ⚠️ 时间戳的键名是 `time`（`factor_library.upsert` 自己写的），**不是** `date`/`when`/`ts`——
    本函数第一版按后三个键取，结果 88 个键全部取不到戳、"命中 0 对"是**瞎尺子的 0**，
    不是"没有重复"。留这段注释是因为它正是"没跑"与"跑对"给出同一个读数那一类。
    """
    out = []
    for name, rec in lib.items():
        hist = rec.get("ic_history") or []
        stamps = []
        for h in hist:
            ts = h.get("time") or h.get("date") or h.get("when") or h.get("ts")
            if not ts:
                continue
            try:
                stamps.append((datetime.fromisoformat(str(ts)), ts, h))
            except ValueError:
                pass
        stamps.sort(key=lambda x: x[0])
        for a, b in zip(stamps, stamps[1:]):
            gap = (b[0] - a[0]).total_seconds()
            if 0 <= gap <= max_gap:
                out.append((name, gap, a[1], b[1],
                            a[2].get("ic"), b[2].get("ic")))
    return out


def stamp_key_probe(lib):
    """自证「键名取错 ⇒ 恒 0」：同一批数据换三种错键名数到 0，换成 `time` 才数得到。"""
    def _count(keys):
        n = 0
        for rec in lib.values():
            for h in (rec.get("ic_history") or []):
                if any(k in h for k in keys):
                    n += 1
        return n
    return {"错误键名 date/when/ts": _count(("date", "when", "ts")),
            "正确键名 time": _count(("time",))}


def log_readings():
    """日志里那几行计数读数（生产自己念的），拿来和本脚本复刻的结果对表。"""
    want = ("产出 17 个因子", "本场净增", "聚类去重: ", "因子库更新: +",
            "多源挖掘追加", "断点已存 主线多源挖掘", "静态体检挡下")
    hits = []
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            if any(w in line for w in want):
                hits.append((i, line.strip()))
    return hits


def main():
    official, lib, ck = load()
    print("== 输入字节 ==")
    for p in (F_JSON, LIB, CK):
        print(f"  {_sha(p)}  {os.path.getsize(p):>10,} B  {os.path.relpath(p, REPO)}")
    names, passed, got, survived, verdict = funnel(official, ck)
    print(f"\n== 漏斗（官方腿 {len(names)} 个名字）==")
    print(f"  产物层 factors.json 条数        : {len(official)}")
    print(f"  甲（|mean_ic| >= {FLOOR}）后     : {len(passed)}")
    print(f"  多源交回（断点 value 现读）     : {len(got)}  ← 日志念的是 7")
    from collections import Counter
    src_cnt = Counter(f.get("source") for f in got)
    print(f"  交回那 7 条按 source: " + "、".join(f"{k}={v}" for k, v in sorted(src_cnt.items())))
    print("  交回那 7 条逐条（★＝名字就在 factors.json 的 17 个里）：")
    for g in got:
        mark = "★" if g.get("name") in set(names) else "☆"
        print(f"    {mark} {str(g.get('source')):<9} mean_ic={str(g.get('mean_ic'))[:10]:>10}"
              f"  {str(g.get('name'))[:62]}")
    print("\n== 逐个点名 ==")
    rows, new_keys = library_side(lib, survived, names)
    for r in rows:
        v = verdict[r["name"]]
        print(f"  {'活' if v['survived'] else '死'} | 库{'有' if r['in_lib'] else '无'} |"
              f" ic={str(r['ic'])[:9]:>9} | mean_ic={str(v['mean_ic'])[:9]:>9} |"
              f" first={str(r['first'])[:19]} | upd={str(r['upd']):>3} |"
              f" {v['cause']} | {r['name'][:52]}")
    print(f"\n== 库层：本场新键 {len(new_keys)} 个（first_seen 前缀 {SESSION_DAY}）==")
    for k in new_keys:
        rec = lib[k]
        src = rec.get("source") or rec.get("extra", {}).get("source")
        print(f"  source={src:<9} ic={str(rec.get('ic'))[:10]:>10}  {k[:66]}")
    print(f"  其中 official 源: {sum(1 for k in new_keys if (lib[k].get('source') or '') == 'official')}")
    print("\n== ⑦ 同名 ic_history 间隔 <= 6 秒 ==")
    print(f"  尺子自证（取不到戳＝恒 0 的那种 0）：{stamp_key_probe(lib)}")
    dups = ic_history_dups(lib)
    print(f"  命中 {len(dups)} 对")
    same_val = sum(1 for d in dups if d[4] == d[5])
    print(f"  其中两条 ic 逐字相同: {same_val} 对"
          f"（同一次运行里两处 upsert 各写一遍 ⇒ 值不可能不同）")
    for name, gap, t1, t2, ic1, ic2 in dups[:20]:
        print(f"  {gap:5.2f}s | {t1} ic={str(ic1)[:12]} ↔ {t2} ic={str(ic2)[:12]}"
              f" | {name[:52]}")
    # 只数本场（first_seen 或任一条 <= 6s 落在 2026-10-06 当天）
    today = [d for d in dups if str(d[2])[:10] == SESSION_DAY]
    print(f"  其中落在 {SESSION_DAY}（本场）的: {len(today)} 对")
    print("\n== 日志读数（对表用）==")
    for i, line in log_readings():
        print(f"  L{i}: {line[:130]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
