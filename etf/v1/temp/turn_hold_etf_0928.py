# -*- coding: utf-8 -*-
"""#52 量换手（选项D）：现状一年把整个组合换 50 遍，这张表数一数"少换"值多少钱

跑法：`/usr/bin/python3.10 etf/v1/temp/turn_hold_etf_0928.py`（实测 <2 分钟，
6 档 × 每档一次生产回测引擎；打分面板复用 `tmp_conc_0927/score_panel.pkl`）

问题从哪来
----------
09-28 集中度档位实测（`conc_tiers*_0927.py`，两族六道反证全过）顺手量出：
现状 `PORTFOLIO` 不动，回测引擎单边口径的**年化换手是 5017%**、16 年 21,012 笔，
也就是整个组合平均每 5 个交易日换一遍。佣金+滑点 = 单边 0.06%
（`config.COMMISSION_RATE=1e-4` + `SLIPPAGE=5e-4`，`cost_of()`），买卖两边都要付
⇒ 税负 ≈ 净值每年 **6.38 个点**（其中最低佣地板 0.36 个）。集中度那两族没有一档
在性价比上打得过现状，所以先把"换得勤"这笔账量清。

判据本体一行未动
----------------
调仓间隔在生产里不是旋钮：`IntradayRotationStrategy.default_rebalance` 日线场景
恒为 1（`strategy.py:72-73`），`generate_signals` 的 `rebalance_every` 是形参。
这里**不改任何生产代码/config**，只是把面板（每个调仓日的全候选分数）按
"每 k 个 bar 才出一行信号"重新喂给同一个引擎——引擎本来就会在两个调仓日之间
沿用上次目标权重（`generate_signals` docstring 明写），所以 k>1 就是"攒着不换"。

读数怎么算（口径写死）
--------------------
- 年化换手：`portfolio_engine.annual_turnover` 原函数，Σ成交金额/2 ÷ 平均净值 ÷ 年数。
  注意它把买卖两边都折半了（一次往返算 1 倍换手），所以**摩擦费占净值的比例
  ≈ 2 × 年化换手 × 单边成本**，不是 1 × ——09-28 第一版在这里写错过 2 倍，
  把现状的税负念成 3.0%/年，实测是净值的 6.38%/年。
- 摩擦成本：`trades["cost"]` 直接求和（引擎落盘的列名是 `cost`，不是 `fee`）
  = Σ max(金额×1e-4, 0.1元) + 金额×5e-4，报三个口径：
  「占初始本金」「占平均净值每年几个点」「其中最低佣地板加了几个点」。
  本金只有 1 万、篮子 7 只 ⇒ 成交单均值 883 元、71.8% 的单子不足 1,000 元，
  最低佣（0.1 元/笔）真会咬到，所以不能只念费率。
- 停留天数：某只 ETF 在篮子里**连续**待 L 次调仓 ⇒ 在场 (L−1)×k+1 个交易日
  （两端都算），对所有连续段取算术平均。同时反算平均连续场数
  `L = 1 + (停留−1)/k`，用来看"少换"有没有变成"每次换得更狠"。
- 反证（不许恒真；第一版有两条是我期望写错，09-28 改过，理由记在下面）：
  ① k=1 必须逐位复现归档净值末值（6,380.39，差 <1e-6）；
  ② **基线档 k=1 必须与归档 `trades_daily.csv` 逐笔同**——笔数相等、Σamount 与
     Σcost 对表（容差 1 元：归档 csv 只落 5 位小数），把"现状 5017%/21,012 笔"
     钉死在生产产物上而不是这个脚本里；
  ③ 笔数与**年化换手**随 k 单调降。第一版这里要的是"绝对费额单调降"，实测
     不过（k=2 9,559 → k=3 9,819、k=5 5,158 → k=10 5,208 两次反常）：各档净值
     复利路径不同（k=3 终值 +86.6%、k=10 +117.8% vs k=1 −36.2%），费额的分母在动，
     绝对元数本来就不该单调 ⇒ 换成金额/净值口径的换手率；
  ④ 停留天数随 k 单调升，且 k=20 ≥ 2× k=1。第一版要的是 ≥5×，实测 2.9×（2.7→7.8 天）
     不过：调仓越稀、每次的目标与现持仓差得越远，连续在场**场数**从 3.0 场掉到
     1.3 场（见 L 读数），所以日历天数只涨 2.9 倍是策略的真实行为，不是漏发信号；
     漏发信号那种失效会由 ⑤ 的调仓次数 ≈ 4,064/k 抓到；
  ⑤ k>1 各档的调仓次数必须 ≈ 4,064/k（容差 1%）——防"漏发信号"把换手降下来
     其实是被我少喂了 bar。

覆写面：只写 `etf/v1/temp/tmp_conc_0927/turn_readings.json`，`etf/v1/data/` 只读。
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
from config import (COMMISSION_RATE, MIN_COMMISSION, PORTFOLIO,  # noqa: E402
                    RISK_CONTROL, SLIPPAGE)
from etf_universe import get_universe  # noqa: E402
from data_loader import DataLoader  # noqa: E402
from strategy import select_weights  # noqa: E402
from backtest import get_backtester  # noqa: E402
from portfolio_engine import annual_turnover  # noqa: E402

KS = [1, 2, 3, 5, 10, 20]
t_all = time.time()

panel = pd.read_pickle(os.path.join(TMP, "score_panel.pkl"))
universe = get_universe()
pool = DataLoader(freq="daily").load_pool(universe.universe["code"].tolist())
all_ts = pool[max(pool, key=lambda c: len(pool[c]))].index
# 显式按时间序建表：面板 groupby 的组序不保证是时间序，"末场"必须是最后一天
by_ts = {ts: dict(zip(g["code"], g["score"]))
         for ts, g in panel.groupby(level=0, sort=True)}
print(f"面板 {len(panel):,} 行 | 池 {len(pool)} 只 | 时间轴 {len(all_ts)} bar "
      f"| 单边成本 {(COMMISSION_RATE + SLIPPAGE) * 100:.3f}%"
      f"（佣金 {COMMISSION_RATE} + 滑点 {SLIPPAGE}，最低佣 "
      f"{MIN_COMMISSION} 元）")


def stay_days(codes_per_bar, k):
    """连续在场天数的均值：某只连 L 次调仓在场 ⇒ (L−1)·k+1 个交易日。"""
    streak, runs = {}, []
    for codes in codes_per_bar:
        for c in set(codes):
            streak[c] = streak.get(c, 0) + 1
        for c in list(streak):
            if c not in codes:
                runs.append((streak.pop(c) - 1) * k + 1)
    for L in streak.values():
        runs.append((L - 1) * k + 1)
    return float(np.mean(runs)), len(runs)


results = {}
for k in KS:
    t = time.time()
    idx = [i for i in range(len(all_ts)) if i % k == 0]
    recs, holdings, exposure, full = [], [], [], 0
    codes_per_bar = []
    for i in idx:
        ts = all_ts[i]
        w = select_weights(by_ts.get(ts, {}))
        if not w:
            recs.append({"t": ts, "c": "", "weight": 0.0, "score": np.nan})
            codes_per_bar.append([])
            continue
        if len(w) == PORTFOLIO["top_k"]:
            full += 1
        holdings.append(len(w))
        exposure.append(sum(w.values()))
        codes_per_bar.append(list(w))
        for c, wt in w.items():
            recs.append({"t": ts, "c": c, "weight": wt, "score":
                         by_ts[ts][c]})
    sig = pd.DataFrame(recs).set_index("t")[["c", "weight", "score"]]
    res = get_backtester(pool, universe, RISK_CONTROL, freq="daily") \
        .run(sig.rename(columns={"c": "code"}))
    eq = res["equity"]["equity"]
    tr = res["trades"]
    rets = eq.pct_change().dropna()
    years = len(eq) / 252.0
    total_ret = float(eq.iloc[-1] / eq.iloc[0] - 1)
    ann_ret = float((1 + total_ret) ** (1 / years) - 1)
    ann_vol = float(rets.std() * np.sqrt(252))
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    to = annual_turnover("date", tr, res["equity"], 252)
    fee = float(tr["cost"].sum()) if len(tr) else 0.0
    mean_eq = float(eq.mean())
    amount = float(tr["amount"].sum()) if len(tr) else 0.0
    # 最低佣地板付掉的部分：实际费额 - 纯费率（金额×(佣金率+滑点率)）
    floor_extra = fee - amount * (COMMISSION_RATE + SLIPPAGE)
    stay, n_runs = stay_days(codes_per_bar, k)
    avg_streak_bars = 1.0 + (stay - 1.0) / k      # 反算连续在场场数
    n_reb = len(idx)
    print(f"\n【每 {k} 天调一次】调仓 {n_reb:,} 次（应 ≈ "
          f"{len(all_ts) / k:,.0f}）| 交易 {len(tr):,} 笔 | 年化换手 "
          f"{to:.0%} | 平均持仓 {np.mean(holdings):.2f} 只 | 在仓权重 "
          f"{np.mean(exposure):.1%}")
    print(f"    总收益 {total_ret:+.2%} | 年化 {ann_ret:+.2%} | 回撤 {dd:.2%} | "
          f"夏普 {ann_ret / (ann_vol + 1e-9):+.3f} | 摩擦费 "
          f"{fee:,.2f} 元 = 本金的 {fee / eq.iloc[0]:.1%} = "
          f"净值的 {fee / mean_eq / years:.2%}/年（最低佣地板占 "
          f"{floor_extra / mean_eq / years:.2%}pp）| {time.time() - t:.1f}s")
    print(f"    在场停留：均值 {stay:.1f} 个交易日 / 连续 "
          f"{avg_streak_bars:.2f} 次调仓（{n_runs:,} 段）")
    results[f"k={k}"] = {
        "rebalance_every": k, "n_rebalance": n_reb, "n_trades": int(len(tr)),
        "sum_amount": amount, "annual_turnover": to,
        "total_return": total_ret,
        "annual_return": ann_ret, "max_drawdown": dd,
        "sharpe": ann_ret / (ann_vol + 1e-9), "fee_total": fee,
        "mean_equity": mean_eq, "fee_pct_of_capital": fee / float(eq.iloc[0]),
        "fee_pct_of_nav_per_year": fee / mean_eq / years,
        "min_commission_pct_of_nav_per_year": floor_extra / mean_eq / years,
        "fee_pp_per_year": fee / float(eq.iloc[0]) / years * 100,
        "avg_holdings": float(np.mean(holdings)),
        "avg_exposure": float(np.mean(exposure)),
        "full_bars": full, "avg_stay_days": stay,
        "avg_streak_rebalances": avg_streak_bars, "n_stay_runs": n_runs,
        "final_equity": float(eq.iloc[-1]),
        "seconds": round(time.time() - t, 1)}

# ---------- 反证 ----------
arc_eq_final = float(pd.read_csv(f"{RESULTS}/equity_daily.csv",
                                 encoding="utf-8-sig")["equity"].iloc[-1])
arc_tr = pd.read_csv(f"{RESULTS}/trades_daily.csv", encoding="utf-8-sig")
r1 = results["k=1"]
tos = [results[f"k={k}"] for k in KS]
c1 = abs(r1["final_equity"] - arc_eq_final) < 1e-6
# ② 基线档与归档 trades 表逐笔同（笔数 + Σamount + Σcost）。容差 1 元而不是 0：
#    归档 csv 把 cost 只写到 5 位小数（1.65726），21,012 笔的舍入预算就有 ~0.1 元。
c2 = (r1["n_trades"] == len(arc_tr)
      and abs(r1["sum_amount"] - float(arc_tr["amount"].sum())) < 1.0
      and abs(r1["fee_total"] - float(arc_tr["cost"].sum())) < 1.0)
c3 = all(a["n_trades"] > b["n_trades"] and a["annual_turnover"] > b["annual_turnover"]
         for a, b in zip(tos, tos[1:]))
c4 = all(a["avg_stay_days"] < b["avg_stay_days"]
         for a, b in zip(tos, tos[1:])) and \
    results["k=20"]["avg_stay_days"] >= 2 * r1["avg_stay_days"]
c5 = all(abs(results[f"k={k}"]["n_rebalance"] - len(all_ts) / k)
         <= 0.01 * len(all_ts) / k for k in KS)
print(f"\n反证：① k=1 末值 {r1['final_equity']:,.2f} vs 归档 {arc_eq_final:,.2f} "
      f"{'✅' if c1 else '❌'}")
print(f"      ② k=1 与归档 trades 逐笔同 {r1['n_trades']:,} vs {len(arc_tr):,} 笔、"
      f"Σamount {r1['sum_amount']:,.0f} vs {float(arc_tr['amount'].sum()):,.0f}、"
      f"Σcost {r1['fee_total']:,.2f} vs {float(arc_tr['cost'].sum()):,.2f} "
      f"{'✅' if c2 else '❌'}")
print(f"      ③ 笔数与年化换手随 k 单调降 {r1['n_trades']:,}→"
      f"{results['k=20']['n_trades']:,} 笔、{r1['annual_turnover']:.0%}→"
      f"{results['k=20']['annual_turnover']:.0%} {'✅' if c3 else '❌'}")
print(f"      ④ 停留天数单调升且 k=20 ≥ 2× k=1："
      f"{r1['avg_stay_days']:.1f}→{results['k=20']['avg_stay_days']:.1f} 天 "
      f"{'✅' if c4 else '❌'}")
print(f"      ⑤ 各档调仓次数 ≈ 4,064/k（容差 1%） {'✅' if c5 else '❌'}")
readings = {"bars": int(len(all_ts)), "unit_cost": COMMISSION_RATE + SLIPPAGE,
            "tiers": results,
            "guards": {"k1_matches_archive_equity": c1,
                       "k1_matches_archive_trades": c2,
                       "turnover_monotone_down": c3,
                       "stay_monotone_up": c4,
                       "n_rebalance_as_expected": c5}}
with open(os.path.join(TMP, "turn_readings.json"), "w",
          encoding="utf-8") as f:
    json.dump(readings, f, ensure_ascii=False, indent=2)
ok = c1 and c2 and c3 and c4 and c5
print(f"{'✅ 五道反证全过' if ok else '❌ 有反证不过，账单作废'} "
      f"| 全程 {time.time() - t_all:.0f}s | 读数 {TMP}/turn_readings.json")
sys.exit(0 if ok else 1)
