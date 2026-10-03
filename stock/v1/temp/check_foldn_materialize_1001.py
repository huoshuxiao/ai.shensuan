#!/usr/bin/env python3.10
"""丙 落盘后的对表（10-01）：一列新增，其余 25 列必须逐字节不动。

面板今天没动（09-30 19:40，中秋休市）⇒ 这一场与下午 13:25 那场的**唯一**允许差异
就是新增的 `fold_n` 列。任何别的字节变化都说明有第二处变了（代码/口径/数据），
当场判红，不许用「差不多」糊过去。

六格：
  T1 表结构 26 → 27 列，新列紧跟 `fold_last`
  T2 `fold_n` 全部为整数且逐行 == 配置折数（只查 status=="ok"，V5 那个错不犯）
  T3 逐因子零复刻：`fold_n` 必须等于当场重切 `_folds()` 的段数
  T4 日频/年频两个产物与备份**逐字节**相同
  T5 汇总表去掉 `fold_n` 后与备份**逐字节**相同（含 float 的字面写法）
  T6 与戊刷过的环 1 归档对表：跨运行差应落到 1e-16 量级（同版面板），
     若还是 1e-4 说明两边吃的不是同一版面板 —— 这是读数，只报不判红
"""
import os
import sys
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, SRC)
RESULTS = os.path.normpath(os.path.join(SRC, "..", "data", "results"))
BACKUP = os.path.join(RESULTS, "backup", "rolling_ic_pre_foldn_1001")

import pandas as pd  # noqa: E402
import _bootstrap  # noqa: E402,F401  必须最先：裸模块名（config / run_ashare_*）靠它挂进 sys.path
from config import ASHARE_ROLL_FOLDS  # noqa: E402
import run_ashare_rolling_ic as R  # noqa: E402

fails, notes = [], []


def chk(ok, label, detail=""):
    print(f"[{'绿' if ok else '红'}] {label}{('  |  ' + detail) if detail else ''}")
    if not ok:
        fails.append(label)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


new_sum = os.path.join(RESULTS, "ashare_rolling_ic.csv")
old_sum = os.path.join(BACKUP, "ashare_rolling_ic.csv")
df = pd.read_csv(new_sum)
old = pd.read_csv(old_sum)

# ---- T1 表结构 ----
cols = list(df.columns)
chk(len(cols) == 27, "T1a 归档 27 列", f"实测 {len(cols)} 列")
chk("fold_n" in cols, "T1b 含 fold_n")
if "fold_n" in cols:
    chk(cols[cols.index("fold_n")] == cols[cols.index("fold_last") + 1],
        "T1c fold_n 紧跟 fold_last（列序没跑偏）",
        f"fold_n@{cols.index('fold_n')}, fold_last@{cols.index('fold_last')}")
chk(len(old.columns) == 26, "T1d 备份仍是 26 列（对照物没被我自己的改动污染）",
    f"备份 {len(old.columns)} 列")

# ---- T2 全行有值、等于配置 ----
ok_df = df[df["status"] == "ok"]
chk(len(ok_df) == 21, "T2a 在库 21 条全 ok", f"实测 {len(ok_df)} 条 / 共 {len(df)} 行")
if "fold_n" in cols:
    vals = sorted(set(ok_df["fold_n"].dropna().astype("int64").tolist()))
    chk(len(ok_df) == len(ok_df["fold_n"].dropna()), "T2b ok 行 fold_n 无一为空")
    chk(vals == [ASHARE_ROLL_FOLDS], "T2c fold_n == 当前配置折数",
        f"归档实切 {vals} / 配置 {ASHARE_ROLL_FOLDS}")
else:
    chk(False, "T2b/T2c fold_n 有值且等于配置", "列还不存在（落盘场没跑成？）")

# ---- T3 零复刻：逐行自己数一遍段数 ----
# 日频表是**宽表**（`date` + 21 列 `rank|名字` + 21 列 `pearson|名字`），不是长表
daily = pd.read_csv(os.path.join(RESULTS, "ashare_ic_daily.csv"))
wide = daily.set_index("date")
by_name = {c[len("rank|"):]: wide[c] for c in wide.columns if c.startswith("rank|")}
chk(len(by_name) == 21, "T3a 日频宽表有 21 条 rank 序列", f"实测 {len(by_name)} 条")
bad = []
for rec in ok_df.itertuples(index=False):
    s = by_name.get(rec.name)
    if s is None:
        bad.append(f"{rec.name}: 日频表里查不到")
        continue
    got = len(R._folds(s.dropna(), R.FOLDS))
    # 零复刻：这一段是**当场重切**出来的段数，不读表里那个数
    if got != ASHARE_ROLL_FOLDS:
        bad.append(f"{rec.name}: 现数 {got} != 配置 {ASHARE_ROLL_FOLDS}")
    if "fold_n" in cols and got != int(rec.fold_n):
        bad.append(f"{rec.name}: 现数 {got} != 表里 {int(rec.fold_n)}")
