# -*- coding: utf-8 -*-
"""给在库 7 条「只有名字没有表达式」的内置因子补 DSL 表达式，并逐位对表

为什么要补：`etf/v1/data/library/factor_library.csv` 里 35 条活跃因子有 7 条 `expr` 为空，
后果有两处（都不是理论风险，是代码里已经写死的取舍）：
1. 环 3 判重的在库侧走 `EA.specs_from_rows(lib)`，它会跳过空 expr ⇒ 只比得到 28 条。
   实测已经撞上：Alpha158 最强候选 `RSV20` 与在库 `price_position_20` 仿射等价
   （12 只 ETF 上 Spearman/Pearson 均 1.000000），本该被 `RED_BAR=0.85` 判重，
   却因为这一格空白而会被说成"可提名"。
2. `run_live.build_factor_signal_fn` 取"有 expr 的活跃因子"按 |IC| 排前 10 当实盘信号，
   空 expr 的 7 条根本进不了候选池 ⇒ 补完会**改变影子盘的因子构成**（见脚本末尾账单）。

表达式一律**从 `common/src/core/factors.py` 的实现倒推**，不按名字猜。唯一一处代数改写是
`rsi_14`：原式 `(100 − 100/(1+RS) − 50)/50` 恒等于 `1 − 2/(1+RS)`，这里用后者是因为 DSL 没有
`clip`，得用 `np.maximum` 拼 gain/loss，少写一层 100 的往返。

判据：每条、每只标的都要求 ①NaN 位置完全一致 ②最大绝对差 ≤ 1e-9（相对差另报）。
只读，不改任何库文件。补完之后再跑 `a158_lib_blindspot_0926.py` 与环 3 复核。
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
from core.factors import FACTOR_REGISTRY          # noqa: E402  权威 pandas 实现

# 名字 -> 拟写入的 DSL 表达式
EXPR = {
    "momentum_20":       "(close / delay(close, 20.0) - 1)",
    "momentum_10":       "(close / delay(close, 10.0) - 1)",
    "reversal_5":        "(1 - close / delay(close, 5.0))",
    "volatility_20":     "(-ts_std(returns, 20.0))",
    "rsi_14":            ("(1 - 2 / (1 + ts_mean(np.maximum(delta(close, 1.0), 0.0), 14.0)"
                          " / (ts_mean(np.maximum(-delta(close, 1.0), 0.0), 14.0) + 1e-9)))"),
    "volume_ratio_20":   "(volume / ts_mean(volume, 20.0))",
    "price_position_20": ("(2 * (close - ts_min(low, 20.0))"
                          " / (ts_max(high, 20.0) - ts_min(low, 20.0) + 1e-9) - 1)"),
}


def main():
    pool = EA.load_pool()
    print(f"[池] {len(pool)} 只，逐只全量对表\n")
    summary = []
    for name, expr in EXPR.items():
        fn = FACTOR_REGISTRY[name]
        worst_abs = 0.0
        worst_rel = 0.0
        nan_mismatch = 0
        n_rows = 0
        n_codes = 0
        for code, df in sorted(pool.items()):
            env = pd.DataFrame({k: df[k].astype("float64")
                                for k in ("open", "high", "low", "close",
                                          "volume", "amount")})
            a = safe_eval(expr, env)                 # 拟写入的 DSL 口径
            b = fn(env)                              # 在库那条的 pandas 原实现
            same_nan = bool((a.isna() != b.isna()).any())
            nan_mismatch += int(same_nan)
            m = pd.concat([a.rename("dsl"), b.rename("pandas")], axis=1).dropna()
            if len(m) == 0:
                continue
            n_codes += 1
            n_rows += len(m)
            diff = (m["dsl"] - m["pandas"]).abs()
            worst_abs = max(worst_abs, float(diff.max()))
            scale = m["pandas"].abs().clip(lower=1e-12)
            worst_rel = max(worst_rel, float((diff / scale).max()))
        summary.append({"name": name, "n_codes": n_codes, "n_points": n_rows,
                        "nan_mask_diff_codes": nan_mismatch,
                        "max_abs_diff": worst_abs, "max_rel_diff": worst_rel})
        print(f"  {name:18s} 对表 {n_codes} 只 / {n_rows:,} 点 · "
              f"NaN 位置不一致 {nan_mismatch} 只 · "
              f"最大绝对差 {worst_abs:.3e} · 最大相对差 {worst_rel:.3e}")
    s = pd.DataFrame(summary)
    bad_nan = s[s.nan_mask_diff_codes > 0]
    bad_val = s[s.max_abs_diff > 1e-9]
    print()
    if bad_nan.empty and bad_val.empty:
        print("✅ 7 条全部逐位对上（NaN 位置也一致）⇒ 补进因子库不会改变任何一条的取值，"
              "只是让它们在环 3 与影子盘里「可见」")
    else:
        print("❌ 有对不上的，先别写库：")
        if not bad_nan.empty:
            print("   NaN 位置不一致：", bad_nan[["name", "nan_mask_diff_codes"]].to_dict("records"))
        if not bad_val.empty:
            print("   数值差 >1e-9：", bad_val[["name", "max_abs_diff"]].to_dict("records"))
        sys.exit(1)

    # 影子盘账单：补完 expr 之后 run_live 的前 10 名会换成谁
    lib = EA.load_active_library(verbose=False)
    before = [r["name"] for r in lib if (r.get("expr") or "").strip()]
    rows = pd.read_csv(os.path.join(REPO, "etf", "v1", "data", "library",
                                    "factor_library.csv"))
    act = rows[rows["status"].astype(str).str.lower() == "active"]
    def top10(names):
        sub = act[act["name"].isin(names)]
        return list(sub.assign(a=sub["ic"].abs()).sort_values("a", ascending=False)["name"][:10])
    after_names = before + list(EXPR)
    tb, ta = top10(before), top10(after_names)
    print("\n[影子盘构成] run_live 取「有 expr 的活跃因子」按 |IC| 前 10：")
    print(f"  补之前: {tb}")
    print(f"  补之后: {ta}")
    print(f"  新挤进来的: {set(ta) - set(tb)}  被挤出去的: {set(tb) - set(ta)}")


if __name__ == "__main__":
    main()
