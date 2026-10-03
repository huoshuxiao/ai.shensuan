# -*- coding: utf-8 -*-
"""选项4（09-27）：名单独立闸 2023 那一年少赚的 50pp，到底是「剔错了票」还是「踩空」。

来由：`dd_gate_depth_0927.py` 量出四档刀口在 5 席下单层全为负，其中 2023 一年
−36~−50pp 是所有深度共同的硬伤 —— 但它只回答「少赚多少」，没回答**为什么少赚**。
这一场把差额拆成两笔互不重叠的账：

  ① 税（换手费）：C 比 B 多换出来的手续费。逐场 `2×单边费率×(1−与上一场的重叠/5)`，
     与回放实现同一个式子，不是估的。
  ② 选股（谁被换进来、谁被挤出去）：同一调仓日内的点差
         点差 = ( Σ 换进那几只的持仓段收益 − Σ 被挤掉那几只的持仓段收益 ) / 5
     这是**精确恒等式**：两篮都是 5 席等权，共同席位在 C−B 里相消，只剩换掉的那几只。

年化层面再对一次总账（同一份回放，cost 传 0 与传真值两条序列 ⇒ 差是恒等而非近似）：
    净差 = 毛差 − 税差        ← 判据：|净差 −(毛差 − 税差)| < 1e-12，不成立就作废

四道不恒真的自检：
  1. 锚点腿 A 的最深回撤必须复现归档 −0.535676（否则拆分用的时序与 ㉗ 不同形）；
  2. 本场逐年的「超额净差」必须与上一场 depth_yearly.csv 的 q0.8−B 逐格对上
     （复用 prepare/build_picks 有没有走形，全押在这一格上）；
  3. 2023 必须确实有换血场次（一场都没换 = 归因无从谈起，直接作废）；
  4. **正对照 = 2015**（四档里唯一的大正年 +23.25pp）：2015 的「换进−挤掉」应当为正、
     2023 应当为负。两年同号 ⇒ 这套拆分分不出好坏，只能回去改拆法（此条只判不杀，
     因为前三道已经锁住读数资格）。
"""

import collections
import gzip
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dd_scale_0926 as D                    # noqa: E402  同一份回放/nav/dd
import dd_gate_depth_0927 as G               # noqa: E402  同一份 prepare/build_picks

OUT = os.path.join(HERE, "tmp_dd_gate_2023_0927")
os.makedirs(OUT, exist_ok=True)
BASE, GATE = "B", "q0.8"          # 对照基准 = 无闸；被审的 = 生产刀口 0.80
PREV_CSV = os.path.join(HERE, "tmp_dd_gate_depth_0927", "depth_yearly.csv")
# 篮子缓存：换切法（换年份、换票级问法）时不必再花 63s 重算名单；只认 env 才复用
PICKS_CACHE = os.path.join(OUT, "picks_A_B_q08.pkl.gz")
TRADING_DAYS = 252
HOLD = D.ASHARE_PORT_HOLD
COST = D.ASHARE_PORT_COST_ONE_WAY


def seg_return(ret, col_of, name, lo, hi):
    """持仓段 [lo, hi) 内这一只票贡献的累计日收益（open→open，**不含费**）

    **取「日收益直接相加」而不是复利连乘**：回放里权重每天重置成 1/5（`w[lo:hi, col] = 1/5`），
    所以组合段收益 = 各票「日收益之和」的平均，只有用同一种加法，点差那条恒等式才对得上回放。
    任一天缺行情就返回 NaN —— 回放序列里那天本来也是 NaN，两边同进同退才对得上。
    """
    r = ret[lo:hi, col_of[name]]
    if np.isnan(r).any():
        return np.nan
    return float(r.sum())


def ann(series):
    p = series.dropna()
    return float(p.mean() * TRADING_DAYS)