chk(not bad, "T3 fold_n 由构造复核（零复刻）", "; ".join(bad[:4]) or "21/21 逐条对上")

# ---- T4 另两个产物逐字节 ----
for fname in ("ashare_ic_daily.csv", "ashare_ic_yearly.csv"):
    a, b = os.path.join(RESULTS, fname), os.path.join(BACKUP, fname)
    if not (os.path.exists(a) and os.path.exists(b)):
        chk(False, f"T4 {fname} 逐字节", "文件缺失")
        continue
    same = md5(a) == md5(b)
    chk(same, f"T4 {fname} 逐字节同下午那版",
        f"{md5(a)[:12]} vs {md5(b)[:12]}" if not same else "md5 相同")

# ---- T5 汇总表去掉新列后逐字节 ----
# ⚠️ 必须走 csv 解析：`expr` 列里有带引号的逗号（整行 naive split 会错列，
#    制造出一堆假红）。csv.reader 交出来的就是**落盘的那个字符串本身**，
#    不 parse 成 float ⇒ 字面写法（`-0.015701354716372153` vs `-0.0157`）照样比得出来。
import csv  # noqa: E402


def read_fields(path):
    with open(path, newline="", encoding="utf-8") as f:
        return [row for row in csv.reader(f)]


new_rows, old_rows = read_fields(new_sum), read_fields(old_sum)
hdr = new_rows[0]
if "fold_n" not in hdr:
    chk(False, "T5 其余 25 列逐字节同下午那版", "新表里没有 fold_n，无从去掉")
else:
    i = hdr.index("fold_n")
    stripped = [r[:i] + r[i + 1:] for r in new_rows]
    diffs = []
    if len(stripped) != len(old_rows):
        diffs.append(f"行数 {len(stripped)} vs {len(old_rows)}")
    else:
        for rn, (a, b) in enumerate(zip(stripped, old_rows)):
            if len(a) != len(b):
                diffs.append(f"第 {rn} 行字段数 {len(a)} vs {len(b)}")
                continue
            for ci, (x, y) in enumerate(zip(a, b)):
                if x != y:
                    diffs.append(f"第 {rn} 行 {hdr[min(ci, len(hdr) - 1)]}: {x!r} vs {y!r}")
    chk(not diffs, "T5 其余 25 列逐字节同下午那版（含 float 字面写法）",
        f"{len(diffs)} 处差异" + (" | " + "; ".join(diffs[:3]) if diffs else ""))

# ---- T6 跨运行 vs 环 1（只报读数） ----
r1 = pd.read_csv(os.path.join(RESULTS, "ashare_factor_eval.csv"))
m = df.merge(r1, left_on="name", right_on="name", how="inner")
if len(m):
    gap = (m["rank_ic_full"].astype("float64")
           - m["cs_rank_ic_mean"].astype("float64")).abs().max()
    notes.append(f"T6 读数：稳不稳 rank_ic_full vs 环1(戊 14:19 版) cs_rank_ic_mean "
                 f"max|Δ|={gap:.3e}")
    chk(True, "T6（读数格，见下行）", f"max|Δ|={gap:.3e}；1e-16 量级=两表同版面板")
else:
    notes.append("T6 读数：与环1 没配上名字")

print("\n".join(["", *notes, ""]))


def _mt(path):
    import time as _t
    return _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(os.path.getmtime(path))) \
        if os.path.exists(path) else "<不存在>"


try:
    from run_ashare_factor_eval import H5_PATH as _h5  # noqa: E402
    print(f"面板 mtime: {_mt(_h5)}  ← 只有它没动，T4/T5 的「逐字节相同」才是硬对表")
except Exception as e:
    print(f"面板 mtime: <取不到 H5_PATH: {e}>")
print(f"环1 归档 mtime: {_mt(os.path.join(RESULTS, 'ashare_factor_eval.csv'))}"
      "（戊=10-01 14:19 刷新）")
print(f"本场三产物 mtime: " + " / ".join(
    _mt(os.path.join(RESULTS, f)).split(" ")[1]
    for f in ("ashare_rolling_ic.csv", "ashare_ic_daily.csv", "ashare_ic_yearly.csv")))
print(f"折数配置: ASHARE_ROLL_FOLDS={ASHARE_ROLL_FOLDS}  |  表结构 {len(cols)} 列")
print(f"\n{'EXIT=0 全绿' if not fails else 'EXIT=1 判红: ' + '; '.join(fails)}")
sys.exit(1 if fails else 0)
