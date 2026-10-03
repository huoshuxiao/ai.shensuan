# -*- coding: utf-8 -*-
"""P1-4 追加（09-27）：仓位层规则说「清仓走人」的那些天，**A 股到底让不让你走**？

来由：`dd_exposure_0927.py` 十二档里 M7-30 同时「多赚 + 大幅降回撤」，而它的探针
（`dd_exposure_m7probe_0927.py` ④）已经证明同一根仓位叠在**域等权基准**上涨得更多
（+36.4pp、回撤买回 47.0pp）⇒ 这笔钱是**择市**不是择股。剩下的最后一问不是「信号准不准」，
而是「信号说走的那天，A 股让不让你走」：
   千股跌停的清晨，5 只票集体**一字跌停 / 无成交** ⇒ 卖不掉 ⇒ 那 −19.9% 的回撤是纸上的。
生产链路从来只判**买入侧**（`tradable_mask` 第 4 道闸 = 次日开盘跳**升** ≥ 涨停阈值 ⇒ 买不进），
**卖出侧从没量过** —— 因为生产是满仓不动、从不整体出场。本轮就是把这笔从没量过的账补上。

被审的三档（都真的会整仓退出，所以都有「走不掉」风险）：
- M4-20 组合净值跌破自身 20 日均线 ⇒ 空仓（一年空仓 42%、313 次调仓，本轮新账）
- M7-30 / M7-45 市场宽度 < x ⇒ 空仓（纸面最好看的那两档）

判据（全部复用单点，不另写一套）：
- 宽度与生效仓位：`dd_exposure_0927.Ctx / breadth_of / rules / apply_e`（E 收盘定、次日生效、|ΔE| 收一边费）
- 每天手里是哪 5 只：`dd_scale_0926.replay_picks` 的 `day_hold`
- 涨跌停阈值：`ashare_screen.gate_vector`（生产同一根 `ASHARE_TRADABLE_GATE`，按板块、按生效日）
- 「卖不掉」= 当天开盘相对昨收跳**跌** ≤ −阈值；「无成交」= 当天真实手数为 0 或缺值
- 「买不进」= 跳**升** ≥ +阈值（对照用：进场那天）
- **基线正对照**：每一档各抽同样多的普通日子（抽自三档出场日的并集之外），量同一件事。
  没有基线，「出场日 12% 卖不掉」这句话没有任何含义 —— 如果随便一天也是 12%，那就不是减仓的代价。
  逐档配平（而不是共用一条基线）是为了表里每一行都是同一口径的对照。

产物 `tmp_dd_exposure_0927/exec_{transitions,baseline,出场明细,基线明细}.csv`。不改生产一行。
"""

import gzip
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "stock/v1/src"))
import _bootstrap  # noqa: E402,F401

import dd_exposure_0927 as X                       # noqa: E402  宽度/E/税 的单点在这
import dd_gate_depth_0927 as G                      # noqa: E402  同一份 prepare()
import dd_scale_0926 as D                            # noqa: E402  同一份 replay_picks

from config import ASHARE_PORT_COST_ONE_WAY, ASHARE_PORT_HOLD   # noqa: E402
from ashare_screen import board_of, gate_vector                  # noqa: E402

OUT = os.path.join(HERE, "tmp_dd_exposure_0927")
PICKS_CACHE = os.path.join(HERE, "tmp_dd_gate_2023_0927", "picks_A_B_q08.pkl.gz")
LABELS = ("M4-20", "M7-30", "M7-45")
FORWARD = 5                 # 卖不掉之后按 5 个交易日的跌幅计代价（= 一个持仓段）
RNG = np.random.default_rng(20260927)


