"""RD-Agent 那 6 条（official/llm）的拔管对照 —— 全程只读，不写 etf/v1/data 一个字节。

问题：把它们从因子库拔掉，环2 的年化 43.42% / 超基准 31.09% 与环3 的判重读数各掉多少？

做法（环3 的「候选 × 在库」全矩阵 09-27 已落盘 ⇒ 无需重跑容器求值）：
  [资格] 用全 21 列重算 max/twin/verdict，须与归档逐位相等，否则本脚本任何读数都不许引用；
  [正对照] 拔掉 source ∈ {official, llm} 的列，重算，看三列变不变；
  [负对照] 拔掉真当过 twin 的列（hybrid_9/mom_5/gp_0/mogp_1），重算 —— 这一步必须动，
           否则「正对照 = 零变化」可能是恒真断言（本仓反复踩过的那类假账）。
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(".")            # 必须在 chdir 之前算，否则相对路径全错位
SRC = os.path.abspath("etf/v1/src")
sys.path.insert(0, SRC)
os.chdir(SRC)
from etf_admission import FAMILY_SPECS, spec_name            # noqa: E402
from run_etf_redundancy_check import verdict, abs_worst      # noqa: E402  判据单点复用

RES = os.path.join(ROOT, "etf/v1/data/results")
LIB_CSV = os.path.join(ROOT, "etf/v1/data/library/factor_library.csv")
MATRIX = os.path.join(RES, "etf_redundancy_matrix_vs_library.csv")
ARCHIVE = os.path.join(RES, "etf_redundancy_check.csv")

mat = pd.read_csv(MATRIX).set_index("Unnamed: 0")
arch = pd.read_csv(ARCHIVE).set_index("name")
lib = pd.read_csv(LIB_CSV)
src_of = dict(zip(lib.name, lib.source))
status_of = dict(zip(lib.name, lib.status))

cols = list(mat.columns)
in_pool = {c: src_of.get(c, "?") for c in cols}
rd_cols = [c for c in cols if in_pool[c] in ("official", "llm")]
rd_lib = lib[lib.source.isin(["official", "llm"])]

print("=" * 96)
print(f"[输入] 矩阵 {mat.shape[0]} 候选 × {mat.shape[1]} 在库列；"
      f"因子库 {len(lib)} 条，其中 RD-Agent 系 {len(rd_lib)} 条")
print(f"[输入] 对手池里 RD-Agent 系的列: {rd_cols or '一条都没有'}")
print(f"[输入] 对手池来源分布: {pd.Series(list(in_pool.values())).value_counts().to_dict()}")

# ---------- [资格] 全列重算必须逐位复现归档 ----------
def recompute(drop):
    """走生产同一套判据：09-28 起环 3 按 **|corr|** 挑近亲，且对侧按名字剔掉"它自己"。

    ⚠️ 这里原来自己抄了一遍带符号 `idxmax()` —— 判据单点在环 3 入口，探针抄一份就是
    第二个真相源；那次口径一改这份账单就会假红。改成 import 同一个 `abs_worst`。
    """
    keep = [c for c in cols if c not in drop]
    sub = mat[keep]
    out = {}
    for nm in mat.index:
        best, _signed, val = abs_worst(sub.loc[nm], exclude=[nm])
        out[nm] = (val, best or "", verdict(val))
    return out

base = recompute([])
dv = max(abs(base[n][0] - arch.loc[n, "max_vs_library"]) for n in mat.index
         if np.isfinite(base[n][0]))
twin_bad = [n for n in mat.index if base[n][1] != arch.loc[n, "library_twin"]]
verdict_bad = [n for n in mat.index if base[n][2] != arch.loc[n, "library_verdict"]]
print("=" * 96)
print(f"[资格 ①] max_vs_library 与归档最大偏差 = {dv:.3e}（要求 <1e-9）")
print(f"[资格 ②] library_twin 不一致的行数 = {len(twin_bad)}"
      + (f" → {twin_bad[:5]}" if twin_bad else "（全 31 行同名）"))
print(f"[资格 ③] library_verdict 文案不一致的行数 = {len(verdict_bad)}"
      + (f" → {verdict_bad[:5]}" if verdict_bad else "（全 31 行同判）"))
qualified = dv < 1e-9 and not twin_bad and not verdict_bad
print(f"[资格] 逐位复现 = {qualified}"
      + ("" if qualified else "  → 不合格，后面一律不引用"))
if not qualified:
    raise SystemExit(1)

# ---------- [正对照] 拔掉 RD-Agent 那 6 条 ----------
after_rd = recompute(rd_cols)
changed = [n for n in mat.index
           if after_rd[n][1] != base[n][1] or abs(after_rd[n][0] - base[n][0]) > 1e-12]
print("=" * 96)
print(f"[正对照] 拔掉 {len(rd_cols)} 列（{rd_cols}）→ 三列有变化的候选行数 = {len(changed)} / {len(mat.index)}")
print(f"[正对照] 拔管前后 max_vs_library 最大变化 = "
      f"{max((abs(after_rd[n][0] - base[n][0]) for n in mat.index), default=0.0):.3f}")

# ---------- [负对照] 拔掉真当过 twin 的列（这一步必须动） ----------
twins = sorted({t for t in (base[n][1] for n in mat.index) if t})
strong = [t for t in twins
          if any(base[n][1] == t and base[n][0] >= 0.85 for n in mat.index)]
after_neg = recompute(strong)
neg_changed = [n for n in mat.index
               if after_neg[n][1] != base[n][1] or abs(after_neg[n][0] - base[n][0]) > 1e-12]
print("=" * 96)
print(f"[负对照] 拔掉 {len(strong)} 列（{strong}，全是当过 ≥0.85 twin 的）")
print(f"[负对照] 三列有变化的候选行数 = {len(neg_changed)} / {len(mat.index)}"
      f"；max_vs_library 平均下降 "
      f"{np.mean([base[n][0] - after_neg[n][0] for n in mat.index]):.4f}")
for n in neg_changed[:4]:
    print(f"           {n:18s} {base[n][0]:+.4f}/{base[n][1]:<9s} → "
          f"{after_neg[n][0]:+.4f}/{after_neg[n][1]:<9s}")
print(f"[负对照] 牙齿 = {bool(neg_changed)}（必须为 True，否则正对照那个 0 不作数）")

# ---------- 附加读数：6 条逐条，以及在现池里的同构造替身 ----------
norm = lambda s: "".join(str(s).split()).replace(" ", "").lower()
pool_expr = {spec_name(s): norm(s["expr"]) for s in FAMILY_SPECS}
rows = []
for r in rd_lib.itertuples():
    twin = [nm for nm, e in pool_expr.items() if e == norm(r.expr)]
    rows.append({"name": r.name, "status": r.status, "ic": r.ic, "icir": r.icir,
                 "在环3对手池": r.name in cols,
                 "环1现池同构造替身": "/".join(twin) or "（无）",
                 "expr": r.expr})
out = pd.DataFrame(rows)
print("=" * 96)
print("[读数] RD-Agent 系 6 条逐条：")
print(out.to_string(index=False))
out_path = os.path.join(ROOT, "shell/etf/ablation_rdagent_0928.csv")
out.to_csv(out_path, index=False)
print(f"\n[输出] {out_path}   （etf/v1/data 与 library 未写入任何字节）")
