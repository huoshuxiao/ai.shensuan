# -*- coding: utf-8 -*-
"""路径 2 的环 2 跑法（09-26）：官方入口 `run_etf_portfolio_eval.py` **一行不改**，
用 `ETF_DATA_DIR` 把它的读写整体搬到 shell/ 下的临时目录，跑三场：

    R1 baseline      输入 = 权威环1表原样（31 条内置模板）⇒ 对照，也是复现校验：
                     它与归档的 `data/results/etf_portfolio_eval.csv` 应当同量级
    R2 with_cand     输入 = 31 条模板 + 3 条 Alpha158 候选（IMAX20/VSTD10/CORR30）
                     ⇒ 候选进不进得了终选合成、合成净年化差多少
    R3 parts_only    输入 = 只有那 3 条候选 ⇒ 每条零件自己的单因子回放（钱口径）

为什么必须绕开权威盘：`run_etf_portfolio_eval.py` **不认 `ETF_SPEC_JSON`**，
`EVAL_CSV`/`OUT_CSV` 直接指向 `EA.*_OUT` ⇒ 照原样跑会读不到候选、还会把
`etf_portfolio_eval.csv` 和 `_yearly.csv` 覆掉。而 `ETF_RESULTS_DIR` 是**空转的**
（两个环 1/环 3 入口的 docstring 都记过这个坑），能用的只有 `ETF_DATA_DIR`
（`RESULTS_DIR` 由它推导）；池子和风险面板在 DATA_DIR 之外，所以要另给
`ETF_UNIVERSE_ALL_DIR` / `ETF_RISK_DIR` 指回真实路径，否则规模闸与可投域会静默变空。

`/usr/bin/time -v` 顺手出这一环的**真实单价与内存峰值**（09-26 之前只有股票线的报价）。
"""

import os
import shutil
import subprocess
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
SRC = f"{ROOT}/etf/v1/src"
S = f"{ROOT}/shell/etf/tmp_a158_etf/ring2data"
REAL = f"{ROOT}/etf/v1/data"
RUNS = (sys.argv[1:] or
        ["R1_baseline", "R2_with_cand", "R3_parts_only"])

env = dict(os.environ,
           ETF_DATA_DIR=S,
           ETF_UNIVERSE_ALL_DIR=f"{REAL}/universe_all",
           ETF_RISK_DIR=f"{REAL}/risk",
           PYTHONUNBUFFERED="1")

for tag in RUNS:
    src_in = f"{S}/results/eval_{tag}.csv"
    out = f"{S}/log_{tag}.txt"
    # 环 2 的输入名是**写死的** `etf_factor_eval.csv`（`EVAL_CSV = EA.FACTOR_EVAL_OUT`），
    # 换名它直接 `[输入缺失]` 退出（1.28s 就报，不烧内存）⇒ 每场跑前拷成那个名字。
    shutil.copy2(src_in, os.path.join(S, "results", "etf_factor_eval.csv"))
    with open(out, "w") as fh:
        rc = subprocess.call(["/usr/bin/time", "-v", "/usr/bin/python3.10", "-u",
                              "run_etf_portfolio_eval.py"],
                             cwd=SRC, env=env, stdout=fh, stderr=subprocess.STDOUT)
    for a in ("etf_portfolio_eval.csv", "etf_portfolio_eval_yearly.csv"):
        p = os.path.join(S, "results", a)
        if os.path.exists(p):
            shutil.move(p, os.path.join(S, f"{tag}_{a}"))
    wall = rss = "?"
    with open(out) as fh:
        for line in fh:
            if "Elapsed (wall clock)" in line:
                wall = line.rsplit(":", 1)[1].strip()
            if "Maximum resident set size" in line:
                rss = f"{int(line.split(':')[1]) / 1048576:.2f}GB"
    print(f"{tag:16s} 退出码 {rc} · 墙钟 {wall} · 峰值内存 {rss}", flush=True)
    if rc != 0:
        print(f"[中止] {tag} 非正常结束，看 {out}", flush=True)
        sys.exit(rc)
print("[三场全部完成]", flush=True)
