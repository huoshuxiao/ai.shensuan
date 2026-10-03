# -*- coding: utf-8 -*-
"""P0 落盘自检（09-24 首版 / 09-27 修）：加行不许改行，且两张归档表自报口径

**原判据**：本次给 `run_ashare_portfolio_eval.py` 加了 allow 旋钮与一条新送验表达式
（`ts_mean(volume,20)`），若旧行的任一数值发生位移，说明并集实测动了原判据
（那是最坏的一种假结论来源）。逐元素比，差值按 float64 严格 0 判。

⚠️ 09-27 回归时发现两件事，都在这里改掉：
1. 对照侧 `ashare_portfolio_eval.csv.bak-0924p0` 已不在仓库（当时是 /tmp 里的一次性
   备份、没随同步入库）⇒ 那半条判据**历史性地不可复验**了。缺文件时以前直接 traceback
   崩掉，现在改成明说的 `[跳过]`，并且**不许把它算成通过**（打印里带 ⚠️）。
   备份在的时候照旧逐元素比，一比出位移就非零退出。
2. 这脚本原来**任何情况都 exit 0**（连崩在 FileNotFoundError 都只是「报了」）——
   打印给人看，退出码才是给链路用的，尾部补了判据。

后半段是仍然可复验的那部分：减法腿与名单腿两张归档表必须自报口径列（`gate`、
`list_scheme`），且值要和 ⑳/㉘ 归档的那一档对得上，否则引用数字前没人会想起先对档。
"""
import os
import sys

import pandas as pd

R = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results"
BAK = f"{R}/ashare_portfolio_eval.csv.bak-0924p0"
FAIL = []
SKIP = []

new = pd.read_csv(f"{R}/ashare_portfolio_eval.csv")
k = lambda d: d["signal"] + "|" + d["top_n"].astype(str)

# ---------- ① 加行不改行（对照侧文件在库里才判） ----------
if os.path.exists(BAK):
    old = pd.read_csv(BAK)
    ok, nk = k(old), k(new)
    print(f"旧表 {old.shape}  新表 {new.shape}  新增行 {sorted(set(nk) - set(ok))}")
    a = old.set_index(pd.Index(k(old), name="row")).sort_index()
    b = new.set_index(pd.Index(k(new), name="row")).sort_index()
    b = b.loc[a.index]                  # 只取旧表已有的那 36 行，按同一顺序
    num = [c for c in a.columns if a[c].dtype != object]
    d = (a[num] - b[num]).abs()
    print(f"旧 {len(a)} 行 × {len(num)} 数值列：最大绝对差 {float(d.values.max()):g}")
    print("逐字相同的列 ", sorted(c for c in num if float(d[c].max()) == 0))
    moved = sorted(c for c in num if float(d[c].max()) > 0)
    print("有位移的列   ", moved)
    if moved:
        FAIL.append(f"旧行数值有位移：{moved}")
else:
    print(f"[跳过 ⚠️ 不是通过] 对照侧不在库里：{os.path.basename(BAK)}")
    SKIP.append("① 加行不改行（09-24 那次备份未入库，历史上一不可复验）")

# ---------- ② 新送验那一条确实进了主表 ----------
COLS = ["top_n", "ann_return", "ann_return_gross", "excess_univ_ew_ann",
        "excess_univ_ew_ir", "one_way_turnover", "q5_ann",
        "excl_worst_vs_pool_ann", "avg_amount_20d"]
add = new[new["signal"].str.contains(r"SMA\(Volume,20\)")]
print("\n生产口径的量能水平 SMA(Volume,20) 在主表里的行")
if add.empty:
    FAIL.append("主表里没有 SMA(Volume,20) 那一行（新送验的表达式没进表）")
else:
    print(add[COLS].to_string(index=False, float_format=lambda v: f"{v:.4f}"))

# ---------- ③ 两张归档表自报口径 ----------
ex = pd.read_csv(f"{R}/ashare_portfolio_exclusion.csv")
bl = pd.read_csv(f"{R}/ashare_portfolio_buylist.csv")
print("\n减法腿 vs 名单腿（同一批调仓日）")
print(ex[["variant", "gain_ann", "gain_ir", "keep_ratio"]]
      .to_string(index=False, float_format=lambda v: f"{v:.4f}"))
print(bl[["variant", "ann_return", "excess_univ_ew_ann", "excess_univ_ew_ir",
          "one_way_turnover"]].to_string(index=False,
                                         float_format=lambda v: f"{v:.4f}"))
if not {"gate", "list_scheme"} <= set(bl.columns):
    FAIL.append(f"名单腿表没自报口径列（现有 {list(bl.columns)[:6]}…）")
if (ex["keep_ratio"].between(0, 1)).sum() != len(ex):
    FAIL.append("减法腿有 keep_ratio 落在 [0,1] 之外")
# 并集那一行必须还在，且它是这张表里剔得最狠的一档（≥1 条判响 = 域口径）
u = ex[ex["variant"].str.contains("并集")]
if u.empty:
    FAIL.append("减法腿表里没有「并集」那一行")
else:
    print(f"\n并集行 keep_ratio 最低 = {float(u['keep_ratio'].min()):.4f}"
          f"　全表最低 = {float(ex['keep_ratio'].min()):.4f}")
    if float(u["keep_ratio"].min()) > float(ex["keep_ratio"].min()) + 1e-12:
        FAIL.append("「并集」不再是剔得最狠的那一档 ⇒ 域口径变了")

print(f"\n[P0 落盘自检] {'通过' if not FAIL else '失败 ' + str(len(FAIL)) + ' 条'}"
      + ("" if not SKIP else f"　[跳过 {len(SKIP)} 项] " + "；".join(SKIP)))
sys.exit(1 if FAIL else 0)
