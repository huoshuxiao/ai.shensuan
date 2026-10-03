# -*- coding: utf-8 -*-
"""10-01 一次性冒烟：归档新加的 `fold_n` 那一列，是不是真的能抓住「口径与数字不同版」。

六格判据（任一红 ⇒ exit=1）：
P1 归档里 `fold_n` 这一列**存在**、21 行全是整数、且等于**当前进程配置**的折数（同版）。
P2 零复刻复核：拿入口自己的 `_folds()`（`run_ashare_rolling_ic.py:98`）对落盘的逐日序列
   现切一次，段数必须逐条等于归档写的 `fold_n` —— 这一格证明那列不是抄来的常数。
P3 整页 0 exception（看板新增的那段警告逻辑不会把页面搞崩）。
P4 **同版时不许报警**：默认档起 AppTest，全页 `st.warning` 里不得出现「归档与配置不同版」。
   （另一半才是牙，见 P5；只验"该响就响"抓不出恒真断言。）
P5 **尺子的牙**：同一把 needle 去量 `STOCK_ROLL_FOLDS=26` 那一臂（配置拨一档、归档没重跑），
   警告必须出现、且写的是「按 [25] 段切的 ... 当前配置是 26 段」。由本文件自己 fork 一个
   带 env 的子进程跑，不手写行形。
P6 注入对照：把归档那一列在内存里改成 9（只有 1 行），P2 的构造必须**恰好抓到这一行**；
   抓不到 ⇒ P2 是恒绿装饰。
"""
import os
import re
import subprocess
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, f"{ROOT}/stock/v1/src")
import _bootstrap  # noqa: F401
import pandas as pd
import run_ashare_rolling_ic as R
from config import ASHARE_ROLLING_OUT, ASHARE_ROLLING_DAILY

WARN_KEY = "归档与配置不同版"
FAIL = []


def seg_counts(daily, names, n_folds):
    """零复刻：段数直接调入口自己的 _folds()，不另写一套 array_split"""
    out = {}
    for n in names:
        s = daily[f"rank|{n}"].dropna()
        out[n] = len(R._folds(s, n_folds))
    return out


def arm_mismatch():
    """子进程臂：配置拨到 26（归档仍是 25 那版）⇒ 看板必须报警"""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(f"{ROOT}/stock/v1/src/app.py", default_timeout=600)
    at.run()
    if at.exception:
        print(f"[作废] 异档臂页面抛异常：{[e.value for e in at.exception][:2]}")
        return 2
    hits = [w.value for w in at.warning if WARN_KEY in w.value]
    print(f"[子臂] 进程内折数={R.FOLDS}｜命中「{WARN_KEY}」的 st.warning {len(hits)} 条")
    if hits:
        print("         原文：" + hits[0].replace("\n", " ")[:220])
    return 0 if hits else 1


if "--arm-mismatch" in sys.argv:
    sys.exit(arm_mismatch())

arc = pd.read_csv(ASHARE_ROLLING_OUT)
daily = pd.read_csv(ASHARE_ROLLING_DAILY, parse_dates=["date"]).set_index("date")
names = [n for n in arc["name"] if f"rank|{n}" in daily.columns]

# ---------- P1 列在不在、是不是整数、与当前配置同不同版 ----------
if "fold_n" not in arc.columns:
    FAIL.append("P1 归档没有 `fold_n` 列 ⇒ 这一列没落盘（入口改了但没重跑？）")
else:
    # 只看**出了读数的那些行**：`stability_stats()` 在「样本不足」那一支根本不返回 fold_n，
    # 把「21 行全都要有值」写成断言 = 又把一份数据快照钉进判据（V5 那个错不能再犯一次）
    ok_arc = arc[arc["status"] == "ok"]
    col = ok_arc["fold_n"]
    are_int = bool((col.dropna() % 1 == 0).all()) and int(col.notna().sum()) == len(ok_arc)
    uniq = sorted(set(col.dropna().astype(int)))
    print(f"  归档 shape={arc.shape}｜status=ok {len(ok_arc)}/{len(arc)} 条｜`fold_n` 取值={uniq}"
          f"｜整数且无缺失={are_int}｜进程配置={R.FOLDS}")
    if not (are_int and uniq == [R.FOLDS]):
        FAIL.append(f"P1 出了读数的 {len(ok_arc)} 行里 `fold_n` 应全是整数且等于当前配置 {R.FOLDS}，"
                    f"实得 {uniq}（整数={are_int}）")
    else:
        print(f"  ✅ [P1] `fold_n` 在列、{len(ok_arc)} 行全整数、与配置同版（={R.FOLDS}）")

