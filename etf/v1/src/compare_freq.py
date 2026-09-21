# -*- coding: utf-8 -*-
"""
频率对比：同一策略在日线 vs 分钟线的表现 + 持仓相似度
用法：python compare_freq.py
"""

import os
import json
import pandas as pd
import numpy as np
import _bootstrap  # noqa: F401  必须先于项目模块导入
from log_kit import setup_logging
from config import RESULTS_DIR


def load_results(freq):
    eq_path = f"{RESULTS_DIR}/equity_{freq}.csv"
    trades_path = f"{RESULTS_DIR}/trades_{freq}.csv"
    if not os.path.exists(eq_path):
        return None
    eq = pd.read_csv(eq_path)
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


def load_daily_positions(freq):
    """从 signals_{freq}.csv 提取每日收盘持仓（当天最后一个非空 target_code）"""
    path = f"{RESULTS_DIR}/signals_{freq}.csv"
    if not os.path.exists(path):
        return None
    s = pd.read_csv(path)
    ts_col = s.columns[0]
    code = s["target_code"].astype(str)
    s["date"] = pd.to_datetime(s[ts_col]).dt.date
    s["target_code"] = code.where(
        code.notna() & ~code.isin(["", "nan", "None"]), "")
    held = s[s["target_code"] != ""]
    all_dates = pd.Index(sorted(s["date"].unique()), name="date")
    daily = held.groupby("date")["target_code"].last().reindex(
        all_dates, fill_value="")
    return daily


def holdings_similarity(pos_a, pos_b):
    """两个频率每日收盘持仓的一致程度"""
    common = pos_a.index.intersection(pos_b.index)
    if len(common) == 0:
        return None
    a, b = pos_a.loc[common].values, pos_b.loc[common].values
    match = a == b
    at_least_one = (a != "") | (b != "")
    both_held = (a != "") & (b != "")
    disagree = [(str(d), x, y or "-") for d, x, y, m
                in zip(common, a, b, match) if not m][:20]
    return {
        "对齐天数": int(len(common)),
        "总相似度": round(float(match.mean()), 4),
        "持仓日相似度": round(float(match[at_least_one].mean()), 4)
                         if at_least_one.any() else None,
        "双边持仓日相似度": round(float(match[both_held].mean()), 4)
                             if both_held.any() else None,
        "持仓占比_a": round(float((a != "").mean()), 4),
        "持仓占比_b": round(float((b != "").mean()), 4),
        "不一致示例": [{"date": d, "a": x, "b": y}
                       for d, x, y in disagree],
    }


def main():
    setup_logging("compare_freq")
    print("=" * 60)
    print("  频率对比：日线 vs 分钟线")
    print("=" * 60)

    results = []
    positions = {}
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
        pos = load_daily_positions(freq)
        if pos is not None:
            positions[freq] = pos

    if results:
        df = pd.DataFrame(results)
        df.to_csv(f"{RESULTS_DIR}/freq_comparison.csv",
                  index=False, encoding="utf-8-sig")
        print(f"\n  ✅ 对比已保存: {RESULTS_DIR}/freq_comparison.csv")

    sim = None
    if "daily" in positions and "1min" in positions:
        sim = holdings_similarity(positions["daily"],
                                  positions["1min"])
    if sim:
        print("\n  🎯 持仓相似度（日线 vs 分钟线，每日收盘仓位）")
        for k, v in sim.items():
            if k != "不一致示例" and v is not None:
                print(f"    {k}: {v}")
        with open(f"{RESULTS_DIR}/holdings_similarity.json", "w",
                  encoding="utf-8") as f:
            json.dump(sim, f, ensure_ascii=False, indent=2,
                      default=str)
        print("  ✅ 已保存: "
              f"{RESULTS_DIR}/holdings_similarity.json")
    elif len(results) == 2:
        print("\n  ⚠️ 缺少 signals_daily.csv / signals_1min.csv，"
              "无法计算持仓相似度")


if __name__ == "__main__":
    main()
