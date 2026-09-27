# -*- coding: utf-8 -*-
"""截面独立性探针：池放宽到 100 只后，横截面还有没有 dispersion。

DSR/PBO 的统计功效来自「截面标的近似独立」这一前提；ETF 是按指数分组的，
同指数的代表已去重，但宽基与行业之间仍有共同 beta。这里量两件能直接查的：
1) 每日可选标的数（截面厚度）——早年间池里根本没几只满 365 天；
2) 剔除当日市场均值后的日截面离散度 std(r_i,t - r̄_t)——共同 beta 拿掉之后
   还剩多少可用于排序的独立信息。
纯读本地缓存，不发网络请求。
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "etf", "v1", "src")
sys.path.insert(0, _SRC)
import _bootstrap  # noqa: E402,F401

import pandas as pd  # noqa: E402
from data_loader import DataLoader  # noqa: E402
from etf_universe import get_universe  # noqa: E402


def main():
    uni = get_universe()
    codes = uni.universe["code"].tolist()
    print(f"池 {len(codes)} 只，逐只读本地日线")

    closes = {}
    for i, c in enumerate(codes, 1):
        df = DataLoader(freq="daily").load(c, use_cache=True)
        if df is None or df.empty:
            print(f"  ⚠️ {c} 无数据")
            continue
        closes[c] = df["close"]
        if i % 20 == 0:
            print(f"  已读 {i}/{len(codes)}", flush=True)

    px = pd.DataFrame(closes).sort_index()
    ret = px.pct_change(fill_method=None)
    n = ret.notna().sum(axis=1)
    # 去均值后的截面离散度：先扣掉当日市场平均，剩下的才是排序用得上的独立信息
    demeaned = ret.sub(ret.mean(axis=1), axis=0)
    disp = demeaned.std(axis=1)
    raw_disp = ret.std(axis=1)
    # 两两相关均值：pandas corr() 走成对完整观测，不必先丢行
    import numpy as np
    recent = ret[ret.index >= "2021-01-01"]
    have = recent.notna().sum().sort_values(ascending=False)
    sub = recent.loc[:, have.index[:40]]
    corr = sub.corr().values
    off = corr[np.triu_indices(len(corr), 1)]

    yearly = pd.DataFrame({
        "有数据只数": n,
        "原始截面std": raw_disp,
        "去均值截面std": disp,
    }).resample("YE").mean(numeric_only=True)
    yearly["有数据只数"] = n.resample("YE").mean()
    print("\n=== 逐年截面状态 ===")
    with pd.option_context("display.width", 140, "display.float_format",
                           lambda v: f"{v:,.4f}"):
        print(yearly.dropna().to_string())

    print(f"\n=== 全期 ===")
    print(f"日截面平均可选只数 {n.mean():.1f}（最多 {n.max()}）")
    print(f"日截面 std 均值：原始 {raw_disp.mean():.4f} →"
          f" 扣掉当日截面均值后 {disp.mean():.4f}"
          f"（差 {(1 - disp.mean() / raw_disp.mean()) * 100:.1f}%：截面均值本身"
          f"几乎不承载信息，分散度不是被「一天大家一起涨跌」撑起来的）")
    print(f"2021+ 的 40 只抽样两两相关均值 {np.nanmean(off):.3f}"
          f"（中位 {np.nanmedian(off):.3f}）—— 越接近 1 越说明截面只剩一个因子")

    # 有效独立数：k_eff ≈ k / (1 + (k-1)·ρ̄)，是「名义 100 只」折成几个独立观测
    for k in (n.mean(), 20.0, 50.0):
        rho = float(np.nanmean(off))
        if rho > 0:
            print(f"名义 {k:.0f} 只 → 有效独立观测 ≈ "
                  f"{k / (1 + (k - 1) * rho):.1f} 只")


if __name__ == "__main__":
    main()
