# -*- coding: utf-8 -*-
"""选项L：把 5 条 Alpha158 的 high/low 构造提成**第五条剔除构造**，量减法腿。

为什么单独量这一腿（与多头腿那张表是两件事）：生产用法是「池内分位 ≥0.8 踢掉最响
的 20%，四条取并集」，剔除是**减法**、不产生新买入、费率近零，所以一条构造在多头腿上
亏钱不代表它在剔除腿上没用。选项K 之前 `factor_matrices` 拿 close 顶替 high/low，
凡是吃 high/low 的表达式在环 2 里根本不是它自己 ⇒ 这一腿以前**量不了**，现在才量得动。

方向约定（每条候选都先整理成「高分端 = 该踢」）：
    IC < 0  ⇒ 高分端本来就是差票 ⇒ 直接用原式（KLEN、KLOW）
    IC > 0  ⇒ 低分端才是差票 ⇒ 用 (-1.0*(原式))，于是它的高分端 = 差票（LOW0、MIN10、MIN5）
这与 `volume_exclusion` 的判据（pct >= 0.8 剔高分）严格同构，不另立方向。

形态网格 = 归档那 13 行（生产四条，原样重算，当**锚点自检**）+ 候选 14 行：
    参照·不剔除 / 单条×4 / 并集(命中>=1..4) / 留一×4           ← 必须与归档逐位相同
    单条·<候选>×5 / 并集9条(命中>=1..5) / 留一×4（含候选）      ← 本次要答的问题
锚点不对，候选那 14 行一律作废。

不动产品代码：VOLUME_RULES / RULE_EXPRS / RULE_NAMES / EXCL_VARIANTS 全在本驱动里
临时替换（模块级常量注入），三份产物覆写到自己目录下，权威归档一个字不碰。
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "stock", "v1", "src")
OUT = os.path.join(HERE, "tmp_alpha158", "sub")
os.makedirs(OUT, exist_ok=True)
os.environ["STOCK_PORT_OUT"] = os.path.join(OUT, "sub_portfolio_eval.csv")
os.environ["STOCK_EXCL_OUT"] = os.path.join(OUT, "sub_exclusion.csv")
os.environ["STOCK_BUYLIST_OUT"] = os.path.join(OUT, "sub_buylist.csv")

sys.path.insert(0, SRC)
os.chdir(SRC)

import numpy as np                                        # noqa: E402
import pandas as pd                                       # noqa: E402
import run_ashare_portfolio_eval as P                     # noqa: E402

# (显示名, 求值表达式[已整理成高分=该踢], 环1 rank IC)
CANDS = [
    ("A158·KLEN振幅", "((high - low) / open)", -0.0658),
    ("A158·KLOW下影", "((np.minimum(open, close) - low) / open)", -0.0426),
    ("A158·LOW0取负", "(-1.0*((low / close)))", 0.0560),
    ("A158·MIN10取负", "(-1.0*(ts_min(low, 10.0) / close))", 0.0547),
    ("A158·MIN5取负", "(-1.0*(ts_min(low, 5.0) / close))", 0.0544),
]

BASE = list(P.VOLUME_RULES)                    # 生产四条，顺序不能动（level 是闸门因子）
RULES = BASE + [(f"a158_{i}", nm, e, f"选项L 候选，环1 rank IC {ic:+.4f}")
                for i, (nm, e, ic) in enumerate(CANDS)]
N4 = tuple(range(len(BASE)))                   # 生产四条下标 0..3
NALL = tuple(range(len(RULES)))                # 九条下标 0..8
NAME = [nm for _k, nm, _e, _d in RULES]

VARIANTS = (
    # ---- 锚点：归档那 13 行，逐字同构 ----
    [("参照·不剔除", "参照", N4, 9)]
    + [(f"单条·{NAME[r]}", "单条", (r,), 1) for r in N4]
    + [(f"并集(命中>={k})", "并集", N4, k) for k in (1, 2, 3, 4)]
    + [(f"留一·去掉{NAME[r]}", "留一", tuple(x for x in N4 if x != r), 1) for r in N4]
    # ---- 候选单独成一条 ----
    + [(f"单条·{NAME[r]}", "单条", (r,), 1) for r in range(len(BASE), len(RULES))]
    # ---- 九条并集：候选进并集后要不要收紧强度 ----
    + [(f"并集9条(命中>={k})", "并集", NALL, k) for k in (1, 2, 3, 4, 5)]
    # ---- 留一（含候选）：候选进来了，生产四条还各自承重吗 ----
    + [(f"留一含候选·去掉{NAME[r]}", "留一", tuple(x for x in NALL if x != r), 1)
       for r in N4]
)
# 名单腿（每行一次完整扣费回放，~9s/行）只跑能答事的 9 行
BUY_VARIANTS = [v for v in VARIANTS
                if v[0] in ("参照·不剔除", "并集(命中>=1)",
                            "并集9条(命中>=1)", "并集9条(命中>=3)")
                or v[1] == "单条" and v[2][0] >= len(BASE)]

P.VOLUME_RULES = RULES
P.RULE_NAMES = NAME
P.RULE_EXPRS = [e for _k, _n, e, _d in RULES]
P._ALL_R = NALL
P.EXCL_VARIANTS = VARIANTS

print(f"[注入] 剔除构造 {len(BASE)} 条 → {len(RULES)} 条（+候选 {len(CANDS)}）"
      f"　形态 {len(VARIANTS)} 种（锚点 13 + 新增 {len(VARIANTS) - 13}）"
      f"　名单腿 {len(BUY_VARIANTS)} 行　落点 {OUT}", flush=True)

t0 = time.time()
exprs = sorted(set(P.RULE_EXPRS) | {P.BUY_EXPR})
wide, bench_open = P.load_panel()
mtx = P.build_matrices(wide)
fms = P.factor_matrices(exprs, mtx)
print(f"[因子] 求值 {len(exprs)} 条完成 {time.time() - t0:.0f}s", flush=True)

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

t1 = time.time()
pool_df, hit_df = P.exclusion_hits(mtx, days, fms, P.ASHARE_PORT_HOLD,
                                   P.ASHARE_SCREEN_QUANTILE)
ex_rows, allows = P.run_exclusion(mtx, days, P.ASHARE_PORT_HOLD, pool_df, hit_df)
print(f"[剔除] {len(VARIANTS)} 形态 × {len(pool_df)} 调仓日 {time.time() - t1:.0f}s",
      flush=True)
ex = pd.DataFrame(ex_rows)
ex.insert(0, "gate", P.ASHARE_TRADABLE_GATE)
ex.insert(1, "n_rules", [len(v[2]) for v in VARIANTS])
pd.set_option("display.width", 240)
pd.set_option("display.unicode.east_asian_width", True)
print("\n===== 选项L 减法腿：剩余池等权 − 不剔除等权（毛收益、同一调仓网格）=====")
print(ex[["variant", "n_rules", "min_hits", "avg_candidates", "avg_kept", "keep_ratio",
          "gain_ann", "gain_ir", "gain_2015_2020", "gain_2021_2026"]]
      .to_string(index=False, float_format=lambda v: f"{v:.4f}"))
ex.to_csv(P.ASHARE_EXCL_OUT, index=False)

P.EXCL_VARIANTS = BUY_VARIANTS
t2 = time.time()
bl = pd.DataFrame(P.buylist_rows(fms, col_of, days, mtx, universe, bench,
                                 allows, P.ASHARE_BUY_TOP_N))
bl.insert(0, "gate", P.ASHARE_TRADABLE_GATE)
bl.insert(1, "list_scheme", P.ASHARE_LIST_SCHEME)
print(f"\n===== 选项L 名单腿（前 {P.ASHARE_BUY_TOP_N} 只，扣双边 "
      f"{P.ASHARE_PORT_COST_ONE_WAY:.4f}，{time.time() - t2:.0f}s）=====")
print(bl[["variant", "top_n", "n_rebal", "ann_return", "ann_return_gross",
          "one_way_turnover", "avg_amount_20d", "excess_univ_ew_ann",
          "excess_univ_ew_ir", "excess_sh000300_ann"]]
      .to_string(index=False, float_format=lambda v: f"{v:.4f}"))
bl.to_csv(P.ASHARE_BUYLIST_OUT, index=False)
print(f"\n[本驱动] 墙钟 {time.time() - t0:.0f}s　产物 {OUT}", flush=True)
