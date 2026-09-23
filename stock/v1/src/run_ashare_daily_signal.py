# -*- coding: utf-8 -*-
"""股票线日频信号：今天哪些票**不该买**（量能族剔除名单）。

定位先说清楚，免得被当成选股器用：本脚本只出「剔除」，不出「买入」。依据是
09-23 的组合层复核（data/results/ashare_portfolio_eval.csv）——量能族截面 IC 为负
（放量/爆量是反向指标），做多低分侧的超额与「低价股」对照同量级、换手却是它的 4~6 倍，
那不是量能自带的信息；而「把最响的那 20% 从可投池里踢掉」有独立依据（水平/波动两条
+4.0%~+4.5%/年 vs 对照 +1.8%~+2.0%），且被踢的票本来也不该买，剔除是减法等权、
不产生新买入。所以给人工下单的信号只有减法。

口径全部复用 strategy/ashare_screen.py（与回测入口同一份实现），此处只做三件事：
1) 取面板最后一个交易日（或 STOCK_SIGNAL_DATE 指定的历史日）为信号日 s；
2) 跑闸门 + 四构造剔除，逐条构造给出当日截面分位；
3) 落一份 per-标的 名单 CSV 到 ASHARE_SIGNAL_DIR，打印汇总。

时序上的一个诚实缺口：信号日若是面板最后一天，第二天开盘价还不存在，
「涨停买不进」这道闸没法前瞻（tradable_mask 的 d1=None 分支），只能到第二天
开盘前人工确认——这与本系统「出信号 → 人工下单」的形态正好吻合。指定历史日
则会补上这道闸，可用于事后核对某天的名单到底长什么样。

只读行情，不写因子库、不下单、不触发 git 提交。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import os
import sys
import time

import numpy as np
import pandas as pd

from config import (ASHARE_SCREEN_QUANTILE, ASHARE_SIGNAL_DIR,
                    ASHARE_PORT_MIN_AMOUNT)
from ashare_screen import (RULE_EXPRS, VOLUME_RULES, build_matrices,
                           factor_matrices, load_panel, screen_on_date)

SIGNAL_DATE = os.environ.get("STOCK_SIGNAL_DATE", "")   # 空 = 面板最后一天


def pick_date(index):
    """信号日与它的下一个交易日；下一个不存在就交 None 让人工确认涨停"""
    if SIGNAL_DATE:
        d = pd.Timestamp(SIGNAL_DATE)
        if d not in index:
            raise SystemExit(f"[信号] STOCK_SIGNAL_DATE={d.date()} 不在面板交易日里，"
                             f"面板区间 {index[0].date()} ~ {index[-1].date()}")
        i = index.get_loc(d)
    else:
        i = len(index) - 1
    d1 = index[i + 1] if i + 1 < len(index) else None
    return index[i], d1


def main():
    t0 = time.time()
    wide, _bench = load_panel()
    mtx = build_matrices(wide)
    del wide
    rule_mats = factor_matrices(RULE_EXPRS, mtx)
    s, d1 = pick_date(mtx["close"].index)
    print(f"[信号] 信号日 {s.date()}，下一交易日 "
          + (f"{d1.date()}（涨停闸生效）" if d1 is not None else "未到（涨停闸留给人工）"))

    gate = rule_mats[VOLUME_RULES[0][2]]          # 闸门用「量能水平」那条判因子有值
    r = screen_on_date(s, d1, mtx, rule_mats, gate, ASHARE_SCREEN_QUANTILE)
    detail, keep, universe = r["detail"], r["keep"], r["universe"]

    # 收盘价与成交额只为人读盘服务，不参与任何筛选判据，故在入口侧补进明细，
    # 不塞进 ashare_screen（那里只放规则）。close 用 raw_price（盘面真实报价）而非
    # 面板复权价：人工下单要能跟券商行情对上，茅台 09-22 复权 304.93 / 盘面 1253.79。
    # amount20_yi 别当市场行情读：本面板 68/30 段 $factor 失真，绝对成交额比真实市场
    # 大一个数量级（判据与量化过程见 ashare_screen 模块 docstring 的「数据口径」段）。
    detail = detail.copy()
    detail.insert(0, "close", mtx["raw_price"].loc[s].reindex(detail.index))
    detail.insert(1, "amount20_yi",
                  mtx["amount20"].loc[s].reindex(detail.index) / 1e8)   # 亿元，人工读盘方便
    out = detail.reset_index(names="code")
    os.makedirs(ASHARE_SIGNAL_DIR, exist_ok=True)
    path = os.path.join(ASHARE_SIGNAL_DIR, f"signal_{s:%Y%m%d}.csv")
    out.to_csv(path, index=False)

    print(f"\n===== {s.date()} 剔除名单（阈值 pct >= {ASHARE_SCREEN_QUANTILE}，四构造取并集） =====")
    print(f"过闸门可投池 {int(universe.sum())} 只　"
          f"被量能族剔除 {int(r['dropped'].sum())} 只 "
          f"({r['dropped'].sum() / max(universe.sum(), 1):.1%})　保留 {int(keep.sum())} 只")
    print(f"涨停剔除（仅历史日可判）{r['n_limit_up']} 只")

    print("\n逐条构造命中（池内，分位 >= 阈值即踢）：")
    for _k, cn, _e, doc in VOLUME_RULES:
        if cn not in detail.columns:
            continue
        fired = detail[cn] >= ASHARE_SCREEN_QUANTILE
        print(f"  {cn:5s} 踢 {int(fired.sum()):4d} 只　"
              f"命中组当日成交额中位 {detail.loc[fired, 'amount20_yi'].median():.2f} 亿 vs "
              f"保留组 {detail.loc[~fired & detail[cn].notna(), 'amount20_yi'].median():.2f} 亿"
              f"　｜ {doc}")
    # 保留池按 20 日均额降序给个「先看得到的」顺序。**这不是买入排序**，
    # 多头腿没过组合层验证，任何按因子排出来的顺序都不构成买入建议
    head = out[out["keep"]].sort_values("amount20_yi", ascending=False)
    print(f"\n保留池前 20（按 20 日均成交额降序，只为可读，**非买入建议**）：")
    cols = ["code", "close", "amount20_yi", "keep"]
    print(head[cols].head(20).to_string(index=False))
    print(f"\n[输出] {path}（{len(out)} 行）")
    print(f"[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