def blocked_at(mtx, day, prev_day, codes, side):
    """这批票在 `day` 开盘时按板块阈值是否贴板；side="sell" 看跳跌、"buy" 看跳升"""
    lim = np.asarray(gate_vector(pd.Index(codes), day), dtype="float64")
    if not np.isfinite(lim).all():
        raise SystemExit(f"⇒ {day} 有票算不出限幅阈值 ⇒ 「卖不掉」判据会静默放行，别看")
    gap = (mtx["open"].loc[day, codes].to_numpy(dtype="float64")
           / mtx["close"].loc[prev_day, codes].to_numpy(dtype="float64") - 1.0)
    hit = (gap <= -lim) if side == "sell" else (gap >= lim)
    novol = ~(mtx["volume_real"].loc[day, codes].to_numpy(dtype="float64") > 0)
    return gap, np.nan_to_num(hit, nan=False).astype(bool), novol.astype(bool), lim


def forward_loss(mtx, codes, day, k=FORWARD):
    """卖不掉 ⇒ 之后 k 个交易日累计跌了多少（开盘可卖那天为止的账面损失）"""
    days = mtx["close"].index
    i = int(days.get_loc(day))
    lo, hi = i + 1, min(i + 1 + k, len(days) - 1)
    out = []
    for c in codes:
        s = mtx["ret_open"].iloc[lo:hi][c]
        out.append(np.nan if s.isna().all() else float(s.sum()))
    return np.array(out)


def observe(mtx, days, pos_of, day_hold, dset, side):
    """逐日量「这些天手里正好是那 5 只」的开盘情况；返回 (汇总, 明细行)

    贴板判据按 `side` 翻向，所以出场（卖）与回场（买）走同一个函数、同一份阈值。
    明细里的「贴板」列对出场 = 卖不掉，对回场 = 买不进；只有出场侧还算后 5 日跌幅。
    """
    seats, block, novol = 0, 0, 0
    gaps, losses, det = [], [], []
    for d in dset:
        codes = day_hold.get(d)
        if not codes or pos_of[d] == 0:      # 第一天没有「昨收」可比，跳过
            continue
        gap, hit, nov, _lim = blocked_at(mtx, d, days[pos_of[d] - 1], codes, side)
        seats += len(codes)
        block += int(hit.sum())
        novol += int(nov.sum())
        gaps.append(float(np.nanmean(gap)))
        stuck = [c for c, h in zip(codes, hit) if h]
        loss = float(np.nansum(forward_loss(mtx, stuck, d))) if stuck else 0.0
        losses.append(loss)
        det.append({"日": d.date(), "席位": len(codes), "贴板": int(hit.sum()),
                    "无成交": int(nov.sum()), "开盘跳空均值": float(np.nanmean(gap)),
                    "贴板票后5日合计": loss})
    agg = {"场次": len(det), "涉及席位": seats,
           "贴板占比": block / seats if seats else np.nan,
           "无成交占比": novol / seats if seats else np.nan,
           "开盘跳空均值": float(np.mean(gaps)) if gaps else np.nan,
           "有贴板的天数": sum(1 for v in det if v["贴板"] > 0),
           "贴板票后5日合计跌幅_场均": float(np.mean(losses)) if losses else 0.0}
    return agg, det


