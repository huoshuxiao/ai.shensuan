# -*- coding: utf-8 -*-
"""环 2 第二轮：把**方向拧正**之后再读一次（第一轮那几条 −68%/−77% 不是判决，是反着做）。

第一轮撞到的口径：`run_ashare_portfolio_eval.SIGNALS` 顶部注释写死「全部做多低分侧
（对应截面 IC<0）」，而 `run_signal` 里 `ordered = cand.sort_values()` 取前 n 只。
在库那 21 条全是负 IC，所以这条约定一直成立。Alpha158 里排名最前的正好有一半是
**正 IC**（VMA60 +0.0587、VMA30 +0.0533、MA20 +0.0483、QTLD30 +0.0502…）⇒ 不取负号
送进去，秤量的是「故意跟信号反着买」，读出 −77%/年的超额只说明拧着信号买会亏。
取负号 = 把「做多低分侧」翻成「做多高分侧」，表达式本身不动别的口径。

本轮只送 **环 2 不会失真的那几条**（`factor_matrices` 在 `ashare_screen.py:469` 把
high/low 用 close 占位，凡用到 high/low 的表达式在这条路上会被静默换成另一条因子
—— 见第一轮 KLEN 与 LOW0 两行读数逐字节相同，就是都退化成了常数）。
所以候选限于只碰 close/volume 的前排：
    VMA60  ts_mean(volume,60)/volume        IC +0.0587
    VMA30  ts_mean(volume,30)/volume        IC +0.0533
    QTLD30 quantile(close,30,0.2)/close     IC +0.0502
    MA20   ts_mean(close,20)/close          IC +0.0483
    MA30   ts_mean(close,30)/close          IC +0.0490
    VMA20  ts_mean(volume,20)/volume        IC +0.0498
落点与第一轮同（`STOCK_PORT_OUT` 等三个环境变量拐到本目录），权威 csv 依旧只读；
四条生产构造照常送验当锚 —— 上一轮锚是 12 格逐位相同（0.000e+00），本轮再验一次。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "stock", "v1", "src"))
OUT = os.path.join(HERE, "tmp_alpha158", "port_neg")
os.makedirs(OUT, exist_ok=True)
for k, f in (("STOCK_PORT_OUT", "a158neg_portfolio_eval.csv"),
             ("STOCK_EXCL_OUT", "a158neg_portfolio_exclusion.csv"),
             ("STOCK_BUYLIST_OUT", "a158neg_portfolio_buylist.csv")):
    os.environ[k] = os.path.join(OUT, f)

sys.path.insert(0, SRC)
os.chdir(SRC)

import run_ashare_portfolio_eval as P                      # noqa: E402

RECS = {r["name"]: r["expr"] for r in json.load(
    open(os.path.join(HERE, "tmp_alpha158", "alpha158_exprs_0926.json"), encoding="utf-8"))}
CAND = ["VMA60", "VMA30", "VMA20", "QTLD30", "MA30", "MA20"]
IC = {"VMA60": 0.0587, "VMA30": 0.0533, "VMA20": 0.0498,
      "QTLD30": 0.0502, "MA30": 0.0490, "MA20": 0.0483}

P.SIGNALS = [
    ("量能水平 SMA(Volume,20)", "ts_mean(volume,20)", "被验"),
    ("量能波动 STD(Volume,5)", "ts_std(volume,5)", "被验"),
    ("量能动量 MOM(Volume,5)", "volume/delay(volume,5)-1", "被验"),
    ("量能比 SMA(Vol,5)/SMA(Vol,20)",
     "(ts_mean(volume,5))/(ts_mean(volume,20))", "被验"),
] + [(f"A158负号·{n}｜IC {IC[n]:+.4f}｜做多高分侧", f"(-1.0*({RECS[n]}))", "对照")
     for n in CAND]

print(f"[注入] 送验 {len(P.SIGNALS)} 条 = 锚 4 + 取负号候选 {len(CAND)}　落点 {OUT}", flush=True)
t0 = time.time()
rc = P.main()
print(f"\n[本驱动] 环 2 第二轮墙钟 {time.time() - t0:.0f}s（含 load_panel）", flush=True)
sys.exit(rc)
