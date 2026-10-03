"""名单腿 13 行在「同一场、同一网格、只差名单形状」下的对照（选项E 落地验收）
写这个脚本的理由：09-24 那批归档表是 569 个调仓日，今天的面板是 570 个 ——
直接拿归档表比会把「多一天」和「换名单形状」混成一笔钱。
"""
import os
import sys

import pandas as pd

R = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results"
pd.set_option("display.width", 220)

g = pd.read_csv(os.path.join(R, "ashare_portfolio_buylist_globalctrl_0925.csv"))
q = pd.read_csv(os.path.join(R, "ashare_portfolio_buylist.csv"))
m = g.merge(q, on=["variant", "kind", "top_n"], suffixes=("_G", "_Q"))
m["Δexcess_pp"] = (m.excess_univ_ew_ann_Q - m.excess_univ_ew_ann_G) * 100
ref_G = float(m.loc[m["variant"] == "参照·不剔除", "excess_univ_ew_ann_G"].iloc[0])
ref_Q = float(m.loc[m["variant"] == "参照·不剔除", "excess_univ_ew_ann_Q"].iloc[0])
u1_G = float(m.loc[m["variant"] == "并集(命中>=1)", "excess_univ_ew_ann_G"].iloc[0])
u1_Q = float(m.loc[m["variant"] == "并集(命中>=1)", "excess_univ_ew_ann_Q"].iloc[0])
m["等于参照_G"] = (m.excess_univ_ew_ann_G - ref_G).abs() < 1e-9
m["等于参照_Q"] = (m.excess_univ_ew_ann_Q - ref_Q).abs() < 1e-9
m["等于并集1_G"] = (m.excess_univ_ew_ann_G - u1_G).abs() < 1e-9
m["等于并集1_Q"] = (m.excess_univ_ew_ann_Q - u1_Q).abs() < 1e-9
print(m[["variant", "kind", "excess_univ_ew_ann_G", "excess_univ_ew_ann_Q", "Δexcess_pp",
         "等于参照_G", "等于参照_Q", "等于并集1_G", "等于并集1_Q",
         "one_way_turnover_G", "one_way_turnover_Q",
         "excess_univ_ew_ir_G", "excess_univ_ew_ir_Q"]].to_string(
    index=False, float_format=lambda v: f"{v:.4f}"))
print(f"\n参照·不剔除：全局 {ref_G:.6f} → 配额 {ref_Q:.6f}（{(ref_Q-ref_G)*100:+.2f}pp）")
for a, b in [("单条·量能水平", "参照·不剔除"), ("单条·量能波动", "参照·不剔除"),
             ("并集(命中>=3)", "参照·不剔除"), ("并集(命中>=4)", "参照·不剔除"),
             ("留一·去掉量能水平", "并集(命中>=1)"), ("留一·去掉量能波动", "并集(命中>=1)")]:
    ra = m.loc[m["variant"] == a].iloc[0]
    gb = float(m.loc[m["variant"] == b, "excess_univ_ew_ann_G"].iloc[0])
    qb = float(m.loc[m["variant"] == b, "excess_univ_ew_ann_Q"].iloc[0])
    print(f"{a} vs {b}: 全局档差 {abs(ra.excess_univ_ew_ann_G - gb):.2e} | "
          f"配额档差 {abs(ra.excess_univ_ew_ann_Q - qb):.2e}")
