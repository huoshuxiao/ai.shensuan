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
    """从 signals_{freq}.csv 还原每日收盘的目标持仓**集合**。

    信号是稀疏长表（只在调仓 bar 出行，每只一行 code/weight），两个调仓
    日之间沿用上一组目标；code 空串是空仓哨兵，表示该次调仓目标为空。"""
    path = f"{RESULTS_DIR}/signals_{freq}.csv"
    if not os.path.exists(path):
        return None
    s = pd.read_csv(path)
    if "code" not in s.columns:
        print(f"  ⚠️ {path} 不是长表格式（缺 code 列），跳过持仓对比")
        return None
    s["ts"] = pd.to_datetime(s[s.columns[0]])
    s["date"] = s["ts"].dt.date
    last_ts = s.groupby("date")["ts"].max()
    daily, cur = {}, set()
    for d in sorted(s["date"].unique()):
        rows = s[(s["date"] == d) & (s["ts"] == last_ts[d])]
        cur = {c for c in rows["code"].astype(str)
               if c not in ("", "nan", "None")}
        daily[d] = frozenset(cur)
    return pd.Series(daily).sort_index()


def holdings_similarity(pos_a, pos_b):
    """两个频率每日收盘目标持仓集合的一致程度：
    完全一致率 = |{d: A_d == B_d}| / 对齐天数；
    重合度（Jaccard）= |A∩B| / |A∪B|，逐日取均值（两边都空仓记 1.0）。"""
    common = pos_a.index.intersection(pos_b.index)
    if len(common) == 0:
        return None
    a, b = pos_a.loc[common].values, pos_b.loc[common].values
    match = np.array([x == y for x, y in zip(a, b)])
    jac = np.array([len(x & y) / len(x | y) if (x | y) else 1.0
                    for x, y in zip(a, b)])
    at_least_one = np.array([bool(x or y) for x, y in zip(a, b)])
    both_held = np.array([bool(x) and bool(y) for x, y in zip(a, b)])

    def _s(x):
        return "+".join(sorted(x)) if len(x) else "-"
    disagree = [(str(d), _s(x), _s(y)) for d, x, y, m
                in zip(common, a, b, match) if not m][:20]
    return {
        "对齐天数": int(len(common)),
        "总相似度": round(float(match.mean()), 4),
        "平均重合度": round(float(jac.mean()), 4),
        "持仓日相似度": round(float(match[at_least_one].mean()), 4)
                         if at_least_one.any() else None,
        "双边持仓日相似度": round(float(match[both_held].mean()), 4)
                             if both_held.any() else None,
        "持仓占比_a": round(float(np.array([bool(x) for x in a]).mean()), 4),
        "持仓占比_b": round(float(np.array([bool(x) for x in b]).mean()), 4),
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
