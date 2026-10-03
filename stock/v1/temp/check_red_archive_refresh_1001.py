# -*- coding: utf-8 -*-
"""10-01 环 3 生产归档刷新（补 `pearson_signed_max` 那一列）的**对表与自证**。

覆写面（用户裁「跑」时就是这两张）：
  `data/results/ashare_redundancy_check.csv`（判重表，7 行 × 11 列）
  `data/results/ashare_redundancy_detail.csv`（候选×在库 全矩阵，7×21=147 格）
备份：`data/results/backup/redundancy_pre_signed_1001/`（`cp -p`，mtime 保留，md5 记在 md5.txt）。

先说清**这一场能逐位、不能逐位**：表结构是新增两列（逐位可比）；但 `pearson_*`/`spearman_*`
四把旧尺**不是**同版面板的产物——旧归档跑在 09-27 16:42（面板更早），本场有效交易日 2796 天，
`mean_t` 的分母多了几天 ⇒ 跨运行必有差（本线同类的差在 1e-4 量级，见「稳不稳」表那条读数 B）。
所以这里对这四列只设**量级闸**（|Δ| < 1e-2，约实测的 70 倍余量），逐位身份闸设在
「候选集/判词/最近邻名字」这些离散量上——它们才是「补一列该不该改变结论」的真问题。

九格：
  R1 旧归档**没有**新列 ⇒ 证明「补列」这件事真有对象（旧版已有就该停手）
  R2 新归档表头 == 11 列且顺序逐字对（`pearson_signed_max` / `signed_nearest_lib` 到位）
  R3 候选集没换人：7 行、`name` 集合与旧版逐字相同、`expr` 逐字相同
  R4 离散结论不变：逐行 `verdict` 与 `nearest_lib` 与旧版相同（补列不许翻判词）
  R5 四把旧尺只挪了量级：max|Δ| < 1e-2，且逐条同号（挪向哪一边都打印出来）
  R6 新列**自证**（跨表独立构造）：`pearson_signed_max` == detail 同一候选行的 `pearson` 最大值；
     `signed_nearest_lib` == 那个 argmax 的在库名（不是拿同一列跟自己比）
  R7 判词复核：表里两个读数重新喂生产单点 `verdict()`，逐行必须等于归档 `verdict`
  R8 覆写**真的发生了**：新 md5 ≠ 备份 md5 且新 mtime 晚于备份目录（防「跑了但没落盘」）
  R9 排序口径：新表按 `pearson_signed_max` 降序（逐行非递增）

负对照：`RED_NEGCTL=1` 把「新归档」指向备份本身（= 生产还停在旧版），必须红**恰好 5 格**
（R2、R6、R7、R8、R9）；多一格或少一格都算对照失效。缺列那三格做了 `in columns` 前置判断，
不许把对照跑成 KeyError 崩溃——崩了不等于红。

只读：不写 `data/results/`，产物只落 `stock/v1/temp/tmp_red_archive_refresh_1001/`。
"""
import hashlib
import os
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
RES = f"{ROOT}/stock/v1/data/results"
sys.path.insert(0, f"{ROOT}/stock/v1/src")
import pandas as pd                                      # noqa: E402
import run_ashare_redundancy_check as R                   # noqa: E402

OLD = f"{RES}/backup/redundancy_pre_signed_1001"
NEGCTL = os.environ.get("RED_NEGCTL") == "1"
NEW = OLD if NEGCTL else RES

COLS_NEW = ["name", "expr", "pearson_signed_max", "signed_nearest_lib", "pearson_max",
            "nearest_lib", "pearson_signed", "pearson_second", "spearman_max", "bar", "verdict"]
OLD_COLS = ["name", "expr", "pearson_max", "nearest_lib", "pearson_signed",
            "pearson_second", "spearman_max", "bar", "verdict"]
COMPARABLE = ["pearson_max", "pearson_signed", "pearson_second", "spearman_max"]

FAIL, DONE = [], []


