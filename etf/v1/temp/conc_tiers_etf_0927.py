# -*- coding: utf-8 -*-
"""#50 档位实测：候选池里「每个粗类最多 N 席」对组合的代价（N=∞/3/2/1）

跑法：`/usr/bin/python3.10 etf/v1/temp/conc_tiers_etf_0927.py`（实测 40 秒 / 4 档，
其中每档 回测引擎 6~14s；打分面板复用 `score_panel.pkl`，不重新打分）

前提（两道锚点先过，本脚本才读）
--------------------------------
`conc_panel_etf_0927.py` 已实测：不加闸重造的 signals 与归档逐格相同
（max|Δweight|=5.6e-17，共同 25,974 行、双向差集均为 0），重跑的净值与归档
`equity_daily.csv` 逐格相同（max|Δequity|=0.000000，4,064 格）。⇒ 这里跑的是
**同一个决策面**，档位之间可比。本脚本第 0 档（N=∞）就是那条基线，
末值必须回到 6,380.39，否则直接退出。

闸放在哪一层（口径写死，改一行要重跑整张表）
--------------------------------------------
- 维度：`conc_categories_etf_0927.py` 造出的 `category`（名字正则抠的粗类，
  17 个，候选池 100 只各归一类）。盘上唯一的分类 `index_group` 每组恰好一只，
  拿它当维度是空判据，已弃用。
- 位置：**打分之后、`select_weights` 之前**，把每个粗类里分数从低往高的
  第 N+1 名及以后从候选里删掉，再交给生产原函数选前 top_k(=10) 只。
  `select_weights` 是「按分数降序取前 k」，所以删掉类内名次 > N 的
  等价于「该类最多占 N 席」，且权重/water-filling/手续费全走生产代码，
  **判据一行未改**（PORTFOLIO、RISK_CONTROL 全部按 config 现值）。

`select_weights` 的归一化方式决定了档位账单怎么读（这是第一版踩过的坑）
--------------------------------------------------------------------
`weighting=equal` 是 `w_i = 1/len(选中的)`，**不是 1/top_k** ⇒ 候选被闸删少
不会自动变成现金，而是剩下那几只各拿更多权重；只有当 `1/只数 > max_weight
(=0.30)` 时 water-filling 才把它压到 0.30，压不掉的部分才成为现金。
所以"闸收紧 ⇒ 现金变多 ⇒ 回撤变小"不是天然的，必须**实测在仓权重**
（Σw 的均值）才能判断回撤改善里有多少只是"钱没在场内"。
⇒ 读数一律带四件：凑满 10 席率、平均持仓只数、平均在仓权重 Σw、
   平均同类席位数（= 集中度本身）。**类别数不能当分散度**：闸越紧篮子越小，
   篮子越小类别数越低，第一版把"类别数上升"当反证，被自己判红。

反证（不许恒真）
----------------
⓪ `cap_scores` 的单位夹具（4 半导体 + 2 军工 + 1 价值，N=2 必须留 5 删 2，
   且删的是**类内分数最低**那两只）——第一版写成"整类成员数 ≤ N 才进候选"，
   17 类里只有 军工航天/港股/价值 三类能通过，账单整张是假的，这条就是防它；
① 第 0 档净值末值与归档差 < 1e-6（否则整个面不忠实）；
② N=1 必须真的挤掉候选（被删名次数 > 0）；
③ 被删名次数随 N 单调不增（1 ≥ 2 ≥ 3 > 0，∞ 恒为 0）；
④ 集中度读数必须真的动：N=1 的「平均同类席位」< 基线，
   且 N=3 的「单次最多同类席位」≤ 3（闸没咬到就是空判据）。
任一条不成立 ⇒ rc=1，档位表作废。

覆写面：只写 `etf/v1/temp/tmp_conc_0927/tier_readings.json`；
`etf/v1/data/` 全程只读，不调 `stage_library_and_clustering`。
"""
import json
import os
import sys
import time

