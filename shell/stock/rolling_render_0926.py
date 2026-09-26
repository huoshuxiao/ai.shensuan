# -*- coding: utf-8 -*-
"""看板「截面评估」那一页要真的把「稳不稳」那栏渲染出来（AppTest，不走浏览器）。

判据里有三条是**反证式**的：拿 CSV 里的原值去比对屏上 dataframe 的数值，改一个数就该不过；
所以同时跑一条「人为把某个数挪开再问一遍」的对照，证明比对不是恒真。
"""
import os
import sys

import pandas as pd

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                   "stock", "v1", "src"))
sys.path.insert(0, SRC)
os.chdir(SRC)

from streamlit.testing.v1 import AppTest  # noqa: E402

RESULTS = os.path.join(os.path.dirname(SRC), "data", "results")
CSV = os.path.join(RESULTS, "ashare_rolling_ic.csv")
if not os.path.exists(CSV):
    print(f"❌ 先跑 run_ashare_rolling_ic.py，{CSV} 还不存在 —— 没有产物就没有读数可渲染")
    sys.exit(1)
ref = pd.read_csv(CSV)

at = AppTest.from_file(os.path.join(SRC, "app.py"), default_timeout=240).run()
texts = [getattr(o, "value", "") or "" for o in list(at.caption) + list(at.markdown)
         + list(at.info) + list(at.error) + list(at.warning)]
blob = "\n".join(texts)

FAILS = []


def chk(tag, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + tag + ("　" + detail if detail else ""))
    if not cond:
        FAILS.append(tag)


chk("R1 页面跑完无异常", not at.exception,
    str(at.exception[0].value)[:150] if at.exception else "")
chk("R2 「稳不稳」那栏的标题与三条边界都在屏上",
    "稳不稳" in blob and "同号占比" in blob and "一条判据都没挂" in blob
    and "不是真样本外" in blob and "同号 ≠ 好" in blob)
chk("R2b 高低列的措辞在屏上（只说高低、不说好坏，负 IC 因子不会被读反）",
    "lo_ic_year" in blob and "hi_ic_year" in blob and "不说好坏" in blob)
chk("R3 环1 那格旧文案（护栏位移 ≤7e-9）没被这次改动挤掉",
    "7e-9" in blob and "guard_ret" in blob)

dfs = []
for d in at.dataframe:
    v = d.value
    if isinstance(v, dict):        # AppTest 有时给 dict（列名 → 值），统一成表
        v = pd.DataFrame(v)
    dfs.append(v)
target = None
for v in dfs:
    if not isinstance(v, pd.DataFrame) or v.empty:
        continue
    if {"rank_ic_full", "roll_same_pct", "lo_ic_year", "hi_ic_year"} <= set(v.columns):
        target = v
        break
chk("R4 屏上确实多了一张带稳不稳列的表", target is not None,
    f"共 {len(dfs)} 张 dataframe")
if target is not None:
    _yr = [c for c in ("lo_ic_year", "hi_ic_year") if c in target.columns]
    chk("R4b 两个「年份列」在屏上仍是年份整数（没被当成 IC 摊成小数）",
        len(_yr) == 2 and all(
            ((target[c] > 1990) & (target[c] < 2100) & (target[c] % 1 == 0)).all()
            for c in _yr), "、".join(_yr))

if target is not None:
    # 逐因子比对：屏上那张表的 rank_ic_full / roll_same_pct 必须等于 CSV 原值。
    # 样本不足的那几条两边都是空值，空==空算一致（但要把条数念出来，别让它悄悄成立）。
    import numpy as np

    def cols_match(col, ref_frame):
        a = target[col].to_numpy(dtype="float64")
        b = ref_frame[col].to_numpy(dtype="float64")
        both_nan = np.isnan(a) & np.isnan(b)
        diff_ok = np.abs(a - b) < 1e-12
        bad = ~(both_nan | diff_ok)
        return bad, int(both_nan.sum())

    names_ok = list(target["name"].astype(str)) == list(ref["name"].astype(str))
    bad_r, nan_r = cols_match("rank_ic_full", ref)
    bad_s, nan_s = cols_match("roll_same_pct", ref)
    chk("R5 表里的数值 = CSV 原值（不是另算一份）",
        names_ok and len(bad_r) == len(ref) and not bad_r.any() and not bad_s.any()
        and len(target) == len(ref),
        f"{len(target)} 行 vs {len(ref)} 行；rank 不吻合 {int(bad_r.sum())} 条、"
        f"roll 不吻合 {int(bad_s.sum())} 条；两边同为空 {nan_r} 条（样本不足）"
        if names_ok else "行序/花名与 CSV 对不上")
    # 反证：把 CSV 里一个**非空**的 rank_ic_full 挪 0.01 再比一次，比对必须判不过
    pit = ref.index[ref["rank_ic_full"].notna()]
    if len(pit) == 0:
        chk("R6 反证：改一个数，比对就报警", False, "CSV 里 rank_ic_full 全为空，造不出对照")
    else:
        shifted = ref.copy()
        shifted.loc[pit[0], "rank_ic_full"] += 0.01
        bad_shift, _ = cols_match("rank_ic_full", shifted)
        chk("R6 反证：改一个数（挪 0.01）比对就报警", bool(bad_shift.any()),
            f"改动落在第 {int(pit[0])} 行，报警 {int(bad_shift.sum())} 条")
else:
    chk("R5 表里的数值 = CSV 原值（不是另算一份）", False, "没有可比对的表")
    chk("R6 反证：改一个数，比对就报警", False, "没有可比对的表")

print(f"\n{'❌ ' + str(len(FAILS)) + ' 条不过：' + '；'.join(FAILS) if FAILS else '✅ 全部通过'}")
sys.exit(1 if FAILS else 0)
