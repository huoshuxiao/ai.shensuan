# -*- coding: utf-8 -*-
"""选项I 账单的第二半：Alpha158 风格表达式**逐条**的实测代价（尺子还是环 1 那一份）

为什么不能直接 `STOCK_SAMPLE=300`：那个开关取的是 `list(pool.items())[:300]`
（`run_ashare_factor_eval.py:96-97`），面板前 300 只全是北交所新股，历史短 ⇒ 会**低估**。
这里改成**等间隔 stride** 抽 1/19（≈299 只，覆盖主板/创业/科创/北交所有同年份），
并且**一条一条计时**（含 `rolling.apply` 的表达式比简单均值贵一个量级，混在一起测平均会骗人）。

只读：不写任何权威产物（`ashare_factor_eval.csv` 一个字节不动），不落任何 csv。
"""
import json
import os
import resource
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "stock", "v1", "src"))
sys.path.insert(0, SRC)
os.chdir(SRC)

from run_ashare_factor_eval import load_panel, evaluate    # noqa: E402  同一把尺子

PROBE = os.path.join(HERE, "tmp_alpha158", "probe_exprs_0926.json")
tasks = json.load(open(PROBE, encoding="utf-8"))
print(f"[探测] {len(tasks)} 条 Alpha158 风格表达式（逐条计时）")

t0 = time.time()
pool_all = load_panel()
n_all = len(pool_all)
load_s = time.time() - t0
print(f"[面板] {n_all} 只，load_panel 用时 {load_s:.0f}s")

STRIDE = 19
import pandas as pd   # noqa: E402
lens_all = pd.Series([len(d) for d in pool_all.values()])
items = list(pool_all.items())
sub = {k: v for i, (k, v) in enumerate(items) if i % STRIDE == 0}
del pool_all, items
lens_sub = pd.Series([len(d) for d in sub.values()])
print(f"[样本] stride={STRIDE} ⇒ {len(sub)} 只（取面板第 0、19、38… 只，跨全表而非前 N 只）")
# 自校验：样本历史长度若明显短于全表，这份外推就偏乐观，两个中位数必须一起念
print(f"[样本长度] 样本中位 {lens_sub.median():.0f} 行、最短 {lens_sub.min()}、最长 {lens_sub.max()}"
      f"　全表中位 {lens_all.median():.0f} 行 ⇒ 比值 {lens_sub.median()/lens_all.median():.2f}"
      f"（≈1 才说明抽样没有偏向短历史）")


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


print("\n===== 逐条：单只·单次秒数 ⇒ 外推全市场 5677 只 =====")
per = []
for t in tasks:
    tt = time.time()
    r = evaluate(sub, [t])
    dt = time.time() - tt
    st = r[0].get("status")
    per.append((t["name"], dt, st))
    print(f"  {t['name']:<14} {dt:6.2f}s / {len(sub)} 只 ⇒ {dt/len(sub)*1000:6.1f} ms/只"
          f"　外推 5677 只 = {dt/len(sub)*n_all:7.0f}s = {dt/len(sub)*n_all/60:5.1f} 分"
          f"　status={st}")
print(f"\n[峰值 RSS] {rss_mb()/1024:.2f} GB（本机上限 15 GB）")

tot = sum(d for _, d, _ in per)
avg = tot / len(per) / len(sub) * n_all
n_tier = {"甲+甲′+乙 不扩算子": 107, "全 158（扩 7 个算子）": 158}
print(f"\n===== 外推账单（按本轮 {len(per)} 条的平均单条成本 {avg:.0f}s/条·全市场）=====")
print(f"  单条平均 {avg/60:.1f} 分钟　最贵一条 {max(d for _, d, _ in per)/len(sub)*n_all/60:.1f} 分钟"
      f"　最便宜 {min(d for _, d, _ in per)/len(sub)*n_all/60:.1f} 分钟")
for label, n in n_tier.items():
    print(f"  {label:<26} ≈ {avg*n/3600:5.2f} 小时（含 load_panel {load_s:.0f}s 一次）")
print(f"  对照：环 1 现有 21 条在库表达式实测 913s ⇒ 单条 {913/21:.0f}s，"
      f"本轮 Alpha158 风格单条 {avg:.0f}s（比值 {avg/(913/21):.2f}×）")
