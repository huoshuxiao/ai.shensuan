# -*- coding: utf-8 -*-
"""环 2 回放对账单：先证明注入通道无失真，再念 6 条 Alpha158 候选的组合层读数。

判据（唯一一条硬失败）：**四条生产构造**在本次产物与权威归档
`stock/v1/data/results/ashare_portfolio_eval.csv` 里同一 (表达式 × top_n) 格子的
`ann_return` / `excess_univ_ew_ann` / `one_way_turnover` 必须逐位对上（atol=1e-9）。
这两次跑用的是同一版面板（h5 末格 2026-09-24）与同一套闸门参数，所以对不上只可能是
本驱动改了什么（送验表注入、环境变量、口径），不是数据往前走 —— 这一点与 ⑳ 的
「环 1 归档跨运行必有差」不同：那里覆写发生在不同日期，这里两边同日。
不过 ⇒ 本表全部候选读数作废，别往下念。

为什么候选只念 top50 那一档：那是生产名单的规模（`ASHARE_BUY_TOP_N` 同源），
100/200 只是给「换个规模还站不站得住」留个影子。
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "..", "..", "stock", "v1", "data", "results")
NEW = os.path.join(HERE, "tmp_alpha158", "port", "a158_portfolio_eval.csv")
OLD = os.path.join(RES, "ashare_portfolio_eval.csv")

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_colwidth", 52)

COLS = ["signal", "top_n", "n_rebal", "universe", "ann_return", "ann_return_gross",
        "sharpe", "max_drawdown", "one_way_turnover", "excess_univ_ew_ann",
        "excess_univ_ew_ir", "excess_sh000300_ann", "excl_worst_vs_pool_ann",
        "factor_coverage"]


def main():
    if not os.path.exists(NEW):
        print(f"缺 {NEW} ⇒ 环 2 还没跑完")
        return 1
    new = pd.read_csv(NEW)
    old = pd.read_csv(OLD)

    print("===== ⓪ 先验锚：四条生产构造必须与归档逐位相同 =====")
    bad = []
    for k in ["ann_return", "excess_univ_ew_ann", "one_way_turnover", "sharpe"]:
        j = new.merge(old, on=["expr", "top_n"], suffixes=("_new", "_old"))
        d = float((j[f"{k}_new"] - j[f"{k}_old"]).abs().max())
        print(f"  {k:22s} 可比 {len(j):3d} 格　最大差 {d:.3e}"
              + ("" if d < 1e-9 else "　←对不上"))
        if d >= 1e-9:
            bad.append((k, d))
    if bad:
        print("\n[判据失败] 锚对不上 ⇒ 本驱动改到了口径，下面所有候选读数作废")
        return 1
    print("  ⇒ 注入通道无失真，候选读数与归档同尺可比 ✅")

    cand = new[new["kind"] == "对照"].copy()
    print(f"\n===== ① 6 条候选的多头腿（做多低分侧，top50 / 100 / 200）=====")
    print(cand[cand.top_n == 50][COLS].to_string(index=False))

    print("\n===== ② 同一个信号换规模：超额是否只在 50 只这一档成立 =====")
    piv = cand.pivot_table(index="signal", columns="top_n",
                           values="excess_univ_ew_ann")
    piv["三档全为正"] = (piv > 0).all(axis=1).map({True: "是", False: "否"})
    print(piv.to_string())

    print("\n===== ③ 单调性（五分位年化，Q1=做多侧；本线认的是单调而不是首尾差）=====")
    q = cand[cand.top_n == 50][["signal", "q5_ann", "excl_worst_ann",
                                "excl_worst_vs_pool_ann"]]
    print(q.to_string(index=False))
    print("  注：Q1..Q5 全序列只在 158 那份日志里逐条打印（本表只留 Q5 与「剔最差五分位」）")

    print("\n===== ④ 已知失真（不列入决策）=====")
    print("  factor_matrices（ashare_screen.py:469）把 high/low 用 close 占位 ⇒")
    print("  KLEN=(high-low)/open 恒为 0、LOW0=low/close 恒为 1（整条变常数），")
    print("  MIN10=ts_min(low,10)/close 被静默换成 ts_min(close,10)/close（另一条因子）。")
    print("  这三条本表里的数字**不是**扫描里那条因子的读数，一律不采信。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
