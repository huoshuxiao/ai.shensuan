# -*- coding: utf-8 -*-
"""
频率对比：同一策略在日线 vs 分钟线的表现
用法：python compare_freq.py
"""

import os
import pandas as pd
import numpy as np


def load_results(freq):
    eq_path = f"equity_{freq}.csv"
    trades_path = f"trades_{freq}.csv"
    if not os.path.exists(eq_path):
        return None
    eq = pd.read_csv(eq_path)
    eq_col = eq.columns[1] if eq.columns[0] in ("date", "datetime") \
        else eq.columns[0]
    eq = eq.set_index(eq.columns[0])
    trades = pd.read_csv(trades_path) if os.path.exists(trades_path) \
        else pd.DataFrame()
    return {"equity": eq, "trades": trades}


def compute_stats(eq_series, trades_df, freq):
    rets = eq_series.pct_change().dropna()
    total = eq_series.iloc[-1] / eq_series.iloc[0] - 1
    days = max((pd.to_datetime(eq_series.index[-1]) -
                pd.to_datetime(eq_series.index[0])).days, 1)
    ann = (1 + total) ** (365 / days) - 1
    if freq == "daily":
        bpy = 252
    else:
        bpy = 240 * 252
    vol = rets.std() * np.sqrt(bpy)
    sharpe = ann / (vol + 1e-9)
    dd = (eq_series - eq_series.cummax()) / eq_series.cummax()
    return {"freq": freq, "总收益率": f"{total*100:.2f}%",
            "年化收益率": f"{ann*100:.2f}%",
            "年化波动率": f"{vol*100:.2f}%",
            "夏普比率": round(sharpe, 3),
            "最大回撤": f"{dd.min()*100:.2f}%",
            "交易次数": len(trades_df)}


def main():
    print("=" * 60)
    print("  频率对比：日线 vs 分钟线")
    print("=" * 60)

    results = []
    for freq in ["daily", "1min"]:
        data = load_results(freq)
        if data is None:
            print(f"  ⚠️ {freq} 结果缺失，跳过")
            continue
        stats = compute_stats(data["equity"]["equity"]
                              if "equity" in data["equity"].columns
                              else data["equity"].iloc[:, 0],
                              data["trades"], freq)
        results.append(stats)
        print(f"\n  [{freq}]")
        for k, v in stats.items():
            print(f"    {k}: {v}")

    if results:
        df = pd.DataFrame(results)
        df.to_csv("freq_comparison.csv", index=False,
                  encoding="utf-8-sig")
        print(f"\n  ✅ 对比已保存: freq_comparison.csv")


if __name__ == "__main__":
    main()