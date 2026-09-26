# -*- coding: utf-8 -*-
"""P0 落盘自检（09-24）：新主表的**旧 36 行**必须与备份逐字相同

加行不许改行：本次给 run_ashare_portfolio_eval.py 加了 allow 旋钮与一条新送验
表达式（ts_mean(volume,20)），若旧行的任一数值发生位移，说明并集实测动了原判据
（那是最坏的一种假结论来源）。逐元素比，差值按 float64 严格 0 判。
"""
import pandas as pd

R = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results"
old = pd.read_csv(f"{R}/ashare_portfolio_eval.csv.bak-0924p0")
new = pd.read_csv(f"{R}/ashare_portfolio_eval.csv")
k = lambda d: d["signal"] + "|" + d["top_n"].astype(str)
ok, nk = k(old), k(new)
print(f"旧表 {old.shape}  新表 {new.shape}  新增行 {sorted(set(nk) - set(ok))}")

a = old.set_index(pd.Index(k(old), name="row")).sort_index()
b = new.set_index(pd.Index(k(new), name="row")).sort_index()
b = b.loc[a.index]                      # 只取旧表已有的那 36 行，按同一顺序
num = [c for c in a.columns if a[c].dtype != object]
d = (a[num] - b[num]).abs()
print(f"旧 {len(a)} 行 × {len(num)} 数值列：最大绝对差 {float(d.values.max()):g}")
print("逐字相同的列 ", sorted(c for c in num if float(d[c].max()) == 0))
print("有位移的列   ", sorted(c for c in num if float(d[c].max()) > 0))

print("\n新增行（生产口径的量能水平 SMA(Volume,20) 第一次过组合层，做多低分侧）")
cols = ["top_n", "ann_return", "ann_return_gross", "excess_univ_ew_ann",
        "excess_univ_ew_ir", "one_way_turnover", "q5_ann",
        "excl_worst_vs_pool_ann", "avg_amount_20d"]
print(new[new["signal"].str.contains("SMA\\(Volume,20\\)")][cols]
      .to_string(index=False, float_format=lambda v: f"{v:.4f}"))

print("\n减法腿 vs 短名单 的一致性对照（同一批调仓日）")
ex = pd.read_csv(f"{R}/ashare_portfolio_exclusion.csv")
bl = pd.read_csv(f"{R}/ashare_portfolio_buylist.csv")
print(ex[["variant", "gain_ann", "gain_ir", "keep_ratio"]]
      .to_string(index=False, float_format=lambda v: f"{v:.4f}"))
print(bl[["variant", "ann_return", "excess_univ_ew_ann", "excess_univ_ew_ir",
          "one_way_turnover"]]
      .to_string(index=False, float_format=lambda v: f"{v:.4f}"))
