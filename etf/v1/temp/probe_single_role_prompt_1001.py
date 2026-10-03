# -*- coding: utf-8 -*-
"""丁：单角色（闸关那一臂）的提示词为什么拿不到 IC —— 四张只读探针（10-01）

不调 LLM、不落生产产物。四张表都指向"上游"而不是"没人挑刺"：
  P1 算子覆盖：执行环境真有的算子 vs 提示词点名的算子 ⇒ 漏报的算子模型**表达不出来**
  P2 频率措辞：提示词说的数据频率 vs 这五只 CSV 的真实 bar 间隔
  P3 示例即模板：提示词给的 example 与内置模板是否逐字相同（= 示范模型去抄最弱那条）
  P4 水位：把 冒烟那 3 条 / 8 条模板 / 在库 35 条可译表达式，放到**同一把尺子
     （`_evaluate` 的逐标的时序 compute_ic）+ 同一批 5 只标的**上重算 |IC| 分布
     ⇒ 0.0103 到底是多少水位的读数（口径：一行内全是这 5 只，不混在库 100 只口径）
"""
import json
import os
import re
import sys

import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

import factor_dsl as FD                      # noqa: F402,E402
import llm_factor_agent as LA                # noqa: F402,E402

CACHE = os.path.join(ROOT, "common/data/etf/cache")
CODES = ["510300", "510500", "159516", "512480", "518880"]

SMOKE3 = [("momentum_vol_ratio", "ts_mean(returns, 5) / (std(df, 60) + 1e-9)"),
          ("volume_price_divergence",
           "(delta(close, 3) - ts_mean(delta(close, 60), 5)) / (abs(volume) + 1e-9)"),
          ("range_expansion_rank",
           "rank(high - low, 240) - rank(delta(close, 5), 60)")]


def load_pool():
    pool = {}
    for c in CODES:
        p = os.path.join(CACHE, f"{c}_daily.csv")
        df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
        df.columns = [str(x).lower() for x in df.columns]
        pool[c] = df
    return pool


def mean_abs_ic(pool, expr):
    """与 `LLMFactorAgent._evaluate` 同一把尺子：逐标的 compute_ic 后取算术均值。"""
    import numpy as np
    from factor_dsl import compute_ic, safe_eval
    ics = []
    for df in pool.values():
        try:
            f = safe_eval(expr, df)
            ic = compute_ic(f, df["close"].pct_change().shift(-1))
            if not pd.isna(ic):
                ics.append(ic)
        except Exception:
            continue
    if not ics:
        return None, 0
    return float(np.mean(ics)), len(ics)


def main():
    sp = LA.SYSTEM_PROMPT
    print("=" * 70)
    print("P1 算子覆盖：执行环境 vs 提示词点名")
    cols = {"close", "open", "high", "low", "volume", "returns"}
    real = set(FD.FACTOR_DSL) - cols
    listed = set(re.findall(r"\b([a-z_]+)\(", sp)) & real
    missing = sorted(real - listed)
    print(f"  执行环境可用算子 {len(real)} 个；提示词点名 {len(listed)} 个")
    print(f"  ❌ 提示词没告诉模型的 {len(missing)} 个：{missing}")

    print("=" * 70)
    print("P2 频率措辞")
    pool = load_pool()
    any_df = pool[CODES[0]]
    gaps = any_df.index.to_series().diff().dropna().dt.days
    print(f"  提示词第 20 行原文：{sp.splitlines()[0]!r}")
    print(f"  真实数据（{CODES[0]}，{len(any_df)} 根）相邻 bar 间隔：众数 "
          f"{int(gaps.mode()[0])} 天、中位数 {gaps.median():.0f} 天 ⇒ **日线**")

    print("=" * 70)
    print("P3 example 与内置模板是否同一条")
    ex = re.search(r'例如: "([^"]+)"', sp)
    tpl = {t["name"]: t["expr"] for t in LA.LLMFactorAgent._fallback(
        object.__new__(LA.LLMFactorAgent), 99)}
    hit = [n for n, e in tpl.items() if ex and e == ex.group(1)]
    print(f"  提示词 example = {ex.group(1) if ex else '<没找到>'!r}")
    print(f"  与模板逐字相同的条目：{hit or '无'}")

    print("=" * 70)
    print("P4 同一把尺子、同这 5 只：|IC| 水位")
    lib = json.load(open(os.path.join(
        ROOT, "etf/v1/data/library/factor_library_index.json")))
    rows = []
    for name, expr in SMOKE3:
        rows.append(("甲臂 LLM(旧 prompt)", name, expr))
    for n, e in tpl.items():
        rows.append(("内置模板", n, e))
    for k, v in sorted(lib.items()):
        e = v.get("expr") if isinstance(v, dict) else None
        if e:
            rows.append(("在库因子", k, e))
    out = []
    for grp, name, expr in rows:
        ic, n = mean_abs_ic(pool, expr)
        out.append({"组": grp, "名字": name, "|IC|": None if ic is None else round(abs(ic), 4),
                    "可算标的数": n, "expr": expr[:60]})
        print(f"  {grp:16s} {name:28s} |IC|="
              f"{'求值失败' if ic is None else f'{abs(ic):.4f}'}  ({n}/5)")
    df = pd.DataFrame(out)
    df.to_csv(os.path.join(ROOT, "etf/v1/temp/tmp_roles_smoke_1001/"
                           "probe_single_role_prompt_1001.csv"), index=False)
    print("\n  分组水位（同一把尺子、同这 5 只）：")
    for g, sub in df.groupby("组"):
        v = pd.to_numeric(sub["|IC|"], errors="coerce").dropna()
        print(f"    {g:16s} n={len(v):2d}  中位 {v.median():.4f}  最大 {v.max():.4f}"
              f"  ≥0.02 的条数 {(v >= 0.02).sum()}")
    print("\n探针表已落盘: etf/v1/temp/tmp_roles_smoke_1001/"
          "probe_single_role_prompt_1001.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
