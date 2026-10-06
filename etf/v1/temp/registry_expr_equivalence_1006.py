# -*- coding: utf-8 -*-
"""发射端等价对拍（10-06 用户裁「第 2 问＝丙：不改闸、改发射端」的前置闸）

要证一件事：给 `FACTOR_REGISTRY` 那 9 条 lambda 补的表达式串，**算出来的列必须与
lambda 逐位相同**（不是"差不多"）。翻错一次，库里就多一条「名字挂着、表达式与实际
算的东西分家」的新事故——那正是这次要治的病。

⚠️ 这条要求不是洁癖：`etf/v1/src/run_live.py:193` 会拿库里的 `expr` **重新求值**
（`float(safe_eval(f["expr"], df).iloc[-1])`）来给实盘打分，而库里存的那个 `ic`
是**用 lambda 算出来的列**去量的。表达式与 lambda 差一点＝实盘用的是另一个数、
却按 lambda 的成绩排队。

两形各拍一遍：
  A＝DSL 惯用形（`close / delay(close, 20) - 1`），库里现有语料是这种风格
  B＝方法链形（`close.pct_change(20)`），与 lambda 是同一台 pandas 调用
  C＝负对照：窗口/防零除故意写错一档 ⇒ 这把尺必须抓到它（抓不到＝恒真尺，全场作废）

**为什么要拍两遍数据**（10-06 自我推翻后加的那一层）：第一版只拍真池子，9 条**全判
「落 A」**。但真池子 `close/high/low/volume` 的内部缺值是 **0 格**（本尺现数，见输出），
而 DSL 的 `returns` 写的是 `pct_change(fill_method=None)`、lambda 写的是
`pct_change()`（默认 `'pad'`）⇒ 这两台机器**只在有停牌缺口的数据上才分家**。
无缺口的池子给的是"今天相等"，不是"按构造相等" ⇒ 同一把尺再拍一遍**人造含缺口**的池子
（从真池子拷一份、把中间三天整行抹成 NaN），只有两遍都逐位相同才叫"按构造相同"。

判定口径：NaN 位置必须一致，共同非 NaN 格 `max|Δ|` **== 0**（不放宽到 1e-12——
放宽了就先看不见 0.00002 级的口径漂移，那是历史上真栽过的量级）。
读数分两层报：**NaN 层差多少格** / **共同格数值差多少**——只看一个数会把
「只欠一行头 NaN」报成 `max|Δ|=0.000e+00`（第一版就这么误报过）。

只读：不写库、不落任何生产产物；人造缺口那份是**内存里的副本**，不覆写真池子。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import numpy as np                         # noqa: E402
import _bootstrap                          # noqa: F401,E402
from config import FREQ                    # noqa: E402
from data_loader import DataLoader         # noqa: E402
from etf_universe import get_universe      # noqa: E402
from factor_dsl import safe_eval           # noqa: E402
from factor_static_check import check_expr  # noqa: E402
from factors import FACTOR_EXPR, FACTOR_REGISTRY        # noqa: E402

COLS = ("close", "open", "high", "low", "volume")

CANDIDATES = {
    "momentum_20": (
        "close / delay(close, 20) - 1",
        "close.pct_change(20)"),
    "momentum_10": (
        "close / delay(close, 10) - 1",
        "close.pct_change(10)"),
    "reversal_5": (
        "0 - (close / delay(close, 5) - 1)",
        "-close.pct_change(5)"),
    "volatility_20": (
        "0 - ts_std(returns, 20)",
        "-close.pct_change().rolling(20).std()"),
    "ma_ratio_5_20": (
        "ma(close, 5) / ma(close, 20) - 1",
        "close.rolling(5).mean() / close.rolling(20).mean() - 1"),
    "ma_ratio_10_30": (
        "ma(close, 10) / ma(close, 30) - 1",
        "close.rolling(10).mean() / close.rolling(30).mean() - 1"),
    "rsi_14": (
        "(100 - 100 / (1 + ts_mean(delta(close, 1).clip(lower=0), 14) / "
        "(ts_mean((-delta(close, 1)).clip(lower=0), 14) + 1e-9)) - 50) / 50",
        "(100 - 100 / (1 + close.diff().clip(lower=0).rolling(14).mean() / "
        "((-close.diff().clip(upper=0)).rolling(14).mean() + 1e-9)) - 50) / 50"),
    "volume_ratio_20": (
        "volume / ts_mean(volume, 20)",
        "volume / volume.rolling(20).mean()"),
    "price_position_20": (
        "2 * (close - ts_min(low, 20)) / (ts_max(high, 20) - ts_min(low, 20) + 1e-9) - 1",
        "2 * (close - low.rolling(20).min()) / "
        "(high.rolling(20).max() - low.rolling(20).min() + 1e-9) - 1"),
}

# 负对照：窗口差一档＝只欠一行头 NaN（NaN 层）；少一个防零除＝纯数值差（数值层）。
# 两支必须落在**不同层**，才证这把尺两层都看得见。
WRONG = {
    "momentum_20": "close / delay(close, 19) - 1",
    "volatility_20": "0 - ts_std(returns, 19)",
    "price_position_20": "2 * (close - ts_min(low, 20)) / "
                         "(ts_max(high, 20) - ts_min(low, 20)) - 1",   # 少了 1e-9 防零除
}


def calc(expr, df):
    """求值；求不出来就把异常当判决返回（不许静默当成相等）"""
    try:
        return safe_eval(expr, df)
    except Exception as e:
        return f"求值失败: {type(e).__name__}: {e}"


def compare(a, b):
    """('相同' | 原因, NaN 层差格数, 共同格 max|Δ|)"""
    if isinstance(b, str):
        return b, 0, 0.0
    if a.shape != b.shape or not a.index.equals(b.index):
        return "形状不同", 0, 0.0
    na = a.isna().to_numpy()
    nb = b.isna().to_numpy()
    na_cells = int((na != nb).sum())
    both = (~na) & (~nb)
    gap = float(np.max(np.abs(a.to_numpy()[both] - b.to_numpy()[both]))) if both.any() else 0.0
    if na_cells == 0 and gap == 0.0:
        return "相同", 0, 0.0
    why = []
    if na_cells:
        why.append(f"NaN 位置差 {na_cells} 格")
    if gap:
        why.append(f"共同格数值差 {gap:.3e}")
    return "｜".join(why), na_cells, gap


def sweep(name, expr, pool):
    """把一条表达式在整个池子上拍一遍，按**层**汇总"""
    fn = FACTOR_REGISTRY[name]
    bad_codes, na_cells, gap = [], 0, 0.0
    err = None
    for code, df in pool.items():
        why, n, g = compare(fn(df), calc(expr, df))
        if why != "相同":
            bad_codes.append(code)
            na_cells += n
            gap = max(gap, g)
            if why.startswith("求值失败") or why == "形状不同":
                err = why
    return {"n": len(pool), "bad": len(bad_codes), "codes": bad_codes[:3],
            "na_cells": na_cells, "gap": gap, "err": err}


def interior_nan(pool):
    """内部缺值＝第一个有效值与最后一个有效值之间的 NaN 格（头部/尾部整段空的不算）"""
    out = {}
    for col in COLS:
        tot = 0
        for df in pool.values():
            m = df[col].isna().to_numpy()
            valid = np.flatnonzero(~m)
            if len(valid) < 2:
                continue
            tot += int(m[valid[0]:valid[-1]].sum())
        out[col] = tot
    return out


def make_holed(pool, k=12):
    """从真池子拷 k 个标的，各挖一个**位置与长度都不同**的整行缺口（停牌形状）。

    只动内存副本：真池子的 DataFrame 一个字节不改，人造缺口只是逼出
    `fill_method='pad'` vs `None` 那台机器的分岔口。挖在中间是为了让缺口落进
    20 日窗口里面，而不是躲在窗口外面。
    """
    holed = {}
    for i, (code, df) in enumerate(pool.items()):
        if i >= k:
            break
        d = df.copy()
        n = len(d)
        start = int(n * (0.25 + 0.05 * i))          # 缺口起点逐个挪开
        length = 1 + i % 4                           # 1~4 天不等
        d.loc[d.index[start:start + length], list(COLS)] = np.nan
        holed[code] = d
    return holed


def main():
    codes = get_universe().universe["code"].tolist()
    pool = DataLoader(freq=FREQ).load_pool(codes)
    holes = interior_nan(pool)
    holed = make_holed(pool)
    print(f"池子：universe {len(codes)} 个代码 → load_pool 实得 {len(pool)} 条，"
          f"总 bar 数 {sum(len(d) for d in pool.values()):,}")
    print(f"内部缺值（头部/尾部整段空的不算）：{holes} 合计 {sum(holes.values())} 格")
    print(f"⇒ 人造缺口那份：{len(holed)} 个标的，缺口起点逐个挪开、长度 1~4 天不等"
          f"（每标的 close 列的内部缺值数＝{[interior_nan({c: d})['close'] for c, d in holed.items()]}）\n")

    unlandable, landed, seam = [], {}, 0
    for name, (a_expr, b_expr) in CANDIDATES.items():
        print(f"{name}")
        picks = {}
        for tag, expr in (("A", a_expr), ("B", b_expr)):
            g = check_expr(expr)
            real = sweep(name, expr, pool)
            hole = sweep(name, expr, holed)
            ok_real = g == "" and real["bad"] == 0
            ok_hole = g == "" and hole["bad"] == 0
            picks[tag] = ok_real and ok_hole
            print(f"  {tag} 真池：{real['bad']}/{real['n']} 不等"
                  f"｜NaN 层 {real['na_cells']} 格｜共同格 max|Δ|={real['gap']:.3e}"
                  f"{('｜' + real['err']) if real['err'] else ''}")
            print(f"  {tag} 缺口池：{hole['bad']}/{hole['n']} 不等"
                  f"｜NaN 层 {hole['na_cells']} 格｜共同格 max|Δ|={hole['gap']:.3e}"
                  f"{('｜' + hole['err']) if hole['err'] else ''}"
                  f"｜静态闸={g or '过'}")
            print(f"     {expr}")
            if not ok_real:
                unlandable.append(f"{name}({tag} 真池不等)")
            elif not ok_hole:
                seam += 1
                print(f"     ↑ 真池相等、缺口池分家 ⇒ 这一形是**数据依赖**的相同，不是按构造")
        if picks["A"] and picks["B"]:
            pick, expr = "A", a_expr      # 两形都按构造相同 ⇒ 取 DSL 惯用形，与库内语料同风格
            note = "两形都按构造相同 ⇒ 取 A"
        elif picks["B"]:
            pick, expr = "B", b_expr
            note = "只有 B 按构造相同 ⇒ 取 B（A 只在无缺口数据上相等，不够）"
        elif picks["A"]:
            pick, expr = "A", a_expr
            note = "只有 A 按构造相同 ⇒ 取 A"
        else:
            # 真池过了、缺口池两形都分家 ⇒ 没有任何一形是"按构造相同"，不许落
            pick, expr = "两形都不许落", ""
            note = "两形在缺口池上都分家＝都欠一条等价证明"
        print(f"  ⇒ 落 {pick}｜{note}\n")
        if pick == "两形都不许落":
            unlandable.append(name)
        else:
            landed[name] = (pick, expr)

    print("负对照（窗口/防零除动过一手，必须被这把尺抓到，且要报清在哪一层）：")
    for name, w_expr in WRONG.items():
        r = sweep(name, w_expr, pool)
        caught = r["bad"] > 0
        layer = []
        if r["na_cells"]:
            layer.append(f"NaN 层 {r['na_cells']} 格")
        if r["gap"]:
            layer.append(f"数值层 max|Δ|={r['gap']:.3e}")
        print(f"  {name}: {r['bad']}/{r['n']} 个标的判为不等 ⇒ {'+'.join(layer) or '只有形状差'}"
              f"  {'✅ 抓到了' if caught else '❌ 这把尺恒真，全场作废'}")
        if not caught:
            unlandable.append(f"负对照 {name}")

    print("\n与生产落地表 `factors.FACTOR_EXPR` 对表（本尺的推荐形 vs 真写进代码的那条）：")
    for name, (tag, expr) in landed.items():
        got = FACTOR_EXPR.get(name, "<表里没有这一条>")
        same = got == expr
        print(f"  {name}: 尺子选 [{tag}]，表里写 {'同一个串' if same else '另一个串'}"
              f"{' ⇒ ' + got if not same else ''}")
        if not same:
            unlandable.append(f"{name} 与 FACTOR_EXPR 漂移")

    if seam == 0:
        print("\n❌ 缺口池那一遍一条都没分家 ⇒ 第二遍是空跑，本尺没有证到"
              "「按构造相同」这一层（人造缺口没落进窗口？先查 `make_holed`）")
        unlandable.append("缺口池空跑")
    else:
        print(f"\n✅ 缺口池那一遍把 {seam} 条形从「按构造相同」里挑了出去"
              "＝第二遍不是空跑，它有判别力")

    if unlandable:
        print(f"\n❌ 不许落地的有 {len(unlandable)} 条：{unlandable}")
        return 1
    print(f"\n✅ 9 条各有至少一形「真池＋缺口池都逐位相同」，负对照三支全被抓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
