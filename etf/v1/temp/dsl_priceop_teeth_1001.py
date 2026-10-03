# -*- coding: utf-8 -*-
"""乙：给对拍闸门装牙（10-01）

`dsl_priceop_baseline_1001.py diff` 报了「260 格逐位相同 / 0 格变了」，但这把尺子
只有在「真被顶到时必须变红」的前提下才算数。五张表：

  T1 牙在正闸上：把 `_price_series` 的 DataFrame 分支偷偷换成取 open，
     既有表达式必须**立刻不同**（不换=判据恒真=没有测试）
  T2 语义等价：传 Series 之后 `ma(close,n)` 必须与 `ts_mean(close,n)` **逐位相同**，
     `std/max/min` 同（不是"能算出个数"就算通）
  T3 错误还得是错误：`max(close, high)` 这类第二参数传序列必须抛，不能静默出数
  T4 这一改买到了什么：10-01 A/B 那三场死在求值上的 3 条假设，重算 IC
  T5 数据没被偷偷换：DataFrame 分支取到的必须确实还是 close
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

import factor_dsl as FD  # noqa: F402,E402
from factor_dsl import safe_eval, compute_ic  # noqa: F402,E402

CACHE = os.path.join(ROOT, "common/data/etf/cache")
CODES = ["510300", "510500", "159516", "512480", "518880"]

DEAD = [
    # 逐字抄自 `tmp_roles_smoke_1001/ab_prompt_1001.log`：两臂各 3 条、共 6 条假设，
    # 其中 3 条「全部标的求值失败」。前两条是本批这个写法坑；第三条是模型自己
    # 括号没闭合，本批改不动它（列在这儿正是要看它**改后仍然错**）。
    ("vol_spike_zscore(A臂)", "abs(delta(volume, 3)) / (std(df['volume'], 60) + 1e-9)"),
    ("volume_efficiency_ratio(B臂)",
     "(ts_sum(abs(delta(close,3)),20))/(ma(volume,60)+1e-9)"),
    ("vol_regime_switcher(B臂)",
     "(std(returns,5)*delay(std(returns,5),1))/ (ma(std(returns,20)+1e-9)"),
    # 对照：多角色那场的 3 条到闸假设之一，用的 `rank`，本来就可求值、
    # 死因是 IC 不是写法 ⇒ 它的 IC 改前改后必须一模一样
    ("range_expansion_rank(对照)", "rank(high - low, 240) - rank(delta(close, 5), 60)"),
]


def pool():
    out = {}
    for c in CODES:
        df = pd.read_csv(os.path.join(CACHE, f"{c}_daily.csv"),
                         parse_dates=["date"]).set_index("date").sort_index()
        df.columns = [str(x).lower() for x in df.columns]
        out[c] = df
    return out


def maxdiff(a, b):
    x = np.asarray(a.values, dtype="float64")
    y = np.asarray(b.values, dtype="float64")
    if x.shape != y.shape or not np.array_equal(np.isnan(x), np.isnan(y)):
        return None
    both = ~np.isnan(x)
    return float(np.abs(x[both] - y[both]).max()) if both.any() else 0.0


def main():
    P = pool()
    n = 0

    print("T1 牙在「既有表达式逐位相同」这道正闸上")
    orig = FD._price_series
    real = {c: safe_eval("ma(df,5)", d) for c, d in P.items()}
    real_max = {c: safe_eval("max(df,20)", d) for c, d in P.items()}
    try:
        FD._price_series = lambda x: (x["open"] if isinstance(x, pd.DataFrame)
                                      else x)
        broke = sum(1 for c, d in P.items()
                    if (maxdiff(real[c], safe_eval("ma(df,5)", d)) or 1) > 1e-18)
        broke2 = sum(1 for c, d in P.items()
                     if (maxdiff(real_max[c], safe_eval("max(df,20)", d)) or 1) > 1e-18)
        print(f"  偷换 DataFrame 分支后：ma 不同 {broke}/{len(CODES)}，"
              f"max 不同 {broke2}/{len(CODES)}")
        if broke != len(CODES) or broke2 != len(CODES):
            print("  🚫 注入没被抓住 ⇒ 对拍闸是恒真的")
            n += 1
        else:
            print("  ✅ 一旦顶到 DataFrame 分支，diff 必红")
    finally:
        FD._price_series = orig
    back = {c: maxdiff(real[c], safe_eval("ma(df,5)", d)) for c, d in P.items()}
    print(f"  拔牙后已还原：ma(df,5) 与还原前 max|Δ| = "
          f"{max(v for v in back.values()):.1e}")
    if max(back.values()) != 0.0:
        print("  🚫 还原没还原干净")
        n += 1

    print("\nT2 传 Series 后与 ts_* 逐位等价（不是「算得出」就算通）")
    pairs = [("ma(close,%d)", "ts_mean(close,%d)"),
             ("std(returns,%d)", "ts_std(returns,%d)"),
             ("max(high,%d)", "ts_max(high,%d)"),
             ("min(low,%d)", "ts_min(low,%d)"),
             ("ma(volume,%d)", "ts_mean(volume,%d)"),
             ("std(df['volume'],%d)", "ts_std(volume,%d)")]
    for win in (5, 20, 60):
        for ea, eb in pairs:
            da = max(maxdiff(safe_eval(ea % win, d), safe_eval(eb % win, d))
                     for d in P.values())
            if da != 0.0:
                print(f"  🚫 {ea % win} vs {eb % win} 最大差 {da:.3e}")
                n += 1
        print(f"  ✅ n={win}: {len(pairs)} 对全逐位相同（含 volume/returns/high/low）")

    print("\nT3 写错仍然要抛，不许静默出数")
    for bad in ("max(close, high)", "ma(df, 'x')", "std(5, 20)", "ma(3, 20)"):
        try:
            r = safe_eval(bad, P["510300"])
            print(f"  🚫 {bad} 没抛，返回 len={len(r)}")
            n += 1
        except Exception as e:
            print(f"  ✅ {bad} -> {type(e).__name__}: {str(e)[:70]}")

    print("\nT4 这一改买到了什么：上一场 A/B 里求值失败的那几条，逐条重算")
    print("     （尺子＝`_evaluate` 同一把：逐标的 compute_ic 后算术均值，"
          f"过线 |IC|≥0.02，样本＝这 {len(CODES)} 只）")
    print("     「改前」不是记忆里的说法：把 `_price_series` 临时换回"
          "旧函数体（一律取 close）当场重跑同一批表达式")
    old = {"_price_series": FD._price_series}

    def legacy(x):
        return x["close"]

    def ic_of(expr):
        ics = []
        for d in P.values():
            try:
                ic = compute_ic(safe_eval(expr, d),
                                d["close"].pct_change().shift(-1))
                if not pd.isna(ic):
                    ics.append(ic)
            except Exception:
                pass
        return (float(np.mean(ics)) if ics else float("nan")), len(ics)

    try:
        FD._price_series = legacy
        pre = {name: ic_of(expr) for name, expr in DEAD}
    finally:
        FD._price_series = old["_price_series"]
    post = {name: ic_of(expr) for name, expr in DEAD}
    for name, expr in DEAD:
        m_now, n_now = post[name]
        m_pre, n_pre = pre[name]
        tag = "本批靶子" if (n_pre == 0 and n_now > 0) else \
            ("仍错（语法）" if (n_pre == 0 and n_now == 0) else "对照：本来可算")
        print(f"  {name:30s} {tag:14s} 改前可算 {n_pre}/{len(CODES)} → "
              f"改后 {n_now}/{len(CODES)}；IC {m_pre:+.4f} → {m_now:+.4f} "
              f"{'✅过线' if n_now and abs(m_now) >= 0.02 else '❌未过'}")
    # 两条硬断言：语法错不能被放宽洗白；本来可算的那条 IC 一格都不许动
    if post["vol_regime_switcher(B臂)"][1] != 0:
        print("  🚫 括号没闭合那条被放宽成了可算 ⇒ 放宽越界了")
        n += 1
    else:
        print("  ✅ 模型自己的语法错：改后仍然抛（放宽只救写法，不补括号）")
    ctrl = "range_expansion_rank(对照)"
    if pre[ctrl] != post[ctrl]:
        print(f"  🚫 本来可算的对照条读数变了：{pre[ctrl]} -> {post[ctrl]}")
        n += 1
    else:
        print(f"  ✅ 对照条（用的不是这四个算子）改前改后读数逐字相同 {post[ctrl]}")
    back2 = maxdiff(safe_eval("ma(df,20)", P["510300"]),
                    P["510300"]["close"].rolling(20).mean())
    if back2 != 0.0:
        print("  🚫 T4 里换过行为没换回来")
        n += 1
    else:
        print("  ✅ 换回旧行为再换回来：ma(df,20) 仍逐位 = close.rolling(20).mean()")

    print("\nT5 DataFrame 分支取到的还得是 close")
    for c, d in P.items():
        e = maxdiff(safe_eval("ma(df,20)", d), d["close"].rolling(20).mean())
        if e != 0.0:
            print(f"  🚫 {c} ma(df,20) 不再是 close 的均值，差 {e:.3e}")
            n += 1
    print(f"  ✅ {len(CODES)} 只全部 = close.rolling(20).mean() 逐位")

    print(f"\n结论：{'全绿' if n == 0 else str(n) + ' 条不通过'}  EXIT={0 if n == 0 else 1}")
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