os.environ["ETF_FREQ"] = "daily"          # 必须在 import config 之前

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "etf", "v1", "src")
RESULTS = os.path.join(REPO, "etf", "v1", "data", "results")
TMP = os.path.join(HERE, "tmp_conc_0927")
PANEL_PKL = os.path.join(TMP, "score_panel.pkl")
CAT_MAP = os.path.join(TMP, "category_map.csv")
sys.path.insert(0, SRC)
os.chdir(SRC)

import _bootstrap  # noqa: F401,E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from config import PORTFOLIO, RISK_CONTROL  # noqa: E402
from etf_universe import get_universe  # noqa: E402
from data_loader import DataLoader  # noqa: E402
from strategy import select_weights  # noqa: E402
from backtest import get_backtester  # noqa: E402
from portfolio_engine import annual_turnover  # noqa: E402

TIERS = [None, 3, 2, 1]                   # None = 现状（不设类闸）
t_all = time.time()

if not os.path.exists(PANEL_PKL):
    sys.exit(f"❌ 缺面板 {PANEL_PKL}：先跑 conc_panel_etf_0927.py")
if not os.path.exists(CAT_MAP):
    sys.exit(f"❌ 缺粗类映射 {CAT_MAP}：先跑 conc_categories_etf_0927.py")

panel = pd.read_pickle(PANEL_PKL)
cat = pd.read_csv(CAT_MAP, encoding="utf-8-sig", dtype={"code": str})
cat_of = dict(zip(cat["code"].str.zfill(6), cat["category"]))
dir_of = dict(zip(cat["code"].str.zfill(6), cat["direction"]))
name_of = dict(zip(cat["code"].str.zfill(6), cat["name"]))

universe = get_universe()
pool = DataLoader(freq="daily").load_pool(universe.universe["code"].tolist())
ref_code = max(pool, key=lambda c: len(pool[c]))
all_ts = pool[ref_code].index
missing = sorted(set(pool) - set(cat_of))
print(f"面板 {len(panel):,} 行 | 池 {len(pool)} 只 | 粗类映射覆盖 "
      f"{len(pool) - len(missing)}/{len(pool)}"
      + (f" ⚠️ 未覆盖: {missing}" if missing else ""))

by_ts = {ts: dict(zip(g["code"], g["score"]))
         for ts, g in panel.groupby(level=0, sort=False)}


def cap_scores(scores, n):
    """每个粗类只保留**本场分数**最高的 n 只；n=None 原样返回。
    返回 (保留的分数表, 被删掉的候选只数)。

    ⚠️ 第一版写成「先数完每个粗类有几只，再按 r[cat] <= n 过滤」——那用的是
    **类的成员总数**当门槛，等价于"整类成员数 ≤ n 才允许进候选"（一份粗类白名
    名单），跟"每类最多 n 席"完全不是一回事：100 只里只有 军工航天(3)/港股(1)/
    价值(1) 三类能整个通过。账单整张作废过一次，反证 ⓪ 就是这个坑的单位测试。"""
    if n is None:
        return scores, 0
    seen, kept = {}, {}
    for c in sorted(scores, key=lambda x: -scores[x]):
        k = cat_of.get(c, f"__unk_{c}")        # 未覆盖的码各自成类，永不被删
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= n:
            kept[c] = scores[c]
    return kept, len(scores) - len(kept)


# ---------- 反证 ⓪：cap_scores 的单位夹具 ----------
# 夹具 = 半导体电子 4 只（该类在池里有 9 只成员 > 2 ⇒ 旧的"白名单"写法会把
# 整类踢光）+ 军工航天 2 只（池里 3 只 > 2 ⇒ 同样会被踢光）+ 价值 1 只（池里
# 恰好 1 只 ≤ 2 ⇒ 旧写法唯一会留下的那类）。N=2 的正确答案是留 2+2+1=5 只、
# 删 2 只，且删的必须是**类内分数最低**的两只半导体。
probe = list(cat[cat["category"] == "半导体电子"]["code"].str.zfill(6))[:4] \
    + list(cat[cat["category"] == "军工航天"]["code"].str.zfill(6))[:2] \
    + ["512040"]
