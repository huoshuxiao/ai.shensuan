# -*- coding: utf-8 -*-
"""环 3 分批跑法（09-26）：把 158 条 Alpha158 候选拆成 5 个**按族整块**的分批，
串行喂给官方入口 `run_etf_redundancy_check.py`。

为什么分批：官方入口一次性把「候选 + 在库」全部因子摊成宽表，158+35=193 张
(约 2400 天 × 851 只) 的 float64 表 ≈ 3.2GB，再叠 `cs_corr_mean` 的 day_chunk 立体块
和秩变换副本，实测峰值 9.3GB ⇒ 在 15GB 单机上连着两次被内核 OOM 杀掉
（16:51 pid 689133、17:36 pid 699660）。`cs_corr_mean` 自己的 docstring 写的就是
"53 张表 ≈ 740MB" 这个量级 ⇒ 一屏 158 条本来就超出这一环的设计载荷。

每批产物立刻改名存到本目录（`check{i}_*.csv`）：候选模式下 OUT_CSV 路径固定，
后一批会把前一批的 `.cand.csv` 直接覆掉。

已知代价（读数时必须念）：**「候选 × 候选」那一列只在批内完整**，跨批的近亲看不到。
「候选 × 在库」这一列每批都是对全部 35 条在库因子做的，完整、不受分批影响
⇒ 本轮真正要回答的问题（补全 7 条表达式后判重覆盖面 28→35 会抓到谁）是可信的。
"""

import os
import shutil
import subprocess
import sys
import time

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/src"
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_a158_etf")
RESULTS = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/data/results"
BATCHES = [f"{TMP}/batch{i}.json" for i in range(1, 6)]
ARTIFACTS = ["etf_redundancy_check.cand.csv",
             "etf_redundancy_detail.cand.csv",
             "etf_redundancy_matrix_cand.cand.csv",
             "etf_redundancy_matrix_vs_library.cand.csv"]

for i, spec in enumerate(BATCHES, 1):
    env = dict(os.environ, ETF_SPEC_JSON=spec, PYTHONUNBUFFERED="1")
    print(f"\n########## 批 {i}/5 · {os.path.basename(spec)} · 起于 "
          f"{time.strftime('%H:%M:%S')}", flush=True)
    rc = subprocess.call(["/usr/bin/python3.10", "-u",
                          "run_etf_redundancy_check.py"], cwd=SRC, env=env)
    print(f"########## 批 {i} 退出码 {rc}", flush=True)
    for a in ARTIFACTS:
        src = os.path.join(RESULTS, a)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(TMP, f"check{i}_{a}"))
    if rc != 0:
        print(f"[中止] 批 {i} 非正常结束，不再往下跑（避免把内存/判读搅在一起）",
              flush=True)
        sys.exit(rc)
print("\n[全部 5 批完成]", flush=True)
