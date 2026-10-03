# -*- coding: utf-8 -*-
"""稳健性核对：㊳ 那条链是我手定的四段，换成**一年一步**（9 步）还成不成立。

读 ㊳ 缓存的日频超额序列（`tmp_buy_extra_oos_0926/daily_excess.csv.gz`），不重跑回放。
两条判据：
    1. 先用缓存复现 ㊳ 那四段链的四个总账数（+0.98 / −0.09 / +0.31 / +1.37）——
       对不上说明缓存与驱动不是一份东西，本脚本全部读数作废；
    2. 然后换成 2018~2026 每年一步（训练窗 = 该年之前所有日子，测试窗 = 该年）重算同一条链。
       段边界是我拍的，所以必须再看一眼「拍成一年一步」会不会把结论拍翻。
"""

import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "tmp_buy_extra_oos_0926", "daily_excess.csv.gz")
TD = 252
BASE = "现≥3"
PROD = 50
QS = ["q=0.80", "q=0.85", "q=0.90", "q=0.95"]

d = pd.read_csv(CSV, parse_dates=["date"])
w = d.pivot(index="date", columns="cell", values="excess").sort_index()
EX = {c: w[c].dropna() for c in w.columns}


def cell_of(lab, n=PROD):
    return f"{lab}@{n}"


def diff(lab, a=None, b=None, n=PROD):
    j = pd.concat([EX[cell_of(lab, n)], EX[cell_of(BASE, n)]], axis=1, join="inner").dropna()
    s = j.iloc[:, 0] - j.iloc[:, 1]
    if a is not None:
        s = s[s.index >= a]
    if b is not None:
        s = s[s.index <= b]
    return float(s.mean() * TD), len(s)


def chain(segs, pick_rule):
    """segs = [(train_end, test_a, test_b), ...]；pick_rule(训练窗各刀年化) → 选中的刀"""
    picks, real = [], []
    for tr_end, a, b in segs:
        cand = {lab: diff(lab, b=tr_end)[0] for lab in QS}
        pick = pick_rule(cand)
        picks.append(pick)
        real.append(diff(pick, a=a, b=b)[0])
    days = [diff(QS[0], a=a, b=b)[1] for _, a, b in segs]
    tot = float(np.average(real, weights=days))
    return picks, real, tot, days


# ---------- 判据 1：复现 ㊳ 的四段链 ----------
S4 = [(pd.Timestamp("2018-12-31"), "2019-01-01", "2020-12-31"),
      (pd.Timestamp("2020-12-31"), "2021-01-01", "2023-12-31"),
      (pd.Timestamp("2023-12-31"), "2024-01-01", "2025-12-31"),
      (pd.Timestamp("2025-12-31"), "2026-01-01", "2026-12-31")]
S4 = [(e, pd.Timestamp(a), pd.Timestamp(b)) for e, a, b in S4]
pk, rl, tot, _ = chain(S4, max_by_val := (lambda c: max(c, key=c.get)))
# 与驱动同口径：各段值按该段天数加权拼成一条链
_w = [diff(QS[0], a=a, b=b)[1] for _, a, b in S4]
rnd = float(np.average([np.mean([diff(l, a=a, b=b)[0] for l in QS]) for _, a, b in S4], weights=_w))
orc = float(np.average([max(diff(l, a=a, b=b)[0] for l in QS) for _, a, b in S4], weights=_w))
fix = float(np.average([diff("q=0.80", a=a, b=b)[0] for _, a, b in S4], weights=_w))
print("===== 判据 1：用缓存复现 ㊳ 的四段链（须与驱动打印值对得上）=====")
print(f"  只挑刀 {tot:+.4%}（㊳ +0.98%）　固定0.80 {fix:+.4%}（㊳ −0.09%）　"
      f"随机 {rnd:+.4%}（㊳ +0.31%）　oracle {orc:+.4%}（㊳ +1.37%）")
# 容差取驱动打印值（两位百分数）的半个末位：1.5e-4 = 0.015pp
for tag, got, want in [("只挑刀", tot, 0.0098), ("固定0.80", fix, -0.0009),
                       ("随机", rnd, 0.0031), ("oracle", orc, 0.0137)]:
    if abs(got - want) > 1.5e-4:
        raise SystemExit(f"[判据1] {tag} 复现不上（{got:+.4%} vs {want:+.4%}）⇒ 本表作废")
print(f"  选中序列 {pk}")

# ---------- 判据 2：一年一步 ----------
SEGS = []
for y in range(2018, 2027):
    SEGS.append((pd.Timestamp(f"{y - 1}-12-31"), pd.Timestamp(f"{y}-01-01"),
                 pd.Timestamp(f"{y}-12-31")))
pk1, rl1, tot1, d1 = chain(SEGS, max_by_val)
w1 = [diff("q=0.80", a=a, b=b)[1] for _, a, b in SEGS]
fix1 = float(np.average([diff("q=0.80", a=a, b=b)[0] for _, a, b in SEGS], weights=w1))
rnd1 = float(np.average([np.mean([diff(l, a=a, b=b)[0] for l in QS]) for _, a, b in SEGS], weights=w1))
orc1 = float(np.average([max(diff(l, a=a, b=b)[0] for l in QS) for _, a, b in SEGS], weights=w1))
print("\n===== 判据 2：换成一年一步（2018~2026，9 步，训练窗只往过去长）=====")
for (e, a, b), p, r, nd in zip(SEGS, pk1, rl1, d1):
    j = [diff(l, a=a, b=b)[0] for l in QS]
    print(f"  {a:%Y}（{nd} 天）选 {p} → 实得 {r:+.2%}　四刀全览 "
          + " ".join(f"{lab}:{v:+.2%}" for lab, v in zip(QS, j))
          + f"　固定0.80 {j[0]:+.2%}")
print(f"\n  一年一步链总账：只挑刀 {tot1:+.2%}　固定0.80 {fix1:+.2%}　随机 {rnd1:+.2%}　"
      f"oracle {orc1:+.2%}")
print(f"  选刀在这 9 步里各选了：{[p.split('=')[1] for p in pk1]}")
pd.DataFrame({"段": [f"{a:%Y}" for _, a, b in SEGS], "选中": pk1, "实差": rl1,
              "天数": d1}).to_csv(os.path.join(HERE, "tmp_buy_extra_oos_0926",
                                                "walkforward_1y.csv"), index=False)

# ---------- 附：如果刀口固定不动（不做任何选择），四刀各自的样本外链 ----------
print("\n===== 附：刀口从头到尾钉死，各自的 2018~2026 链（9 步一年一步）=====")
for lab in QS:
    seg = [diff(lab, a=a, b=b)[0] for _, a, b in SEGS]
    print(f"  钉死 {lab}：年化超额差 {float(np.average(seg, weights=w1)):+.2%}"
          f"　逐年 " + " ".join(f"{v:+.1%}" for v in seg))