probe_scores = {c: 1.0 + i / 100 for i, c in enumerate(probe)}
kept0, dropped0 = cap_scores(probe_scores, 2)
c0 = (len(kept0) == 5 and dropped0 == 2
      and probe[0] not in kept0 and probe[1] not in kept0
      and all(c in kept0 for c in probe[2:]))
print(f"反证 ⓪ cap_scores 夹具（半导体 4 + 军工 2 + 价值 1，N=2）⇒ 保留 "
      f"{len(kept0)} 只（须 5）/ 删 {dropped0} 只（须 2）/ 删的是类内分数最低的"
      f"那两只 "
      f"{'✅' if c0 else '❌'}")


results = {}
for n in TIERS:
    t = time.time()
    recs, dropped, full, empty = [], 0, 0, 0
    n_cats, growth, seats, holdings, exposure = [], [], 0, [], []
    last_basket = {}
    max_seat = 0
    for ts in all_ts:
        scores, d = cap_scores(by_ts.get(ts, {}), n)
        dropped += d
        w = select_weights(scores)
        if not w:
            empty += 1
            recs.append({"t": ts, "c": "", "weight": 0.0, "score": np.nan})
            continue
        if len(w) == PORTFOLIO["top_k"]:
            full += 1
        cats = [cat_of.get(c, "?") for c in w]
        n_cats.append(len(set(cats)))
        holdings.append(len(w))
        exposure.append(sum(w.values()))
        growth.append(sum(1 for c in w
                          if dir_of.get(c) == "成长系") / len(w))
        seat = max(pd.Series(cats).value_counts())
        max_seat = max(max_seat, seat)
        seats += seat
        last_basket = {"date": str(pd.Timestamp(ts).date()),
                       "rows": [{"code": c, "name": name_of.get(c, "?"),
                                 "category": cat_of.get(c, "?"),
                                 "weight_pct": round(w[c] * 100, 1)}
                                for c in sorted(w, key=lambda x: -w[x])]}
        for c, wt in w.items():
            recs.append({"t": ts, "c": c, "weight": wt, "score":
                         by_ts[ts][c]})
    sig = pd.DataFrame(recs).set_index("t")[["c", "weight", "score"]]
    res = get_backtester(pool, universe, RISK_CONTROL, freq="daily") \
        .run(sig.rename(columns={"c": "code"}))
    eq = res["equity"]["equity"]
    rets = eq.pct_change().dropna()
    years = len(eq) / 252.0
    total_ret = float(eq.iloc[-1] / eq.iloc[0] - 1)
    ann_ret = float((1 + total_ret) ** (1 / years) - 1)
    ann_vol = float(rets.std() * np.sqrt(252))
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yearly = {str(k): round(float(v.iloc[-1] / v.iloc[0] - 1) * 100, 2)
              for k, v in eq.groupby(eq.index.year)}
    tag = "N=∞" if n is None else f"N={n}"
    bars_scored = len(all_ts) - empty
    print(f"\n【{tag}】被闸删掉 {dropped:,} 只·次 | 凑满 10 席 "
          f"{full}/{bars_scored} 次调仓 ({full / max(bars_scored, 1):.1%}) | "
          f"空仓 {empty} bar")
    print(f"    总收益 {total_ret:+.2%} | 年化 {ann_ret:+.2%} | 回撤 {dd:.2%} | "
          f"夏普 {ann_ret / (ann_vol + 1e-9):+.3f} | 换手 "
          f"{annual_turnover('date', res['trades'], res['equity'], 252):.0%}/年 | "
          f"交易 {len(res['trades']):,} 笔 | {time.time() - t:.1f}s")
    print(f"    篮子：平均持仓 {np.mean(holdings):.2f} 只 | 平均在仓权重 Σw "
          f"{np.mean(exposure):.1%} | 平均类别数 {np.mean(n_cats):.2f} | "
          f"单次最多同类 {max_seat} 席 | 平均同类 "
          f"{seats / max(bars_scored, 1):.2f} 席 | 成长系占比 "
          f"{np.mean(growth):.1%}")
    print("    逐年收益%: " + "  ".join(f"{k}:{v:+.1f}" for k, v in yearly.items()))
    print("    末场持仓 " + last_basket["date"] + ": " + "  ".join(
        f"{r['name']}({r['category']} {r['weight_pct']}%)"
        for r in last_basket["rows"]))
    results[tag] = {
        "dropped_candidates": dropped, "rebalance_bars": bars_scored,
        "empty_bars": empty, "full_bars": full,
        "full_rate": full / max(bars_scored, 1),
        "avg_holdings": float(np.mean(holdings)),
        "avg_exposure": float(np.mean(exposure)),
        "total_return": total_ret, "annual_return": ann_ret,
        "annual_vol": ann_vol,
        "sharpe": ann_ret / (ann_vol + 1e-9), "max_drawdown": dd,
        "annual_turnover": annual_turnover("date", res["trades"],
                                           res["equity"], 252),
        "n_trades": int(len(res["trades"])),
        "avg_categories": float(np.mean(n_cats)),
        "max_same_category": int(max_seat),
        "avg_same_category": seats / max(bars_scored, 1),
        "growth_share": float(np.mean(growth)),
        "final_equity": float(eq.iloc[-1]), "yearly_return_pct": yearly,
        "last_basket": last_basket,
        "seconds": round(time.time() - t, 1)}

