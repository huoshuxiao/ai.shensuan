# -*- coding: utf-8 -*-
"""P1-4（09-27 凌晨）：筛名单这条路在回撤上已经量到头 ⇒ 去量**仓位/减仓层**。

来由：㊶ 第一次把 5 席层的回撤做成一条时间轴，读数是「六条腿最深回撤全部锁在
−53.6%~−57.9%，没有一条腿把回撤做出有意义的改善」；㊷㊸ 又证明名单独立闸搬到下单层
反而 −8.78pp/年 ⇒ **可投域、观察名单这两层都碰不到回撤，剩下能动仓位的只有「买多少」
这根旋钮**（本轮之前从没量过：生产永远是 5 席满仓 100%）。

本轮只量不接：不改 config、不改 src 一行，产物全落 `stock/v1/temp/tmp_dd_exposure_0927/`。

判据口径（一句解释）：**仓位 E ∈ [0,1]** = 那天拿多少比例的资金持这 5 只票，其余拿现金。
`E` 在**当天收盘**根据**只到那天为止**的历史算出来，**第二天**才生效（T+1 执行，不许偷看）；
每调一次仓位付一边手续费 `ASHARE_PORT_COST_ONE_WAY=15bp × |ΔE|`，记在生效那天。

量什么（M = 实测，全部只用过去数据）：
  M1          满仓不动                        —— 对照基准，所有「差」都减它
  M2-{20,60,120}  域等权净值 < 它自己的 W 日均线 ⇒ E=0（空仓）
  M3-60       同上但只减到 E=0.5（半仓，温和版）
  M4-{20,60}  组合**自己**的净值 < 它自己的 W 日均线 ⇒ E=0
  M5-{15,25}  波动目标：E = min(1, 目标年化σ ÷ 组合过去 20 日实际σ)
  M6-{15,25}  回撤断路器：满仓净值距峰值 ≤ −d ⇒ E=0.5，≤ −2d ⇒ E=0
  M7-{30,45}  市场宽度：可投域里「收盘 > 自己 20 日均线」的只数占比 < x ⇒ E=0

⚠️ 三处刻意的口径决定，读表前先记住：
① M4/M6 用的净值是 **M1 满仓那条**（外生），不是「减完之后自己那条」⇒ 没有反馈回路，
   代价是实盘上减仓后谷底更浅、触发更晚，这层二阶效应本表**读不出来**。
② 额外税按「每次调仓只付一边」记，与调仓日那笔换票费**相加**（真实盘部分重叠 ⇒
   本表把税**算重**，宁可多算）。
③ 均线/波动没值的头几天按 E=1 满仓走（不是空仓），免得开局白捡一段行情。

五道自检（断言不许恒真）：
  ① 锚点腿 A 复现归档最深回撤 −0.535676（时序与 ㉗㊷ 同一份 `replay_picks`）
  ② M1 与 `depth_compare.csv` 里 **B 行**的年化/超额/最深回撤逐格对上（参照值现读，不写死）
  ③ **无未来函数**：把历史截到任意一天 T 重算 E，T 之前每一天必须与全样本逐位相等
  ④ **恒等式**：E≡1 时叠加后的序列与 M1 逐位相等、且额外税恰好为 0
  ⑤ **正对照**：十二条 E 序列不能全都等于 M1（全等 = 这台秤压根没动过仓位）
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

import dd_gate_depth_0927 as G        # noqa: E402  同一份 prepare()
import dd_scale_0926 as D             # noqa: E402  同一份 replay/nav/dd

from config import (ASHARE_PORT_COST_ONE_WAY,   # noqa: E402
                    ASHARE_PORT_HOLD, ASHARE_SCREEN_QUANTILE)

OUT = os.path.join(HERE, "tmp_dd_exposure_0927")
os.makedirs(OUT, exist_ok=True)
PICKS_CACHE = os.path.join(HERE, "tmp_dd_gate_2023_0927", "picks_A_B_q08.pkl.gz")
REF_CSV = os.path.join(HERE, "tmp_dd_gate_depth_0927", "depth_compare.csv")
TRADING_DAYS = 252
COST = ASHARE_PORT_COST_ONE_WAY
LEG = "B"                    # 配额名单·无闸 = 09-27 退闸之后的生产形态


class Ctx:
    """喂给规则的历史。`end` 把序列截到那一天 ⇒ 「截断重算」就是无未来函数的证法"""

    def __init__(self, r, bench, close, univ, end=None):
        sl = slice(None) if end is None else slice(None, end)   # .loc 语义：含 end 那天收盘
        self.r = r.loc[sl]
        self.bench = bench.loc[sl]
        self.close = close.loc[sl]
        self.univ = univ.loc[sl]
        self.breadth = breadth_of(self.close, self.univ)


def breadth_of(close, univ, win=20):
    """市场宽度 = 可投域里「收盘价 > 自己 win 日日均线」的只数占比（只用 ≤当天收盘的数据）

    单点定义在这里，`Ctx` 构造时算一次、主表落盘复用同一份 —— 宽度这根尺子要是两处各写一遍，
    下次改窗口就等于悄悄换了一个判据。
    """
    sma = close.where(univ).rolling(win).mean()
    return ((close > sma) & univ).sum(axis=1) / univ.sum(axis=1).replace(0, np.nan)


def rules(ctx):
    """返回 [(标签, 人话判据, E 序列)]；E 在当天收盘定，由 `apply_e` 右移一天生效"""
    idx = ctx.r.index
    nav1 = (1.0 + ctx.r).cumprod()                        # M1 满仓净值（外生）
    nvb = (1.0 + ctx.bench.reindex(idx).fillna(0.0)).cumprod()

    def step(mask, e_low):
        return pd.Series(np.where(mask, e_low, 1.0), index=idx)

    breadth = ctx.breadth                               # 由 Ctx 统一算一次（截断重算时也一致）
    out = [("M1", "满仓不动（对照基准）", pd.Series(1.0, index=idx))]
    for w in (20, 60, 120):
        out.append((f"M2-{w}", f"域等权净值跌破自身 {w} 日均线 ⇒ 空仓",
                    step((nvb < nvb.rolling(w).mean()).fillna(False), 0.0)))
    out.append(("M3-60", "域等权净值跌破自身 60 日均线 ⇒ 减到半仓",
                step((nvb < nvb.rolling(60).mean()).fillna(False), 0.5)))
    for w in (20, 60):
        out.append((f"M4-{w}", f"组合净值跌破自身 {w} 日均线 ⇒ 空仓",
                    step((nav1 < nav1.rolling(w).mean()).fillna(False), 0.0)))
    vol20 = ctx.r.rolling(20).std() * np.sqrt(TRADING_DAYS)
    for t in (0.15, 0.25):
        out.append((f"M5-{int(t * 100)}", f"波动目标 {t:.0%}：E=min(1, {t:.0%}÷过去20日实际σ)",
                    (t / vol20).clip(upper=1.0).fillna(1.0)))
    dd1 = nav1 / nav1.cummax() - 1.0
    for d in (0.15, 0.25):
        e = pd.Series(1.0, index=idx)
        e[dd1 <= -d] = 0.5
        e[dd1 <= -2 * d] = 0.0
        out.append((f"M6-{int(d * 100)}", f"回撤断路器：满仓净值距峰 ≤−{2 * d:.0%} 空仓、≤−{d:.0%} 半仓", e))
    for x in (0.30, 0.45):
        out.append((f"M7-{int(x * 100)}", f"市场宽度（域内站上20日均线的占比）< {x:.0%} ⇒ 空仓",
                    step((breadth < x).fillna(False), 0.0)))
    return out


def apply_e(r, e_dec):
    """E 右移一天生效 + 调仓税 ⇒ (净日收益, 生效后的 E, 每日额外税)"""
    e = e_dec.shift(1).fillna(1.0)
    tax = (COST * e.diff().abs()).fillna(0.0)
    return r * e - tax, e, tax


def ann(s):
    p = s.dropna()
    return float(p.mean() * TRADING_DAYS)


def yearly(s, f):
    p = s.dropna()
    return p.groupby(p.index.year).apply(f)


def main():
    t0 = time.time()
    P = G.prepare()
    mtx, days, off, bench = P["mtx"], P["days"], P["off"], P["bench"]
    with gzip.open(PICKS_CACHE, "rb") as fh:
        picks = pickle.load(fh)
    if LEG not in picks or "A" not in picks:
        raise SystemExit(f"⇒ 缓存里没有 A/B 两条腿（现有 {sorted(picks)}）"
                         f"⇒ 先跑 dd_gate_depth_0927.py 建篮子")
    print(f"[名单] 复用缓存（改动 {time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(PICKS_CACHE)))}）"
          f"｜腿 {sorted(picks)}｜B = {len(picks[LEG])} 个调仓日")

    port_b, _ = D.replay_picks(days, mtx, picks[LEG], ASHARE_PORT_HOLD, COST, off)
    r = port_b.iloc[off:]
    idx = r.index
    univ = P["universe"].reindex(idx).fillna(False)
    close = mtx["close"].loc[idx]
    ex_full = bench.reindex(idx).fillna(0.0)

    # ---- 自检①：锚点腿 A 复现归档回撤（证明这份时序与 ㉗㊷ 同形）----
    a_net, _ = D.replay_picks(days, mtx, picks["A"], ASHARE_PORT_HOLD, COST, off)
    a_dd = float(D.dd_of(D.nav_of(a_net.iloc[off:])).min())
    print(f"\n[自检1·锚点腿 A] 最深回撤 {a_dd:.6f}｜归档 {D.ANCHOR_REF_DD:.6f}")
    if abs(a_dd - D.ANCHOR_REF_DD) > D.ANCHOR_TOL_DD:
        raise SystemExit("⇒ 锚点腿没复现归档 ⇒ 时序与 ㉗㊷ 不同形，本轮读数一律作废")
    print("⇒ 通过")

    # ---- 自检④：恒等式 E≡1 ⇒ 序列与税都必须原样不动 ----
    r_ones, _e1, tax1 = apply_e(r, pd.Series(1.0, index=idx))
    d4 = float((r_ones - r).abs().max())
    print(f"[自检4·恒等式] E≡1 vs M1 最大差 {d4:.3e}｜额外税合计 {float(tax1.sum()):.3e}")
    if d4 > 1e-15 or float(tax1.sum()) > 1e-15:
        raise SystemExit("⇒ E≡1 都不等于满仓 ⇒ 叠加写法在改收益本身，不是只改仓位")

    ctx_full = Ctx(r, bench, close, univ)
    rs = rules(ctx_full)

    # ---- 自检③：无未来函数（三个截面各重算一遍，不重复建 breadth 那台rolling）----
    probes = [idx[len(idx) // 3], idx[len(idx) * 2 // 3], idx[-40]]
    cut = {pr: {lab: e for lab, _w, e in rules(Ctx(r, bench, close, univ, end=pr))}
           for pr in probes}
    worst, where = 0.0, None
    for lab, _why, e_full in rs:
        for pr in probes:
            diff = float((e_full.loc[:pr] - cut[pr][lab].loc[:pr]).abs().max())
            if diff > worst:
                worst, where = diff, (lab, pr.date())
    print(f"[自检3·无未来函数] {len(rs)} 条规则 × {len(probes)} 个截断截面，"
          f"最大差 {worst:.3e}（{where}）")
    if worst > 1e-12:
        raise SystemExit("⇒ 截断重算与全样本不等 ⇒ 某条规则用了未来的数，全部作废")
    print("⇒ 通过（每条 E 都只由 ≤当天收盘的数据决定）")

    # ---- 主表 ----
    rows, yr_ex, yr_dd, navs, ecols = [], {}, {}, {}, {}
    for lab, why, e_dec in rs:
        net, e, tax = apply_e(r, e_dec)
        dd = D.dd_of(D.nav_of(net))
        rows.append({"规则": lab, "判据": why,
                     "年化净收益": ann(net),
                     "超额(vs 域等权)": ann(net - ex_full),
                     "波动": float(net.dropna().std() * np.sqrt(TRADING_DAYS)),
                     "最深回撤": float(dd.min()),
                     "Calmar": ann(net) / abs(float(dd.min())),
                     "水下天数占比": float((dd < 0).mean()),
                     "平均仓位": float(e.mean()),
                     "空仓天数占比": float((e <= 1e-9).mean()),
                     "调仓次数": int((e.diff().abs() > 1e-9).sum()),
                     "额外税(年化)": ann(tax)})
        navs[lab] = D.nav_of(net)
        ecols[lab] = e
        yr_ex[lab] = yearly(net - ex_full, lambda s: float(s.mean() * TRADING_DAYS))
        yr_dd[lab] = dd.groupby(dd.index.year).min()
    cmp_ = pd.DataFrame(rows).set_index("规则")
    b = cmp_.loc["M1"]
    cmp_["Δ年化 vs M1"] = cmp_["年化净收益"] - b["年化净收益"]
    cmp_["Δ回撤 vs M1"] = cmp_["最深回撤"] - b["最深回撤"]   # 正 = 回撤变浅 = 买回来了
    cmp_["Δ超额 vs M1"] = cmp_["超额(vs 域等权)"] - b["超额(vs 域等权)"]
    cmp_["每买回1pp回撤要付的年化"] = np.where(
        cmp_["Δ回撤 vs M1"] > 1e-6,
        -cmp_["Δ年化 vs M1"] / cmp_["Δ回撤 vs M1"], np.nan)

    # ---- 自检②：M1 必须复现 ㊷ 那张 CSV 的 B 行（参照值现读）----
    ref = pd.read_csv(REF_CSV, index_col=0).loc[LEG]
    chk = {"年化净收益": (b["年化净收益"], float(ref["年化净收益"])),
           "超额(vs 域等权)": (b["超额(vs 域等权)"], float(ref["超额(vs 域等权)"])),
           "最深回撤": (b["最深回撤"], float(ref["最深回撤"]))}
    print("\n[自检2·M1 == ㊷ 的 B 行]")
    for k, (got, want) in chk.items():
        print(f"  {k:<14} 本轮 {got:+.6f}｜㊷ {want:+.6f}｜差 {abs(got - want):.2e}")
        if abs(got - want) > 1e-6:
            raise SystemExit(f"⇒ {k} 对不上 ㊷ ⇒ 名单/回放不是同一批，全部作废")

    # ---- 自检⑤：正对照 ----
    same = [k for k in cmp_.index[1:] if abs(cmp_.loc[k, "Δ年化 vs M1"]) < 1e-12]
    print(f"\n[自检5·正对照] 年化与 M1 完全相同的规则 {len(same)}/{len(cmp_) - 1} 条：{same or '无'}")
    if len(same) == len(cmp_) - 1:
        raise SystemExit("⇒ 十二条 E 序列一条都没动过仓位 ⇒ 这台秤是空转的")

    yex, ydd = pd.DataFrame(yr_ex), pd.DataFrame(yr_dd)
    cmp_.to_csv(os.path.join(OUT, "exp_compare.csv"), index_label="规则")
    yex.to_csv(os.path.join(OUT, "exp_yearly_excess.csv"), index_label="年")
    ydd.to_csv(os.path.join(OUT, "exp_yearly_dd.csv"), index_label="年")
    pd.DataFrame(navs).to_csv(os.path.join(OUT, "exp_nav.csv.gz"), compression="gzip")
    pd.DataFrame(ecols).to_csv(os.path.join(OUT, "exp_exposure.csv.gz"), compression="gzip")
    # 日频三列单独落盘：以后换切法（分组、半年、择时vs择股）只读这张表，不必再花 86s 重跑面板
    pd.DataFrame({"M1净日收益": r, "域等权日收益": ex_full,
                  "市场宽度": ctx_full.breadth.reindex(idx)}).to_csv(
        os.path.join(OUT, "exp_daily.csv.gz"), compression="gzip")

    with pd.option_context("display.width", 300, "display.max_colwidth", 44,
                           "display.unicode.east_asian_width", True):
        print("\n===== 仓位层总账（每一行都是同一条 B 腿 × 同一个 E 旋钮，差一律减 M1）=====")
        print(cmp_.to_string(float_format=lambda v: f"{v:+.4f}"))
        print("\n===== 逐年超额（年化，vs 域等权）=====")
        print(yex.to_string(float_format=lambda v: f"{v:+.3f}"))
        print("\n===== 逐年最深回撤 =====")
        print(ydd.to_string(float_format=lambda v: f"{v:+.3f}"))
    print(f"\n[读数] 面板口径 {ASHARE_SCREEN_QUANTILE}｜交易日 {len(idx)} 天"
          f"｜{idx[0].date()} ~ {idx[-1].date()}")
    print(f"[输出] {OUT}/ 五张表　总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
