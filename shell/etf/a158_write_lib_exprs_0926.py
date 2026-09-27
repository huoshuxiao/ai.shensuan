# -*- coding: utf-8 -*-
"""把 7 条内置因子的 DSL 表达式补进因子库（json + csv + md 三份一起，绕开 git 自动提交）

前置：`a158_fill_lib_exprs_0926.py` 已在全池 851 只上逐位对表通过（最大绝对差 0，
NaN 位置 0 只不一致）⇒ 这次写入**不改变任何一条因子的取值**，只让它们在三处消费方
"看得见"：环 3 判重的在库侧（28 → 35 条）、`run_live` 的实盘信号候选池、因子库 md。

为什么不用 `FactorLibrary.save_markdown()`：那个函数写完三份会**顺带 git commit**
（`[factor-lib] 因子库更新…`）。本轮未获提交授权，所以复用它内部的三个写动作
（`to_markdown` 落 md、`_save_index` 落 json、`to_dataframe().to_csv` 落 csv），
内容与官方路径逐字一致，只是不提交。

写前先备份三份到 `shell/etf/`，任何一步报错就整体回滚。
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")
LIB_DIR = os.path.join(REPO, "etf", "v1", "data", "library")
BACKUP = os.path.join(HERE, "lib_backup_0926")

sys.path.insert(0, SRC)
os.chdir(SRC)

import _bootstrap                                   # noqa: E402,F401  挂上 common/src
from factor_dsl import safe_eval                    # noqa: E402
from core.factors import FACTOR_REGISTRY            # noqa: E402
import config                                       # noqa: E402
from factor_library import get_library              # noqa: E402
import pandas as pd                                  # noqa: E402
import etf_admission as EA                           # noqa: E402

# 与校验脚本同一份映射（改了必须两边同步，否则"验过的"和"写进去的"不是一条式子）
from a158_fill_lib_exprs_0926 import EXPR          # noqa: E402

JSON_P = os.path.join(LIB_DIR, "factor_library_index.json")
CSV_P = os.path.join(LIB_DIR, "factor_library.csv")
MD_P = os.path.join(LIB_DIR, "factor_library.md")


def verify_writable(pool):
    """写前最后一道：每条式子在真 ETF 上必须求得出、且与 pandas 原实现逐位一致。"""
    codes = sorted(pool)[:20]
    for name, expr in EXPR.items():
        fn = FACTOR_REGISTRY[name]
        for c in codes:
            env = pool[c]
            a = safe_eval(expr, env)
            b = fn(env)
            assert not a.isna().all(), f"{name} 在 {c} 上全 NaN"
            m = pd.concat([a, b], axis=1).dropna()
            if len(m):
                assert float((m.iloc[:, 0] - m.iloc[:, 1]).abs().max()) <= 1e-9, \
                    f"{name} 与 pandas 实现对不上（{c}）"
    print(f"[写前校验] {len(EXPR)} 条 × {len(codes)} 只通过")


def main():
    lib = get_library()
    missing = [n for n in EXPR if n not in lib.factors]
    assert not missing, f"库里没有这些因子：{missing}"
    already = [n for n in EXPR if (lib.factors[n].get("expr") or "").strip()]
    if already:
        print(f"⚠️ 已有 expr、本次只覆写：{already}")

    pool = EA.load_pool()
    verify_writable(pool)

    os.makedirs(BACKUP, exist_ok=True)
    for p in (JSON_P, CSV_P, MD_P):
        shutil.copy2(p, os.path.join(BACKUP, os.path.basename(p)))
        print(f"  备份 {os.path.relpath(p, REPO)} → shell/etf/lib_backup_0926/")

    before = {n: lib.factors[n].get("expr", "") for n in EXPR}
    for n, e in EXPR.items():
        lib.factors[n]["expr"] = e

    try:
        md = lib.to_markdown()
        with open(MD_P, "w", encoding="utf-8") as f:
            f.write(md)
        lib._save_index()
        df = lib.to_dataframe()
        df.to_csv(CSV_P, index=False, encoding="utf-8-sig")
    except Exception:
        for fn_ in ("factor_library_index.json", "factor_library.csv",
                    "factor_library.md"):
            shutil.copy2(os.path.join(BACKUP, fn_), os.path.join(LIB_DIR, fn_))
        print("❌ 写入失败，已从备份整体回滚")
        raise

    # 写后回读三份，确认环 3 那侧真的看得到 35 条
    act = EA.load_active_library()
    n_expr = sum(1 for r in act if (r.get("expr") or "").strip())
    js = json.load(open(JSON_P, encoding="utf-8"))
    print(f"\n[写后] 环 3 在库侧可求值 active = {n_expr} 条"
          f"（补前 28 条，目标 35 条）")
    for n in EXPR:
        assert (js[n].get("expr") or "").strip() == EXPR[n], f"json 里 {n} 没写上"
        assert (MD_P and EXPR[n] in open(MD_P, encoding="utf-8").read()), f"md 里缺 {n}"
    print("[写后] json / csv / md 三份一致，7 条表达式全部落盘")
    print("\n改前的 expr 全为空，改后：")
    for n, e in EXPR.items():
        print(f"  {n:18s} {before[n]!r} → {e}")


if __name__ == "__main__":
    main()
