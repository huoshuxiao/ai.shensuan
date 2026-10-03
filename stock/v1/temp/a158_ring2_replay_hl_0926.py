# -*- coding: utf-8 -*-
"""选项K 收尾：8 条吃 high/low 的 Alpha158 走**多头腿**（环 2 组合层回放）。

为什么只送 8 条：选项K 修的是 `factor_matrices` 拿 close 顶替 high/low 的占位缺陷，
修完这 40 条才**第一次**能在环 2 里被正确回放（修复前 KLEN 整列是常量 0、LOW0 整列是 1，
回放的是不存在的东西）。40 条里 |环1 rank IC| 排到 0.0426 以上的就这 8 条，其余 32 条
连环 1 那关的读数都低于本线已判死的名字，再送只是把 8 行的账单摊成 40 行。

方向约定与环 2 的判据一致（`SIGNALS` 那句「全部做多低分侧，对应截面 IC<0」）：
    IC < 0 ⇒ 低分侧就是好票 ⇒ 直接送原式
    IC > 0 ⇒ 高分侧才是好票 ⇒ 必须包成 (-1.0*(原式))，否则量到的是「买错方向」
             （这个坑 09-26 第一轮已经踩过一次，见 a158_ring2_replay_neg_0926.py）

锚点自检：前 4 行是生产在用的四条量能构造，与权威归档 `ashare_portfolio_eval.csv`
同格对表 ⇒ **最大绝对差必须为 0**，否则本表候选那 8 行读数一律作废（判据不恒真：
修复前用同一把尺子量过，锚点确实逐位为 0，见 stock/v1/temp/a158_ring2_bill_0926.py）。

产物全部落在 stock/v1/temp/tmp_alpha158/port_hl/，权威归档不碰。
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "stock", "v1", "src")
OUT = os.path.join(HERE, "tmp_alpha158", "port_hl")
os.makedirs(OUT, exist_ok=True)
os.environ["STOCK_PORT_OUT"] = os.path.join(OUT, "hl_portfolio_eval.csv")
os.environ["STOCK_EXCL_OUT"] = os.path.join(OUT, "hl_exclusion.csv")
os.environ["STOCK_BUYLIST_OUT"] = os.path.join(OUT, "hl_buylist.csv")

sys.path.insert(0, SRC)
os.chdir(SRC)

import run_ashare_portfolio_eval as P                      # noqa: E402

RECS = {r["name"].split("|")[-1]: r["expr"] for r in json.load(
    open(os.path.join(HERE, "tmp_alpha158", "alpha158_exprs_0926.json"), encoding="utf-8"))}
# (裸名, 环1 rank IC)：IC<0 的原样送，IC>0 的取负号做多高分侧
CANDS = [("KLEN", -0.0658), ("LOW0", 0.0560), ("MIN10", 0.0547), ("MIN5", 0.0544),
         ("MIN20", 0.0497), ("MIN30", 0.0478), ("MIN60", 0.0458), ("KLOW", -0.0426)]


def wrap(n, ic):
    return RECS[n] if ic < 0 else f"(-1.0*({RECS[n]}))"


P.SIGNALS = [
    ("量能水平 SMA(Volume,20)", "ts_mean(volume,20)", "被验"),
    ("量能波动 STD(Volume,5)", "ts_std(volume,5)", "被验"),
    ("量能动量 MOM(Volume,5)", "volume/delay(volume,5)-1", "被验"),
    ("量能比 SMA(Vol,5)/SMA(Vol,20)",
     "(ts_mean(volume,5))/(ts_mean(volume,20))", "被验"),
] + [(f"A158HL·{n}｜rank IC {ic:+.4f}｜{'做多低分侧' if ic < 0 else '做多高分侧'}",
      wrap(n, ic), "对照") for n, ic in CANDS]

print(f"[注入] 送验 {len(P.SIGNALS)} 条 = 锚 4 + high/low 候选 {len(CANDS)}"
      f"　落点 {OUT}", flush=True)
t0 = time.time()
rc = P.main()
print(f"\n[本驱动] 环 2 high/low 轮墙钟 {time.time() - t0:.0f}s（含 load_panel）", flush=True)
sys.exit(rc)
