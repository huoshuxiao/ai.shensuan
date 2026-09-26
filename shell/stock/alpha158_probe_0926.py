# -*- coding: utf-8 -*-
"""能不能跑 Qlib 内置的 Alpha158？先在**小样本**上真跑一遍，拿到秒/GB 的实测再说代价

为什么先量小样本：全市场截面评估（21 条、15.17M 行）实测 659 秒（`stock/v1/log/
run_ashare_factor_eval_0924.log`），外推到 158 条是一回事，但 Alpha158 走的是 qlib 自己
的 `D.features` 求值路径（不是我们的 DSL），**它到底跑不跑得动、要多少内存是另一个问题**
（本线面板只有 6 列、没有 $vwap/$amount，而 Alpha158 的 WVMA 一族要用 $vwap —— bin 里有，
pregen 生成的 h5 里没有）。所以这一步只回答两个问题：① qlib 0.9.7 在这份 bin 上能不能算出
完整的 158 列；② 单条股票·年 的秒数量级是多少，好外推。

只读：不写任何产物、不改配置、不进生产目录。
"""
import os
import sys
import time

BIN = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/qlib/qlib_data/cn_data"
N_STOCK = int(os.environ.get("PROBE_N", "40"))
SPAN = os.environ.get("PROBE_SPAN", "2023-01-01 2024-12-31").split()

import numpy as np        # noqa: E402
import pandas as pd       # noqa: E402
import qlib             # noqa: E402
from qlib.constant import REG_CN    # noqa: E402
from qlib.data import D             # noqa: E402
from qlib.contrib.data.handler import Alpha158    # noqa: E402

qlib.init(provider_uri=BIN, region=REG_CN)

lines = [ln.split() for ln in open(os.path.join(BIN, "instruments", "all.txt")) if ln.strip()]
inst = sorted({c for c, *_ in lines})[:N_STOCK]
print(f"[探测] {N_STOCK} 只 × {SPAN[0]}~{SPAN[1]}　bin = {BIN}")

t0 = time.time()
h = Alpha158(instruments=inst, start_time=SPAN[0], end_time=SPAN[1])
df = h.fetch(col_set="feature")
dur = time.time() - t0
print(f"[探测] 求值耗时 {dur:.1f}s　形状 {df.shape}")
print(f"[探测] 列数 {len(df.columns)}（Alpha158 应为 158 + LABEL0）")
nan = df.isna().mean().sort_values(ascending=False)
print(f"[探测] 全 NaN 列 {int((nan == 1).sum())} 条　NaN 中位数 {nan.median():.3f}")
print("[探测] NaN 最重的 6 列：\n" + nan.head(6).to_string())
rows, cols = df.shape
print(f"[外推] 单只·单日 {dur/len(df):.2e}s ⇒ 全市场 15.17M 行 ≈ "
      f"{dur/len(df)*15_170_000/60:.0f} 分钟（同机、同算子数、不含写盘）")
print(f"[外推] 峰值内存粗估（float64 宽表）15.17M×{cols} ≈ "
      f"{15_170_000*cols*8/1024**3:.1f} GB")
