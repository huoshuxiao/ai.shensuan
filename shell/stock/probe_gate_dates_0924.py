"""涨停闸三档 flat / board / dated 的**逐日期**归因（09-24，dated 那一轮补的账）。

compare_gate_ab_0924.py 给的是整窗口差异；这一支只回答两个问题：

1. **差异落在哪一年？** 三档的 yearly 表（`<前缀>_<闸>_yearly.csv`，行 = 构造 × 年）
   按 (构造, 年) 对齐后比 `excess`。按代码定义，dated 与 board 只在
   各段生效日之前才有差 ⇒ 面板上唯一有票的那一段是创业板（2020-08-24），
   所以**逐年差异必须全部落在 2020 及更早**；2021 年起两档应逐位相同。
2. **机制账对得上吗？** 主表 `avg_blocked_limit_up`（每调仓日被第 4 道闸挡掉的只数）
   三档各是多少，dated 相对 board 少挡（=少放行）多少。

用法：/usr/bin/python3.10 shell/probe_gate_dates_0924.py [前缀] [调仓次数]
前缀默认 full（全窗口 2015~，那一轮 569 次调仓），冒烟轮给 smoke 并把次数换成
它的实际值（脚本里 只·次 = 每调仓日只数 × 这个次数）。
"""
import sys

import pandas as pd

PRE = sys.argv[1] if len(sys.argv) > 1 else "full"
N_REBAL = int(sys.argv[2]) if len(sys.argv) > 2 else 569
D = f"/tmp/gate_ab_0924"
GATES = ["flat", "board", "dated"]

pd.set_option("display.width", 200, "display.max_columns", 40,
              "display.unicode.east_asian_width", True)

# ---------- 1. 逐年 excess 差异 ----------
ys = {}
for g in GATES:
    p = f"{D}/{PRE}_{g}_yearly.csv"
    df = pd.read_csv(p).rename(columns={"Unnamed: 0": "signal", "datetime": "year"})
    if "gate" in df.columns:      # yearly 表没有 gate 列，只有主表/腿表有
        assert sorted(set(df["gate"].astype(str))) == [g], p
    ys[g] = df.set_index(["signal", "year"])["excess"]

print("===== 逐年 excess：三档两两之差（按年汇总到构造中位）=====")
tab = pd.DataFrame({g: ys[g] for g in GATES})
tab["db"] = tab["dated"] - tab["board"]
tab["df_"] = tab["dated"] - tab["flat"]
by_year = tab.groupby("year")[["db", "df_"]].agg(["median", "min", "max"]).round(5)
by_year.columns = [" · ".join(c) for c in by_year.columns]
print(by_year.to_string())

nonzero = tab[tab["db"].abs() > 1e-9].reset_index()
print(f"\ndated ≠ board 的 (构造,年) 格子：{len(nonzero)}/{len(tab)}，"
      f"年份范围 {nonzero['year'].min()}~{nonzero['year'].max()}"
      f"（判据：只应 ≤2020）")
nz2 = tab[tab["df_"].abs() > 1e-9].reset_index()
print(f"dated ≠ flat 的格子：{len(nz2)}/{len(tab)}，"
      f"年份范围 {nz2['year'].min()}~{nz2['year'].max()}"
      f"（应贯穿全窗口——2020-08 之后三段的放宽仍然生效）")

# ---------- 2. 机制账：三档被挡只数 ----------
print("\n===== 主表机制项 avg_blocked_limit_up（只/调仓日，三档并列）=====")
m = {}
for g in GATES:
    df = pd.read_csv(f"{D}/{PRE}_{g}.csv")
    m[g] = df.set_index(["signal", "kind", "top_n"])["avg_blocked_limit_up"]
mm = pd.DataFrame(m)
# 机制项与构造无关，但**与腿有关**（多头回放腿按建仓域数、分位数腿按五分位域数），
# 所以一档内会有少数几个取值 —— 第 4 段把层次摊开
print(f"每档取值个数：{mm.nunique().to_dict()}，逐档取值 {mm.iloc[0].to_dict()}")
r0 = mm.iloc[0]
print(f"board 比 flat 每调仓日少挡 {r0['flat'] - r0['board']:.2f} 只"
      f"（=多放行 {(r0['flat'] - r0['board']) * N_REBAL:.0f} 只·次）")
print(f"dated 比 board 每调仓日**多挡** {r0['dated'] - r0['board']:.2f} 只"
      f"（=收回 {(r0['dated'] - r0['board']) * N_REBAL:.0f} 只·次；这些是 2020-08-24 前"
      f"创业板被 board 误放行、其实 ±10% 早就封死的那批）")
print(f"dated 相对 flat 仍少挡 {r0['flat'] - r0['dated']:.2f} 只/调仓日"
      f"（净放行 {(r0['flat'] - r0['dated']) * N_REBAL:.0f} 只·次 = 三段尾板块的真口径）")

# ---------- 3. 在用的那根轴：STD(Volume,20)@50 ----------
print("\n===== 在用的排序轴 STD($volume,20) 各档表现 =====")
b = {}
for g in GATES:
    df = pd.read_csv(f"{D}/{PRE}_bl_{g}.csv")
    row = df[(df["variant"] == "参照·不剔除") & (df["top_n"] == 50)].iloc[0]
    b[g] = row
cols = ["ann_return", "excess_univ_ew_ann", "excess_sh000300_ann",
        "one_way_turnover", "max_drawdown", "avg_blocked_limit_up", "universe"]
print(pd.DataFrame({g: b[g][cols] for g in GATES}).to_string())

# ---------- 4. 机制项为什么一档有两个值 ----------
print("\n===== 机制项 avg_blocked_limit_up 的分层（一档内不恒等，因为分位数腿的域不一样）=====")
print(mm.reset_index().groupby(["kind"])["flat"].agg(["nunique", "min", "max"]).to_string())

# ---------- 5. 整窗口钱与换手的两两之差分布 ----------
print("\n===== 主表 39 行两两之差（整窗口）=====")
wide = {}
for g in GATES:
    df = pd.read_csv(f"{D}/{PRE}_{g}.csv")
    wide[g] = df.set_index(["signal", "kind", "top_n"])
for a, c in (("board", "flat"), ("dated", "flat"), ("dated", "board")):
    x = wide[a].join(wide[c], lsuffix="_a", rsuffix="_c", how="inner")
    d = x["ann_return_a"] - x["ann_return_c"]
    e = x["excess_univ_ew_ann_a"] - x["excess_univ_ew_ann_c"]
    print(f"{a} − {c}：|Δann| 中位 {d.abs().median():+.5f}、最大 {d.abs().max():.5f}"
          f"；超额符号翻转 {(x['excess_univ_ew_ann_a'] * x['excess_univ_ew_ann_c'] < 0).sum()}"
          f"/{len(x)} 行；结果逐位相同 {(d.abs() < 5e-7).sum()}/{len(x)} 行"
          f"；Δ换手 中位 {(x['one_way_turnover_a'] - x['one_way_turnover_c']).median():+.5f}")
