# -*- coding: utf-8 -*-
"""P1-4 追加（09-27）：M4-20 那档减仓规则把「代价」摊到 12 个自然年上，看它是不是只靠一年。

来由：`dd_exposure_0927.py` 的总账里 M4-20 是这台秤上**汇率最好看**的一档 —— 全期
Δ年化 −4.34pp、Δ回撤 +28.2pp ⇒「每买回 1pp 最深回撤只付 0.154pp 年化」。但全期一个数
说不清两件事：① 这 28pp 的回撤改善是不是集中在 2015/2018/2022 某一年，其余年份白付税；
② 它一年空仓 42.3%、调仓 313 次，税年年要付 4.15pp，亏损是否也一年年均匀地付。
本轮就是把这两问逐结清。

**只读缓存、不重跑面板**（`tmp_dd_exposure_0927/exp_daily.csv.gz` + `exp_exposure.csv.gz`
+ `exp_yearly_dd.csv` + `exp_compare.csv` 是 09-27 主表落的四张表）⇒ 本脚本几秒跑完，
以后换切法（换档、换半年、leave-one-out）都在这里改，不必再花 86s 重读面板。

口径（与主表一字不差，靠「逐格对表」自证而不是靠声明）：
- `E` = 当天收盘定、**次日**生效的仓位比例；缓存里存的就是**生效**那一列，直接用
- 净日收益 = `M1满仓日收益 × E − 15bp × |ΔE|`（调仓税只记一边，与换票费相加 = 故意多算）
- 年化 = 日收益均值 × 252；回撤沿用主表：**全样本累计净值**的水下深度按年取最深
  （不是「每年从 0 重算」⇒ 2023 那年读到的是「距历史峰值还差多少」，跨年可比但含义要说清）
- 汇率 = −Δ年化 ÷ Δ回撤，只有 Δ回撤为正（回撤变浅）才算得出，否则 NaN
- 逐年内 M1 在空仓那些天累计涨/跌 = 减仓的**机会成本或躲过的跌幅**（负数 = 躲对了）

四道自检（断言不许恒真）：
  ① **对表**：本脚本从缓存重算出的 M4-20 六格（年化/最深回撤/平均仓位/空仓占比/调仓次数/额外税）
     必须与主表 `exp_compare.csv` 的 M4-20 行逐格对上 ⇒ 证明「只读缓存」这条路没走形
  ② **分解恒等式**：Σ(该年天数占比 × 该年 Δ年化) 必须恰好等于全期 Δ年化 ⇒ 逐年账没有漏项
  ③ **恒等式腿**：把 E 换成全 1 重算，序列必须与 M1 逐位相等、税恰好为 0
  ④ **正对照**：M4-20 必须真有日子空仓、且逐年 Δ年化不能全为 0（全 0 = 这台秤压根没减仓）
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
LABELS = ("M4-20",)          # 要加档就在这里加：判据、税率、逐年切法全部同一套
REF_FULL = os.path.join(IN, "exp_compare.csv")


def nav_of(port):
    """与 ㊶/主表同一句：(1+r).cumprod()，先 dropna"""
    return (1.0 + port.dropna()).cumprod()


def dd_of(nav):
    return nav / nav.cummax() - 1.0


def ann(s):
    p = s.dropna()
    return float(p.mean() * TRADING_DAYS)


def overlay(r, e):
    """生效仓位直接叠在满仓日收益上：净 = r×E − 税（税 = 15bp × |ΔE|，记在调仓那天）"""
    tax = (COST * e.diff().abs()).fillna(0.0)
    return r * e - tax, tax


def main():
    dly = pd.read_csv(os.path.join(IN, "exp_daily.csv.gz"), index_col=0,
                      parse_dates=["datetime"]).rename_axis("datetime")
    exp = pd.read_csv(os.path.join(IN, "exp_exposure.csv.gz"), index_col=0,
                      parse_dates=["datetime"]).rename_axis("datetime")
    ydd = pd.read_csv(os.path.join(IN, "exp_yearly_dd.csv"), index_col=0)
    ref = pd.read_csv(REF_FULL, index_col=0)
    if not dly.index.equals(exp.index):
        raise SystemExit("⇒ 两张缓存的日期轴不齐 ⇒ 逐日相减不成立，别看")
    r = dly["M1净日收益"]
    if r.isna().any():
        raise SystemExit(f"⇒ M1 日收益有 {int(r.isna().sum())} 天缺值 ⇒ 缓存不完整")

    # ---- 自检③：E≡1 必须原样退回 M1（否则叠加写法在改收益本身）----
    ones = pd.Series(1.0, index=r.index)
    n1, t1 = overlay(r, ones)
    if float((n1 - r).abs().max()) > 1e-15 or float(t1.sum()) > 1e-15:
        raise SystemExit("⇒ E≡1 都不等于满仓 ⇒ 本脚本的叠加口径与主表不同形")

    years = r.index.year
    tabs, robs = [], []
    for lab in LABELS:
        e = exp[lab]
        net, tax = overlay(r, e)
        # ---- 自检①：与主表 M4-20 行逐格对表（参照值现读，不写死）----
        got = {"年化净收益": ann(net), "最深回撤": float(dd_of(nav_of(net)).min()),
               "平均仓位": float(e.mean()), "空仓天数占比": float((e <= 1e-9).mean()),
               "调仓次数": float(int((e.diff().abs() > 1e-9).sum())),
               "额外税(年化)": ann(tax)}
        print(f"\n[自检1·对表 {lab}] 本脚本重算 vs 主表 {os.path.basename(REF_FULL)}")
        for k, v in got.items():
            w = float(ref.loc[lab, k])
            print(f"  {k:<12} 重算 {v:+.6f}｜主表 {w:+.6f}｜差 {abs(v - w):.2e}")
            if abs(v - w) > 1e-9:
                raise SystemExit(f"⇒ {k} 对不上主表 ⇒ 缓存重算与全样本重跑不同形，全部作废")

        # ---- 自检④：正对照 —— 真的减过仓、逐年内有变化 ----
        if float((e <= 1e-9).mean()) < 0.01:
            raise SystemExit(f"⇒ {lab} 空仓天数占比不到 1% ⇒ 这一档压根没减仓，无账可结")
        d_ann = net - r                                   # 逐日「减完之后 − 满仓」
        if float(d_ann.abs().max()) < 1e-12:
            raise SystemExit(f"⇒ {lab} 逐日差全程为 0 ⇒ 这台秤空转")

        g = pd.DataFrame({"r": r, "net": net, "e": e, "tax": tax,
                          "diff": d_ann, "bench": dly["域等权日收益"]})
        rows = []
        for y, sub in g.groupby(years):
            n = len(sub)
            dd_ref = ydd.loc[int(y)]                       # 沿用主表：全样本水下深度按年取最深
            out_days = sub[sub["e"] <= 1e-9]
            da = float(sub["diff"].mean() * TRADING_DAYS)
            draw = float(dd_ref[lab] - dd_ref["M1"])       # 正 = 回撤变浅
            rows.append({"年": y, "交易日": n,
                         "M1年化": float(sub["r"].mean() * TRADING_DAYS),
                         f"{lab}年化": float(sub["net"].mean() * TRADING_DAYS),
                         "Δ年化": da,
                         "M1该年最深水下": float(dd_ref["M1"]), f"{lab}该年最深水下": float(dd_ref[lab]),
                         "Δ回撤": draw,
                         "汇率(付年化/买回回撤)": (-da / draw) if draw > 1e-6 else np.nan,
                         "平均仓位": float(sub["e"].mean()),
                         "空仓天数": len(out_days),
                         "空仓日M1累计涨跌": float(out_days["r"].sum()),
                         "调仓次数": int((sub["e"].diff().abs() > 1e-9).sum()),
                         "额外税": float(sub["tax"].sum()),
                         "Δ年化贡献(占全期)": n / len(g) * da})
        tab = pd.DataFrame(rows).set_index("年")
        # ---- 自检②：逐年账必须加得回全期 ----
        tot = ann(net) - ann(r)
        s = float(tab["Δ年化贡献(占全期)"].sum())
        print(f"[自检2·分解] 全期 Δ年化 {tot:+.6f}｜逐年加总 {s:+.6f}｜差 {abs(tot - s):.2e}")
        if abs(tot - s) > 1e-12:
            raise SystemExit("⇒ 逐年加不回全期 ⇒ 分组漏了日子，逐年账不可信")

        # ---- leave-one-out：去掉某一年，全期 Δ年化还剩多少 ⇒ 是否只靠一年 ----
        contrib = tab["Δ年化贡献(占全期)"]
        loo = []
        for y in tab.index:
            keep = g[years != y]
            rest = ann(keep["net"]) - ann(keep["r"])
            loo.append({"规则": lab, "去掉的年": y, "该年Δ年化": float(tab.loc[y, "Δ年化"]),
                        "该年Δ年化贡献": float(contrib[y]),
                        "去掉该年后的Δ年化": rest,
                        "去掉该年后还剩多少": rest - tot})
        lo = pd.DataFrame(loo).set_index("去掉的年")
        # 半期分界**按自然年**切（不是按行数）⇒ 「段」这一列写的年份就是切片里真有的年份
        yset = sorted(set(years))
        mid = yset[len(yset) // 2]
        front, back = g[years < mid], g[years >= mid]
        tot_dd = float(dd_of(nav_of(net)).min()) - float(dd_of(nav_of(r)).min())
        rob = pd.DataFrame([
            {"口径": "全期", "段": f"{years.min()}–{years.max()}", "Δ年化": tot,
             "Δ回撤": tot_dd},
            {"口径": "只看前半期", "段": f"{yset[0]}–{mid - 1}",
             "Δ年化": ann(front["net"]) - ann(front["r"]), "Δ回撤": np.nan},
            {"口径": "只看后半期", "段": f"{mid}–{yset[-1]}",
             "Δ年化": ann(back["net"]) - ann(back["r"]), "Δ回撤": np.nan},
            {"口径": "去掉贡献最负的一年", "段": f"去年 {int(contrib.idxmin())}",
             "Δ年化": float(lo.loc[contrib.idxmin(), "去掉该年后的Δ年化"]), "Δ回撤": np.nan},
            {"口径": "去掉贡献最正的一年", "段": f"去年 {int(contrib.idxmax())}",
             "Δ年化": float(lo.loc[contrib.idxmax(), "去掉该年后的Δ年化"]), "Δ回撤": np.nan},
            {"口径": "Δ回撤为正(变浅)的年数", "段": f"{int((tab['Δ回撤'] > 0).sum())}/{len(tab)}",
             "Δ年化": np.nan, "Δ回撤": np.nan},
            {"口径": "Δ年化为正(减仓反赚)的年数", "段": f"{int((tab['Δ年化'] > 0).sum())}/{len(tab)}",
             "Δ年化": np.nan, "Δ回撤": np.nan},
        ]).set_index("口径")

        with pd.option_context("display.width", 300, "display.unicode.east_asian_width", True):
            print(f"\n===== {lab} 逐年账（差一律减 M1 满仓；回撤沿用主表的全样本水下深度）=====")
            print(tab.to_string(float_format=lambda v: f"{v:+.4f}"))
            print(f"\n===== {lab} 稳健性：leave-one-out 与前后半 =====")
            print(lo.to_string(float_format=lambda v: f"{v:+.4f}"))
            print(rob.to_string(float_format=lambda v: f"{v:+.4f}"))
        tab.to_csv(os.path.join(IN, f"m4year_{lab}.csv"), index_label="年")
        tabs.append(tab)
        robs.append(rob)
        if len(LABELS) == 1:
            lo.to_csv(os.path.join(IN, "m4year_leaveoneout.csv"), index_label="去掉的年")
    pd.concat(robs, keys=LABELS).to_csv(os.path.join(IN, "m4year_robust.csv"))
    print(f"\n[输出] {IN}/m4year_*.csv　｜　缓存四张表来自 09-27 主表，未重跑面板")


if __name__ == "__main__":
    main()