# ---------- P2 零复刻：真段数 == 归档写的段数 ----------
if len(names) != len(arc):
    FAIL.append(f"P2 逐日宽表取不到全部因子列：{len(names)}/{len(arc)}")
else:
    want = seg_counts(daily, names, R.FOLDS)
    got = dict(zip(arc["name"], arc.get("fold_n", pd.Series(dtype="float64"))))
    bad = {n: (got.get(n), want[n]) for n in names if got.get(n) != want[n]}
    print(f"  现切段数（入口 `_folds`）={sorted(set(want.values()))}｜{len(names)} 条")
    if bad:
        FAIL.append(f"P2 归档写的 `fold_n` 与现切段数不符：{list(bad.items())[:3]}")
    else:
        print(f"  ✅ [P2] 21 条逐条相等 ⇒ 那列是真的数出来的，不是抄配置值")

# ---------- P3/P4 整页 + 同版不许报警 ----------
from streamlit.testing.v1 import AppTest
at = AppTest.from_file(f"{ROOT}/stock/v1/src/app.py", default_timeout=600)
at.run()
if at.exception:
    FAIL.append(f"P3 页面抛异常 {len(at.exception)} 条：{[e.value for e in at.exception][:2]}")
else:
    print(f"  ✅ [P3] 整页零异常（{len(at.tabs)} 个 tab 全过 script runner）")
warns = [w.value for w in at.warning if WARN_KEY in w.value]
if warns:
    FAIL.append(f"P4 归档与配置**同版**（`fold_n`={R.FOLDS}）却报了警：{warns[0][:120]}")
else:
    print(f"  ✅ [P4] 同版不报警（全页 st.warning {len(at.warning)} 条，无一命中「{WARN_KEY}」）")

# ---------- P5 异档臂必须报警 ----------
env = dict(os.environ, STOCK_ROLL_FOLDS="26")
r = subprocess.run([sys.executable, os.path.abspath(__file__), "--arm-mismatch"],
                   env=env, capture_output=True, text=True)
print("  " + (r.stdout + r.stderr).strip().replace("\n", "\n  "))
if r.returncode == 0 and "折数=26" in r.stdout:
    print("  ✅ [P5] 折数拨一档（26）⇒ 看板当场报警，且报的是当次配置的数")
else:
    FAIL.append(f"P5 负对照没响：子进程 exit={r.returncode}、stdout 见上 ⇒ 那段警告是恒不触发"
                "（配置拨了也不会被人知道，`fold_n` 这列白加）")

# ---------- P6 注入对照：改坏一行的 fold_n，P2 那把尺子必须抓到 ----------
dirty = arc.copy()
victim = dirty["name"].iloc[3]
dirty.loc[3, "fold_n"] = 9
want2 = seg_counts(daily, [victim], R.FOLDS)
caught = int(dirty.loc[3, "fold_n"]) != want2[victim]
if caught:
    print(f"  ✅ [P6] 注入对照：把 `{victim}` 的 `fold_n` 改成 9（现切 {want2[victim]}）⇒ P2 的构造抓到")
else:
    FAIL.append("P6 注入 9 之后 P2 的构造仍然放行 ⇒ 那格是恒真断言，没有牙")

print("\n" + "=" * 78)
if FAIL:
    for f in FAIL:
        print(f"❌ {f}")
    print(f"结论：{len(FAIL)} 格红")
    sys.exit(1)
print("结论：P1~P6 全绿 ⇒ `fold_n` 这一列落盘了、数是真切的、同版不吵、拨一档就报警")