def main():
    t0 = time.time()
    P = G.prepare()
    mtx, days, off, bench = P["mtx"], P["days"], P["off"], P["bench"]
    with gzip.open(PICKS_CACHE, "rb") as fh:
        picks = pickle.load(fh)["B"]
    net, day_hold = D.replay_picks(days, mtx, picks, ASHARE_PORT_HOLD,
                                   ASHARE_PORT_COST_ONE_WAY, off)
    r = net.iloc[off:]
    idx = r.index
    univ = P["universe"].reindex(idx).fillna(False)
    ctx = X.Ctx(r, bench, mtx["close"].loc[idx], univ)     # 宽度只在这一个地方定义
    pos_of = {d: i for i, d in enumerate(days)}
    dec = {lab: e for lab, _w, e in X.rules(ctx)}    # E 判据只在 `rules()` 一处，这里只挑要审的

    rows, det_out = [], {}
    for lab in LABELS:
        _net, e, _tax = X.apply_e(r, dec[lab])
        full_out = [d for d, v, pv in zip(e.index, e, e.shift(1))
                    if v <= 1e-9 and pv > 0.5]        # 整仓退出（前一天还在满仓）
        en_days = list(e.index[(e.diff() > 1e-9) & (e.shift(1) <= 1e-9)])
        a_out, d_out = observe(mtx, days, pos_of, day_hold, full_out, "sell")
        a_in, _d_in = observe(mtx, days, pos_of, day_hold, en_days, "buy")
        if a_out["场次"] == 0:
            raise SystemExit(f"⇒ {lab} 一场出场都没量到 ⇒ 这一行是空转，别看")
        rows.append({"规则": lab,
                     "出场日数(E→0)": len(full_out), **{f"出场·{k}": v for k, v in a_out.items()},
                     "回场日数(0→1)": len(en_days),
                     "回场·场次": a_in["场次"],
                     "回场·贴涨停买不进占比": a_in["贴板占比"],
                     "空仓日占比": float((e <= 1e-9).mean()),
                     "调仓次数(|ΔE|>0)": int((e.diff().abs() > 1e-9).sum())})
        det_out[lab] = d_out

    tab = pd.DataFrame(rows).set_index("规则")

    # ---- 基线正对照：随机抽普通日子、手持同一批票，量同一件事 ----
    # 抽一次、按各档出场场次取前缀 ⇒ 三行的基线是嵌套样本，可比且可复现
    all_out = {r["日"] for v in det_out.values() for r in v}
    pool = [d for d in idx[1:] if d.date() not in all_out and day_hold.get(d)]
    need = int(tab["出场·场次"].max())
    if not pool or need == 0:
        raise SystemExit("⇒ 出场日或候选池为空 ⇒ 基线无从抽起，这台秤空转")
    order = RNG.permutation(len(pool))
    sampled = [pool[int(k)] for k in order[:min(need, len(pool))]]
    base_rows = []
    for d in sampled:
        _a, one = observe(mtx, days, pos_of, day_hold, [d], "sell")
        base_rows += one
    if len(base_rows) < need:
        raise SystemExit(f"⇒ 基线只跑出 {len(base_rows)} 场 < 需要的 {need} 场 ⇒ 正对照不成立")
    bt = pd.DataFrame(base_rows)

    btab = []
    for lab in LABELS:
        n = int(tab.loc[lab, "出场·场次"])
        b = bt.iloc[:n]
        seats = int(b["席位"].sum())
        btab.append({"规则": lab, "基线·场次": n, "基线·涉及席位": seats,
                     "基线·贴跌停卖不掉占比": float(b["贴板"].sum() / seats),
                     "基线·无成交占比": float(b["无成交"].sum() / seats),
                     "基线·开盘跳空均值": float(b["开盘跳空均值"].mean()),
                     "卖不掉占比−基线": float(tab.loc[lab, "出场·贴板占比"])
                                          - float(b["贴板"].sum() / seats)})
    b_tab = pd.DataFrame(btab).set_index("规则")
    tab = tab.join(b_tab.drop(columns=["基线·场次", "基线·涉及席位"]))

    with pd.option_context("display.width", 260):
        print("\n===== 出场侧可执行性：三档减仓规则整仓清掉的那些天（对照=随机普通日子）=====")
        print(tab.to_string(float_format=lambda v: f"{v:+.4f}"))
        print("\n===== 基线本身（逐档配平的抽样）=====")
        print(b_tab.to_string(float_format=lambda v: f"{v:+.4f}"))
    tab.to_csv(os.path.join(OUT, "exec_transitions.csv"), index_label="规则")
    b_tab.to_csv(os.path.join(OUT, "exec_baseline.csv"), index_label="规则")
    pd.concat([pd.DataFrame(v).assign(规则=k) for k, v in det_out.items()]).to_csv(
        os.path.join(OUT, "exec_出场明细.csv"), index=False)
    bt.to_csv(os.path.join(OUT, "exec_基线明细.csv"), index=False)
    print(f"\n[输出] {OUT}/exec_transitions.csv｜exec_baseline.csv｜exec_出场明细.csv"
          f"｜exec_基线明细.csv　{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
