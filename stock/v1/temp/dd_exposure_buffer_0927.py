# -*- coding: utf-8 -*-
"""P1-4 追加（09-27）：M8 = 给 M4-20 那档减仓规则加「缓冲带」，看 2025 那笔 −41pp 能不能砍掉。

来由：㊺ 把 M4-20 的逐年账结清了 —— 全期 Δ年化 −4.34pp 里 **3.52pp 来自 2025 一年**，
机制是那 73 个空仓日里满仓那条累计 **+35.30%**：净值 20 日均线在单边快涨里被反复穿越，
规则每天「跌破就走、站上就回」⇒ 卖在启动前、买回后又被打出去（whipsaw）。税只是小头
（4.15pp/年），**大头是「一进一出之间错过的那段」**。所以这一轮量三种缓冲，逐档配网格：

  h  触发带：净值要跌破均线 **−h**（如 h=2% ⇒ 跌破 MA×0.98）才清仓      —— 治「贴着均线抖」
  g  回场带：净值要站上均线 **+g** 才算数（如 g=2% ⇒ 站上 MA×1.02）      —— 治「刚站上就回场」
  n  回场确认天数：上面那个条件要**连续 n 天**成立才回场                  —— 治「一天的假突破」

h=g=0、n=1 就是原来的 M4-20（无记忆）⇒ 这条恒等式是本脚本的**自检①**，不通过就不许看别的。

**只读缓存、不重跑面板**：M4 的判据只用到「满仓那条净值 + 它自己的均线」，
而满仓日收益已经落在 `tmp_dd_exposure_0927/exp_daily.csv.gz` 的 `M1净日收益` 列 ⇒
nav = (1+r).cumprod() 就能原样重建。生效仓位与 `exp_exposure.csv.gz` 的 M4-20 列逐位对表（自检①）。

口径与 ㊺ 主表一字不差（同一份 `apply_e` 语义：E 收盘定、**次日**生效；税 = 15bp×|ΔE| 记一边；
年化 = 日均值×252；最深回撤 = 叠加后净值的 `nav/nav.cummax()−1` 最小值）。

五道自检（断言不许恒真）：
  ① **恒等式**：h=g=0、n=1 的生效仓位必须与缓存里 M4-20 那一列**逐位相等**，
     且由此重算的年化/最深回撤/税/调仓次数必须与 `exp_compare.csv` 的 M4-20 行逐格对上
  ② **无未来函数**：状态机是递归的 ⇒ 把历史截到两个截面重算，截面之前必须逐位相等
  ③ **正对照**：网格里至少一档的 E 序列与 M4-20 不同（全都相同 = 这三根旋钮压根没牙）
  ④ **基准腿**：满仓那条（E≡1）的年化与最深回撤必须复现 M1（对照值现读 `exp_compare.csv`）
  ⑤ **覆盖**：18 档全部跑出、且 Δ回撤为正（真的把回撤做浅了）的档数 > 0，否则网格白扫
"""

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "stock/v1/src"))
import _bootstrap  # noqa: E402,F401

from config import ASHARE_PORT_COST_ONE_WAY as COST   # noqa: E402  与主表同一边费

IN = os.path.join(HERE, "tmp_dd_exposure_0927")
TRADING_DAYS = 252
WIN = 20                    # M4-20 的均线长度，本轮只动缓冲三根旋钮，不动窗
HS = (0.00, 0.02, 0.04)     # 触发带
GS = (0.00, 0.02)           # 回场带
NS = (1, 2, 5)              # 回场确认天数


def ann(s):
    p = s.dropna()
    return float(p.mean() * TRADING_DAYS)


def dd_min(r):
    nav = (1.0 + r.dropna()).cumprod()
    return float((nav / nav.cummax() - 1.0).min())


def buffered(nav, ma, h, g, n):
    """带缓冲的仓位状态机（当天收盘决策，次日生效由 `overlay` 那一步统一处理）

    持有中：nav < 均线×(1−h) ⇒ 清空；空仓中：nav > 均线×(1+g) **连续 n 天** ⇒ 满仓。
    均线没值的头几天一律满仓（与主表 `fillna(False)` 同口径，不许开局白捡/白躲）。
    """
    lov = ma.to_numpy(dtype="float64") * (1.0 - h)
    hiv = ma.to_numpy(dtype="float64") * (1.0 + g)
    v = nav.to_numpy(dtype="float64")
    out = np.empty(len(v))
    state, streak = 1.0, 0
    for i in range(len(v)):
        if not np.isfinite(lov[i]):
            out[i] = state = 1.0
            streak = 0
            continue
        if state == 1.0:
            if v[i] < lov[i]:
                state, streak = 0.0, 0
        else:
            streak = streak + 1 if v[i] > hiv[i] else 0
            if streak >= n:
                state, streak = 1.0, 0
        out[i] = state
    return pd.Series(out, index=nav.index)


