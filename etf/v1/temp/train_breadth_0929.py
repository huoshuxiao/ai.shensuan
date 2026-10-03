# -*- coding: utf-8 -*-
"""「训练段样本是不是少了些」——只读量三件事，不改任何配置。

量的不是"bar 数够不够"这一个数，而是**训练样本的两个维度**：
  深度 = 训练段有多少根 bar（时间长度）
  宽度 = 训练段里有多少只标的同时活着（截面厚度）
挖因子靠的是截面（每天在可用标的之间排序），所以宽度比深度更要命：
4 只标的 × 690 天 ≈ 每天在一个 4 元的横截面上算 IC，噪声占满。

自检（不成立就 die，不许静默出数）：
  S1 复刻出来的三折测试段**起止日期**必须与产物 walk_forward_daily.csv 的
     「回测区间」列一致 ⇒ 证明我用的分折几何与判据那套是同一套；对不上整张表作废。
  S2 三折的可用标的数必须与链路日志里那行「训练段可用标的: n/100」同口径
     （分母 = len(pool)），日志里能读到折 1 ⇒ 折 1 必须相等。
  S3 每只标的的 bar 数必须单调不减地来自它自己的首末根，若出现
     "窗口内 bar 数 > 全历史 bar 数" 说明取数越界，作废。

产出：每折 深度/宽度/总格数/日均截面 + 全池首根年份分布（回答"2010-2012
到底有几只 ETF 可选"）。只读，一行盘都不写。
"""
import os
import re
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BYPASS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(BYPASS, "src"))
import _bootstrap  # noqa: F401  必须最先：挂载 src/ 与 common/ 的路径

from config import FREQ, WALK_FORWARD                      # noqa: E402
from data_loader import DataLoader                           # noqa: E402
from etf_universe import get_universe                         # noqa: E402
from walk_forward import make_splits                          # noqa: E402

GATE = 240            # walk_forward.py:93 那条「需 >240 根 bar」
# ⚠️ 默认对拍的是**快照**，不是 data/results/ —— 后者正被 [8/9] 那一场覆写
SNAP = os.path.join(HERE, "snap_before_resume_run_0929_1640", "results")
RESULTS = sys.argv[1] if len(sys.argv) > 1 else SNAP
FOLD_TABLE = os.path.join(RESULTS, "walk_forward_daily.csv")
LOG = os.path.join(HERE, "chain_resume_0929_1645.log")


def die(msg):
    print(f"❌ {msg}")
    sys.exit(1)


universe = get_universe()
codes = universe.universe["code"].tolist()
loader = DataLoader(freq=FREQ)
pool = loader.load_pool(codes)
if not pool:
    die("池子空 ⇒ 没数据可量")

ref_code = max(pool, key=lambda c: len(pool[c]))
all_ts = pool[ref_code].index
splits = make_splits(all_ts)
print(f"全时间轴 {len(all_ts)} bar：{all_ts[0]:%Y-%m-%d} ~ "
      f"{all_ts[-1]:%Y-%m-%d}（参考标的 {ref_code}）")
print(f"分折几何：n_splits={WALK_FORWARD['n_splits']} "
      f"train_ratio={WALK_FORWARD['train_ratio']} "
      f"embargo_bars={WALK_FORWARD['embargo_bars']}，"
      f"训练样本闸 >{GATE} bar\n")

# ── S1：复刻的折窗必须与产物折表逐字相同 ──
if not os.path.exists(FOLD_TABLE):
    die(f"S1 缺少产物折表 {FOLD_TABLE} ⇒ 没有对拍基准，整张表不许出")
ft = pd.read_csv(FOLD_TABLE)
mine = [f"{te_s:%Y-%m-%d} ~ {te_e:%Y-%m-%d}"
        for (_, _, te_s, te_e) in splits]
# 产物那一列是 "2012-10-26 ~ 2015-08-03 (673 bars) ..."，只取日期段来对
pat = re.compile(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})")
theirs = []
for s in ft["回测区间"].tolist():
    m = pat.search(str(s))
    if not m:
        die(f"S1 产物折表的「回测区间」列解不出日期：{s!r}")
    theirs.append(f"{m.group(1)} ~ {m.group(2)}")
