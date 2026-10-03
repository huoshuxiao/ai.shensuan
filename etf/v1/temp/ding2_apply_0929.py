# -*- coding: utf-8 -*-
"""丁2 落地：把 |corr| >= RDAGENT_BAR(0.99) 簇里的重复条目在库中标为 inactive。

刀口不是手打的名单，是从 09-29 那场对角线实测（etf/v1/temp/snap_ding2_0929/
diagonal_remeasure_36.csv）的配对里按同一规则并簇算出来的：
    并簇线 = EA.RDAGENT_BAR（RD-Agent 循环自己 deduplicate_new_factors 的那道 0.99）
    每簇留 = 簇内库自带 |ic| 最大者（同分按 first_seen 早者）
改的是权威口径的**源头** factor_library_index.json；csv/md 是 save_markdown() 重写的导出物，
它会顺带打一个 [factor-lib] 提交（pathspec 只签库三件，不会吞别人的暂存区）。

踩过的一个坑（09-29 第一次落库就写错，所以这条写进脚本来防复发）：`status_reason` 里那句
"|corr|=多少"必须取**这一对（被剔的那条 ↔ 它自己的替身）**的读数，不能取簇内最大值。
`mogp_5` 那簇的 1.0000 来自 `mogp_5`↔`mogp_7`（两条逐字同式），而它的替身是 `mom_20`，
这一对只有 0.9968 —— 拿簇内最大值配替身名就是一句假账。`edge()` 现在要求这一对必须直接存在，
簇靠传递并出来时**当场炸**，不许硬写一个数。

跑之前已快照到 etf/v1/temp/snap_ding2_0929/（回滚 =  cp 回 etf/v1/data/library/ 再 save_markdown）。
"""
import os
import sys
import json

BASE = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(BASE, "etf/v1/src"))
sys.path.insert(0, os.path.join(BASE, "common/src"))
sys.path.insert(0, os.path.join(BASE, "common/src/core"))

import pandas as pd  # noqa: E402
import etf_admission as EA  # noqa: E402

DET = os.path.join(BASE, "etf/v1/temp/snap_ding2_0929/diagonal_remeasure_36.csv")
APPLY = "--apply" in sys.argv


def main():
    det = pd.read_csv(DET)
    pairs = det[det["abs_rank_corr"] >= EA.RDAGENT_BAR][["侧1", "侧2", "abs_rank_corr"]]
    names = sorted(set(pairs["侧1"]) | set(pairs["侧2"]))
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in pairs.itertuples(index=False):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    idx = json.load(open(os.path.join(BASE, "etf/v1/data/library/factor_library_index.json")))
    abs_ic = {k: abs(float(v.get("ic") or 0.0)) for k, v in idx.items()}
    clusters = {}
    for n in names:
        clusters.setdefault(find(n), []).append(n)

    drop, keep_of = [], {}
    for members in clusters.values():
        m = sorted(members, key=lambda n: (-abs_ic.get(n, 0.0),
                                           idx.get(n, {}).get("first_seen", "")))
        for x in m[1:]:
            drop.append(x)
            keep_of[x] = m[0]
    drop = sorted(drop)
    print(f"[刀口] |corr| >= {EA.RDAGENT_BAR} 的配对 {len(pairs)} 张、"
          f"并出簇 {len(clusters)} 个 ⇒ 拟标 inactive {len(drop)} 条")

    # 三道反证式自检：库在实测之后又变过的话，这里必须先炸掉而不是照着旧名单乱标
    assert drop == ['hybrid_2', 'hybrid_5', 'mogp_3', 'mogp_5', 'mogp_6', 'mogp_7',
                    'momentum_20', 'reversal_5', 'volatility_20'], \
        f"拟剔名单与 09-29 实测不符，先重测再动手：{drop}"
    for x, k in keep_of.items():
        prev = idx.get(x, {}).get("status")
        # 只允许两种前置状态：还没清（active）或这场脚本自己清过（幂等重跑改文案）
        assert prev == "active" or str(idx[x].get("status_reason", "")).startswith("丁2 库内判重"), \
            f"{x} 当前状态 {prev!r} 且不是本脚本标的 inactive，库在实测后又变过 ⇒ 先重测"
        assert idx.get(k, {}).get("status") == "active", \
            f"替身 {k} 不在库里活跃，剔 {x} 会丢信息"
        assert abs_ic.get(k, 0) >= abs_ic.get(x, 0), f"{k} 的 |ic| 不比 {x} 高"
    print("[自检] 名单一致 ✅ / 每条拟剔的都是 active 或本脚本清过 ✅ / 每条都留着 |ic| 更高的替身 ✅")

    # 打印与落库都只用**这一对自己**的读数：簇内最大 |corr| 常常来自另一条边
    # （mogp_5 那簇里 1.0000 是 mogp_5↔mogp_7 那条边，mogp_5↔mom_20 只有 0.9968），
    # 把它写在"与 mom_20"后面就是一句假账 —— 09-29 第一次落库就踩了这个坑。
    def edge(a, b):
        hit = pairs[((pairs["侧1"] == a) & (pairs["侧2"] == b)) |
                    ((pairs["侧1"] == b) & (pairs["侧2"] == a))]
        assert len(hit) == 1, f"{a} 与替身 {b} 之间没有直接的 ≥{EA.RDAGENT_BAR} 边，" \
                              f"簇是靠传递并出来的，别硬写读数"
        return float(hit["abs_rank_corr"].iloc[0])

    corr_of = {x: edge(x, k) for x, k in keep_of.items()}
    for x in drop:
        print(f"   - {x:14s} ← 留 {keep_of[x]:24s}（这一对 |corr|={corr_of[x]:.4f}）")

    if not APPLY:
        print("\n[只读] 未加 --apply，一个字节没改")
        return 0

    from factor_library import get_library
    lib = get_library()
    before = len(lib.get_active())
    for x in drop:
        lib.mark_status(
            x, "inactive",
            reason=(f"丁2 库内判重(09-29)：与 {keep_of[x]} 的逐日截面 |Spearman|={corr_of[x]:.4f} "
                    f">= RDAGENT_BAR={EA.RDAGENT_BAR}，同簇留 |ic| 高者；"
                    f"读数 etf/v1/temp/snap_ding2_0929/diagonal_remeasure_36.csv"))
    lib.save_markdown()
    after = len(lib.get_active())
    print(f"\n[已写] active {before} → {after}；"
          f"inactive {sum(1 for f in lib.factors.values() if f.get('status') != 'active')} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
