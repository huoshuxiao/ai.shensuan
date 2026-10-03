# -*- coding: utf-8 -*-
"""10-01 读数：生产因子库里有多少条**只在放宽后才算得出**（= 这一改已被库依赖）。

问的是「撤销的代价」：`ma/std/max/min` 放宽（乙）之后，LLM 自然会写传列形式，
新入库的因子就会用上它。要撤销这一改，不能只看老表达式有没有变，还得数清
**当前库里已经有多少条会当场变成不可求值**。

做法：同一进程里两臂 —— 新臂用现文件，旧臂把 `factor_dsl._price_series`
monkeypatch 回旧函数体（`lambda x: x["close"]`）。逐条 × 池内每只，任一只能算出即「可求值」。
只用 monkeypatch，不碰生产文件（10-01 教训：那是两条线共用的 import 目标）。
"""
import os
import sys

import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

import factor_dsl as FD  # noqa: F402,E402
from hypothesis_roles import check_expr  # noqa: F402,E402

CACHE = os.path.join(ROOT, "common/data/etf/cache")
# 与日更链的折内池不是一回事：这里只用本机缓存的真日线，够判断「求值得出/求值不出」
CODES = [f[:6] for f in sorted(os.listdir(CACHE)) if f.endswith("_daily.csv")]


def load_pool():
    pool = {}
    for c in CODES:
        df = pd.read_csv(os.path.join(CACHE, f"{c}_daily.csv"),
                         parse_dates=["date"]).set_index("date").sort_index()
        df.columns = [str(x).lower() for x in df.columns]
        pool[c] = df
    return pool


def computable(expr, pool):
    for df in pool.values():
        try:
            FD.safe_eval(expr, df)
            return True
        except Exception:
            continue
    return False


def main():
    pool = load_pool()
    d = pd.read_csv(os.path.join(ROOT, "etf/v1/data/library/factor_library.csv"))
    orig = FD._price_series
    dep, both_err, la = [], [], []
    for _, r in d.iterrows():
        e = str(r.get("expr") or "")
        if not e or e == "nan":
            continue
        why = check_expr(e)
        if why:
            la.append((r["name"], r["status"], why, e[:70]))
        new_ok = computable(e, pool)
        FD._price_series = lambda x: x["close"]      # 旧臂：只认 DataFrame
        try:
            old_ok = computable(e, pool)
        finally:
            FD._price_series = orig
        if new_ok and not old_ok:
            dep.append((r["name"], r["status"], float(r.get("ic") or 0.0), e[:70]))
        if not new_ok and not old_ok:
            both_err.append((r["name"], e[:70]))
    print(f"# 池={len(pool)} 只缓存日线｜在库可核 {len(d) - int((d.expr.isna()).sum())} 条")
    print(f"  只在放宽后才算得出（撤销即变不可求值）= {len(dep)} 条")
    for x in dep:
        print(f"    {x}")
    print(f"  两臂都算不出（与这一改无关）= {len(both_err)} 条")
    for x in both_err:
        print(f"    {x}")
    print(f"  被静态闸 check_expr 判红（含未来函数）= {len(la)} 条")
    for x in la:
        print(f"    {x}")
    # 牙：撤销旧臂必须是「真旧」——把这一步的读数与老文件对上；
    # 若旧臂也全都算得出，说明 monkeypatch 没生效，上面的计数是假的。
    demo = "ma(volume, 20)"
    FD._price_series = lambda x: x["close"]
    old_demo = computable(demo, pool)
    FD._price_series = orig
    new_demo = computable(demo, pool)
    if old_demo or not new_demo:
        print("  🚫 monkeypatch 没生效（旧臂可算 或 新臂不可算）⇒ 依赖计数不可信")
        return 1
    print(f"  ✅ 牙：`{demo}` 旧臂不可算 / 新臂可算 ⇒ monkeypatch 有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
