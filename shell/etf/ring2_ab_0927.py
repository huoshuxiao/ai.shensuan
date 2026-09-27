# -*- coding: utf-8 -*-
"""#41 路径⑤落地的验收驱动：把改过的官方环 2 入口在 **scratch 盘** 上跑两场。

    A factor  旧口径（按条发席位）—— 必须与 09-24 归档那张表的**行结构**逐行对得上
    B family  新口径（一族一票）  —— 必须多出 8 行单族 + 1 行族间合成 + 1 行因子层对照

为什么不直接跑权威盘：`EVAL_CSV`/`OUT_CSV` 由 `ETF_DATA_DIR` 推导，指到 shell/ 下的
临时目录就不会覆掉 `etf/v1/data/results/`（`ETF_RESULTS_DIR` 单独设是空转的，
见 `a158_ring2_0926.py` 记过的坑）。池子与风险面板在 DATA_DIR 之外，必须显式指回真实路径，
否则规模闸和可投域会静默变空。

跑法：`/usr/bin/python3.10 shell/etf/ring2_ab_0927.py`
"""
import os
import shutil
import subprocess
import sys

import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
SRC = f"{ROOT}/etf/v1/src"
REAL = f"{ROOT}/etf/v1/data"
S = f"{ROOT}/shell/etf/tmp_a158_etf/ring2ab"
SNAP = f"{ROOT}/shell/etf/tmp_a158_etf/snap_ring2_port_eval_0926pre.csv"
os.makedirs(os.path.join(S, "results"), exist_ok=True)

shutil.copy2(f"{REAL}/results/etf_factor_eval.csv",
             os.path.join(S, "results", "etf_factor_eval.csv"))
env = dict(os.environ, ETF_DATA_DIR=S, ETF_UNIVERSE_ALL_DIR=f"{REAL}/universe_all",
           ETF_RISK_DIR=f"{REAL}/risk", PYTHONUNBUFFERED="1")

RUNS = [("A_factor", "factor"), ("B_family", "family")]
outs = {}
for tag, lvl in RUNS:
    log = f"{S}/log_{tag}.txt"
    with open(log, "w") as fh:
        rc = subprocess.call(["/usr/bin/time", "-v", "/usr/bin/python3.10", "-u",
                              "run_etf_portfolio_eval.py"],
                             cwd=SRC, env=dict(env, ETF_COMPOSITE_LEVEL=lvl),
                             stdout=fh, stderr=subprocess.STDOUT)
    for a in ("etf_portfolio_eval.csv", "etf_portfolio_eval_yearly.csv"):
        p = os.path.join(S, "results", a)
        if os.path.exists(p):
            shutil.move(p, os.path.join(S, f"{tag}_{a}"))
    wall = rss = "?"
    with open(log) as fh:
        for line in fh:
            if "Elapsed (wall clock)" in line:
                wall = line.rsplit(":", 1)[1].strip()
            if "Maximum resident set size" in line:
                rss = f"{int(line.split(':')[1]) / 1048576:.2f}GB"
    print(f"[{tag}] level={lvl} 退出码 {rc} · 墙钟 {wall} · 峰值内存 {rss}", flush=True)
    if rc != 0:
        print(f"[中止] {tag} 非正常结束，看 {log}", flush=True)
        sys.exit(rc)
    outs[tag] = pd.read_csv(os.path.join(S, f"{tag}_etf_portfolio_eval.csv"))

pd.set_option("display.width", 300)
pd.set_option("display.max_columns", 30)
cols = ["label", "kind", "k", "ann_return", "sharpe", "max_drawdown",
        "excess_ew_universe_ann", "turnover_ann", "avg_amount20"]
print("\n===== A factor（旧口径，应与 09-24 归档同结构）=====")
print(outs["A_factor"][cols].round(4).to_string(index=False))
print("\n===== B family（新口径）=====")
print(outs["B_family"][cols].round(4).to_string(index=False))

# ---- 三条硬对表（都能失败）----
fails = []
old = pd.read_csv(SNAP)
s_new = set(outs["A_factor"].label)
s_old = set(old.label)
if s_new != s_old:
    fails.append(f"[A 结构] 与归档行名不重合：多 {sorted(s_new - s_old)} 少 {sorted(s_old - s_new)}")
else:
    j = outs["A_factor"].merge(old, on="label", suffixes=("_新", "_旧"))
    d = (j.ann_return_新 - j.ann_return_旧).abs().max()
    print(f"\n[对表 A] 行结构与 09-24 归档完全一致（{len(s_new)} 行）；"
          f"净年化 max|Δ|={d:.4f}（数据这几天日更过，非零是预期的，只看不判）")

bf = outs["B_family"]
n_fam = (bf.kind == "family").sum() // 2
n_blend = (bf.kind == "family_composite").sum() // 2
n_ctl = bf[bf.kind == "composite"].label.str.startswith("因子层合成").sum() // 2
if n_fam != 8:
    fails.append(f"[B 单族行] 期望 8 族，实到 {n_fam}")
if n_blend != 1:
    fails.append(f"[B 族间合成行] 期望 1，实到 {n_blend}")
if n_ctl != 1:
    fails.append(f"[B 因子层对照行] 期望 1，实到 {n_ctl}")
# 族层模式的对照行必须与 A 场那条旧合成逐位相等（同一批因子、同一套权重、同一份数据）
a_c = outs["A_factor"][outs["A_factor"].label.str.startswith("合成·")].set_index("k").ann_return
b_c = bf[bf.label.str.startswith("因子层合成")].set_index("k").ann_return
dd = (a_c - b_c).abs().max()
print(f"[对表 对照行] 因子层合成 A vs B：max|Δ净年化|={dd:.2e}")
if dd > 1e-12:
    fails.append(f"[对表 对照行] 同一口径两场算出两个数：{dd:.2e}")

print("\n[验收] " + ("全部通过" if not fails else "未通过：\n  " + "\n  ".join(fails)))
sys.exit(1 if fails else 0)