if mine != theirs:
    die(f"S1 复刻的测试段与产物不一致：\n  本次 {mine}\n  产物 {theirs}"
        f" ⇒ 分折几何不同一套，下面的数全部作废")
print(f"[S1 ✅] 复刻的三折测试段与 {os.path.basename(FOLD_TABLE)} 起止日期相同："
      f"{'、'.join(mine)}")

# ── S2：折 1 的可用标的数必须与链路日志那行同读数 ──
tr_s0, tr_e0 = splits[0][0], splits[0][1]
fold1_bars = {c: len(pool[c].loc[tr_s0:tr_e0]) for c in pool}
n_avail_fold1 = sum(1 for v in fold1_bars.values() if v > GATE)
logged = None
if os.path.exists(LOG):
    with open(LOG, errors="ignore") as f:
        for line in f:
            m = re.search(r"训练段可用标的:\s*(\d+)/(\d+)", line)
            if m:
                logged = (int(m.group(1)), int(m.group(2)))
                break
if logged:
    if logged != (n_avail_fold1, len(pool)):
        die(f"S2 折 1 可用标的与链路日志对不上：本脚本 "
            f"{n_avail_fold1}/{len(pool)} vs 日志 {logged[0]}/{logged[1]}"
            f" ⇒ 池子加载口径不同，作废")
    print(f"[S2 ✅] 折 1 可用标的与链路日志同读数：{logged[0]}/{logged[1]}")
else:
    print("[S2 ⚠️] 没在日志里找到「训练段可用标的」那行 ⇒ 这一条自检没牙，"
          "下面的折 1 宽度数只能自证")

rows = []
for i, (tr_s, tr_e, te_s, te_e) in enumerate(splits):
    bars = {c: len(df.loc[tr_s:tr_e]) for c, df in pool.items()}
    avail = {c: b for c, b in bars.items() if b > GATE}
    # S3：窗口内 bar 数不得超过该标的全历史 bar 数
    for c, b in bars.items():
        if b > len(pool[c]):
            die(f"S3 越界：{c} 窗口内 {b} bar > 全历史 {len(pool[c])} bar")
    grid = sum(avail.values())
    rows.append({
        "折": i + 1,
        "训练段": f"{tr_s:%Y-%m-%d}~{tr_e:%Y-%m-%d}",
        "训练bar": len(all_ts[(all_ts >= tr_s) & (all_ts <= tr_e)]),
        "可用标的": f"{len(avail)}/{len(pool)}",
        "标的bar中位": int(np.median(sorted(avail.values()))) if avail else 0,
        "标的bar最短": min(avail.values()) if avail else 0,
        "总格数": grid,
        "日均截面": round(grid / max(1, len(all_ts[
            (all_ts >= tr_s) & (all_ts <= tr_e)])), 2),
    })

df = pd.DataFrame(rows)
print("\n──────── 训练段的两个维度 ────────")
print(df.to_string(index=False))

# ── 全池首根年份：回答"2010-2012 到底有几只可选" ──
first_year = pd.Series([pd.Timestamp(pool[c].index[0]).year for c in pool])
vc = first_year.value_counts().sort_index()
print("\n──────── 池内 100 只标的的首根年份分布 ────────")
cum = 0
out = []
for y, n in vc.items():
    cum += n
    out.append(f"{y}:{n}(累计{cum})")
print("  " + "  ".join(out))
print(f"  ⇒ 截至 2012-10-18（折 1 训练段末）已在世的标的 = "
      f"{int((first_year <= 2012).sum())} 只；"
      f"截至 2018-05-23（折 2 训练段末）= "
      f"{int((first_year <= 2018).sum())} 只")

# ── 与判据读数的关系：合并样本外那条链用的样本量 ──
if "测试段bar数" in ft.columns:
    tot = int(ft["测试段bar数"].sum())
    print(f"\n──────── 对照 ────────")
    print(f"  三折测试段合计 {tot} bar（占全时间轴 {tot / len(all_ts) * 100:.1f}%）")
    print(f"  全样本那遍用的时间轴 = {len(all_ts)} bar、库内因子一起放")
    print("\n读数边界：本脚本只量「训练样本有多大」，不回答「样本小了结论就错」——"
          "那要看折内 IC 的截面噪声，是另一把尺子。")
