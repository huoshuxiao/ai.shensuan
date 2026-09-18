# -*- coding: utf-8 -*-
"""净值曲线可视化"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import rcParams

rcParams["font.sans-serif"] = ["SimHei", "Arial Unicode MS",
                                "DejaVu Sans"]
rcParams["axes.unicode_minus"] = False


def plot_equity(equity_df, save_path="equity_curve.png"):
    fig, axes = plt.subplots(2, 1, figsize=(12, 8),
                             gridspec_kw={"height_ratios": [3, 1]})
    eq = equity_df["equity"]
    axes[0].plot(eq.index, eq.values, color="steelblue", linewidth=1.5)
    axes[0].axhline(eq.iloc[0], color="gray", linestyle="--",
                     linewidth=0.8)
    axes[0].set_title("ETF 轮动策略净值曲线", fontsize=14)
    axes[0].set_ylabel("资金 (元)")
    axes[0].grid(alpha=0.3)

    dd = (eq - eq.cummax()) / eq.cummax() * 100
    axes[1].fill_between(dd.index, dd.values, 0, color="red", alpha=0.4)
    axes[1].set_ylabel("回撤 (%)")
    axes[1].set_xlabel("日期")
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(save_path, dpi=120)
    print(f"  📈 净值图已保存: {save_path}")