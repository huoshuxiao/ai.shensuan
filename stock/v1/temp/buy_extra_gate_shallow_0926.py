# -*- coding: utf-8 -*-
"""选项C 的量测：把名单独立闸「下影深度」那一刀**收浅**，四档读数还翻不翻得过来。

㊱ 那笔账的结论是「切 20% 太狠」：毛增益四档都是正的，但单程换手一律抬 +0.07~0.11，
多付的手续费把毛增益吃光（除 top50）。本脚本就是去量那个直觉——只挡下影**最深的那一批**，
切浅一点会不会「税少付得比毛增益多」这条规律就翻过来：

    quantile q = 0.80  挡池内最高的 20%   ← 现在生产在踩的（㊱ 已量，本脚本拿来当锚点）
    q = 0.85 / 0.90 / 0.95  只挡 15% / 10% / 5%

形态只有两个，逐档各出一行扣费账单：
    现≥3      = 并集 4 条命中 ≥3 才挡（今天生产的名单口径，本脚本自己复算一遍当基线）
    叠加@q    = 现≥3 放行之后，再挡「下影深度」池内分位 ≥ q 的那些

⚠️ 与 ㊱ 同一立场：这量的是**纯筛选层**（同一个 `run_signal`、同一调仓网格、open→open、
hold=5、双边 15bp、`quota` 名单、`board` 涨停闸），**不含**日频那三道执行闸。

判据不恒真，两道锚：
    A. 本脚本手写的分位公式在 q=0.8 必须与 `P.exclusion_hits` 吐出的命中矩阵**逐位相同**
       （不同就说明我另立了判据 ⇒ 全部读数作废）；
    B. q=0.8 那 4 行 + 现≥3 那 4 行必须与 ㊱ 的产物 `tmp_buy_extra_grid_0926/grid.csv`
       逐位相同（不同 = 面板在这 20 分钟里变了，本表与 ㊱ 不可并读）。

本脚本**不改任何产品配置**：只往自己的目录写文件，仓库里的归档一张都不碰。
（若将来真要把 q 当第二个旋钮接进生产，得另立 `ASHARE_BUY_EXTRA_QUANTILE` ——
`buy_extra_block` 现在与那四条**共用** `ASHARE_SCREEN_QUANTILE`，拨它会连「不该买」那张域
一起收紧，不是这一刀的事。）
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "stock", "v1", "src")
OUT = os.path.join(HERE, "tmp_buy_extra_shallow_0926")
os.makedirs(OUT, exist_ok=True)
GRID_CSV = os.path.join(HERE, "tmp_buy_extra_grid_0926", "grid.csv")

sys.path.insert(0, SRC)
os.chdir(SRC)

import pandas as pd                                         # noqa: E402
import run_ashare_portfolio_eval as P                       # noqa: E402

Q_PRODUCTION = P.ASHARE_SCREEN_QUANTILE          # 0.8，㉟ 接线时踩的那一刀
QS = [Q_PRODUCTION, 0.85, 0.90, 0.95]
TOP_N = [20, 50, 100, 200]
BASE = "现≥3（并集4条，今天生产在跑）"
LAB = lambda q: f"叠加@q={q:.2f}"                # noqa: E731

FIFTH = ("price_low0", "下影深度", "(-1.0*((low / close)))",
         "名单层独立闸那条构造（本脚本量它切浅之后的成色）")
RULES = list(P.VOLUME_RULES) + [FIFTH]
N4 = tuple(range(4))
NAME = [nm for _k, nm, _e, _d in RULES]

# 注入：环2 那两个函数读的是模块级常量，整表换掉就能把第五条送进同一套判定
P.VOLUME_RULES = RULES
P.RULE_NAMES = NAME
P.RULE_EXPRS = [e for _k, _n, e, _d in RULES]
P._ALL_R = tuple(range(5))

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
      f"{time.time() - t0:.0f}s", flush=True)

# 过闸池 + 五条构造在**生产那一刀**(0.8) 上的命中矩阵
pool, hit = P.exclusion_hits(mtx, days, fms, P.ASHARE_PORT_HOLD, Q_PRODUCTION)


def hit_at(q):
    """把「下影深度」在**同一批过闸池**上按分位 q 重判一次命中

    与 `exclusion_hits` 逐字同式：pct = rank_{j∈pool}(F_{j,s})/|pool|，
    缺值不占名额也不挡（`pct.notna()` 那道），唯一区别是这里的 q 由本脚本给。
    锚点 A 就是拿 q=0.8 回来对 `hit` —— 对不上即本函数与生产判据不同式。
    """
    f = fms[FIFTH[2]]
    rows = [f.loc[s].reindex(pool.columns).where(pool.loc[s]).rank(pct=True)
            for s in pool.index]
    pct = pd.DataFrame(rows, index=pool.index, columns=pool.columns)
    return pct.notna() & (pct >= q)


h08 = hit_at(Q_PRODUCTION)
if "下影深度" not in hit:
    raise SystemExit(f"[锚点A] exclusion_hits 没吐「下影深度」这条命中矩阵（只有 "
                     f"{sorted(hit)}）⇒ 对不了表")
same = h08.equals(hit["下影深度"])
diff08 = int((h08 != hit["下影深度"]).to_numpy().sum())
print(f"[锚点A] 手写分位公式 @0.8 与 exclusion_hits 的命中矩阵：逐位相同 = {same}"
      f"、不一致格数 {diff08}（必须为 True / 0）", flush=True)
if not same or diff08:
    raise SystemExit("[锚点A] 本脚本另立了判据 ⇒ 全部读数作废")

HIT_Q = {q: (h08 if q == Q_PRODUCTION else hit_at(q)) for q in QS}
allows = {BASE: P.exclusion_mask((BASE, "并集", N4, 3), pool, hit)}
for q in QS:
    allows[LAB(q)] = allows[BASE] & ~HIT_Q[q]
print(f"[掩码] 平均留池：现≥3 {int(allows[BASE].sum(axis=1).mean())} 只　"
      + "　".join(f"{LAB(q)} {int(allows[LAB(q)].sum(axis=1).mean())}" for q in QS),
      flush=True)
n_pool = int(pool.sum(axis=1).mean())
for q in QS:
    h = HIT_Q[q] & pool
    n_cut = int(h.sum(axis=1).mean())
    n_net = int((HIT_Q[q] & allows[BASE] & pool).sum(axis=1).mean())
    print(f"[刀口] q={q:.2f} 当日池内挡 {n_cut} 只（占池 {n_cut / n_pool:.1%}）、"
          f"其中现≥3 已放行的 {n_net} 只 = 这道闸真正新增的工作量", flush=True)

rows, yr = [], {}
for lab in [BASE] + [LAB(q) for q in QS]:
    for n in TOP_N:
        port, st, _ = P.run_signal(fms[P.BUY_EXPR], col_of, days, mtx, n,
                                   hold=P.ASHARE_PORT_HOLD, universe=universe,
                                   quintiles=0, allow=allows[lab])
        row = {"variant": lab, "top_n": n, **st}
        for bn, bs in bench.items():
            row.update(P._excess(port, bs, bn))
        rows.append(row)
        yr[(lab, n)] = P._yearly(port, bench["univ_ew"])
g = pd.DataFrame(rows)
g.to_csv(os.path.join(OUT, "shallow.csv"), index=False)
pd.concat({f"{k[0]}@{k[1]}": v for k, v in yr.items()}, names=["form"]) \
    .to_csv(os.path.join(OUT, "shallow_yearly.csv"))

# ---------- 锚点 B：q=0.8 那批必须复现 ㊱ 的产物 ----------
grid = pd.read_csv(GRID_CSV)
grid["variant"] = grid["variant"].replace(
    {"独立叠加（现≥3 且 下影不命中）": LAB(Q_PRODUCTION)})
ANCH = ["ann_return", "ann_return_gross", "one_way_turnover", "max_drawdown",
        "excess_univ_ew_ann", "excess_univ_ew_ir"]
worst_all = 0.0
for lab in [BASE] + [LAB(q) for q in QS if q == Q_PRODUCTION]:
    for n in TOP_N:
        a = grid[(grid.variant == lab) & (grid.top_n == n)]
        b = g[(g.variant == lab) & (g.top_n == n)]
        if not (len(a) and len(b)):
            raise SystemExit(f"[锚点B] {lab}@{n} 在 ㊱ 那份里找不到 ⇒ 两表不可并读")
        d = max(abs(float(b[c].iloc[0]) - float(a[c].iloc[0])) for c in ANCH)
        worst_all = max(worst_all, d)
        print(f"[锚点B] {lab}@{n:<3} 与 ㊱ grid.csv 最大差 {d:.3e}", flush=True)
if worst_all > 1e-9:
    raise SystemExit(f"[锚点B] 最大差 {worst_all:.3e} ⇒ 面板或口径变了，本表读数作废")

# ---------- 读数台 ----------
pd.set_option("display.width", 250)
pd.set_option("display.unicode.east_asian_width", True)
COLS = ["variant", "top_n", "ann_return", "ann_return_gross", "one_way_turnover",
        "max_drawdown", "avg_amount_20d", "excess_univ_ew_ann", "excess_univ_ew_ir"]
print(f"\n===== 切浅四刀 × 四档（扣双边 {P.ASHARE_PORT_COST_ONE_WAY:.4f}、"
      f"名单={P.list_desc()}、涨停闸 {P.gate_desc()}）=====")
for n in TOP_N:
    print(f"\n--- top_n = {n} ---")
    print(g[g.top_n == n][COLS].to_string(index=False,
                                          float_format=lambda v: f"{v:.4f}"))

print("\n===== 相对现口径的增量（超额差 = 该刀 − 现≥3；费耗 = 毛 − 净 = 手续费吃掉多少）=====")
b = g[g.variant == BASE].set_index("top_n")
hdr = f"{'刀口':<12}{'档':>5}{'超额差':>9}{'毛差':>9}{'换手':>16}{'费耗 现→该刀':>18}{'逐年为正':>10}"
print(hdr)
for q in QS:
    for n in TOP_N:
        r = g[(g.variant == LAB(q)) & (g.top_n == n)].iloc[0]
        bb = b.loc[n]
        d = yr[(LAB(q), n)]["excess"] - yr[(BASE, n)]["excess"]
        top = d.abs().idxmax()
        print(f"{LAB(q):<12}{n:>5}{r.excess_univ_ew_ann - bb.excess_univ_ew_ann:>+9.2%}"
              f"{r.ann_return_gross - bb.ann_return_gross:>+9.2%}"
              f"{bb.one_way_turnover:>7.3f}→{r.one_way_turnover:<7.3f}"
              f"{bb.ann_return_gross - bb.ann_return:>7.2%}→"
              f"{r.ann_return_gross - r.ann_return:<7.2%}"
              f"{int((d > 0).sum()):>7}/{len(d)}"
              f"  去掉{top}后累计{d.drop(top).sum():+.2%}")

print("\n===== 一屏结论：每一刀在四档里翻正了几档 =====")
for q in QS:
    pos = [n for n in TOP_N
           if g[(g.variant == LAB(q)) & (g.top_n == n)].excess_univ_ew_ann.iloc[0]
           > b.loc[n].excess_univ_ew_ann]
    print(f"q={q:.2f}　超额比现口径高的档位：{pos if pos else '无'}"
          f"　（12 年逐年为正的档数："
          + "，".join(f"top{n}:{int(((yr[(LAB(q), n)]['excess'] - yr[(BASE, n)]['excess']) > 0).sum())}/12"
                      for n in TOP_N) + "）")
print(f"\n[产物] {OUT}/shallow.csv、shallow_yearly.csv　墙钟 {time.time() - t0:.0f}s")
