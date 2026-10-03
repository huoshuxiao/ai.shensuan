# -*- coding: utf-8 -*-
"""名单独立闸「下影深度」的第二笔账：换个档还成立吗、逐年是正是负。

接线时只量了 top50 一档、全窗一个数（+1.04%/年，样本内）。这一轮补两件事：
    1. 档位铺开：top20 / 50 / 100 / 200 四档 × 6 种形态，各出一行扣费账单；
    2. 年份铺开：每档都算逐年超额，并给 2015~2020 / 2021~2026 / 2024~2026 三段小计。

口径与生产名单腿**逐字相同**（同一个 `run_signal`、同一个调仓网格、open→open、
hold=5 日、双边 15bp、名单按板块配额走 `apply_board_quota`）。⚠️ 这条腿**不含**
日频那三道执行闸（无成交 / 贴涨停 / ST）⇒ 它量的是纯筛选层的相对高低，别读成实盘收益。

判据不恒真：两条锚点行（`参照·不剔除`@50 与 `独立叠加`@50）必须与已有产物逐位相同
—— 前者对权威归档 `ashare_portfolio_buylist.csv`，后者对 09-26 那次控制实验
`tmp_alpha158/five/five_buylist.csv`。任一条对不上就说明本驱动改到了口径 ⇒ 全部读数作废。
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "stock", "v1", "src")
OUT = os.path.join(HERE, "tmp_buy_extra_grid_0926")
os.makedirs(OUT, exist_ok=True)

sys.path.insert(0, SRC)
os.chdir(SRC)

import numpy as np                                          # noqa: E402
import pandas as pd                                         # noqa: E402
import run_ashare_portfolio_eval as P                       # noqa: E402

PORT_CSV = os.path.join(ROOT, "stock/v1/data/results/ashare_portfolio_buylist.csv")
FIVE_CSV = os.path.join(HERE, "tmp_alpha158", "five", "five_buylist.csv")

FIFTH = ("price_low0", "下影深度", "(-1.0*((low / close)))",
         "名单层独立闸那条构造（本脚本量它在各档的成色）")
RULES = list(P.VOLUME_RULES) + [FIFTH]
N4 = tuple(range(4))
N5 = tuple(range(5))
NAME = [nm for _k, nm, _e, _d in RULES]

# 注入：环2 那两个函数（exclusion_hits / exclusion_mask）读的是模块级常量，
# 整表换掉就能把第五条塞进同一套判定，不改产品代码一行
P.VOLUME_RULES = RULES
P.RULE_NAMES = NAME
P.RULE_EXPRS = [e for _k, _n, e, _d in RULES]
P._ALL_R = N5

VARIANTS = {
    "参照·不剔除": ("参照", N5, 9),
    "现≥3（并集4条，今天生产在跑）": ("并集", N4, 3),
    "独立叠加（现≥3 且 下影不命中）": None,      # 两个掩码取交，单独算
    "并进并集·5条≥3": ("并集", N5, 3),
    "并进并集·5条≥4（并集里最好的一档）": ("并集", N5, 4),
    "只用下影·单条不命中": ("单条", (4,), 1),
}
TOP_N = [20, 50, 100, 200]

t0 = time.time()
exprs = sorted(set(P.RULE_EXPRS) | {P.BUY_EXPR})
wide, bench_open = P.load_panel()
mtx = P.build_matrices(wide)
fms = P.factor_matrices(exprs, mtx)
live = mtx["ret_open"].index >= pd.Timestamp(P.ASHARE_PORT_START)
mtx = {k: v.loc[mtx["ret_open"].index[live]] for k, v in mtx.items()}
fms = {e: m.loc[m.index[live]] for e, m in fms.items()}
days = mtx["ret_open"].index
col_of = {c: i for i, c in enumerate(mtx["close"].columns)}
universe = ((mtx["listed_days"] >= P.ASHARE_PORT_MIN_LISTED)
            & (mtx["amount20"] >= P.ASHARE_PORT_MIN_AMOUNT))
bench = {"univ_ew": mtx["ret_open"].where(universe).mean(axis=1)}
if len(bench_open):
    bench["sh000300"] = bench_open.reindex(days).astype("float64") \
        .pct_change(fill_method=None)
print(f"[装载] 面板 {len(days)} 个交易日、{mtx['close'].shape[1]} 只　"
      f"求值 {len(exprs)} 条表达式　{time.time() - t0:.0f}s", flush=True)

pool, hit = P.exclusion_hits(mtx, days, fms, P.ASHARE_PORT_HOLD,
                             P.ASHARE_SCREEN_QUANTILE)
allows = {}
for lab, spec in VARIANTS.items():
    if spec is None:
        continue
    # exclusion_mask 内已经与 pool 取交（True 的列必然在当天过闸池里）
    allows[lab] = P.exclusion_mask((lab,) + spec, pool, hit)
allows["独立叠加（现≥3 且 下影不命中）"] = \
    allows["现≥3（并集4条，今天生产在跑）"] & allows["只用下影·单条不命中"]
for lab in VARIANTS:
    keep = int(allows[lab].sum(axis=1).mean())
    print(f"[掩码] {lab:<28} 平均留池 {keep} 只", flush=True)

rows, yr_frames = [], {}
for lab in VARIANTS:
    for n in TOP_N:
        port, st, _ = P.run_signal(fms[P.BUY_EXPR], col_of, days, mtx, n,
                                   hold=P.ASHARE_PORT_HOLD, universe=universe,
                                   quintiles=0, allow=allows[lab])
        row = {"variant": lab, "top_n": n, **st}
        for bn, bs in bench.items():
            row.update(P._excess(port, bs, bn))
        rows.append(row)
        yr_frames[(lab, n)] = P._yearly(port, bench["univ_ew"])
g = pd.DataFrame(rows)
g.to_csv(os.path.join(OUT, "grid.csv"), index=False)
yr = pd.concat({f"{lab}@{n}": f for (lab, n), f in yr_frames.items()}, names=["form"])
yr.to_csv(os.path.join(OUT, "grid_yearly.csv"))
pd.set_option("display.width", 240)
pd.set_option("display.unicode.east_asian_width", True)

COLS = ["variant", "top_n", "n_rebal", "ann_return", "one_way_turnover",
        "avg_amount_20d", "max_drawdown", "excess_univ_ew_ann", "excess_univ_ew_ir"]
print("\n===== 档位铺开（每档 6 形态，扣双边 "
      f"{P.ASHARE_PORT_COST_ONE_WAY:.4f}、名单={P.list_desc()}、涨停闸 "
      f"{P.gate_desc()}）=====")
for n in TOP_N:
    print(f"\n--- top_n = {n} ---")
    print(g[g.top_n == n][COLS].to_string(
        index=False, float_format=lambda v: f"{v:.4f}"))

# ---------- 逐年：只念两件事 —— 独立叠加相对现口径的差，正负各几年 ----------
base = g[g.variant == "现≥3（并集4条，今天生产在跑）"].set_index("top_n")
extra = g[g.variant == "独立叠加（现≥3 且 下影不命中）"].set_index("top_n")
print("\n===== 独立叠加 − 现口径：逐年超额差（正 = 接了更好）=====")
for n in TOP_N:
    d = yr_frames[("独立叠加（现≥3 且 下影不命中）", n)]["excess"] \
        - yr_frames[("现≥3（并集4条，今天生产在跑）", n)]["excess"]
    seg = lambda a, b: f"{d[(d.index >= a) & (d.index <= b)].sum():+.2%}"     # noqa: E731
    top = d.abs().idxmax()                       # 逐年累计差里最吃重的那一年
    ex_best = d.drop(top)
    print(f"top{n:>3}　全窗 {extra.loc[n, 'excess_univ_ew_ann'] - base.loc[n, 'excess_univ_ew_ann']:+.2%}"
          f"　毛收益差 {(g[(g.variant == '独立叠加（现≥3 且 下影不命中）') & (g.top_n == n)].ann_return_gross.iloc[0] - g[(g.variant == '现≥3（并集4条，今天生产在跑）') & (g.top_n == n)].ann_return_gross.iloc[0]):+.2%}"
          f"　为正 {int((d > 0).sum())}/{len(d)} 年"
          f"　2015~2020 {seg(2015, 2020)}　2021~2026 {seg(2021, 2026)}"
          f"　近3年 {seg(2024, 2026)}")
    print(f"        逐年 {d.index.min()}~{d.index.max()} 累计 {d.sum():+.2%}"
          f"；去掉最吃重的 {top}（{d[top]:+.1%}）后剩 {ex_best.sum():+.2%}"
          f"，年均 {ex_best.mean():+.2%}/年")
    print("        " + "  ".join(f"{y}:{v:+.1%}" for y, v in d.items()))

# ---------- 锚点判据（对不上就当场作废，不留到读表之后）----------
def _check(arch, key, cols, tag, g_key=None):
    """`g_key` = 本表里同一件事的标签；不同名时（形态换过主语）显式传，别靠改标签蒙混"""
    g_key = key if g_key is None else g_key
    a = arch[arch["variant"] == key]
    if not len(a):
        raise SystemExit(f"[锚点] 归档里找不到形态「{key}」⇒ 对不了表，{tag} 作废")
    sel = g[(g.variant == g_key) & (g.top_n == 50)]
    if not len(sel):
        raise SystemExit(f"[锚点] 本表里没有形态「{g_key}」@50 ⇒ 对不了表，{tag} 作废")
    a = a.iloc[0]
    worst = 0.0
    for c in cols:
        if c in g.columns and c in a.index:
            worst = max(worst, abs(float(sel[c].iloc[0]) - float(a[c])))
    print(f"[锚点] {tag}「{g_key}」@50 与归档最大差 {worst:.3e}")
    if worst > 1e-9:
        raise SystemExit(f"[锚点] {tag} 对不上（差 {worst:.3e}）⇒ 本驱动改到了口径，全部读数作废")

ANCH = ["ann_return", "one_way_turnover", "max_drawdown", "excess_univ_ew_ann"]
arch = pd.read_csv(PORT_CSV)
# 归档那行叫「并集(命中>=3)」（生产表自带形态），本表换了个说得清的主语，其余两行同名
arch["variant"] = arch["variant"].replace(
    {"并集(命中>=3)": "现≥3（并集4条，今天生产在跑）"})
_check(arch, "参照·不剔除", ANCH, "多头参照")
_check(arch, "现≥3（并集4条，今天生产在跑）", ANCH, "生产名单口径")
five = pd.read_csv(FIVE_CSV)
FIVE_KEY = "叠加·现≥3 且 LOW0不命中"       # 09-26 控制实验里同一件事的旧标签
_check(five, FIVE_KEY, ANCH, "09-26 控制实验的叠加形态",
       g_key="独立叠加（现≥3 且 下影不命中）")
five_row = five[five.variant == FIVE_KEY].iloc[0]
now_row = g[(g.variant == "独立叠加（现≥3 且 下影不命中）") & (g.top_n == 50)].iloc[0]
print(f"[判据] 独立叠加@50 超额 = {now_row.excess_univ_ew_ann:+.2%}/年、"
      f"单程换手 {now_row.one_way_turnover:.3f}"
      f"（控制实验那份 {five_row.excess_univ_ew_ann:+.2%}/年、"
      f"{five_row.one_way_turnover:.3f}）")
print(f"\n[产物] {OUT}/grid.csv、grid_yearly.csv　墙钟 {time.time() - t0:.0f}s")
