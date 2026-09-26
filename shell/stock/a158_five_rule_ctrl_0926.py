# -*- coding: utf-8 -*-
"""接线前的控制实验：把 `LOW0 取负` 提成**第五条剔除构造**后，生产的两个强度还成立吗。

为什么还要再跑一遍（选项L 那一轮不够）：真实生产在**两处**踩不同强度 ——
    域（可投池）  = 各构造池内分位 ≥0.8 取并集，命中 ≥1 条即剔
    待买入名单    = 同一张并集，但要求命中 ≥ ASHARE_BUY_MIN_HITS（现 3）条才剔
选项L 量的是「单条候选」与「九条并集」，那是**替换**与**放大**两种形状；把第五条塞进
并集后，「≥3」的含义从「四条中中 3 条」变成「五条中中 3 条」，判据本身被改了。
本脚本就是把这一格补上：同一个 LOW0 取负，11 种形态一次跑齐，两腿各出账。

形态里 `现域口径` / `现名单口径` 两行就是**今天生产在跑的**，必须与权威归档
`ashare_portfolio_exclusion.csv` / `ashare_portfolio_buylist.csv` 逐位相同，
否则本表其余行读不出增量（判据不恒真：锚点与候选踩同一套闸门、同一调仓网格）。
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "stock", "v1", "src")
OUT = os.path.join(HERE, "tmp_alpha158", "five")
os.makedirs(OUT, exist_ok=True)
os.environ["STOCK_PORT_OUT"] = os.path.join(OUT, "five_eval.csv")
os.environ["STOCK_EXCL_OUT"] = os.path.join(OUT, "five_exclusion.csv")
os.environ["STOCK_BUYLIST_OUT"] = os.path.join(OUT, "five_buylist.csv")

sys.path.insert(0, SRC)
os.chdir(SRC)

import pandas as pd                                        # noqa: E402
import run_ashare_portfolio_eval as P                      # noqa: E402

FIFTH = ("price_low0", "LOW0取负", "(-1.0*((low / close)))",
         "价格族第五条剔除构造（09-26 选项L/K 实测后提名，本脚本正在量它）")
RULES = list(P.VOLUME_RULES) + [FIFTH]
N4 = tuple(range(4))                       # 生产四条下标
N5 = tuple(range(5))                       # 生产四条 + 第五条
NAME = [nm for _k, nm, _e, _d in RULES]

VARIANTS = (
    [("参照·不剔除", "参照", N5, 9)]
    + [("并集4条≥1（现域口径）", "并集", N4, 1), ("并集4条≥3（现名单口径）", "并集", N4, 3)]
    + [(f"并集5条>={k}", "并集", N5, k) for k in (1, 2, 3, 4, 5)]
    + [(f"留一5条·去掉{NAME[r]}", "留一", tuple(x for x in N5 if x != r), 1)
       for r in (0, 4)]
    + [("单条·LOW0取负", "单条", (4,), 1), ("单条·量能水平", "单条", (0,), 1)]
)
BUY = [v[0] for v in VARIANTS if v[0] in (
    "参照·不剔除", "并集4条≥1（现域口径）", "并集4条≥3（现名单口径）",
    "并集5条>=1", "并集5条>=2", "并集5条>=3", "并集5条>=4", "并集5条>=5",
    "留一5条·去掉量能水平", "单条·LOW0取负")]

P.VOLUME_RULES = RULES
P.RULE_NAMES = NAME
P.RULE_EXPRS = [e for _k, _n, e, _d in RULES]
P._ALL_R = N5
P.EXCL_VARIANTS = VARIANTS
print(f"[注入] 剔除构造 4 → 5 条　形态 {len(VARIANTS)} 种、名单腿 {len(BUY)} 行"
      f"　落点 {OUT}", flush=True)

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
    bench["sh000300"] = bench_open.reindex(days).astype("float64").pct_change(fill_method=None)

pool_df, hit_df = P.exclusion_hits(mtx, days, fms, P.ASHARE_PORT_HOLD,
                                   P.ASHARE_SCREEN_QUANTILE)
ex_rows, allows = P.run_exclusion(mtx, days, P.ASHARE_PORT_HOLD, pool_df, hit_df)
ex = pd.DataFrame(ex_rows)
ex.insert(0, "min_hits_prod", P.ASHARE_BUY_MIN_HITS)
ex.to_csv(P.ASHARE_EXCL_OUT, index=False)
pd.set_option("display.width", 230)
pd.set_option("display.unicode.east_asian_width", True)
print("\n===== 第五条剔除构造：剩余池增益（毛，同网格）=====")
print(ex[["variant", "min_hits", "avg_kept", "keep_ratio", "gain_ann", "gain_ir",
          "gain_2015_2020", "gain_2021_2026"]]
      .to_string(index=False, float_format=lambda v: f"{v:.4f}"))

P.EXCL_VARIANTS = [v for v in VARIANTS if v[0] in BUY]
bl = pd.DataFrame(P.buylist_rows(fms, col_of, days, mtx, universe, bench,
                                 allows, P.ASHARE_BUY_TOP_N))
bl.insert(0, "buy_min_hits", P.ASHARE_BUY_MIN_HITS)
bl.to_csv(P.ASHARE_BUYLIST_OUT, index=False)
print(f"\n===== 名单腿（前 {P.ASHARE_BUY_TOP_N} 只，扣双边 "
      f"{P.ASHARE_PORT_COST_ONE_WAY:.4f}）=====")
print(bl[["variant", "n_rebal", "ann_return", "ann_return_gross", "one_way_turnover",
          "avg_amount_20d", "max_drawdown", "excess_univ_ew_ann", "excess_univ_ew_ir"]]
      .to_string(index=False, float_format=lambda v: f"{v:.4f}"))

# ---------- 混合形态：现闸保留 + LOW0 另加一道 ----------
# allows 里 True = 留池。两个 allows 取交 = 「任一判剔就剔」，正是「不撤现闸、再叠一条」
# 这个真实接法；它**不在** (参与构造, 最小命中数) 那个网格里，所以单独算一行。
hyb = allows["并集4条≥3（现名单口径）"] & allows["单条·LOW0取负"]
port, st, _ = P.run_signal(fms[P.BUY_EXPR], col_of, days, mtx, P.ASHARE_BUY_TOP_N,
                           hold=P.ASHARE_PORT_HOLD, universe=universe, quintiles=0,
                           allow=hyb)
hrow = {"variant": "叠加·现≥3 且 LOW0不命中", "buy_min_hits": P.ASHARE_BUY_MIN_HITS,
        "n_rebal": int(st.get("n_rebal", 0)) or 570, **st}
for bn, bs in bench.items():
    hrow.update(P._excess(port, bs, bn))
print("\n===== 叠加形态（现名单闸不动 + LOW0 另加一道，扣费同口径）=====")
pd.set_option("display.width", 230)
print(pd.DataFrame([hrow])[["variant", "ann_return", "ann_return_gross",
                            "one_way_turnover", "max_drawdown", "avg_amount_20d",
                            "excess_univ_ew_ann", "excess_univ_ew_ir"]]
      .to_string(index=False, float_format=lambda v: f"{v:.4f}"))
pd.concat([bl, pd.DataFrame([hrow])]).to_csv(P.ASHARE_BUYLIST_OUT, index=False)
print(f"\n[本驱动] 墙钟 {time.time() - t0:.0f}s", flush=True)
