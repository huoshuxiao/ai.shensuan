# -*- coding: utf-8 -*-
"""裁令④⑤：官方腿那 17 个名字，到底死在哪一步 —— 逐函数复刻生产漏斗（只读，不写任何生产产物）

为什么要复刻而不是读日志：日志只念「产出 17」和「聚类去重 11 → 7」，中间那一步死了谁、
死时的 IC 是多少，一个字节都没落盘。前一轮（`temp/bill_funnel_1006.py`）我拿
`rdagent_output/factors.json` 的 `mean_ic` 去判地板，**判错了树**，现把错处写死在这里：
factors.json 里 17 个不同名字只带 7 个不同的 `mean_ic`（同组逐字相同，如 0.0044074×4），
那是容器按**实验**摊派的自报值；而 `multi_source_mining._attach_impl`（:71-101）会把
`mean_ic` **就地重算成本池逐标的 IC 的均值**再交出去，地板甲（:203-205）吃的是重算值。
⇒ 自报值从来不是判据的输入，拿它分类得到的「死在甲」全都不成立。

复刻口径：不重写公式，直接 `import` 生产那几个函数本体来调
  `_attach_impl` → `merge_factors` → `dedup_factors` → `cluster_factors` → `dedup_by_cluster`
（`common/src/core/multi_source_mining.py:203-218` 的顺序），调用的入参也照生产：
  - 官方 17 条：factors.json 的 name/expr/formulation（`_run_official` 给 `_attach_impl` 的就是这个形状）
  - llm 3 条：**直接取生产断点里已经交回的那 3 个字典**（含逐标的 `impl`），不重算 llm
  - 池：`get_universe()` + `DataLoader(FREQ).load_pool(codes)`，与 `main.py:684-689` 同一条路

三道闸（任何一道不过 ⇒ 本场复刻作废，只报闸门读数、不报结论）：
  闸一 身份：断点里 4 个官方幸存者的 mean_ic，复刻值与生产值最大绝对差要 < 1e-12
              （面板若在 07:46 之后动过，这里就会露出来）
  闸二 计数：日志 07:46:12 那行「聚类去重: 11 → 7」的两个端点都是生产打过的数，
              且那行是 `dedup_by_cluster` 内部打的 ⇒ 左端 11 ＝ `dedup_factors` 之后、
              右端 7 ＝ 聚类之后。复刻必须在这两处分别落在 11 与 7。
              （并组前那一步生产没有打印 ⇒ 没有可比读数，不拿它当闸。本闸第一版就是
              栽在这里：我误把 11 当成"甲后官方+llm"，红了的是尺子不是漏斗，详见 [6] 后。）
  闸三 集合：复刻最后交回的名字集合，必须与断点那 7 个逐字相同

跑法：`timeout 1800 python3 -u etf/v1/temp/replay_official_funnel_1006.py`
"""
import collections
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")
sys.path.insert(0, SRC)
os.chdir(SRC)

import numpy as np                                          # noqa: E402

import _bootstrap                                           # noqa: E402,F401
from config import MULTI_SOURCE, FREQ                       # noqa: E402
from etf_universe import get_universe                       # noqa: E402
from data_loader import DataLoader                           # noqa: E402
from multi_source_mining import (_attach_impl, merge_factors,
                                 dedup_factors)              # noqa: E402
from factor_clustering import cluster_factors, dedup_by_cluster  # noqa: E402

FACTORS_JSON = os.path.join(REPO, "etf", "v1", "data", "results",
                            "rdagent_output", "factors.json")
CK_PKL = os.path.join(REPO, "common", "data", "etf", "cache",
                      "run_checkpoint_daily.pkl")
LIB_JSON = os.path.join(REPO, "etf", "v1", "data", "library",
                        "factor_library_index.json")
CK_KEY = "主线多源因子"   # 现读断点键名（日志里那句「断点已存 主线多源挖掘」用的是 label，不是键）