def g(cell, ok, read=""):
    (DONE if ok else FAIL).append(cell)
    print(f"{'OK ' if ok else '红 '} {cell}｜{read}")


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def load(d):
    return (pd.read_csv(f"{d}/ashare_redundancy_check.csv"),
            pd.read_csv(f"{d}/ashare_redundancy_detail.csv"))


oc, ot = load(OLD)
nc, nt = load(NEW)
NC = nc.set_index("name")

# ---- R1 旧归档没有新列 ----
g("R1 旧归档确无新列（这场有对象）",
  ("pearson_signed_max" not in oc.columns) and (list(oc.columns) == OLD_COLS),
  f"旧表头 {len(oc.columns)} 列：{list(oc.columns)}")

# ---- R2 新归档表结构 ----
g("R2 新归档 11 列且顺序逐字对", list(nc.columns) == COLS_NEW,
  f"新表头 {len(nc.columns)} 列：{list(nc.columns)}")

# ---- R3 候选集没换人 ----
g("R3 候选集未换人（7 行、name/expr 逐字同）",
  len(nc) == 7 and sorted(oc["name"]) == sorted(nc["name"])
  and (oc.set_index("name")["expr"].sort_index() == NC["expr"].sort_index()).all(),
  f"旧 {len(oc)} 行 / 新 {len(nc)} 行；expr 全部逐字同="
  f"{(oc.set_index('name')['expr'].sort_index() == NC['expr'].sort_index()).all()}")

# ---- R4 离散结论不变 ----
same_v = (oc.set_index("name")["verdict"].sort_index() == NC["verdict"].sort_index()).all()
same_l = (oc.set_index("name")["nearest_lib"].sort_index() == NC["nearest_lib"].sort_index()).all()
g("R4 判词与最近邻逐行不变", bool(same_v) and bool(same_l),
  f"verdict 同={bool(same_v)}，nearest_lib 同={bool(same_l)}；"
  f"分布 {nc['verdict'].value_counts().to_dict()}")

# ---- R5 四把旧尺只挪了量级 ----
worst, which, sign_bad = 0.0, "", []
for c in COMPARABLE:
    d = (oc.set_index("name")[c].astype(float) - NC[c].astype(float)).abs().max()
    if pd.isna(d):
        g("R5 四把旧尺量级 < 1e-2 且同号", False, f"{c} 两侧有 NaN")
        break
    s1 = (oc.set_index("name")[c].astype(float) > 0)
    s2 = (NC[c].astype(float) > 0)
    sign_bad += [n for n, a_, b_ in zip(nc["name"], s1, s2) if a_ != b_]
    if d > worst:
        worst, which = float(d), c
else:
    g("R5 四把旧尺量级 < 1e-2 且同号", worst < 1e-2 and not sign_bad,
      f"max|Δ|={worst:.3e}（{which}），换号 {len(sign_bad)} 条 {sign_bad[:2]}"
      f"｜旧场面板更早、本场有效交易日 2796 ⇒ 跨运行必有差，只卡量级")

# ---- R6 新列自证（跨表独立构造）----
if "pearson_signed_max" not in nc.columns:
    g("R6 新列 == detail 独立重算（含最近邻名字）", False, "新列不存在，无从重算")
else:
    dmax = nt.groupby("candidate")["pearson"].max()
    darg = nt.loc[nt.groupby("candidate")["pearson"].idxmax()].set_index("candidate")["in_library"]
    bad_v = [n for n in nc["name"] if abs(float(dmax[n]) - float(NC.loc[n, "pearson_signed_max"])) > 1e-12]
    bad_l = [n for n in nc["name"] if str(darg[n]) != str(NC.loc[n, "signed_nearest_lib"])]
    g("R6 新列 == detail 独立重算（含最近邻名字）", not bad_v and not bad_l,
      f"值不符 {len(bad_v)} 条 {bad_v}；名字不符 {len(bad_l)} 条 {bad_l}；detail {len(nt)} 格 = "
      f"{nt['candidate'].nunique()}×{nt['in_library'].nunique()}")

# ---- R7 判词复核（喂生产单点）----
if "pearson_signed_max" not in nc.columns:
    g("R7 判词 == verdict() 重算", False, "缺 `pearson_signed_max`，硬闸没有输入")