def main():
    t0 = time.time()
    P = G.prepare()
    if os.environ.get("STOCK_ATTR_REUSE_PICKS") == "1" and os.path.exists(PICKS_CACHE):
        with gzip.open(PICKS_CACHE, "rb") as fh:
            picks = pickle.load(fh)
        print(f"[名单] 复用缓存 {PICKS_CACHE}（改动时间 "
              f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(PICKS_CACHE)))}）"
              f"⇒ 想重算篮子就删掉它或去掉 env STOCK_ATTR_REUSE_PICKS")
    else:
        picks = G.build_picks(P, ["A", BASE, GATE])
        with gzip.open(PICKS_CACHE, "wb") as fh:
            pickle.dump(picks, fh)
        print(f"[名单] 已缓存 → {PICKS_CACHE}")
    mtx, days, off, bench = P["mtx"], P["days"], P["off"], P["bench"]
    ret = mtx["ret_open0"].to_numpy(dtype="float64")
    col_of = {c: i for i, c in enumerate(mtx["close"].columns)}
    pos_of = {d: i for i, d in enumerate(days)}
    n = len(next(iter(picks[BASE].values())))     # 席位宽（生产 5）；恒等式里除的就是它

    # ---- 自检 1：锚点腿 A 必须复现归档最深回撤 ----
    a_net, _ = D.replay_picks(days, mtx, picks["A"], HOLD, COST, off)
    a_dd = float(D.dd_of(D.nav_of(a_net.iloc[off:])).min())
    print(f"\n[自检1·锚点腿 A] 最深回撤 {a_dd:.6f}｜归档 {D.ANCHOR_REF_DD:.6f}")
    if abs(a_dd - D.ANCHOR_REF_DD) > D.ANCHOR_TOL_DD:
        raise SystemExit("⇒ 锚点腿没复现归档 ⇒ 拆分用的时序与 ㉗ 不同形，全部作废")
    print("⇒ 通过（复用 prepare/build_picks + replay_picks 没走形）")

    # ---- 两条腿 × 毛/净 四条日频序列（同一份回放，只差 cost 那一根）----
    port = {}
    for lab in (BASE, GATE):
        g, _ = D.replay_picks(days, mtx, picks[lab], HOLD, 0.0, off)
        net, _ = D.replay_picks(days, mtx, picks[lab], HOLD, COST, off)
        port[lab] = {"gross": g.iloc[off:], "net": net.iloc[off:]}
    idx = port[BASE]["net"].index
    if not port[GATE]["net"].index.equals(idx):
        raise SystemExit("⇒ 两腿日频索引不齐 ⇒ 逐日相减不成立")

    # ---- 年化总账：净差 = 毛差 − 税差（绝对收益口径，不减基准）----
    yrs = idx.year.to_numpy()
    rows = []
    for seg, m in [("全期", np.ones(len(idx), bool)),
                   ("2015（正对照）", yrs == 2015),
                   ("2023（被审的一年）", yrs == 2023)]:
        mask = pd.Series(m, index=idx)
        r = {"段": seg}
        for lab, pre in ((BASE, "B"), (GATE, "C")):
            a_g, a_n = ann(port[lab]["gross"][mask]), ann(port[lab]["net"][mask])
            r[f"{pre}毛"], r[f"{pre}净"], r[f"{pre}税"] = a_g, a_n, a_g - a_n
        r["净差"] = r["C净"] - r["B净"]
        r["毛差"] = r["C毛"] - r["B毛"]
        r["税差"] = r["C税"] - r["B税"]
        assert abs(r["净差"] - (r["毛差"] - r["税差"])) < 1e-12, "⇒ 净差 ≠ 毛差 − 税差，拆账不闭合"
        rows.append(r)
    dec = pd.DataFrame(rows).set_index("段")

    # ---- 超额口径（与上一场同一函数、同一基准），逐年 ----
    ex = {lab: G.year_excess(port[lab]["net"], bench) for lab in (BASE, GATE)}
    dex = (ex[GATE] - ex[BASE]).rename("超额净差")

    # ---- 自检 2：跨场逐格对表（上一场 depth_yearly.csv 的 q0.8−B 那一列）----
    if not os.path.exists(PREV_CSV):
        raise SystemExit(f"⇒ 缺 {PREV_CSV} ⇒ 跨场对表做不了，拒绝裸报")
    prev = pd.read_csv(PREV_CSV, index_col=0)
    prev.index = prev.index.astype(str)
    col = f"{GATE}−B"
    print("\n[自检2·跨场对表] 本场超额净差 vs 上一场 depth_yearly.csv 的 q0.8−B")
    gaps = {int(y): float(dex.loc[y]) - float(prev.loc[str(y), col]) for y in sorted(set(yrs))}
    for y, g in gaps.items():
        if abs(g) > 1e-9:
            raise SystemExit(f"⇒ {y} 对不上（差 {g:+.2e}）⇒ 两次跑的不是同一件事，别看")
    print(f"　12 个年份全部逐格相等（最大 |Δ| = {max(abs(v) for v in gaps.values()):.2e}）")
    print(f"　全期（= 逐年均值）本场 {dex.mean():+.6f}｜上一场 {float(prev.loc['逐年平均', col]):+.6f}")

    # ---- 席位级拆分：逐调仓日把 C−B 拆成「换进 vs 挤掉」----
    rec = []
    for s in picks[BASE]:
        i = pos_of[s]
        lo, hi = i + 2, min(i + 2 + HOLD, len(days))
        b, c = picks[BASE][s], picks[GATE][s]
        common = set(b) & set(c)
        dropped, added = sorted(set(b) - common), sorted(set(c) - common)
        g_b = [seg_return(ret, col_of, x, lo, hi) for x in b]
        g_c = [seg_return(ret, col_of, x, lo, hi) for x in c]
        g_d = [seg_return(ret, col_of, x, lo, hi) for x in dropped]
        g_a = [seg_return(ret, col_of, x, lo, hi) for x in added]
        clean = lambda v: (not len(v)) or not any(map(np.isnan, v))     # noqa: E731
        if not added:
            point = 0.0                       # 这一场闸没咬 ⇒ 点差恒为 0
        elif clean(g_a) and clean(g_d):
            point = (sum(g_a) - sum(g_d)) / n  # 精确恒等：共同席位相消后剩下的就是这笔
        else:
            point = np.nan
        rec.append({"信号日": s, "年": s.year, "换血只数": len(dropped),
                    "共同席位": len(common),
                    "B段收益": np.mean(g_b) if clean(g_b) else np.nan,
                    "C段收益": np.mean(g_c) if clean(g_c) else np.nan,
                    "挤掉均收": np.mean(g_d) if (dropped and clean(g_d)) else np.nan,
                    "换进均收": np.mean(g_a) if (added and clean(g_a)) else np.nan,
                    "点差": point})
    rb = pd.DataFrame(rec)
    print(f"\n[口径] {len(rb)} 个调仓日｜{int(rb['点差'].isna().sum())} 场因缺行情算不出段收益"
          f"⇒ 只从席位级拆分里剔除（上面那两张年化表用的是回放序列，不受影响）")

    # ---- 恒等式自检：逐票相加那套写法，必须与回放的日频序列逐位相等 ----
    #      段内组合毛收益 = Σ_t port_gross[t] = (1/5)Σ_i Σ_t r_it = 各票段收益之和的平均
    bad = []
    for _, r in rb[rb["年"].isin([2015, 2023])].iterrows():
        i = pos_of[r["信号日"]]
        lo, hi = i + 2 - off, min(i + 2 + HOLD, len(days)) - off
        got = float(port[BASE]["gross"].iloc[lo:hi].sum())
        if abs(got - float(r["B段收益"])) > 1e-12:
            bad.append((str(r["信号日"])[:10], got, float(r["B段收益"])))
    if bad:
        raise SystemExit(f"⇒ 席位级拆分与回放序列对不上（前 3 例 {bad[:3]}）⇒ 点差那套算式是假的")
    print("[恒等式] 2015+2023 全部场次的「各票段收益之和 ÷5」与回放毛收益逐位相等 ⇒ 点差是精确拆解")

    # ---- 自检 3：2023 必须确实有换血 ----
    z_all = rb[rb["年"] == 2023]
    if not (z_all["换血只数"] > 0).any():
        raise SystemExit("⇒ 2023 一场都没换血 ⇒ 这道闸那年压根没咬，归因无从谈起")
    print(f"[自检3·有牙] 2023 换血场次 {int((z_all['换血只数'] > 0).sum())}/{len(z_all)} 场"
          f"｜一次换 1 席 {int((z_all['换血只数'] == 1).sum())}、2 席 {int((z_all['换血只数'] == 2).sum())}、"
          f"≥3 席 {int((z_all['换血只数'] >= 3).sum())}（含 5 席整篮换掉的退化场）")

    z = z_all.dropna(subset=["点差"]).sort_values("点差")
    tot = float(z["点差"].sum())
    worst = float(z["点差"].head(5).sum())
    with pd.option_context("display.width", 240):
        print("\n===== 年化总账：净差 = 毛差 − 税差（绝对收益，不减基准）=====")
        print(dec.to_string(float_format=lambda v: f"{v:+.4f}"))
        print("\n===== 逐年：换血只数 / 被挤掉 vs 换进来的平均段收益 / 点差合计 =====")
        y = rb.dropna(subset=["点差"]).groupby("年").agg(
            场次=("点差", "size"), 平均换血=("换血只数", "mean"),
            挤掉均收=("挤掉均收", "mean"), 换进均收=("换进均收", "mean"),
            点差合计=("点差", "sum"),
            点差为负占比=("点差", lambda v: float((v < 0).mean())))
        y["换进−挤掉"] = y["换进均收"] - y["挤掉均收"]
        y = y.join(dex.rename("超额净差"), how="left")
        print(y.to_string(float_format=lambda v: f"{v:+.4f}"))

        print(f"\n===== 2023 的 {len(z)} 场：点差合计 {tot * 100:+.2f}%"
              f"（= 全年『选股』那一笔，与超额净差 {float(dex.loc[2023]) * 100:+.2f}% 对照）=====")
        print(f"点差为负的场次 {int((z['点差'] < 0).sum())}/{len(z)}"
              f"｜最差 5 场合计 {worst * 100:+.2f}% = 占全年选股亏损的 {worst / tot * 100:.0f}%")
        print("最差 8 场：")
        w = z.head(8)[["信号日", "换血只数", "挤掉均收", "换进均收", "点差"]].copy()
        w["信号日"] = [str(x)[:10] for x in w["信号日"]]
        print(w.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))

        cnt_out = collections.Counter(x for s in z["信号日"]
                                      for x in (set(picks[BASE][s]) - set(picks[GATE][s])))
        cnt_in = collections.Counter(x for s in z["信号日"]
                                     for x in (set(picks[GATE][s]) - set(picks[BASE][s])))

        def name_table(cnt, belongs):
            rows = []
            for x, k in cnt.most_common(10):
                ds = [s for s in z["信号日"] if x in belongs(s)]
                vals = [seg_return(ret, col_of, x, pos_of[s] + 2,
                                   min(pos_of[s] + 2 + HOLD, len(days))) for s in ds]
                vals = [v for v in vals if not np.isnan(v)]
                rows.append({"代码": x, "被换次数": k,
                             "这些场次它自己的平均段收益": float(np.mean(vals)) if vals else np.nan})
            return pd.DataFrame(rows)

        t_out = name_table(cnt_out, lambda s: set(picks[BASE][s]) - set(picks[GATE][s]))
        t_in = name_table(cnt_in, lambda s: set(picks[GATE][s]) - set(picks[BASE][s]))
        print("\n—— 2023 被闸挤掉最多的 10 只：它们在被剔掉那几场涨了多少 ——")
        print(t_out.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))
        print("\n—— 2023 被闸换进来最多的 10 只：换进来的这些涨了多少 ——")
        print(t_in.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))

        # ---- 自检 4：正对照 2015 与 2023 应当反号 ----
        g15 = float(y.loc[2015, "换进−挤掉"]) if 2015 in y.index else np.nan
        g23 = float(y.loc[2023, "换进−挤掉"]) if 2023 in y.index else np.nan
        print(f"\n[自检4·正对照] 2015 换进−挤掉 = {g15:+.4f}｜2023 = {g23:+.4f}"
              f" ⇒ {'有牙（两年反号：闸在 2015 换对、在 2023 换错）' if (g15 > 0 > g23) else '⚠️ 没牙（同号或缺年）⇒ 这套拆分分不出好坏，回去改拆法'}")

    rb.to_csv(os.path.join(OUT, "attr_rebal_all.csv"), index=False)
    y.to_csv(os.path.join(OUT, "attr_yearly.csv"))
    dec.to_csv(os.path.join(OUT, "attr_decomp.csv"), index_label="段")
    t_out.to_csv(os.path.join(OUT, "attr_2023_被挤掉.csv"), index=False)
    t_in.to_csv(os.path.join(OUT, "attr_2023_被换进.csv"), index=False)
    print(f"\n[输出] {OUT}/attr_decomp.csv｜attr_yearly.csv｜attr_rebal_all.csv"
          f"｜attr_2023_被挤掉.csv｜attr_2023_被换进.csv　总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
