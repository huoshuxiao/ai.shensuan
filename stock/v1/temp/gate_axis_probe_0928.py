# -*- coding: utf-8 -*-
"""探针（09-28）：换排序轴会不会连带换掉闸门覆盖面 —— 把「两臂数字一样」证死

B 路那张表里「旧轴·只换排序键」和「旧轴·闸门也跟轴」两臂**逐格相同**。这句话有两种读法：
  (i) 真的等价：`tradable_mask` 第 1 道闸判的是「传入因子当日有值」，而
      `ts_mean(volume,20)` 与 `ts_std(volume,20)` 吃的是**同一列 $volume、同一个 20 日窗**
      ⇒ 两者的 NaN 位置天生一模一样 ⇒ 闸门 Nobody 换；
  (ii) 假的等价：我的注入根本没生效，两臂其实都用了现轴。

单看那张表分不出 (i) 还是 (ii)，所以这里直接量：
  P1  两根表达式在全矩阵上的 NaN 位置：不同格子数必须 = 0
  P2  正对照（矩阵层）：`ts_std(volume,5)` 与 `ts_mean(volume,20)` 的 NaN 位置必须**不同**
      （>0）。若它也相同，说明 P1 的比较是恒真的，P1 的绿不作数
  P3  活体闸门：5 个抽样调仓日上分别用不同因子跑 `tradable_mask` 比可选池
      - 跟轴那臂（`ts_std(volume,20)`）与固定那臂（`ts_mean(volume,20)`）必须**零差异**；
      - 正对照必须**有差异** —— 用的是 `ts_std(volume,250)`：它的额外缺口落在
        「已上市 60~249 天」这批**过得了次新闸**的票上，所以能在池子上显形。
        ⚠️ 换**短**窗（`ts_std(volume,5)`）在这里**测不出差**（实测 5 天全部零差异），
        因为短窗多出来的覆盖全是上市不足 60 天的新票，那些票本来就被
        `ASHARE_PORT_MIN_LISTED=60` 那道闸杀掉了 ⇒ 它只能当矩阵层的对照，别当闸门层的。

任何一条不成立 → exit=1。
"""
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, SRC)

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401

from config import ASHARE_PORT_MIN_LISTED
from ashare_screen import build_matrices, factor_matrices, load_panel, tradable_mask

CUR = "ts_mean(volume,20)"
OLD = "ts_std(volume,20)"
CTRL_S = "ts_std(volume,5)"      # 矩阵层正对照：同列、更短窗 ⇒ NaN 该更少
CTRL_L = "ts_std(volume,250)"    # 闸门层正对照：同列、更长窗 ⇒ 缺口落在次新闸之上


def nan_diff(a, b):
    va, vb = a.to_numpy(), b.to_numpy()
    return int((np.isnan(va) != np.isnan(vb)).sum()), va.shape


def main():
    wide, _ = load_panel()
    mtx = build_matrices(wide)
    fms = factor_matrices([CUR, OLD, CTRL_S, CTRL_L], mtx)
    days = mtx["ret_open"].index
    n_cells = fms[CUR].size

    d1 = nan_diff(fms[CUR], fms[OLD])
    d2 = nan_diff(fms[CUR], fms[CTRL_S])
    d3 = nan_diff(fms[CUR], fms[CTRL_L])
    print(f"[P1] {CUR} vs {OLD}：NaN 位置不同 {d1[0]} 格 / 共 {n_cells:,} 格 {d1[1]}")
    print(f"[P2] {CUR} vs {CTRL_S}：NaN 位置不同 {d2[0]} 格（矩阵层正对照，必须 >0）")
    print(f"[P2b] {CUR} vs {CTRL_L}：NaN 位置不同 {d3[0]} 格（闸门层对照的前置条件）")

    step = max(len(days) // 5, 1)
    probe_days = [days[i] for i in (1, step, 2 * step, len(days) - 3, len(days) - 2)]
    pool = {}
    for f in (CUR, OLD, CTRL_S, CTRL_L):
        for s in probe_days:
            if s == days[-1]:
                continue
            ok, _n = tradable_mask(s, days[days.get_loc(s) + 1], fms[f], mtx)
            pool[(f, s)] = set(ok[ok].index)
    same_cur_old = sum(len(pool[(CUR, s)] ^ pool[(OLD, s)]) for s in probe_days)
    diff_long = sum(len(pool[(CUR, s)] ^ pool[(CTRL_L, s)]) for s in probe_days)
    diff_short = sum(len(pool[(CUR, s)] ^ pool[(CTRL_S, s)]) for s in probe_days)
    for s in probe_days:
        print(f"[P3] {s.date()}：池 {len(pool[(CUR, s)])} 只（现轴）｜跟轴差 "
              f"{len(pool[(CUR, s)] ^ pool[(OLD, s)])} 只｜长窗差 "
              f"{len(pool[(CUR, s)] ^ pool[(CTRL_L, s)])} 只｜短窗差 "
              f"{len(pool[(CUR, s)] ^ pool[(CTRL_S, s)])} 只"
              f"｜上市≥{ASHARE_PORT_MIN_LISTED} 日这道闸把短窗多出来的新票全吃掉")

    bad = []
    if d1[0] != 0:
        bad.append(f"P1 两根轴 NaN 不同 {d1[0]} 格 ⇒ 换轴会改闸门覆盖面")
    if d2[0] == 0:
        bad.append("P2 正对照没牙：换窗长都测不出 NaN 差 ⇒ 比较恒真，本探针不作数")
    if d3[0] == 0:
        bad.append("P2b 长窗对照在矩阵层都没差 ⇒ 它也不配当闸门层的对照")
    if same_cur_old != 0:
        bad.append(f"P3 活体闸门：跟轴与固定两臂的可选池差 {same_cur_old} 只次")
    if diff_long == 0:
        bad.append("P3 正对照：长窗的闸门池与现轴完全相同 ⇒ 闸门层的比较没牙")

    print("\n[判据] " + ("全部成立 ⇒ 换排序轴**不会**连带换闸门覆盖面（等价是真的，不是注入坏了）"
          f"；顺带记下：短窗对照在闸门层测不出差（{diff_short} 只次），原因见上面那行"
          if not bad else "❌ " + "；".join(bad)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
