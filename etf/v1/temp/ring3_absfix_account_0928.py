# -*- coding: utf-8 -*-
"""环3「|corr| 判重 + 按名字剔自己」这次改动的账单，把两个原因拆开量。

为什么必须拆：09-24 那份归档的库侧只有 **21** 列可译因子，而因子库现在 35 条全部可译。
所以"改后判重变严了"里面混着两件事 ——
    (i) 口径换了（带号最大值 → 绝对值最大值）
    (ii) 对侧变宽了（21 → 35 条在库因子）
混在一起报数就是假账：读者会以为是 (i) 的功劳/代价，实际一部分是 (ii)。

三步：
  [资格] 用**旧规则**（带号 idxmax）在归档矩阵上重算，须与归档 csv 逐位相等。
         这一步不通过就说明本脚本对旧规则的理解是错的，后面所有读数一律不许引用。
  [负对照] 同一批归档矩阵改用**新规则**（|corr|）重算，必须与归档**不等** ——
         否则"改了判据"这件事在这份数据上是恒真的空话（改动根本没牙）。
  [拆账] 31 条候选分别按 旧规则/21 列、新规则/21 列、新规则/35 列 各出一版判决，
         数清哪些行是口径换掉的、哪些行是库扩容换掉的。

只读：两份矩阵与两份 csv 全部从盘上读，一个字节都不写回 results/。
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(ROOT, "etf", "v1", "src")
sys.path.insert(0, SRC)
import etf_admission as EA                                # noqa: E402
from run_etf_redundancy_check import verdict, abs_worst    # noqa: E402  判据单点复用

OLD = os.path.join(ROOT, "etf", "v1", "temp", "archive_ring3fix_0928")
NEW = os.path.join(ROOT, "etf", "v1", "data", "results")


def load(d):
    return (pd.read_csv(os.path.join(d, "etf_redundancy_matrix_cand.csv"), index_col=0),
            pd.read_csv(os.path.join(d, "etf_redundancy_matrix_vs_library.csv"), index_col=0),
            pd.read_csv(os.path.join(d, "etf_redundancy_check.csv")).set_index("name"))


def row_stats(row, signed):
    """按指定规则从一行里挑近亲：signed=True 用带号最大值（旧），False 用 |corr|（新）。"""
    s = row.dropna()
    if s.empty:
        return None, np.nan
    nm = s.idxmax() if signed else s.abs().idxmax()
    return nm, float(s.loc[nm])


def main():
    o_c, o_l, o_csv = load(OLD)
    n_c, n_l, n_csv = load(NEW)
    print("=" * 78)
    print("环3 |corr| 改动账单 · 口径效应与库扩容效应拆开量")
    print("=" * 78)
    print(f"[输入] 归档矩阵 内部{o_c.shape} × 对库{o_l.shape}（库侧 {o_l.shape[1]} 列）")
    print(f"[输入] 现矩阵 内部{n_c.shape} × 对库{n_l.shape}（库侧 {n_l.shape[1]} 列）")

    # ---------- [资格] 旧规则须逐位复现归档 ----------
    dv_t, dv_v, bad = 0, [], []
    for nm in o_l.index:
        twin, val = row_stats(o_l.loc[nm], signed=True)
        if twin != o_csv.loc[nm, "library_twin"]:
            bad.append(nm)
        dv_t = max(dv_t, abs(val - o_csv.loc[nm, "max_vs_library"]))
        dv_v.append((verdict(val), o_csv.loc[nm, "library_verdict"]))
    same_verdict = all(a == b for a, b in dv_v)
    print(f"\n[资格 ①] 旧规则重算 vs 归档：max_vs_library 最大偏差 = {dv_t:.3e}（要求 <1e-9）")
    print(f"[资格 ②] library_twin 不一致的行数 = {len(bad)}"
          + (f" → {bad[:5]}" if bad else "（全 31 行同名）"))
    print(f"[资格 ③] 判决文案不一致的行数 = {sum(a != b for a, b in dv_v)}"
          + ("" if same_verdict else f" → {[x for x in dv_v if x[0] != x[1]][:3]}"))
    qualified = dv_t < 1e-9 and not bad and same_verdict
    print(f"[资格] => {'通过，下面所有读数可引用' if qualified else '不通过，本脚本读数一律作废'}")
    if not qualified:
        return 1

    # ---------- [负对照] 新规则在旧归档上必须读出不一样 ----------
    flip_neg = sum(1 for nm in o_l.index
                   if (abs_worst(o_l.loc[nm])[2] >= EA.RED_BAR)
                   != (row_stats(o_l.loc[nm], signed=True)[1] >= EA.RED_BAR))
    changed_neg = sum(1 for nm in o_l.index
                      if abs_worst(o_l.loc[nm])[0] != row_stats(o_l.loc[nm], signed=True)[0])
    print(f"\n[负对照] 同一批旧矩阵换新规则：近亲换人的行数 = {changed_neg}、"
          f"是否过红线翻面的行数 = {flip_neg}")
    print("          （这两项必须 >0，否则『改了判据』是恒真的空话）")

    # ---------- [拆账] 三版判决对齐着看 ----------
    acct = []
    for nm in n_c.index:
        old_signed = row_stats(o_l.loc[nm], signed=True) if nm in o_l.index else (None, np.nan)
        new21 = abs_worst(o_l.loc[nm].dropna()) if nm in o_l.index else (None, np.nan, np.nan)
        new35 = abs_worst(n_l.loc[nm], exclude=[nm])
        acct.append({"候选": nm,
                     "旧·带号21列": old_signed[1], "旧判决": verdict(old_signed[1]),
                     "新·|corr|21列": new21[2], "判决@口径": verdict(new21[2]),
                     "新·|corr|35列": new35[2], "判决@合计": verdict(new35[2]),
                     "近亲@口径": new21[0] or "", "近亲@合计": new35[0] or ""})
    a = pd.DataFrame(acct)
    a["纯口径换判决"] = a["旧判决"] != a["判决@口径"]
    a["库扩容再换"] = a["判决@口径"] != a["判决@合计"]
    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 20)
    print("\n===== 只有「口径换了」就翻判决的行（库侧同样 21 列，纯效应）=====")
    only = a[a["纯口径换判决"]]
    print(only[["候选", "旧·带号21列", "旧判决", "新·|corr|21列", "判决@口径", "近亲@口径"]]
          .round(4).to_string(index=False) if len(only) else "（无 ⇒ 新规则没有牙）")
    print(f"\n===== 口径没翻、但库从 21 扩到 35 之后才翻的行 = {int(a['库扩容再换'].sum())} =====")
    print(a[a["库扩容再换"]][["候选", "判决@口径", "新·|corr|35列", "判决@合计", "近亲@合计"]]
          .round(4).to_string(index=False) if a["库扩容再换"].any() else "（无）")
    print(f"\n[汇总] 31 条候选：纯口径效应翻判决 {len(only)} 条、"
          f"库扩容再翻 {int(a['库扩容再换'].sum())} 条、"
          f"现归档 vs 旧归档判决不同 {int((a['旧判决'] != a['判决@合计']).sum())} 条")
    print("[对照] 现归档判决分布：")
    print(n_csv["library_verdict"].value_counts().to_string())
    print("[对照] 旧归档判决分布：")
    print(o_csv["library_verdict"].value_counts().to_string())

    # ---------- 反号那一类：改前到底漏了多少 ----------
    has_neg = [nm for nm in o_l.index if (o_l.loc[nm].dropna() <= -EA.RED_BAR).any()]
    print(f"\n[反号漏网] 旧归档矩阵（库侧 {o_l.shape[1]} 列）里存在 <=-{EA.RED_BAR} "
          f"反号近亲的候选 = {len(has_neg)} 条")
    for nm in has_neg:
        s = o_l.loc[nm]
        worst = s[s <= -EA.RED_BAR].abs().idxmax()
        print(f"   {nm:16s} 对库内 {worst:22s} 带号 {s.loc[worst]:+.4f}"
              f"   旧读数的近亲 {o_csv.loc[nm, 'library_twin']:22s}"
              f" {o_csv.loc[nm, 'max_vs_library']:+.4f} ⇒ {o_csv.loc[nm, 'library_verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