def overlay(r, e_dec):
    e = e_dec.shift(1).fillna(1.0)
    tax = (COST * e.diff().abs()).fillna(0.0)
    return e, r * e - tax, tax


def label(h, g, n):
    return f"M8-h{int(round(h * 100))}g{int(round(g * 100))}n{n}"


def why(h, g, n):
    a = "跌破均线就清仓" if h == 0 else f"跌破均线−{h:.0%}才清仓"
    b = "站上均线" if g == 0 else f"站上均线+{g:.0%}"
    c = "就回场" if n == 1 else f"连续 {n} 天才回场"
    return f"{a}；{b}{c}"


def main():
    dly = pd.read_csv(os.path.join(IN, "exp_daily.csv.gz"), index_col=0,
                      parse_dates=["datetime"]).rename_axis("datetime")
    exp = pd.read_csv(os.path.join(IN, "exp_exposure.csv.gz"), index_col=0,
                      parse_dates=["datetime"]).rename_axis("datetime")
    ref = pd.read_csv(os.path.join(IN, "exp_compare.csv"), index_col=0)
    if not dly.index.equals(exp.index):
        raise SystemExit("⇒ 两张缓存日期轴不齐 ⇒ 逐位对表不成立")
    r = dly["M1净日收益"]
    if r.isna().any():
        raise SystemExit("⇒ 满仓日收益有缺值 ⇒ 缓存不完整")
    nav = (1.0 + r).cumprod()
    ma = nav.rolling(WIN).mean()
    years = r.index.year
    ys = sorted(set(years))
    MID = ys[len(ys) // 2]                  # 半期分界**按年份**切（不是按行数）⇒ 标签与数据同一批
    idx2 = years >= MID
    print(f"[切分] 前半期 {ys[0]}–{MID - 1}｜后半期 {MID}–{ys[-1]}（按自然年分界，与逐年表同批）")

    # ---- 自检④：满仓腿必须复现 M1（对照值现读）----
    for k, got in (("年化净收益", ann(r)), ("最深回撤", dd_min(r))):
        want = float(ref.loc["M1", k])
        print(f"[自检4·M1] {k} 本轮 {got:+.6f}｜主表 {want:+.6f}｜差 {abs(got - want):.2e}")
        if abs(got - want) > 1e-9:
            raise SystemExit(f"⇒ 满仓腿对不上主表 ⇒ 缓存重建走形，全部作废")

    # ---- 自检①：h=g=0、n=1 必须逐位退回 M4-20 ----
    e0, net0, tax0 = overlay(r, buffered(nav, ma, 0.0, 0.0, 1))
    d1 = float((e0 - exp["M4-20"]).abs().max())
    print(f"\n[自检1·恒等式] 缓冲关掉 ≡ 缓存 M4-20 生效列 ⇒ 最大差 {d1:.3e}")
    if d1 > 0:
        n_diff = int((e0 != exp["M4-20"]).sum())
        raise SystemExit(f"⇒ 无缓冲的状态机与主表 M4-20 有 {n_diff} 天不同 ⇒ 状态机另写了口径，作废")
    for k, got in (("年化净收益", ann(net0)), ("最深回撤", dd_min(net0)),
                   ("额外税(年化)", ann(tax0)), ("调仓次数", float(int((e0.diff().abs() > 1e-9).sum()))),
                   ("空仓天数占比", float((e0 <= 1e-9).mean()))):
        want = float(ref.loc["M4-20", k])
        print(f"  {k:<12} 本轮 {got:+.6f}｜主表 {want:+.6f}｜差 {abs(got - want):.2e}")
        if abs(got - want) > 1e-9:
            raise SystemExit(f"⇒ {k} 对不上主表 ⇒ 叠加写法与 ㊺ 不同形")

    rows, es = [], {}
    for h in HS:
        for g in GS:
            for n in NS:
                lab = label(h, g, n)
                e, net, tax = overlay(r, buffered(nav, ma, h, g, n))
                dd = dd_min(net)
                da = ann(net) - ann(r)
                draw = dd - dd_min(r)
                sub = {}
                for y in (2015, 2018, 2021, 2024, 2025):
                    m = years == y
                    sub[f"Δ年化{y}"] = float((net[m] - r[m]).mean() * TRADING_DAYS)
                m25 = years == 2025
                rows.append({"档": lab, "判据": why(h, g, n),
                             "年化": ann(net), "最深回撤": dd,
                             "Δ年化": da, "Δ回撤": draw,
                             "汇率(付年化/买回1pp)": (-da / draw) if draw > 1e-6 else np.nan,
                             "调仓次数": int((e.diff().abs() > 1e-9).sum()),
                             "空仓占比": float((e <= 1e-9).mean()),
                             "税(年化)": ann(tax),
                             "后半期Δ年化": float((net[idx2] - r[idx2]).mean() * TRADING_DAYS),
                             # 去掉 2025 ⇒ 只在那批日子上算「减完之后 − 满仓」的年化，别再减一次满仓
                             "去掉2025的Δ年化": float((net[~m25] - r[~m25]).mean() * TRADING_DAYS),
                             **sub})
                es[lab] = e
    tab = pd.DataFrame(rows).set_index("档")

    # ---- 自检③：正对照 —— 缓冲必须真的改过仓位 ----
    diff = [k for k in tab.index if float((es[k] - exp["M4-20"]).abs().max()) > 0]
    print(f"\n[自检3·正对照] E 与 M4-20 不同的档 {len(diff)}/{len(tab)}")
    if not diff:
        raise SystemExit("⇒ 三根旋钮一档都没改变仓位 ⇒ 这台秤没牙，别看表")

    # ---- 自检②：无未来函数（状态机递归 ⇒ 必须截断重算）----
    probes = [r.index[len(r) // 3], r.index[len(r) * 2 // 3]]
    worst, where = 0.0, None
    for h in HS:
        for g in GS:
            for n in NS:
                lab = label(h, g, n)
                for pr in probes:
                    m = r.index <= pr
                    cut = overlay(r[m], buffered(nav[m], ma[m], h, g, n))[0]
                    d = float((es[lab][m] - cut).abs().max())
                    if d > worst:
                        worst, where = d, (lab, pr.date())
    print(f"[自检2·无未来函数] {len(tab)} 档 × {len(probes)} 截面截断重算，最大差 {worst:.3e}（{where}）")
    if worst > 1e-12:
        raise SystemExit("⇒ 状态机截断重算与全样本不等 ⇒ 有档位偷看了未来")

    n_ok = int((tab["Δ回撤"] > 1e-6).sum())
    print(f"[自检5·覆盖] Δ回撤为正（真的把回撤做浅）的档 {n_ok}/{len(tab)}")
    if n_ok == 0:
        raise SystemExit("⇒ 十八档没有一档买回过回撤 ⇒ 网格白扫")

    with pd.option_context("display.width", 320, "display.unicode.east_asian_width", True):
        b = tab.loc["M8-h0g0n1"]        # 无缓冲那一档 = M4-20（自检①已证逐位相同），参照值现读
        print("\n===== M8 缓冲网格（对照 = 满仓 M1；「汇率」越小越划算）=====")
        print(f"      无缓冲基准 M8-h0g0n1 = M4-20：汇率 {b['汇率(付年化/买回1pp)']:+.3f}、"
              f"后半期 Δ年化 {b['后半期Δ年化']:+.4f}、去掉 2025 后 {b['去掉2025的Δ年化']:+.4f}")
        print(tab.to_string(float_format=lambda v: f"{v:+.4f}"))

    # 逐年：18 档**全部**落盘（换切法只读这张表），终端只挑「汇率最优」与「后半期最优」两组
    yr = []
    for lab in tab.index:
        e = es[lab]                                  # 已是「生效」那一列
        tax = (COST * e.diff().abs()).fillna(0.0)
        net = r * e - tax
        g = pd.DataFrame({"r": r, "net": net, "e": e, "tax": tax})
        for y, s in g.groupby(years):
            yr.append({"档": lab, "年": y,
                       "Δ年化": float((s["net"] - s["r"]).mean() * TRADING_DAYS),
                       "空仓天数": int((s["e"] <= 1e-9).sum()),
                       "空仓日满仓累计涨跌": float(s.loc[s["e"] <= 1e-9, "r"].sum()),
                       "税": float(s["tax"].sum()),
                       "调仓次数": int((s["e"].diff().abs() > 1e-9).sum())})
    ytab = pd.DataFrame(yr).set_index(["档", "年"])
    pick = (["M8-h0g0n1"]
            + list(tab.drop(index="M8-h0g0n1").sort_values("汇率(付年化/买回1pp)").index[:3])
            + list(tab[(tab["Δ回撤"] > 0.10)].drop(index="M8-h0g0n1")
                   .sort_values("后半期Δ年化", ascending=False).index[:2]))
    with pd.option_context("display.width", 320, "display.unicode.east_asian_width", True):
        print("\n===== 逐年 Δ年化：基准 + 汇率最优三档 + 后半期最优两档（全 18 档在 CSV 里）=====")
        print(ytab.loc[sorted(set(pick))].to_string(float_format=lambda v: f"{v:+.4f}"))
    tab.to_csv(os.path.join(IN, "m8_grid.csv"), index_label="档")
    ytab.to_csv(os.path.join(IN, "m8_yearly.csv"), index_label=["档", "年"])
    print(f"\n[输出] {IN}/m8_grid.csv｜m8_yearly.csv　｜　只读 09-27 缓存，未重跑面板")


if __name__ == "__main__":
    main()
