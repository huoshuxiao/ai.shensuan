# -*- coding: utf-8 -*-
"""#50 第二族档位：把「分散」定义在**方向**（10 类）上，且换一种闸的形态

跑法：`/usr/bin/python3.10 etf/v1/temp/conc_tiers_dir_etf_0927.py`（实测 <2 分钟/5 档）
成本口径：打分面板复用 `tmp_conc_0927/score_panel.pkl`（不重新打分），
每档只跑一次生产回测引擎（实测 6~14s/档）。

为什么还要这一族（第一族的三个问题）
------------------------------------
第一族 `conc_tiers_etf_0927.py` 量的是「17 个粗类里每类最多 N 席」，实测暴露：
① 用户在意的那句"10 只里 8 只科技/科创/军工"，在 17 类口径下**不算集中**
   （09-24 那一篮 10 只落在 9 个粗类里）——粗类太细，闸咬不到 perceived 的集中；
② 席位闸会**顺带缩小篮子**（17 类正分候选本就不多），篮子从 6.9 只掉到 2.3 只，
   `w=1/只数` 撞上单标的 0.30 上限 ⇒ 在仓权重从 91% 掉到 58.8%，
   "回撤变浅"里混着"钱没在场内"；
③ 三档收益不单调（N=3 −29.9% / N=2 −48.2% / N=1 −32.2% vs 基线 −36.2%），
   看不出是分散的钱还是噪音。
⇒ 这一族把维度换成 `direction`（10 个方向，成长系 39 只是最挤的一格），
   并且加一种**只动权重、不删候选**的形态：某方向合计权重超过 M 就整块等比
   压到 M，超出的部分留在现金（只减不增，免得压下来的钱又堆进别的方向）。
   这样篮子大小、凑满率与基线完全相同，在仓权重的下降是**明码标价**的那一项，
   不会和"换人"混在一起。

口径（改一行要重跑整张表）
--------------------------
- 席位闸：与第一族同法，在 `select_weights` **之前**删掉方向内名次 > N 的候选；
- 权重闸：在 `select_weights` **之后**按方向聚合，Σw_dir > M 时该方向全体乘
  M/Σw_dir，缺口进现金；其余方向不动。
- 两种形态都不改 `select_weights`、`PORTFOLIO`、`RISK_CONTROL`，也不碰生产文件。

反证（不许恒真）
----------------
⓪ `seat_cap` 的单位夹具（5 成长系 + 1 港股，N=2 必须留 3 删 3——留的是
   成长系分数前 2 加那只独苗港股，删的是成长系里分数最低的 3 只）——兄弟脚本 `conc_tiers_etf_0927.py` 第一版把
   闸写成了"整方向成员数 ≤ N 才进候选"（＝方向白名单），账单整张是假的；
   这个夹具在旧写法下只会留 1 只（港股），所以它抓得住；
① 第 0 档（不设闸）净值末值与归档 `equity_daily.csv` 差 < 1e-6；
② 席位闸真删候选：方向 ≤1 的「被删只次」> 0，且 ≤1 ≥ ≤2（单调）；
③ 权重闸**不删任何候选**：每档的「有仓 bar 数、凑满 10 席 bar 数」必须与基线
   逐格相同（这是它跟席位闸的本质区别，也是这条判据的意义）；
④ 权重闸真的在压：平均「单方向最大权重」必须 基线 ≥ M=0.40 ≥ M=0.30，
   且 M=0.30 严格小于基线（否则闸是空转的）。
任一条不成立 ⇒ rc=1，账单作废。

覆写面：只写 `etf/v1/temp/tmp_conc_0927/tier_dir_readings.json`。
"""
import json
import os
import sys
import time

os.environ["ETF_FREQ"] = "daily"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "etf", "v1", "src")
RESULTS = os.path.join(REPO, "etf", "v1", "data", "results")
TMP = os.path.join(HERE, "tmp_conc_0927")
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

# (标签, 席位闸 N, 权重闸 M)；N/M 为 None 即不设那道闸
TIERS = [("基线", None, None), ("方向≤2席", 2, None), ("方向≤1席", 1, None),
         ("方向权重≤40%", None, 0.40), ("方向权重≤30%", None, 0.30)]
t_all = time.time()

panel = pd.read_pickle(os.path.join(TMP, "score_panel.pkl"))
cat = pd.read_csv(os.path.join(TMP, "category_map.csv"),
                  encoding="utf-8-sig", dtype={"code": str})
dir_of = dict(zip(cat["code"].str.zfill(6), cat["direction"]))
name_of = dict(zip(cat["code"].str.zfill(6), cat["name"]))
cat_of = dict(zip(cat["code"].str.zfill(6), cat["category"]))

