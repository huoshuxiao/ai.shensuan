# -*- coding: utf-8 -*-
"""探针：在库里 7 条「只有名字没有表达式」的因子，环 3 判不到 —— 漏掉的是不是要紧的一对

`run_etf_redundancy_check.py` 待在库侧走 `EA.specs_from_rows(lib)`，而它会
`if not e: continue` 跳过 expr 为空的行 ⇒ `data/library/factor_library.csv` 里 35 条活跃的
7 条（来源 pipeline：`price_position_20` / `momentum_20` / `rsi_14` …）**不参与判重**。

要紧不在于"少比了 7 条"，而在于其中 `price_position_20` 与本轮 Alpha158 的最强候选
`RSV20` 是**仿射等价**：
    RSV20            = (C − min(L,20)) / (max(H,20) − min(L,20))
    price_position_20 = 2·(C − min(L,20)) / (max(H,20) − min(L,20)) − 1  ⇒ RSV20 = (PP20+1)/2
秩相关对仿射变换不变 ⇒ Spearman 恒为 1.000，本该在 `RED_BAR=0.85` 上被判重复。

这里不做任何"我觉得一样"的口头结论：直接用**在库那条自己的实现**
（`common/src/core/factors.price_position`）与**环 1 那条的 DSL 实现**
（`factor_dsl.safe_eval`）在同一批真实 ETF 上算出来，再逐位对表。
只读，不写任何产物。
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")
sys.path.insert(0, SRC)
os.chdir(SRC)

import etf_admission as EA                        # noqa: E402
from factor_dsl import safe_eval                  # noqa: E402
from core.factors import price_position           # noqa: E402  在库那条的实现

RSV20_EXPR = "((close - ts_min(low, 20.0)) / ((ts_max(high, 20.0) - ts_min(low, 20.0)) + 1e-12))"


def main():
    lib = EA.load_active_library()
    rows = pd.read_csv(os.path.join(REPO, "etf", "v1", "data", "library",
                                    "factor_library.csv"))
    act = rows[rows["status"].astype(str).str.lower().isin(["active", "true", "1"])] \
        if "status" in rows.columns else rows
    no_expr = act[act["expr"].isna() | (act["expr"].astype(str).str.strip() == "")]
    print(f"[库] 活跃 {len(act)} 条，其中无表达式 {len(no_expr)} 条 ⇒ 环 3 在库侧只看得到 "
          f"{len(act) - len(no_expr)} 条")
    print("     被跳过的是：" + "、".join(no_expr["name"].tolist()))

    pool = EA.load_pool()
    codes = sorted(pool)[:12]
    per_code = []
    for c in codes:
        df = pool[c]
        env = pd.DataFrame({k: df[k].astype("float64")
                            for k in ("open", "high", "low", "close", "volume", "amount")})
        a = safe_eval(RSV20_EXPR, env)                     # 候选：环 1 的 DSL 口径
        b = price_position(env, 20)                        # 在库：它自己的实现
        m = pd.concat([a.rename("rsv"), b.rename("pp")], axis=1).dropna()
        if len(m) < 60:
            continue
        per_code.append({
            "code": c, "n": len(m),
            "spearman": float(m["rsv"].corr(m["pp"], method="spearman")),
            "pearson": float(m["rsv"].corr(m["pp"])),
            # 仿射断言：RSV20 应精确等于 (PP20+1)/2，只差 1e-12 那个分母保护项
            "max_abs_diff_vs_affine": float((m["rsv"] - (m["pp"] + 1) / 2).abs().max()),
        })
    r = pd.DataFrame(per_code)
    print("\n[逐只对表] RSV20(候选) vs price_position_20(在库)")
    print(r.to_string(index=False))
    print(f"\nSpearman 最小值 {r['spearman'].min():.6f} · Pearson 最小值 "
          f"{r['pearson'].min():.6f} · 仿射差最大值 {r['max_abs_diff_vs_affine'].max():.2e}")
    assert r["spearman"].min() > 0.999, "秩相关没到 1 ⇒ 上面那个仿射推论是错的，别这么说"
    print("\n结论：这两条是同一个因子。RED_BAR=0.85 本该判重，但在库侧缺 expr ⇒ "
          "环 3 看不见这一对，判重表会把 RSV20 说成『可提名』。")


if __name__ == "__main__":
    main()
