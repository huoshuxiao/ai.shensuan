# -*- coding: utf-8 -*-
"""把判重闸放行的 6 条 Alpha158 候选送进环 2（组合层回放），产物一律落到本目录。

为什么是这一步：本线的准入文档写死了「是否值得并进取决于组合层的超额与单调性，
不看截面 IC」（`run_ashare_portfolio_eval.py:82-90`、`ashare_screen.py` 的 `lib:`
提名注释「未过组合层回放」）。而 157 条扫描给的全是样本内截面读数 ⇒ 拿不到本表
就没资格谈「接进判据」。

不碰产品代码的做法：环 2 的送验清单 `SIGNALS` 是模块级常量，这里 import 之后整表
替换（`P.SIGNALS = [...]`），**不改 `run_ashare_portfolio_eval.py` 一个字**。
四个落点在 import config 之前先用环境变量拐到本目录：
  STOCK_PORT_OUT（连带 `_yearly.csv`，它由该路径 `.replace(".csv","_yearly.csv")` 派生）
  STOCK_EXCL_OUT / STOCK_BUYLIST_OUT
⇒ `data/results/` 下的权威 csv 全程只读。

送验表的组成（两半）：
- **锚**：生产四条剔除构造原样送验（花名/表达式与 09-24 归档逐字相同）。它们的读数
  必须落在归档值附近，否则说明本驱动或面板变了 ⇒ 本表全部读数作废。这是本脚本唯一的
  自检，缺了它就等于凭一个没验过的注入通道出结论。
- **候选**：判重闸（09-26 那次，`red/ashare_redundancy_check_a158.csv`）判「可提名」
  且样本内 |Rank IC| ≥ 0.05 的 6 条。标成 `对照` 是为了绕过 `main()` 的因子库过滤器
  （`:376` `if e in have or k == "对照"`：不在 factors.json 里的表达式会被静默丢掉）。
  ⚠️ `kind` 在全脚本只用于那一行过滤与表格标签（grep 过 :281/:321/:359/:415/:478），
  回放数学与 `被验` 完全一致 ⇒ 标 `对照` 不降级读数。

口径不另起一灶：起点/规模档/持有期/费率/涨停闸全部沿用 config 默认（2015-01-01、
top50/100/200、hold 5、单边 15bp、board 档），所以本表与归档表同尺可比。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "..", "stock", "v1", "src"))
OUT = os.path.join(HERE, "tmp_alpha158", "port")
os.makedirs(OUT, exist_ok=True)

# 必须在 import config 之前落位：config 在 import 时就读完全部环境变量
os.environ["STOCK_PORT_OUT"] = os.path.join(OUT, "a158_portfolio_eval.csv")
os.environ["STOCK_EXCL_OUT"] = os.path.join(OUT, "a158_portfolio_exclusion.csv")
os.environ["STOCK_BUYLIST_OUT"] = os.path.join(OUT, "a158_portfolio_buylist.csv")

sys.path.insert(0, SRC)
os.chdir(SRC)

import run_ashare_portfolio_eval as P                      # noqa: E402

ANCHORS = [                                   # 与 09-24 归档逐字相同的四条生产构造
    ("量能水平 SMA(Volume,20)", "ts_mean(volume,20)", "被验"),
    ("量能波动 STD(Volume,5)", "ts_std(volume,5)", "被验"),
    ("量能动量 MOM(Volume,5)", "volume/delay(volume,5)-1", "被验"),
    ("量能比 SMA(Vol,5)/SMA(Vol,20)",
     "(ts_mean(volume,5))/(ts_mean(volume,20))", "被验"),
]
# 表达式从扫描清单里取原文，不在此重打一遍（打错一个字符就换了个因子）
import json                                                  # noqa: E402
RECS = {r["name"]: r["expr"] for r in json.load(
    open(os.path.join(HERE, "tmp_alpha158", "alpha158_exprs_0926.json"), encoding="utf-8"))}
CAND = ["KLEN", "VMA60", "LOW0", "MIN10", "VMA30", "BETA10"]
IC = {"KLEN": -0.0658, "VMA60": 0.0587, "LOW0": 0.0560,
      "MIN10": 0.0547, "VMA30": 0.0533, "BETA10": -0.0402}
RED = {"KLEN": 0.427, "VMA60": 0.501, "LOW0": 0.432,
       "MIN10": 0.717, "VMA30": 0.581, "BETA10": 0.705}

P.SIGNALS = ANCHORS + [(f"A158·{n}｜判重 {RED[n]:.2f}｜IC {IC[n]:+.4f}",
                        RECS[n], "对照") for n in CAND]

print(f"[注入] 送验 {len(P.SIGNALS)} 条 = 锚 {len(ANCHORS)} + 候选 {len(CAND)}", flush=True)
print(f"[注入] 落点改到 {OUT}　权威 csv 只读", flush=True)
for _l, _e, _k in P.SIGNALS:
    print(f"        {_k:3s} {_l:44s} {_e}", flush=True)
print(f"[口径] 起点 {P.ASHARE_PORT_START}　规模档 {P.ASHARE_PORT_TOP_N}　"
      f"持有 {P.ASHARE_PORT_HOLD} 日　单边费 {P.ASHARE_PORT_COST_ONE_WAY}　"
      f"闸 board（与归档同尺）", flush=True)

t0 = time.time()
rc = P.main()
print(f"\n[本驱动] 环 2 整场墙钟 {time.time() - t0:.0f}s（含 load_panel）", flush=True)
sys.exit(rc)