universe = get_universe()
pool = DataLoader(freq="daily").load_pool(universe.universe["code"].tolist())
all_ts = pool[max(pool, key=lambda c: len(pool[c]))].index
# 逐 bar 查表用 dict（键是 Timestamp，取值与迭代序无关；主循环一律 `for ts in all_ts`
# 走时间序，"末场"读数以 all_ts 的最后一次赋值为准）
by_ts = {ts: dict(zip(g["code"], g["score"]))
         for ts, g in panel.groupby(level=0, sort=False)}
print(f"面板 {len(panel):,} 行 | 池 {len(pool)} 只 | 方向 "
      f"{sorted(set(dir_of.values()))}")


def seat_cap(scores, n):
    """方向内按分数留前 n 只；返回 (候选, 被删只次)。

    ⚠️ 第一版写成「先数完每个方向有几只，再按 r[dir] <= n 过滤」，那是在用
    **方向的成员总数**当门槛 ⇒ 等价于"只保留成员数 ≤ n 的整个方向"（一份方向
    白名单），跟"每方向最多 n 席"完全不是一回事，账单整张作废。判据是**按分数
    降序边走边数**，第 n+1 只起踢出——下面的反证 ⓪ 就是这个坑的单位测试。"""
    if n is None:
        return scores, 0
    seen, kept = {}, {}
    for c in sorted(scores, key=lambda x: -scores[x]):
        k = dir_of.get(c, f"__unk_{c}")
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= n:
            kept[c] = scores[c]
    return kept, len(scores) - len(kept)


def weight_cap(w, m):
    """某方向合计权重 > m 时整块等比压到 m，缺口进现金（只减不增）。"""
    if m is None:
        return w
    tot = {}
    for c, wt in w.items():
        tot[dir_of.get(c, "?")] = tot.get(dir_of.get(c, "?"), 0.0) + wt
    out = dict(w)
    for d, s in tot.items():
        if s > m:
            k = m / s
            for c in list(out):
                if dir_of.get(c, "?") == d:
                    out[c] *= k
    return out


# ---------- 反证 ⓪：seat_cap 的单位判据（专治"方向白名单"那种写法） ----------
# 用真方向造一个夹具：成长系取 5 只（成员数 39 > 2 ⇒ 白名单写法会整组踢掉），
# 港股取 1 只（成员数 1 ≤ 2 ⇒ 白名单写法会整组保留）。正确写法只看 bar 内名次。
probe_codes = list(cat[cat["direction"] == "成长系"]["code"].str.zfill(6))[:5]
probe_scores = {c: 1.0 + i / 100 for i, c in enumerate(probe_codes)}
probe_scores["159920"] = 0.5                      # 港股唯一一只，分数最低
kept, dropped = seat_cap(probe_scores, 2)
c0 = (len(kept) == 3 and dropped == 3                       # 成长系前 2 + 港股 1
      and max(kept, key=probe_scores.get) == probe_codes[-1]
      and probe_codes[0] not in kept)                       # 分数最低的被踢
print(f"反证 ⓪ seat_cap 单位夹具：5 只同方向 + 1 只独方向，N=2 ⇒ 保留 "
      f"{len(kept)} 只（须 3）、删 {dropped} 只（须 3）、且踢的是分数最低那只 "
      f"{'✅' if c0 else '❌'}")