# ---------- 反证 ----------
base = results["N=∞"]
r1, r2, r3 = results["N=1"], results["N=2"], results["N=3"]
arc_eq = pd.read_csv(f"{RESULTS}/equity_daily.csv", encoding="utf-8-sig")
arc_final = float(arc_eq["equity"].iloc[-1])
c1 = abs(base["final_equity"] - arc_final) < 1e-6
c2 = r1["dropped_candidates"] > 0
c3 = (r1["dropped_candidates"] >= r2["dropped_candidates"]
      >= r3["dropped_candidates"] > 0)
c4 = (r1["avg_same_category"] < base["avg_same_category"]
      and r3["max_same_category"] <= 3)
print(f"\n反证：① 基线末值 {base['final_equity']:,.2f} vs 归档 {arc_final:,.2f} "
      f"{'✅' if c1 else '❌'} ② N=1 真删掉候选 "
      f"{r1['dropped_candidates']:,} 只·次 "
      f"{'✅' if c2 else '❌'} ③ 删减量随 N 单调 "
      f"{r1['dropped_candidates']}/{r2['dropped_candidates']}/"
      f"{r3['dropped_candidates']} {'✅' if c3 else '❌'} "
      f"④ 平均同类席位 基线 {base['avg_same_category']:.2f} → N=1 "
      f"{r1['avg_same_category']:.2f}，N=3 单次最多 "
      f"{r3['max_same_category']} 席 {'✅' if c4 else '❌'}")

readings = {"universe_codes": len(pool), "panel_rows": int(len(panel)),
            "map_missing": missing, "tiers": results,
            "guards": {"unit_fixture": c0, "baseline_matches_archive": c1, "cap_bites": c2,
                       "monotone": c3, "concentration_moves": c4}}
with open(os.path.join(TMP, "tier_readings.json"), "w", encoding="utf-8") as f:
    json.dump(readings, f, ensure_ascii=False, indent=2)
ok = c0 and c1 and c2 and c3 and c4
print(f"{'✅ 五道反证全过' if ok else '❌ 有反证不过，档位表作废'} "
      f"| 全程 {time.time() - t_all:.0f}s | 读数 {TMP}/tier_readings.json")
sys.exit(0 if ok else 1)