def sha(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()[:16], os.path.getsize(p)


def main():
    print("== 输入字节 ==")
    for p in (FACTORS_JSON, CK_PKL, LIB_JSON):
        s, n = sha(p)
        print(f"  {s}  {n:>13,} B  {os.path.relpath(p, REPO)}")

    # ---- 1. 断点：生产那一场真正交回的 7 条（含 llm 的 impl，逐字拿来用）
    with open(CK_PKL, "rb") as f:
        ck = pickle.load(f)
    ent = ck["entries"].get(CK_KEY)
    if not ent:
        print(f"❌ 断点里没有 {CK_KEY}，本场无从复刻（现有条目：{list(ck['entries'])}）")
        return 1
    returned = ent["value"]
    off_ret = {d["name"]: d for d in returned if d.get("source") == "official"}
    llm_ret = [d for d in returned if d.get("source") != "official"]
    print(f"\n断点交回 {len(returned)} 条：official {len(off_ret)}、非 official {len(llm_ret)}")
    print(f"  非 official 的名字（本场不重算，直接当输入）: "
          f"{[d['name'] for d in llm_ret]}")

    # ---- 2. 池（与 main.py 同一条路）
    print("\n[1] 建池……")
    universe = get_universe()
    codes = universe.universe["code"].tolist()
    pool = DataLoader(freq=FREQ).load_pool(codes)
    print(f"  池: {len(pool)} 个标的，最长 {max(len(d) for d in pool.values())} 行")

    # ---- 3. 复刻地板甲的输入：_attach_impl 重算逐标的 IC
    raw = json.load(open(FACTORS_JSON, encoding="utf-8"))
    print(f"\n[2] _attach_impl 重算 {len(raw)} 条官方候选（factors.json 的自报值不参与）")
    replayed = _attach_impl(raw, pool, "official")
    print(f"  求得出值的: {len(replayed)}  求值失败被 _attach_impl 丢掉的: "
          f"{len(raw) - len(replayed)}")
    rmap = {d["name"]: d for d in replayed}

    # ---- ④ 的字面问题：同名同式的到底有几组、谁活下来
    #      `merge_factors` 只按 name 并组（:137-139），**同式不同名它看不见**，
    #      那种只有 dedup_factors 的相关闸管得到（阈值见 [5]）
    by_expr = {}
    for d in replayed:
        by_expr.setdefault(d["expr"], []).append(d["name"])
    same_expr = {e: ns for e, ns in by_expr.items() if len(ns) > 1}
    same_name = len(raw) - len({r["name"] for r in raw})
    print(f"  ④ 字面口径：factors.json 里同名重复 {same_name} 组、"
          f"同式不同名 {len(same_expr)} 组")
    for e, ns in same_expr.items():
        print(f"    同式 {len(ns)} 个｜expr 全文＝{e}\n                名字＝{ns}")

    # ---- 闸一：身份
    print("\n== 闸一 身份（复刻值 vs 断点里生产值）==")
    diffs = []
    for name, d in off_ret.items():
        r = rmap.get(name)
        if r is None:
            print(f"  ❌ {name}：断点有、复刻没有")
            continue
        dv = abs(float(r["mean_ic"]) - float(d["mean_ic"]))
        diffs.append(dv)
        print(f"  {'✅' if dv < 1e-12 else '❌'} |Δ|={dv:.3e}  "
              f"复刻={r['mean_ic']:.12g} 生产={d['mean_ic']:.12g}  {name}")
    gate1 = bool(diffs) and max(diffs) < 1e-12
    print(f"  闸一：{'过' if gate1 else '不过'}（{len(diffs)} 点，max|Δ|="
          f"{max(diffs) if diffs else float('nan'):.3e}）")

    # ---- 4. 地板甲
    min_ic = MULTI_SOURCE["min_ic_per_source"]
    passed = [d for d in replayed if abs(d.get("mean_ic", 0)) >= min_ic]
    print(f"\n[3] 地板甲 |mean_ic| >= {min_ic}：{len(replayed)} → {len(passed)}")
    for d in sorted(replayed, key=lambda x: abs(x.get("mean_ic", 0))):
        if abs(d.get("mean_ic", 0)) < min_ic:
            print(f"    死@甲 |ic|={abs(d['mean_ic']):.6f}  {d['name']}")

    # ---- 5. merge → dedup → cluster（顺序照 :207-218）
    #      生产 all_factors 的顺序＝as_completed 顺序：llm 先(05:09)、official 后(07:45)
    all_f = llm_ret + passed
    merged = merge_factors(all_f)
    print(f"\n[4] merge_factors（按名字并组）: {len(all_f)} → {len(merged)}")
    deduped = dedup_factors(merged, pool)
    #   这里一律按 id() 比，不用 `in`：这些字典里挂着 pandas Series，
    #   dict == 会去比 Series 并抛 "truth value is ambiguous"——判据自己先崩
    kept_id = {id(d) for d in deduped}
    dead_dedup = [d["name"] for d in merged if id(d) not in kept_id]
    print(f"[5] dedup_factors（同名/同式与逐标的相关 >"
          f"{MULTI_SOURCE['corr_dedup_threshold']}）: {len(merged)} → {len(deduped)}")
    for n in dead_dedup:
        print(f"    死@判重: {n}")
    clu = cluster_factors(deduped, pool)
    after = dedup_by_cluster(deduped, clu) if clu else deduped
    print(f"[6] cluster_factors + dedup_by_cluster: {len(deduped)} → {len(after)}")
    after_id = {id(d) for d in after}
    for d in deduped:
        if id(d) not in after_id:
            print(f"    死@聚类: {d['name']}")

    # ---- ④ 的落点：每一个死掉的名字，是不是被"同式的另一个名字"顶替掉了
    #      expr 表要连 llm 那 3 条一起建：`after` 里既有官方幸存者也有 llm 幸存者，
    #      只按 replayed 建表会在 llm 名字上 KeyError（第一版就崩在这）
    expr_of = {d["name"]: d.get("expr", "") for d in replayed + llm_ret}
    survivors = {d["name"] for d in after}
    twin = {s: expr_of.get(s, "") for s in survivors}
    print("\n  ④ 逐条死者 ⇒ 有没有同式的幸存者顶替：")
    n_twin = 0
    for d in replayed:
        if d["name"] in survivors:
            continue
        twins = ([s for s, e in twin.items()
                  if e and e == d["expr"] and s != d["name"]] if d["expr"] else [])
        if twins:
            n_twin += 1
        print(f"    {'被同式顶替 ' + str(twins) if twins else '无同式幸存者（是相关/聚类层面被归并，或 IC 不够）'}"
              f" | {d['name']}")
    n_dead = sum(1 for d in replayed if d["name"] not in survivors)
    print(f"  ④ 合计：{n_dead} 条官方候选没交回（17 减官方幸存者 "
          f"{len(replayed) - n_dead}；survivors 里另有 llm {len(survivors) - (len(replayed) - n_dead)} 条不算），"
          f"其中 {n_twin} 条是「同式不同名」被顶替（其余为地板/相关闸/聚类）")

    # ---- 闸二：计数（判的是生产真打过的那两个数）
    #   ⚠️ 这条闸我第一版把预测写错了，错过一次，错处留在这里当说明：
    #   原按「日志 07:46:12 那行 11 ＝ 甲后官方 + llm 3」来判，实测甲后 13、并组 16，
    #   当时打了「不过」。回头看 `factor_clustering.dedup_by_cluster`（:116-121）——
    #   「聚类去重: 11 → 7」是**聚类那一步**里打的，11 是 `dedup_factors` 之后的条数，
    #   不是并组前的条数。⇒ 生产可观测的只有 11→7 这一对；并组 16 那一步生产压根没打印，
    #   拿一个生产没打的数去当闸，红了的是我的尺子，不是漏斗。
    gate2 = len(deduped) == 11 and len(after) == 7
    print(f"\n== 闸二 计数：判重后 {len(deduped)}（日志那行的左端 11）、"
          f"聚类后 {len(after)}（右端 7）⇒ {'过' if gate2 else '不过'}")

    # ---- 闸三：集合
    got = {d["name"] for d in after}
    gate3 = got == set(d["name"] for d in returned)
    print(f"\n== 闸三 集合：复刻交回 {len(got)} 个名字，断点 {len(returned)} 个 ⇒ "
          f"{'逐字相同' if gate3 else '不同'}")
    if not gate3:
        print(f"  只复刻里有: {sorted(got - {d['name'] for d in returned})}")
        print(f"  只断点里有: {sorted({d['name'] for d in returned} - got)}")

    print(f"\n== 三道闸总结：{'复刻成立' if (gate1 and gate2 and gate3) else '复刻不成立，下面这些数只能当线索、不能当结论'}")
    print(f"  闸一(身份 1e-12)={'过' if gate1 else '不过'} "
          f"闸二(计数=11)={'过' if gate2 else '不过'} "
          f"闸三(集合相等)={'过' if gate3 else '不过'}")

    # ---- 净增那枚（⑤）到底死在哪
    net_new = "1-day SMA of Price over 30-day MIN of Price"
    print(f"\n== ⑤ 本场净增那枚 {net_new}")
    r = rmap.get(net_new)
    if r is None:
        print("  复刻里连值都没求出 ⇒ 死在 _attach_impl 求值（DSL 在本池求不出来）")
    else:
        stage = ("甲（地板）" if abs(r["mean_ic"]) < min_ic else
                 "判重（dedup_factors）" if r["name"] in dead_dedup else
                 "聚类（dedup_by_cluster）" if id(r) not in after_id else
                 "活到交回")
        print(f"  重算 mean_ic={r['mean_ic']:.12g}（factors.json 自报值="
              f"{[x.get('mean_ic') for x in raw if x['name'] == net_new][0]:.12g}）"
              f" ⇒ 死在 {stage}")

    # ---- ⑦ 两处写库点：本场（2026-10-06）落在 ic_history 上的行
    print("\n== ⑦ 本场 ic_history 逐行（两处写库点：multi_source_mining.py:220-224 "
          "与 main.py:329-337）==")
    lib = json.load(open(LIB_JSON, encoding="utf-8"))
    rows = []
    for name, rec in lib.items():
        for h in (rec.get("ic_history") or []):
            t = str(h.get("time", ""))
            if t.startswith("2026-10-06"):
                rows.append((t, name, h.get("ic"), rec.get("source")))
    rows.sort()
    print(f"  本场 ic_history 行数: {len(rows)}  涉及名字: {len(set(r[1] for r in rows))}")
    prev = {}
    dup6 = 0
    for t, name, ic, src in rows:
        gap = None
        if name in prev:
            gap = (np.datetime64(t) - np.datetime64(prev[name])) / np.timedelta64(1, "s")
            if 0 <= gap <= 6:
                dup6 += 1
        print(f"    {t} | gap={'' if gap is None else f'{gap:.0f}s'} | "
              f"ic={ic} | src={src} | {name}")
        prev[name] = t
    twice = sum(1 for _n, c in collections.Counter(r[1] for r in rows).items() if c >= 2)
    print(f"  本场同名写了两遍（不论间隔）的名字: {twice} 个；其中间隔 <= 6 秒的: {dup6} 对")
    print("  ⚠️ 口径更正：`temp/bill_funnel_1006.py` 那版只按「间隔 <= 6 秒」数，本场数到 0 对，"
          "看着像「本场没双写」。真机制是同一场里两处写库点各写一遍（07:46:12 与 07:46:36），"
          "间隔由这两步之间跑了多少活决定 ⇒ 6 秒只是 09-25/10-03/10-05 那几场的偶然值，"
          "拿观测窗口当判据，本场 7 个名字照样各写了两遍、只是隔 24 秒。")

    # ---- ④ 的库层一侧：这一串漏斗（甲/并组/判重/聚类）全程只比**本批内部**，
    #      一次都没拿 expr 去比库 ⇒ 同式不同名在库里会长期并存。数一下并存成什么样。
    print("\n== ④ 库层：幸存者/死者的 expr 与库里其它名字逐字相同有几条 ==")
    expr2lib = {}
    for name, rec in lib.items():
        e = (rec.get("expr") or "").strip()
        if e:
            expr2lib.setdefault(e, []).append(name)
    n_share = 0
    for name in sorted(survivors):
        e = expr_of.get(name, "").strip()
        if not e:
            print(f"    （{name} 没有 expr 可比，llm 源的式子不落这里）")
            continue
        others = [x for x in expr2lib.get(e, []) if x != name]
        if others:
            n_share += 1
            print(f"    同式并存 | 幸存者 {name} ⇔ 库里 {others}")
    print(f"  本场 7 个幸存者里，与库里其它名字逐字同式的: {n_share} 个"
          f"（判据：expr 字符串逐字相等；这条漏斗没有任何一步比过库）")

    # ---- ④ 最尖的一刀：`factor_library.upsert` 的「已存在」分支（:62-73）
    #      只改 ic/icir/last_seen/status/update_count/extra，**从不改 expr**，
    #      而 batch_upsert（:85-95）与 main.py:329-337 两个调用点的 extra 都只带 source ⇒
    #      同一个名字第二次入库时换了式子，库里留的就是「第一次那版式子 + 这一场的 IC」。
    print("\n== ④ 库里存量 expr 是否还是本场这一版（同名换式＝旧式配新 IC）==")
    cur_expr = {r["name"]: (r.get("expr") or "").strip() for r in raw}
    n_stale = 0
    for name in sorted(cur_expr):
        rec = lib.get(name)
        if not rec:
            continue
        stored = (rec.get("expr") or "").strip()
        if stored != cur_expr[name]:
            n_stale += 1
            print(f"    ❌ 不一致 | {name}\n         库里 expr={stored}\n"
                  f"         本场 expr={cur_expr[name]}\n"
                  f"         库里 ic={rec.get('ic')} last_seen={rec.get('last_seen')} "
                  f"update_count={rec.get('update_count')}")
    print(f"  17 条里在库且有名字的 {len([n for n in cur_expr if n in lib])} 条，"
          f"其中库里式子与本场式子不一致: {n_stale} 条"
          f"{'（⇒ 「旧式＋新 IC」确实发生在生产，不是推演）' if n_stale else '（本场没撞上）'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
