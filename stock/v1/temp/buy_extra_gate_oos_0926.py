# -*- coding: utf-8 -*-
"""样本外验证：名单独立闸「下影深度」这一刀，**只用过去挑、拿没见过的年份计分**还剩多少。

㊱㊲ 那两张表全是**样本内**（2852 个交易日 ≈ 2015-01~2026-09、570 个调仓段，一次性看完全窗
再挑最好的一格）。选项C 扫出来的四刀 × 四档 = 16 个格子，「+1.68pp@top50,q=0.85」这种数
天生就是被挑出来的，所以真正要回答的是另一句话：

    一个只活在盘前的人，在 t 年只用 t 之前的数据挑刀，t 年之后真能多赚吗？

本脚本就做这个：**链式 walk-forward**（训练窗只往过去长，测试窗一段一段往后走），
每一步的「选哪一刀」只允许看切点之前，「得几分」只允许看切点之后。

三层对照（缺任何一层都读不出选择性）：
    选刀规则   = 训练窗年化超额差最大的那一格
    固定刀口   = 生产现在踩的 q=0.80（不做任何选择）
    oracle     = 拿**全样本**挑出的最好那一格（= ㊱㊲ 里那个被批评为 overclaim 的读数的上限）
    随机       = 四刀在该档的平均（这台秤「有没有牙」的底线：选刀规则连随机都不如就等于没有）

另外把 16 格按**前半 / 后半**各算一遍年化超额差，看同号率 —— 前后都正的格子才是形状，
只在一半里蹦出来的基本都是噪音。

口径与 ㊱㊲ 逐字相同（同一个 `run_signal`、同一调仓网格、open→open、hold=5、双边 15bp、
`quota` 名单、`board` 涨停闸）⇒ 纯筛选层，不含日频三道执行闸。

判据（都不恒真，对不上就当场 SystemExit 作废）：
    C. 每个 cell 的**全窗**年化（净/毛/换手/超额）必须与 ㊲ 的 `shallow.csv` 逐位相同
       ⇒ 钉住「切窗口这套统计没碰到收益序列本身」；
    D. 任一切点：训练天数 + 测试天数 == 全窗天数，且各测试段两两不相交
       ⇒ 钉住「没有同一段日被算两次」（链式计分最容易犯的错）。
本脚本不改任何产品配置、不写仓库归档，产物只进自己的目录。
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "stock", "v1", "src")
OUT = os.path.join(HERE, "tmp_buy_extra_oos_0926")
os.makedirs(OUT, exist_ok=True)
SHALLOW_CSV = os.path.join(HERE, "tmp_buy_extra_shallow_0926", "shallow.csv")

sys.path.insert(0, SRC)
os.chdir(SRC)

import numpy as np                                          # noqa: E402
import pandas as pd                                         # noqa: E402
import run_ashare_portfolio_eval as P                       # noqa: E402

Q_PRODUCTION = P.ASHARE_SCREEN_QUANTILE
QS = [Q_PRODUCTION, 0.85, 0.90, 0.95]
TOP_N = [20, 50, 100, 200]
PROD_N = P.ASHARE_BUY_TOP_N                  # 50，生产档位（政策定的，不是这轮挑的）
BASE = "现≥3"
LAB = lambda q: f"q={q:.2f}"                 # noqa: E731

FIFTH = ("price_low0", "下影深度", "(-1.0*((low / close)))", "名单层独立闸那条构造")
RULES = list(P.VOLUME_RULES) + [FIFTH]
N4 = tuple(range(4))
P.VOLUME_RULES = RULES
P.RULE_NAMES = [nm for _k, nm, _e, _d in RULES]
P.RULE_EXPRS = [e for _k, _n, e, _d in RULES]
P._ALL_R = tuple(range(5))

t0 = time.time()
wide, bench_open = P.load_panel()
mtx = P.build_matrices(wide)
fms = P.factor_matrices(sorted(set(P.RULE_EXPRS) | {P.BUY_EXPR}), mtx)
live = mtx["ret_open"].index >= pd.Timestamp(P.ASHARE_PORT_START)
mtx = {k: v.loc[mtx["ret_open"].index[live]] for k, v in mtx.items()}
fms = {e: m.loc[m.index[live]] for e, m in fms.items()}
days = mtx["ret_open"].index
col_of = {c: i for i, c in enumerate(mtx["close"].columns)}
universe = ((mtx["listed_days"] >= P.ASHARE_PORT_MIN_LISTED)
            & (mtx["amount20"] >= P.ASHARE_PORT_MIN_AMOUNT))
EW = mtx["ret_open"].where(universe).mean(axis=1)            # 可投域等权 = 计分基准
print(f"[装载] 全窗 {len(days)} 个交易日 {days.min():%Y-%m-%d}~{days.max():%Y-%m-%d}　"
      f"{time.time() - t0:.0f}s", flush=True)

pool, hit = P.exclusion_hits(mtx, days, fms, P.ASHARE_PORT_HOLD, Q_PRODUCTION)


def hit_at(q):
    """「下影深度」在同一批过闸池上按分位 q 重判命中（与 exclusion_hits 逐字同式）"""
    f = fms[FIFTH[2]]
    pct = pd.DataFrame([f.loc[s].reindex(pool.columns).where(pool.loc[s]).rank(pct=True)
                        for s in pool.index], index=pool.index, columns=pool.columns)
    return pct.notna() & (pct >= q)


HIT_Q = {q: hit_at(q) for q in QS}      # 0.8 也重算一遍：锚点 A 才不是拿自己比自己
if not HIT_Q[Q_PRODUCTION].equals(hit["下影深度"]):
    raise SystemExit("[锚点A] 分位公式与 exclusion_hits 不同式 ⇒ 全部读数作废")
print(f"[锚点A] 手写分位公式 @0.8 与 exclusion_hits 逐位相同 = True、不一致格数 "
      f"{int((HIT_Q[Q_PRODUCTION] != hit['下影深度']).to_numpy().sum())}", flush=True)
base_allow = P.exclusion_mask((BASE, "并集", N4, 3), pool, hit)
allows = {BASE: base_allow}
allows.update({LAB(q): base_allow & ~HIT_Q[q] for q in QS})

# ---------- 20 个 cell 的日频净值序列（一次算完，后面所有切窗都只是切片）----------
port, stat = {}, {}
for lab in [BASE] + [LAB(q) for q in QS]:
    for n in TOP_N:
        r, st, _ = P.run_signal(fms[P.BUY_EXPR], col_of, days, mtx, n,
                                hold=P.ASHARE_PORT_HOLD, universe=universe,
                                quintiles=0, allow=allows[lab])
        port[(lab, n)] = r
        stat[(lab, n)] = st
print(f"[回放] {len(port)} 个 cell 算完　{time.time() - t0:.0f}s", flush=True)


def ex_series(cell):
    """日频超额（该 cell − 可投域等权），inner join + dropna，与 `_excess` 同一立场"""
    j = pd.concat([port[cell], EW], axis=1, join="inner").dropna()
    return j.iloc[:, 0] - j.iloc[:, 1]


EX = {c: ex_series(c) for c in port}
long = pd.concat([pd.DataFrame({"date": v.index, "cell": f"{k[0]}@{k[1]}",
                                "excess": v.to_numpy(),
                                "ret": port[k].reindex(v.index).to_numpy()})
                  for k, v in EX.items()], ignore_index=True)
long.to_csv(os.path.join(OUT, "daily_excess.csv.gz"), index=False, compression="gzip")


def ann(s):
    return float(s.mean() * P.TRADING_DAYS) if len(s) else np.nan


def diff_ann(cell, n, a=None, b=None):
    """该 cell 相对 `现≥3` 同档的年化超额差（只在两列都有值的日子比）"""
    j = pd.concat([EX[cell], EX[(BASE, n)]], axis=1, join="inner").dropna()
    d = j.iloc[:, 0] - j.iloc[:, 1]
    if a is not None:
        d = d.loc[d.index >= a]
    if b is not None:
        d = d.loc[d.index <= b]
    return ann(d), len(d), d


# ---------- 锚点 C：全窗读数必须复现 ㊲ ----------
sh = pd.read_csv(SHALLOW_CSV)
sh["variant"] = sh["variant"].str.replace("叠加@q=", "q=", regex=False) \
    .replace({"现≥3（并集4条，今天生产在跑）": BASE})
ANCH = ["ann_return", "ann_return_gross", "one_way_turnover", "excess_univ_ew_ann",
        "max_drawdown"]
worst = 0.0
for (lab, n), st in stat.items():
    a = sh[(sh.variant == lab) & (sh.top_n == n)]
    if not len(a):
        raise SystemExit(f"[锚点C] ㊲ 里找不到 {lab}@{n} ⇒ 两份不可并读")
    for c in ANCH:
        if c in st:
            worst = max(worst, abs(float(st[c]) - float(a[c].iloc[0])))
print(f"[锚点C] 20 个 cell 的全窗读数与 ㊲ shallow.csv 最大差 {worst:.3e}（须 <1e-9）",
      flush=True)
if worst > 1e-9:
    raise SystemExit(f"[锚点C] 差 {worst:.3e} ⇒ 收益序列被动过，本表作废")

# ---------- 锚点 D：切点可加、测试段互不相交 ----------
SPLITS = [pd.Timestamp(f"{y}-01-01") for y in (2019, 2021, 2023, 2025, 2026)]
for sp in SPLITS:
    e = EX[(BASE, PROD_N)]
    tr, te = e[e.index < sp], e[e.index >= sp]
    if len(tr) + len(te) != len(e) or not (tr.index.max() < te.index.min()):
        raise SystemExit(f"[锚点D] 切点 {sp:%Y-%m-%d} 天数不可加或有重叠 ⇒ 作废")
    # 反对照：真把窗口切歪了，这条就会响（下面用一个人造错位窗口自证判据非恒真）
bad = 0
e = EX[(BASE, PROD_N)]
tr, te = e[e.index < SPLITS[1]], e[e.index >= SPLITS[0]]      # 故意让两段重叠
if len(tr) + len(te) == len(e):
    bad += 1
if bad:
    raise SystemExit("[锚点D] 判据恒真（重叠窗口都没被抓住）⇒ 这条断言没牙，作废")
print(f"[锚点D] {len(SPLITS)} 个切点天数可加、段间不相交；重叠窗口反对照被正确抓住", flush=True)

# ---------- 一、16 格的前半 / 后半同号率 ----------
mid = EX[(BASE, PROD_N)].index[len(EX[(BASE, PROD_N)]) // 2]
print(f"\n===== 一、前半 / 后半各算一次年化超额差（切点 {mid:%Y-%m-%d}）=====")
rows = []
for q in QS:
    for n in TOP_N:
        f, nd_f, _ = diff_ann((LAB(q), n), n, b=mid - pd.Timedelta(days=1))
        s, nd_s, _ = diff_ann((LAB(q), n), n, a=mid)
        rows.append({"刀口": LAB(q), "档": n, "前半差pp": f * 100, "后半差pp": s * 100,
                     "同号": "同正" if (f > 0 and s > 0) else ("同负" if (f < 0 and s < 0) else "翻号"),
                     "前半天数": nd_f, "后半天数": nd_s})
half = pd.DataFrame(rows)
pd.set_option("display.width", 240)
pd.set_option("display.unicode.east_asian_width", True)
print(half.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
print(f"⇒ 16 格里两半同正的 **{int((half.同号 == '同正').sum())}** 格、"
      f"同负 {int((half.同号 == '同负').sum())} 格、翻号 {int((half.同号 == '翻号').sum())} 格")
half.to_csv(os.path.join(OUT, "halves.csv"), index=False)

# ---------- 二、固定切点：只用过去挑刀 ----------
print("\n===== 二、固定切点（训练=切点之前，测试=切点之后；档位锁生产 top50）=====")
step_rows = []
for sp in SPLITS[1:]:
    tr_b = sp - pd.Timedelta(days=1)
    cand = {LAB(q): diff_ann((LAB(q), PROD_N), PROD_N, b=tr_b)[0] for q in QS}
    pick = max(cand, key=cand.get)
    te_ew = {LAB(q): diff_ann((LAB(q), PROD_N), PROD_N, a=sp)[0] for q in QS}
    oracle = max(te_ew, key=te_ew.get)
    step_rows.append({"切点": f"{sp:%Y-%m}", "训练选中的刀": pick,
                      "训练差pp": cand[pick] * 100, "该刀测试差pp": te_ew[pick] * 100,
                      "固定0.80测试差pp": te_ew[LAB(Q_PRODUCTION)] * 100,
                      "随机四刀均值pp": np.mean(list(te_ew.values())) * 100,
                      "oracle刀": oracle, "oracle测试差pp": te_ew[oracle] * 100})
st1 = pd.DataFrame(step_rows)
print(st1.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

# ---------- 三、链式 walk-forward：一段一段往后走，累计成一条真样本外曲线 ----------
print("\n===== 三、链式 walk-forward（训练窗只往过去长；测试段首尾相接、不重叠）=====")
CHAIN = [(pd.Timestamp("2019-01-01"), pd.Timestamp("2020-12-31")),
         (pd.Timestamp("2021-01-01"), pd.Timestamp("2023-12-31")),
         (pd.Timestamp("2024-01-01"), pd.Timestamp("2025-12-31")),
         (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-12-31"))]
prev = CHAIN[0][0] - pd.Timedelta(days=1)
pick_all, picked_only_q, real_q, real_both, fixed80, rnd, orac = [], [], [], [], [], [], []
for a, b in CHAIN:
    # (i) 只用过去挑刀（档锁生产 top50）
    cq = {LAB(q): diff_ann((LAB(q), PROD_N), PROD_N, b=prev)[0] for q in QS}
    q_pick = max(cq, key=cq.get)
    dd = diff_ann((q_pick, PROD_N), PROD_N, a=a, b=b)[1]
    picked_only_q.append(f"{q_pick}@{PROD_N}")
    real_q.append(diff_ann((q_pick, PROD_N), PROD_N, a=a, b=b)[0])
    # (ii) 刀与档一起挑（更狠：16 格里挑）
    cb = {(LAB(q), n): diff_ann((LAB(q), n), n, b=prev)[0] for q in QS for n in TOP_N}
    cell2 = max(cb, key=cb.get)
    pick_all.append(f"{cell2[0]}@{cell2[1]}")
    real_both.append(diff_ann(cell2, cell2[1], a=a, b=b)[0])
    fixed80.append(diff_ann((LAB(Q_PRODUCTION), PROD_N), PROD_N, a=a, b=b)[0])
    te = {LAB(q): diff_ann((LAB(q), PROD_N), PROD_N, a=a, b=b)[0] for q in QS}
    orac.append(max(te.values()))
    # 随机：这一段里四刀各 1/4 概率的期望（= 四刀均值）
    rnd.append(np.mean(list(te.values())))
    print(f"  训练<={prev:%Y-%m} → 测试 {a:%Y-%m}~{b:%Y-%m}（{dd} 天）"
          f"　挑刀={q_pick}（实得 {real_q[-1]:+.2%}）　挑刀+档={cell2[0]}@{cell2[1]}"
          f"（实得 {real_both[-1]:+.2%}）　固定0.80 {fixed80[-1]:+.2%}"
          f"　随机 {rnd[-1]:+.2%}　oracle {orac[-1]:+.2%}", flush=True)
    prev = b


def chain_ann(v):
    """把各测试段的年化差按天数加权拼成一条链的年化差"""
    w = [len(EX[(BASE, PROD_N)].loc[a:b]) for a, b in CHAIN]
    return float(np.average(v, weights=w))


print("\n===== 链式总账（四段测试窗拼起来 = 2019-01~2026-09 的样本外）=====")
for tag, v in [("只用过去挑刀（档固定 top50）", real_q), ("刀+档一起挑", real_both),
               ("固定生产刀口 q=0.80", fixed80), ("随机四刀均值", rnd),
               ("oracle（全样本挑最好，作弊上限）", orac)]:
    print(f"  {tag:<28} 各段 {['%+.2f%%' % (x * 100) for x in v]}　"
          f"拼成一条链的年化超额差 = {chain_ann(v):+.2%}")
pd.DataFrame({"段": [f"{a:%Y-%m}~{b:%Y-%m}" for a, b in CHAIN],
              "只挑刀": real_q, "挑刀+档": real_both, "固定0.80": fixed80,
              "随机": rnd, "oracle": orac, "选中_只挑刀": picked_only_q,
              "选中_刀加档": pick_all}).to_csv(
    os.path.join(OUT, "walkforward.csv"), index=False)
print(f"\n[产物] {OUT}/daily_excess.csv.gz、halves.csv、walkforward.csv　"
      f"墙钟 {time.time() - t0:.0f}s")
