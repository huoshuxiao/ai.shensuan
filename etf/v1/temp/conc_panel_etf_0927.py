# -*- coding: utf-8 -*-
"""#50 前置：重建归档那场的打分面板，并用「逐位复现归档产物」当锚点。

跑法：`/usr/bin/python3.10 etf/v1/temp/conc_panel_etf_0927.py`（约 5 分钟）
成本口径：生产同一件事的实测是 09-27 11:17:28 → 11:21:46 的 [6/9] 段
= **4 分 18 秒**（4,064 个调仓 bar × 每 bar 全部可投标的），这里只多写一份面板。

覆写面：只写 `etf/v1/temp/tmp_conc_0927/`。`etf/v1/data/` 全程只读——**不调**
`stage_library_and_clustering`（它会 lib.upsert + save_markdown + git commit），
也不落任何 results csv。

为什么不直接读 signals.csv
-------------------------
`signals_daily.csv` 只写了**被选中**的那几只（25,974 行 / 4,064 次调仓 ≈ 6.3 只/次），
而集中度档位要回答"第 11 名顶上来会怎样"，那一档的分数盘上没有 ⇒ 必须按生产
同一个 `IntradayRotationStrategy._score_one` 把每个调仓 bar 的**全部可投标的**重算。

归档那场的因子集（从 `etf/v1/log/research_daily_20260927.log` 反推，不是猜的）
------------------------------------------------------------------------------
注册表 9 条评估 → `|mean_ic| ≥ 0.005` 留 5 条；多源里 GP/mogp/混合全关，
official 被新鲜度闸挡死产出 0，llm 走模板兜底出 1 条 `mom_5`（日志 IC −0.0206，
与因子库里存的那条同名同值）；合计 6 条进去重与聚类，簇数=4 → **终态 4 条**，
[5/9] 正交化只花 1.6 秒（说明联合优化/LLM 正交调参都没真跑）。
本脚本按这条链重建，唯一的外来物是 `mom_5` 的表达式——取因子库落库的那份。

两条锚点（任一不过就别谈档位）
------------------------------
A 面板 + 生产 `select_weights`（不加任何闸）重造 signals，与归档
  `data/results/signals_daily.csv` 逐格对表：成员差集、权重与分数的最大绝对差。
B 重造的 signals 跑生产回测引擎，与归档 `equity_daily.csv` 逐格对表。
  两条同过 ⇒ 面板与生产是同一个决策面，档位之间的差可比；
  不过 ⇒ 差额必须报出来，档位只读相对差、不引绝对水平。
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
os.makedirs(TMP, exist_ok=True)
sys.path.insert(0, SRC)
os.chdir(SRC)

import _bootstrap  # noqa: F401,E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from config import (FACTOR_CLUSTERING, JOINT_LLM, LOOKBACK_BARS,  # noqa: E402
                    ORTHO_LLM, RISK_CONTROL)
from etf_universe import get_universe  # noqa: E402
from data_loader import DataLoader  # noqa: E402
from factor_clustering import cluster_factors, dedup_by_cluster  # noqa: E402
from factor_library import get_library  # noqa: E402
from factor_orthogonal import orthogonalize_factors  # noqa: E402
from main import mine_factors, select_factors  # noqa: E402
from multi_source_mining import _attach_impl, dedup_factors  # noqa: E402
from risk_budget import compute_factor_weights  # noqa: E402
from strategy import IntradayRotationStrategy, select_weights  # noqa: E402
from backtest import get_backtester  # noqa: E402

readings = {}
t_all = time.time()

if JOINT_LLM["enabled"] or ORTHO_LLM["enabled"]:
    print("⚠️ 配置里联合优化/LLM 正交调参是开着的，但归档日志的 [5/9] 只花 1.6s"
          "（没真调 LLM）⇒ 本脚本按默认分支（纯 gram-schmidt）重建，"
          "锚点 A/B 若不过先怀疑这里。")

# ---------- [1] 数据面（与 main.py:635-657 同一串） ----------
t = time.time()
universe = get_universe()
codes = universe.universe["code"].tolist()
pool = DataLoader(freq="daily").load_pool(codes)
ref_code = max(pool, key=lambda c: len(pool[c]))
all_ts = pool[ref_code].index
print(f"[1] 池 {len(pool)} 只 | 时间轴锚 {ref_code} "
      f"{all_ts[0]:%Y-%m-%d}~{all_ts[-1]:%Y-%m-%d} {len(all_ts)} bar "
      f"| 回看 {LOOKBACK_BARS} | {time.time() - t:.1f}s")
readings["n_bars"] = int(len(all_ts))
readings["n_pool"] = len(pool)

# ---------- [2]-[5] 因子集重建（复刻 main.py [3/9]~[5/9] 的本地部分） ----------
t = time.time()
raw = mine_factors(pool)
print(f"[2] 注册表基线入池 {len(raw)} 条 / {time.time() - t:.1f}s")

t = time.time()
lib = get_library()
expr = (lib.factors.get("mom_5") or {}).get("expr")
if not expr:
    sys.exit("❌ 因子库里没有 mom_5：归档那场用的因子集已变，锚点无从对表")
mom5 = _attach_impl([{"name": "mom_5", "expr": expr}], pool, "llm")
if not mom5:
    sys.exit("❌ mom_5 表达式在本地池上求值失败")
print(f"[3] 库内 mom_5 重建 mean_ic={mom5[0]['mean_ic']:+.4f}"
      f"（归档日志 -0.0206）/ {time.time() - t:.1f}s")
readings["mom5_mean_ic"] = float(mom5[0]["mean_ic"])

raw = select_factors(dedup_factors(raw + mom5, pool) or (raw + mom5))
print(f"[4] 去重 + |IC|门槛后 {len(raw)} 条: {[f['name'] for f in raw]}")
if FACTOR_CLUSTERING["enabled"]:
    reps = dedup_by_cluster(raw, cluster_factors(raw, pool))
    if reps:
        raw = reps
print(f"[5] 聚类去重后 {len(raw)} 条: {[f['name'] for f in raw]}")
factors = orthogonalize_factors(raw) or raw
weights = compute_factor_weights(factors, pool)
print(f"[6] 终态 {len(factors)} 条 | 权重 "
      f"{ {k: round(float(v), 4) for k, v in sorted(weights.items())} }")
readings["final_factors"] = [f["name"] for f in factors]
readings["weights"] = {k: float(v) for k, v in weights.items()}

strat = IntradayRotationStrategy(factors, pool, universe,
                                 factor_weights=weights)

# ---------- [6] 全候选打分面板（与 generate_signals 同一判据） ----------
PANEL_PKL = os.path.join(TMP, "score_panel.pkl")
t = time.time()
if os.path.exists(PANEL_PKL):
    panel = pd.read_pickle(PANEL_PKL)
    print(f"[6] 复用已落盘面板 {PANEL_PKL}（{len(panel):,} 行）"
          "——面板没变就别重算，删掉这个 pkl 才会重跑")
else:
    rows = []
    for i, ts in enumerate(all_ts):
        tradable = (universe.get_tradable_at(ts) if universe
                    else list(pool.keys()))
        scores = {c: s for c in tradable
                  if (s := strat._score_one(c, ts)) is not None
                  and not np.isnan(s)}
        for c, s in scores.items():
            rows.append((ts, c, s))
        if (i + 1) % 800 == 0:
            print(f"    面板 {i + 1}/{len(all_ts)} bar / {time.time() - t:.0f}s",
                  flush=True)
    panel = pd.DataFrame(rows, columns=["ts", "code", "score"]).set_index("ts")
    panel.to_pickle(PANEL_PKL)
per_bar = panel.groupby(level=0).size()
print(f"[6] 面板 {len(panel):,} 行 | 每 bar 平均 {per_bar.mean():.1f} 只有分 "
      f"| {time.time() - t:.0f}s")
readings["panel_rows"] = int(len(panel))
readings["panel_mean_candidates"] = float(per_bar.mean())
readings["panel_seconds"] = round(time.time() - t, 1)

# ---------- 锚点 A：不加闸重造 signals vs 归档 ----------
# 按 all_ts 逐 bar 取分数（面板里没有的那 29 个史前 bar 照样出空仓哨兵，
# 这样"成员差集必须为 0"才是可判的）；对表用显式 merge —— a.join(mine) 在
# 两侧 level 名字不同时会把行数放大到 22 万，第一版就被它骗过。
by_ts = {ts: dict(zip(g["code"], g["score"]))
         for ts, g in panel.groupby(level=0, sort=False)}
recs = []
for ts in all_ts:
    scores = by_ts.get(ts, {})
    w = select_weights(scores)
    if not w:
        recs.append({"t": ts, "c": "", "weight": 0.0, "score": np.nan})
    for c, wt in w.items():
        recs.append({"t": ts, "c": c, "weight": wt, "score": scores[c]})
mine_raw = pd.DataFrame(recs).set_index("t")[["c", "weight", "score"]]
mine_sig = pd.DataFrame(recs)

arc = pd.read_csv(f"{RESULTS}/signals_daily.csv", encoding="utf-8-sig")
ts_col = arc.columns[0]
arc[ts_col] = pd.to_datetime(arc[ts_col])
arc["code"] = arc["code"].astype(str).str.extract(r"(\d{6})")[0].fillna("")
a = arc.rename(columns={ts_col: "t", "code": "c"})[["t", "c", "weight", "score"]]
j = a.merge(mine_sig, on=["t", "c"], how="outer",
            suffixes=("_arc", "_new"))
both = j.dropna(subset=["weight_arc", "weight_new"])
only_arc = int((j["weight_arc"].notna() & j["weight_new"].isna()).sum())
only_new = int((j["weight_new"].notna() & j["weight_arc"].isna()).sum())
max_dw = float((both["weight_arc"] - both["weight_new"]).abs().max())
sm = both.dropna(subset=["score_arc", "score_new"])
max_ds = float((sm["score_arc"] - sm["score_new"]).abs().max())
print(f"\n锚点 A signals 对表：归档 {len(a):,} 行 / 重建 {len(mine_sig):,} 行 "
      f"| 共同 {len(both):,} | 只在归档 {only_arc} | 只在重建 {only_new} "
      f"| max|Δweight| = {max_dw:.3e} | max|Δscore| = {max_ds:.3e}")
readings["anchor_A"] = {"archived_rows": int(len(a)),
                        "rebuilt_rows": int(len(mine_sig)),
                        "common": int(len(both)),
                        "only_archived": only_arc,
                        "only_rebuilt": only_new,
                        "max_abs_dweight": max_dw,
                        "max_abs_dscore": max_ds}

# ---------- 锚点 B：重跑引擎 vs 归档净值 ----------
t = time.time()
res = get_backtester(pool, universe, RISK_CONTROL, freq="daily") \
    .run(mine_raw.rename(columns={"c": "code"}))
mine_eq = res["equity"]["equity"]
arc_eq_df = pd.read_csv(f"{RESULTS}/equity_daily.csv", encoding="utf-8-sig")
arc_eq = arc_eq_df.set_index(
    pd.to_datetime(arc_eq_df[arc_eq_df.columns[0]]))["equity"]
common_idx = arc_eq.index.intersection(mine_eq.index)
d = (arc_eq.loc[common_idx] - mine_eq.loc[common_idx]).abs()
print(f"锚点 B 净值对表：共同 {len(common_idx):,} 格 | max|Δequity| = "
      f"{d.max():.6f} | 末日 归档 {arc_eq.iloc[-1]:,.2f} vs "
      f"重建 {mine_eq.loc[common_idx[-1]]:,.2f} | 引擎耗时 "
      f"{time.time() - t:.1f}s")
readings["anchor_B"] = {"common": int(len(common_idx)),
                        "max_abs_dequity": float(d.max()),
                        "archived_final": float(arc_eq.iloc[-1]),
                        "rebuilt_final": float(mine_eq.loc[common_idx[-1]]),
                        "engine_seconds": round(time.time() - t, 1)}

ok_a = only_arc == 0 and only_new == 0 and max_dw < 1e-12
ok_b = bool(len(common_idx)) and float(d.max()) < 1e-8
readings["anchor_pass"] = {"A": ok_a, "B": ok_b,
                           "total_seconds": round(time.time() - t_all, 1)}
print(f"\n锚点 A {'✅' if ok_a else '❌'}  锚点 B {'✅' if ok_b else '❌'} "
      f"| 全程 {time.time() - t_all:.0f}s")
with open(os.path.join(TMP, "panel_readings.json"), "w", encoding="utf-8") as f:
    json.dump(readings, f, ensure_ascii=False, indent=2)
print(f"读数已存 {TMP}/panel_readings.json")
sys.exit(0 if (ok_a and ok_b) else 1)