else:
    vv = NC.apply(lambda r: R.verdict(float(r["pearson_signed_max"]), float(r["pearson_max"])), axis=1)
    diff = [f"{n}→归档「{NC.loc[n, 'verdict']}」/重算「{v}」" for n, v in vv.items() if NC.loc[n, "verdict"] != v]
    g("R7 判词 == verdict() 重算", len(diff) == 0, f"{len(nc)} 行，不符 {len(diff)} 条 {diff[:2]}")

# ---- R8 覆写真的发生 ----
bak_mt = os.path.getmtime(OLD)
new_md5, old_md5 = md5(f"{NEW}/ashare_redundancy_check.csv"), md5(f"{OLD}/ashare_redundancy_check.csv")
mnew = os.path.getmtime(f"{NEW}/ashare_redundancy_check.csv")
g("R8 生产归档确被重写（md5 变 + mtime 晚于备份）",
  new_md5 != old_md5 and mnew > bak_mt,
  f"新 md5 {new_md5[:12]}… / 备份 {old_md5[:12]}…；新 mtime "
  f"{pd.Timestamp(mnew, unit='s', tz='Asia/Shanghai').strftime('%m-%d %H:%M:%S')}，"
  f"备份目录 {pd.Timestamp(bak_mt, unit='s', tz='Asia/Shanghai').strftime('%m-%d %H:%M:%S')}；"
  f"{os.path.getsize(f'{OLD}/ashare_redundancy_check.csv')}B → {os.path.getsize(f'{NEW}/ashare_redundancy_check.csv')}B")

# ---- R9 排序口径 ----
if "pearson_signed_max" not in nc.columns:
    g("R9 按 pearson_signed_max 降序", False, "缺列")
else:
    s = NC["pearson_signed_max"].astype(float).to_numpy()
    g("R9 按 pearson_signed_max 降序", bool((s[:-1] >= s[1:]).all()), f"首末 {s[0]:+.6f} → {s[-1]:+.6f}")

print("\n===== 两把尺并排（带符号硬闸 / |corr| 政策闸 / 判词 / 两道尺之差）=====")
# 显示段自己也要有牙地处理缺列：负对照那一臂没有新列，在此 KeyError = 崩了不等于红。
has_new = "pearson_signed_max" in nc.columns
show = nc[["name", "pearson_signed_max", "pearson_max", "verdict"] if has_new
          else ["name", "pearson_max", "verdict"]].copy()
if has_new:
    show["两尺之差"] = (show["pearson_max"] - show["pearson_signed_max"]).abs()
for c in show.columns:
    if c not in ("name", "verdict"):
        show[c] = show[c].map(lambda x: f"{float(x):+.6f}")
show["name"] = show["name"].str.slice(0, 46)
print(show.to_string(index=False))
n_mirror = (int(((nc["pearson_max"] >= R.ASHARE_RED_BAR)
                 & (nc["pearson_signed_max"] < R.ASHARE_RED_BAR)).sum()) if has_new else "缺列")
print(f"\n镜像同簇（|corr|≥0.99 而带符号<0.99）候选 {n_mirror}/7 条")

EXP_NEG = {"R2 新归档 11 列且顺序逐字对",
           "R6 新列 == detail 独立重算（含最近邻名字）", "R7 判词 == verdict() 重算",
           "R8 生产归档确被重写（md5 变 + mtime 晚于备份）", "R9 按 pearson_signed_max 降序"}
if NEGCTL:
    ok = set(FAIL) == EXP_NEG
    print(f"\n[负对照] 红 {len(FAIL)}/9 格，期望恰好 5 格且集合 == 预置集合："
          f"{'通过' if ok else '失效'}")
    print(f"  实际红集：{FAIL}")
    print(f"  多红：{sorted(set(FAIL) - EXP_NEG)}｜漏红：{sorted(EXP_NEG - set(FAIL))}")
    sys.exit(0 if ok else 1)

print(f"\n===== 结果：{len(DONE)}/9 绿，红 {len(FAIL)} 条 {FAIL} =====")
sys.exit(1 if FAIL else 0)
