"""护栏前后 · 全市场截面评估对照（一次性核查，判据来路见 CHANGELOG）

老基线 = 已提交的 stock/v1/data/results/ashare_factor_eval.csv（收益未裁剪）
新产物 = /tmp/factor_eval_retclip.csv（ashare_screen.guard_ret 裁掉复权假台阶后）

看三件事：
1. Pearson 口径（cs_ic_mean / ts_ic_mean）位移多少 —— 假台阶是极端值，最吃这条；
2. 秩口径（cs_rank_ic_mean）位移多少 —— 秩统计量理论上几乎不动，这是对照；
3. 排序与符号有没有翻 ——「量能族 vs 价格水平族」的相对次序是本线的载荷结论。
"""
import pandas as pd

OLD = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results/ashare_factor_eval.csv"
NEW = "/tmp/factor_eval_retclip.csv"

COLS = ["ts_ic_mean", "cs_ic_mean", "cs_rank_ic_mean", "cs_icir", "cs_rank_icir",
        "cs_ic_win_rate", "ts_icir"]

o = pd.read_csv(OLD)
n = pd.read_csv(NEW)
j = o.merge(n, on="name", suffixes=("_旧", "_新"))
print(f"老 {len(o)} 行 / 新 {len(n)} 行 / 同名配对 {len(j)} 行")
missing = set(o.name) ^ set(n.name)
if missing:
    print("!! 名字集合不一致:", missing)

for c in COLS:
    d = (j[f"{c}_新"] - j[f"{c}_旧"])
    print(f"\n== {c}: 中位位移 {d.median():+.5f}  绝对最大 {d.abs().max():.5f} "
          f"符号翻转 {(j[f'{c}_旧'] * j[f'{c}_新'] < 0).sum()} 个")
    top = j.assign(d=d).nlargest(3, "d")[["name", f"{c}_旧", f"{c}_新", "d"]]
    print(top.to_string(index=False))

# 相对次序：按 cs_rank_ic_mean 排序，看两版排名差
for c in ["cs_rank_ic_mean", "cs_ic_mean", "ts_ic_mean"]:
    ro = j.sort_values(f"{c}_旧").reset_index().reset_index()
    rn = j.sort_values(f"{c}_新").reset_index().reset_index()
    rk = ro[["name", "level_0"]].merge(rn[["name", "level_0"]], on="name", suffixes=("_旧", "_新"))
    print(f"\n[{c}] 秩相关(名次) {rk['level_0_旧'].corr(rk['level_0_新'], method='spearman'):.4f}"
          f"  最大名次位移 {int((rk['level_0_旧'] - rk['level_0_新']).abs().max())}")

vol = j[j["name"].str.contains("Volume")]
print("\n量能族（本线的剔除过滤器来源）：")
print(vol[["name", "cs_rank_ic_mean_旧", "cs_rank_ic_mean_新",
           "cs_ic_mean_旧", "cs_ic_mean_新", "ts_ic_mean_旧", "ts_ic_mean_新"]].to_string(index=False))
print("\n价格水平族（对照）：")
print(j[~j["name"].str.contains("Volume")][["name", "cs_rank_ic_mean_旧", "cs_rank_ic_mean_新",
                                            "cs_ic_mean_旧", "cs_ic_mean_新"]].to_string(index=False))
