# -*- coding: utf-8 -*-
"""BIAS20（20 日乖离率）再验证 —— 上一场只留了每行最大值，这场把整行落盘（09-28）。

`BIAS20 = close/ma(close,20) - 1`：现价偏离 20 日均线的比例，v2 蓝图（`live2etf/v2/AGENT.md:437`）
把它归在"回答贪婪还是恐惧"的情绪类。上一场探针读出它与本线候选 `位置·C/MA20` 的
|corr| = 1.0000（**逐字同式**），但那场没存下整行，只存了每行最大值 ⇒ 这一场补三件事：

1. **整行撞线读数**：BIAS20 对 66 条对手池（本线 31 候选 + 在库 35 条可译 active）的
   逐条 |corr|，全表落盘。点名两格：与 `rsi_14`（库里那条真 RSI）、与 `情绪·RSI14` 草稿。
2. **换窗是不是换皮**：BIAS5/10/20/60/120 五档两两之间的 |corr|。若不同窗读到 1.0000
   ⇒ 说明切窗没生效（反证）；若彼此 ≥0.85 ⇒ "换个窗=换一条因子"这条路在本线不成立
   （与股票线量能族同结论，那条是独立量出来的，不是照搬）。
3. **信号面**：环 1 口径 @h5/10/20 的 RankIC/ICIR/胜率/t + 逐年符号（只问稳不稳，不问好坏）
   + 五档分层单调性/多空年化/高分侧换手。
   ⚠️ 分层是**逐日再平衡**的读数，在 ETF 上做不到（`layer_summary` docstring 自己写着
   "不能拿去和组合层的扣费净收益比大小"），这里只用于看单调性与斜率。

三条反证（防的是探针自己算错，不是防判据）：
- BIAS20 对 `位置·C/MA20` 必须 = 1.0000（同式换名，读不到就是求值/对表坏了）
- BIAS20 对 BIAS5/10/60/120 必须 **< 1.0000**（不同窗=不同序列，读到 1.0 说明窗口没生效）
- BIAS20 的量纲是百分比偏离，不该落在 [0,1]；若读到 ∈[0,1] 说明表达式被谁改写过

只读：不写 `etf/v1/data/results` 任何 csv、不碰因子库、不改 `.env`、不进 FAMILY_SPECS。
产物只落在本脚本自己的目录 `etf/v1/temp/bias20_verify_0928/`。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(ROOT, "etf", "v1", "src")
sys.path.insert(0, SRC)
os.chdir(SRC)

import numpy as np                                            # noqa: E402
import pandas as pd                                           # noqa: E402
import etf_admission as EA                                    # noqa: E402
from run_etf_redundancy_check import verdict                  # noqa: E402  判决文案单点复用

OUT_DIR = os.path.join(ROOT, "etf", "v1", "temp", "bias20_verify_0928")
os.makedirs(OUT_DIR, exist_ok=True)
UP = "(delta(close,1)+abs(delta(close,1)))/2"
DN = "(abs(delta(close,1))-delta(close,1))/2"

FOCUS = [
    {"family": "乖离", "name": "BIAS20", "expr": "close/ma(df,20)-1"},
    {"family": "乖离", "name": "BIAS5", "expr": "close/ma(df,5)-1"},
    {"family": "乖离", "name": "BIAS10", "expr": "close/ma(df,10)-1"},
    {"family": "乖离", "name": "BIAS60", "expr": "close/ma(df,60)-1"},
    {"family": "乖离", "name": "BIAS120", "expr": "close/ma(df,120)-1"},
    {"family": "情绪", "name": "情绪·RSI14",
     "expr": f"ts_mean({UP},14)/(ts_mean({UP},14)+ts_mean({DN},14)+1e-9)"},
    {"family": "情绪", "name": "情绪·波动分位", "expr": "rank(ts_std(returns,20),120)"},
]
PRIMARY_H = 10
HORIZONS = EA.HORIZONS


def abs_row(row, self_names=()):
    """整行 |corr|（对侧只按名字剔掉"它自己"，同式不同名的照算 —— 与环 3 同一规矩）。"""
    s = row[~row.index.isin(list(self_names))].dropna()
    return (s.abs().rename("abs_corr").to_frame().assign(带符号=s)
            .sort_values("abs_corr", ascending=False))


print("=" * 100)
print(f"[输入] 主测 {len(FOCUS)} 条 × 对手池（本线候选 {len(EA.FAMILY_SPECS)} + 在库可译）")
pool = EA.load_pool()
lib_specs = EA.specs_from_rows(EA.load_active_library(), family="在库")
opp = list(EA.FAMILY_SPECS) + lib_specs
print(f"[输入] 对手池 {len(opp)} 条；评估窗起点 {EA.EVAL_START}")

f_new = {k: EA.slice_to_start(v, EA.EVAL_START)
         for k, v in EA.evaluate_factors(pool, FOCUS).items()}
f_opp = {k: EA.slice_to_start(v, EA.EVAL_START)
         for k, v in EA.evaluate_factors(pool, opp).items()}
missing = [s["name"] for s in FOCUS if s["name"] not in f_new]
assert not missing, f"主测条目不可求值：{missing}"

# ---------- 反证：同式必须 1.0000、异窗必须 <1.0000、量纲不该 ∈[0,1] ----------
_, M_opp = EA.cs_corr_mean(f_new, f_opp, min_cs=EA.MIN_CS)
same = float(M_opp.loc["BIAS20", "位置·C/MA20"])
print(f"[反证] BIAS20 ↔ 位置·C/MA20（同式换名）|corr| = {abs(same):.4f}")
assert abs(same) > 0.999, f"同式没读到 1.0000（{same}）：求值/对表环节坏了，本场读数全废"
b = f_new["BIAS20"]
_, M_bias = EA.cs_corr_mean({"BIAS20": b}, {k: f_new[k] for k in f_new if k != "BIAS20"},
                            min_cs=EA.MIN_CS)
for nm, v in M_bias.loc["BIAS20"].items():
    print(f"[反证] BIAS20 ↔ {nm:12s} |corr| = {abs(v):.4f}")
    assert abs(v) < 0.999, f"不同窗读到 {abs(v):.4f}=同一条，窗口没生效"
v = b.values[np.isfinite(b.values)]
print(f"[量纲] BIAS20 ∈ [{v.min():.3%}, {v.max():.3%}]（百分比偏离，天然不落在 [0,1]）")
assert not (0.0 <= v.min() and v.max() <= 1.0), "BIAS20 落在 [0,1] 内，表达式被改写？"

# ---------- ① 整行撞线：BIAS20 对 66 条对手池 ----------
row = abs_row(M_opp.loc["BIAS20"])
row.insert(0, "对手池成员", row.index)
row["判决"] = row["abs_corr"].map(verdict)
full = row.reset_index(drop=True)
full.to_csv(os.path.join(OUT_DIR, "bias20_vs_pool_all66.csv"), index=False)
pd.set_option("display.width", 240)
pd.set_option("display.max_colwidth", 40)
print("\n===== ① BIAS20 对对手池的整行 |corr|（前 12 条，全表已落盘）=====")
print(full.head(12).round(4).to_string(index=False))
n_ge85 = int((full["abs_corr"] >= EA.RED_BAR).sum())
print(f"[小结] 66 条里 {n_ge85} 条 ≥0.85、{int((full['abs_corr'] >= 0.99).sum())} 条 ≥0.99、"
      f"中位 |corr| = {full['abs_corr'].median():.3f}；"
      f"BIAS20↔rsi_14 = {abs(M_opp.loc['BIAS20', 'rsi_14']):.4f}，"
      f"BIAS20↔情绪·RSI14 = {abs(M_bias.loc['BIAS20', '情绪·RSI14']):.4f}")
print("\n[对照] 其余各条的整行最大值（上一场就是这个读数把 3 条挡在门外）")
mx = (pd.DataFrame({"最像谁": [M_opp.loc[n].abs().idxmax() for n in f_new],
                    "|corr|": [M_opp.loc[n].abs().max() for n in f_new]})
      .assign(判决=lambda d: d["|corr|"].map(verdict)))
print(mx.round(4).to_string())

# ---------- ② 换窗=换皮？乖离族内部两两 ----------
names = [n for n in f_new if n.startswith("BIAS")]
_, MM = EA.cs_corr_mean({n: f_new[n] for n in names}, min_cs=EA.MIN_CS)
off = MM.to_numpy()[np.triu_indices(len(names), 1)]
print("\n===== ② 乖离族五档窗两两 |corr|（对角=自己=1，按规矩不读）=====")
print(MM.round(4).to_string())
MM.to_csv(os.path.join(OUT_DIR, "bias_windows_matrix.csv"))
print(f"[读数] 两两 {len(off)} 格里 ≥0.85 的 {int((off >= EA.RED_BAR).sum())} 格、"
      f"≥0.70 的 {int((off >= EA.NEAR_DUP).sum())} 格；最小 {off.min():.3f}、最大 {off.max():.3f}")

# ---------- ③ 信号面：三期限 + 逐年 + 分层 ----------
m = EA.take_window(EA.build_matrices(pool))
sig, yearly = [], {}
for nm, w in f_new.items():
    r = {"候选": nm}
    for h in HORIZONS:
        # 标签侧的秩每次重算：daily_cs_ic 的 mask 随因子变，缓存秩等于偷换定义
        ic, ric, n = EA.daily_cs_ic(w, m[f"fwd{h}"], min_cs=EA.MIN_CS)
        s = EA.ic_summary(ric, n, EA.MIN_CS)
        r[f"rank_ic_h{h}"] = s["mean"]
        r[f"rank_icir_h{h}"] = s["icir"]
        r[f"win_h{h}"] = s["win"]
        r[f"t_h{h}"] = s["t"]
        if h == PRIMARY_H:
            yearly[nm] = ric.groupby(ric.index.year).mean()
    yr = pd.Series(yearly[nm])
    r[f"逐年同号h{PRIMARY_H}"] = f"{int((np.sign(yr) == np.sign(yr.mean())).sum())}/{len(yr)}"
    q = EA.layer_summary(EA.quintile_layers(w, m["ret"], q=EA.QUINTILES, min_cs=EA.MIN_CS))
    r.update({"mono": q["mono"], "ls_ann": q["ls_ann"], "top_turnover_ann": q["top_turnover_ann"]})
    sig.append(r)
sig = pd.DataFrame(sig)
sig.to_csv(os.path.join(OUT_DIR, "bias_signal.csv"), index=False)
print(f"\n===== ③ 信号面（环 1 口径；mono/ls_ann/换手那三列是逐日再平衡、不可交易，"
      f"只看单调性与斜率）=====")
print(sig.round(4).to_string(index=False))

yr_tab = pd.DataFrame(yearly)
yr_tab.round(4).to_csv(os.path.join(OUT_DIR, "bias_yearly_rankic.csv"))
print(f"\n[逐年 RankIC @h{PRIMARY_H}]（正=高分侧未来涨得多；只问符号稳不稳，不问好坏）")
print(yr_tab.round(4).to_string())

print(f"\n[归档] 4 张表落在 {OUT_DIR}")
print("[只读声明] 未写 etf/v1/data/results、未碰因子库、未改 .env、未动 FAMILY_SPECS")
