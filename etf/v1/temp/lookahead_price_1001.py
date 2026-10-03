# -*- coding: utf-8 -*-
"""10-01 量一笔：在库 active 因子 `volatility_breakout_momentum` 的「偷看下一根」值多少钱。

动机：改完共享求值层（`ma/std/max/min` 收 Series）后，用同一把尺子扫在库表达式，
发现这条 LLM 产出的 active 因子只在放宽之后才算得出，而且它写的是
`delay(max(high, 5), -1)` —— `delay(s, -1) = s.shift(-1)`，读的是**下一根 K 线**；
它的标签又是 `close.pct_change().shift(-1)`（t→t+1 的收益），
⇒ 因子值里直接含进了决定标签的那根价。拦住这类写法的静态闸 `check_lookahead`
只在默认关闭的多角色定稿闸里被调用（`hypothesis_roles.py:320` 是它唯一的调用点），
生产准入链（IC 过线 → 写库）不经过它。

本脚本只读取数，不改库、不改判据。三臂同池同尺，只换 delay 的窗口位：
  V0 在库原样        delay(...,-1)  今天的读数
  V1 同一根（去掉 delay）           合法写法里与 V0 最像的一臂
  V2 滞后一根        delay(..., 1)  真正可成交的写法
对照 CTRL 不含任何 delay ⇒ 三臂必须逐位相同，用来证明「差异是 -1 那个窗口给的，
不是尺子造的」。

口径边界（必读）：库里那条 `ic=0.0383` 是生产链**折内池子**算的，本脚本用日线缓存，
两把尺子的池子不同 ⇒ 0.0383 与这里的任何一臂都不可直接对表；本脚本要的是
**同池三臂的差**，那个差才是「偷看值多少钱」。
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

from factor_dsl import compute_ic, safe_eval  # noqa: F402,E402
from config import IC_THRESHOLD  # noqa: F402,E402

CACHE = os.path.join(ROOT, "common/data/etf/cache")
GATE_POOL = ["510300", "510500", "159516", "512480", "518880"]

ARMS = [
    ("V0 在库原样 delay(-1)",
     "delay(max(high, 5), -1) / ma(df, 90) - delay(min(low, 5), -1) / ma(df, 90)"),
    ("V1 去掉 delay（同一根）",
     "max(high, 5) / ma(df, 90) - min(low, 5) / ma(df, 90)"),
    ("V2 合法滞后 delay(1)",
     "delay(max(high, 5), 1) / ma(df, 90) - delay(min(low, 5), 1) / ma(df, 90)"),
    ("CTRL 无 delay 对照",
     "rank(high - low, 60) - rank(delta(close, 5), 20)"),
    # 正对照：**就是标签本身**。若尺子看不见偷看，这条会给出 0 而不是 +1，
    # 那「V0 只虚高一点」的读数就没有牙齿（09-28 教训：差异必须活到判据那一层）。
    ("LEAK 标签原样（正对照，应≈+1）",
     "delay(close, -1) / close - 1"),
]


def load_codes(codes):
    pool = {}
    for c in codes:
        p = os.path.join(CACHE, f"{c}_daily.csv")
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
        df.columns = [str(x).lower() for x in df.columns]
        if len(df) >= 300:
            pool[c] = df
    return pool


def mean_ic(expr, pool):
    """与生产尺子同式：逐标的 compute_ic(f, close.pct_change().shift(-1)) 取算术均值。"""
    ics = []
    for code, df in pool.items():
        try:
            f = safe_eval(expr, df)
            ic = compute_ic(f, df["close"].pct_change().shift(-1))
        except Exception as e:
            print(f"    [{code}] 求值失败 {type(e).__name__}: {e}")
            continue
        if not np.isnan(ic):
            ics.append(ic)
    return (float(np.mean(ics)) if ics else float("nan"), len(ics))


def main():
    codes_all = sorted(x[:6] for x in os.listdir(CACHE) if x.endswith("_daily.csv"))
    pools = {"M1 闸门口径那 5 只": load_codes(GATE_POOL),
             f"M2 全部缓存 {len(codes_all)} 只": load_codes(codes_all)}
    rc = 0
    print(f"# 尺子 IC_THRESHOLD={IC_THRESHOLD}｜标签 close.pct_change().shift(-1)")
    for pname, pool in pools.items():
        print(f"\n## {pname}（实得 {len(pool)} 只，"
              f"平均 {int(np.mean([len(d) for d in pool.values()]))} 根 K 线）")
        got = {}
        for label, ex in ARMS:
            m, n = mean_ic(ex, pool)
            got[label] = m
            print(f"  {label:24s} mean_ic={m:+.4f}  标的数={n}  "
                  f"过线={abs(m) >= IC_THRESHOLD}")
        d01 = got[ARMS[0][0]] - got[ARMS[1][0]]
        d02 = got[ARMS[0][0]] - got[ARMS[2][0]]
        print(f"  → 偷看下一根带来的 IC 虚高：V0−V1={d01:+.4f}   V0−V2={d02:+.4f}")
        # 牙 1：尺子必须**看得见**偷看。LEAK 臂就是标签本身，读不出 +1 说明这把尺子
        #        是死的，上面那句「虚高多少」当场作废。
        if not (got[ARMS[4][0]] >= 0.9):
            print(f"  🚫 正对照 LEAK mean_ic={got[ARMS[4][0]]:+.4f} 未接近 +1，"
                  f"尺子看不见未来函数 ⇒ 本表读数不可用")
            rc = 1
        # 牙 2：结论建立在「delay(s,-1) = 取下一根」这条语义上，直接在真数据上核一遍
        any_df = next(iter(pool.values()))
        a = safe_eval("delay(close, -1)", any_df)
        b = any_df["close"].shift(-1)
        if float(np.abs(a.dropna() - b.dropna()).max()) != 0.0:
            print("  🚫 delay(close,-1) 与 close.shift(-1) 不逐位相同，语义假设崩了")
            rc = 1
        # 读数（不是判据）：三臂无差 → 这个池子上偷看不值钱，如实报，不当脚本故障
        if not np.isnan(d01) and abs(d01) < 1e-6:
            print("  ⚠️ V0 与 V1 无差：这条差异在此池上不成立，别拿去当结论")
    print(f"\nEXIT={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