results = {}
for tag, n, m in TIERS:
    t = time.time()
    recs, dropped, full, empty = [], 0, 0, 0
    holdings, exposure, n_dirs, dir_w_max, growth = [], [], [], [], []
    last = {}
    for ts in all_ts:
        scores, d = seat_cap(by_ts.get(ts, {}), n)
        dropped += d
        w = weight_cap(select_weights(scores), m)
        if not w:
            empty += 1
            recs.append({"t": ts, "c": "", "weight": 0.0, "score": np.nan})
            continue
        if len(w) == PORTFOLIO["top_k"]:
            full += 1
        agg = {}
        for c, wt in w.items():
            agg[dir_of.get(c, "?")] = agg.get(dir_of.get(c, "?"), 0.0) + wt
        holdings.append(len(w))
        exposure.append(sum(w.values()))
        n_dirs.append(len(agg))
        dir_w_max.append(max(agg.values()))
        growth.append(agg.get("成长系", 0.0) / max(sum(agg.values()), 1e-9))
        last = {"date": str(pd.Timestamp(ts).date()),
                "by_direction": {k: round(v * 100, 1)
                                 for k, v in sorted(agg.items(),
                                                    key=lambda kv: -kv[1])},
                "rows": [{"code": c, "name": name_of.get(c, "?"),
                          "direction": dir_of.get(c, "?"),
                          "weight_pct": round(w[c] * 100, 1)}
                         for c in sorted(w, key=lambda x: -w[x])]}
        for c, wt in w.items():
            recs.append({"t": ts, "c": c, "weight": wt,
                         "score": by_ts[ts][c]})
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
    bars_scored = len(all_ts) - empty
    yearly = {str(k): round(float(v.iloc[-1] / v.iloc[0] - 1) * 100, 2)
              for k, v in eq.groupby(eq.index.year)}
    print(f"\n【{tag}】删候选 {dropped:,} 只·次 | 有仓 {bars_scored} bar | "
          f"凑满 10 席 {full} 次 | 平均持仓 {np.mean(holdings):.2f} 只 | "
          f"平均在仓权重 {np.mean(exposure):.1%} | 平均方向数 "
          f"{np.mean(n_dirs):.2f}")
    print(f"    总收益 {total_ret:+.2%} | 年化 {ann_ret:+.2%} | 回撤 {dd:.2%} | "
          f"夏普 {ann_ret / (ann_vol + 1e-9):+.3f} | 换手 "
          f"{annual_turnover('date', res['trades'], res['equity'], 252):.0%}/年 | "
          f"交易 {len(res['trades']):,} 笔 | 成长系占在仓权重 "
          f"{np.mean(growth):.1%} | {time.time() - t:.1f}s")
    print(f"    单方向最大权重：均值 {np.mean(dir_w_max):.1%} / "
          f"最坏 {max(dir_w_max):.1%}")
    print("    逐年收益%: " + "  ".join(f"{k}:{v:+.1f}" for k, v in yearly.items()))
    print(f"    末场 {last['date']} 方向权重: " + "  ".join(
        f"{k} {v}%" for k, v in last["by_direction"].items()))
    results[tag] = {
        "seat_cap": n, "weight_cap": m,
        "dropped_candidates": dropped, "bars_scored": bars_scored,
        "empty_bars": empty, "full_bars": full,
        "avg_holdings": float(np.mean(holdings)),
        "avg_exposure": float(np.mean(exposure)),
        "avg_directions": float(np.mean(n_dirs)),
        "avg_max_dir_weight": float(np.mean(dir_w_max)),
        "worst_max_dir_weight": float(max(dir_w_max)),
        "growth_share_of_exposure": float(np.mean(growth)),
        "total_return": total_ret, "annual_return": ann_ret,
        "annual_vol": ann_vol, "sharpe": ann_ret / (ann_vol + 1e-9),
        "max_drawdown": dd,
        "annual_turnover": annual_turnover("date", res["trades"],
                                           res["equity"], 252),
        "n_trades": int(len(res["trades"])),
        "final_equity": float(eq.iloc[-1]),
        "yearly_return_pct": yearly, "last_basket": last,
        "seconds": round(time.time() - t, 1)}

base = results["基线"]
r_d1, r_d2 = results["方向≤1席"], results["方向≤2席"]
r_w40, r_w30 = results["方向权重≤40%"], results["方向权重≤30%"]
arc_final = float(pd.read_csv(f"{RESULTS}/equity_daily.csv",
                              encoding="utf-8-sig")["equity"].iloc[-1])
c1 = abs(base["final_equity"] - arc_final) < 1e-6
c2 = (r_d1["dropped_candidates"] > 0
      and r_d1["dropped_candidates"] >= r_d2["dropped_candidates"] > 0)
c3 = all(r["bars_scored"] == base["bars_scored"]
         and r["full_bars"] == base["full_bars"]
         and r["dropped_candidates"] == 0 for r in (r_w40, r_w30))
c4 = (base["avg_max_dir_weight"] > r_w40["avg_max_dir_weight"]
      > r_w30["avg_max_dir_weight"])
print(f"\n反证：① 基线末值 {base['final_equity']:,.2f} vs 归档 "
      f"{arc_final:,.2f} {'✅' if c1 else '❌'}")
print(f"      ② 席位闸删候选 {r_d1['dropped_candidates']:,} ≥ "
      f"{r_d2['dropped_candidates']:,} > 0 {'✅' if c2 else '❌'}")
print(f"      ③ 权重闸不删候选（有仓/凑满 bar 数与基线相同）"
      f" {'✅' if c3 else '❌'}")
print(f"      ④ 平均单方向最大权重 基线 {base['avg_max_dir_weight']:.1%} ≥ "
      f"M40 {r_w40['avg_max_dir_weight']:.1%} ≥ M30 "
      f"{r_w30['avg_max_dir_weight']:.1%}（须严格降） {'✅' if c4 else '❌'}")
readings = {"universe_codes": len(pool), "panel_rows": int(len(panel)),
            "tiers": results,
            "guards": {"unit_fixture": c0, "baseline_matches_archive": c1, "seat_cap_bites": c2,
                       "weight_cap_keeps_members": c3, "weight_cap_bit": c4}}
with open(os.path.join(TMP, "tier_dir_readings.json"), "w",
          encoding="utf-8") as f:
    json.dump(readings, f, ensure_ascii=False, indent=2)
ok = c0 and c1 and c2 and c3 and c4
print(f"{'✅ 五道反证全过' if ok else '❌ 有反证不过，账单作废'} "
      f"| 全程 {time.time() - t_all:.0f}s | 读数 {TMP}/tier_dir_readings.json")
sys.exit(0 if ok else 1)
