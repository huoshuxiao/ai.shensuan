# -*- coding: utf-8 -*-
"""验收 #48/#49：回测合并成单 tab + 候选池/标的池两块新读数是否真在页上。

跑法：`/usr/bin/python3.10 etf/v1/temp/appcheck_etf_pool_tab_0927.py`

为什么另写一份
--------------
`apptest_etf_dashboard_0924.py` 只断言「异常 0 个 + 那几张指标卡在」，它验不到
这一轮新加的两件事：候选池表格有没有真的渲染出来、六位代码有没有被贴上 ETF 名称。
整页跑完没报错也可能是一张空表。所以这里逐条判据都配了反证口径（宁可是脚本写错，
也不要"页面早就坏了但断言还在通过"）。

四条判据（任何一条不过就 exit 1）
--------------------------------
A  前 9 个主 tab 的名称与顺序必须等于设计值，且旧的两个回测 tab 名
   （「💼 持仓与交易」「📈 净值」）必须消失 ⇒ 证明是真合并，不是我把标题改个字糊过去。
   `at.tabs` 是摊平的：影子盘监控内部的子 tab（账户/订单/风控）会排在第 10 个之后，
   所以只校前 9 个、不许反查总数。
B  页首「候选池」这格的数值 == `etf_universe_cache.csv` 的行数（不是写死的 100）。
C  候选池那块表渲染出来了，行数与池缓存一致，且带 name / index_group 两列。
D  最新一次调仓那张贴名表里，`name` 列非空比例 ≥ 90%，且**逐条等于**盘上
   `etf_universe_cache.csv` 里那只 ETF 的名字（防"非空但来源不对"）。
   反证是结构性的：`_decorate_codes` 一旦失效，这张表根本不会带 name 列，
   上面 `not named` 那条会先红 —— 所以「非空 100%」不是恒真断言。
"""
import json
import os
import sys

import pandas as pd
from streamlit.testing.v1 import AppTest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "etf", "v1", "src")
sys.path.insert(0, SRC)
os.chdir(SRC)

fail = []
at = AppTest.from_file(os.path.join(SRC, "app.py"), default_timeout=900).run()

if at.exception:
    fail.append(f"页面抛异常 {len(at.exception)} 个："
                + " | ".join(str(e.value)[:200] for e in at.exception))

# ---------- A：tab 结构 ----------
# 注意 at.tabs 是**摊平**的：`app_live.py`（📡 影子盘监控）内部自己还开了一排子 tab
# （账户/订单/风控），所以这里只校前 9 个必须是主 tab 且顺序一致，后面允许多。
MAIN = ["📉 回测（纸面重放）", "🎯 统计验证", "🧪 ETF 风险",
        "🧬 因子", "⚙️ 配置与生命周期", "📖 口径",
        "🗂 日志与报告", "🔄 反馈闭环", "📡 影子盘监控"]
labels = [str(t.label) for t in at.tabs] if hasattr(at, "tabs") else []
if labels:
    if labels[:9] != MAIN:
        fail.append(f"A 主 tab 前 9 个不符：{labels[:9]}")
    if any("持仓与交易" in l for l in labels):
        fail.append(f"A 「持仓与交易」仍独占 tab（没真合并）：{labels}")
    if any(l == "📈 净值" for l in labels):
        fail.append(f"A 「净值」仍独占 tab（没真合并）：{labels}")
    print(f"A 主 tab {labels[:9]}（内层子 tab {labels[9:]}）")
else:
    print("A ⚠️ 这个 streamlit 版本的 AppTest 不暴露 at.tabs，退回查 caption")
    caps = [str(getattr(c, "value", "")) for c in at.caption]
    if not any("这一页全是**回测**" in c for c in caps):
        fail.append("A 找不到回测 tab 的开场 caption")

# ---------- B：页首候选池那格跟着盘上文件走 ----------
pool = pd.read_csv(os.path.join(SRC, os.pardir, "data", "cache",
                                "etf_universe_cache.csv"), encoding="utf-8-sig")
metrics = {str(m.label): str(m.value) for m in at.metric}
cell = metrics.get("候选池", "")
print(f"B 页首「候选池」= {cell!r}，盘上池缓存 {len(pool)} 行")
if cell != f"{len(pool)} 只":
    fail.append(f"B 候选池格 {cell!r} 与盘上 {len(pool)} 行不一致")
if "同期持仓上限" not in metrics or "全市场镜像" not in metrics:
    fail.append(f"B 缺口径纠正后的新格子，实有 {sorted(metrics)[:8]}…")

# ---------- C / D：两张表 ----------
frames = []
for d in at.dataframe:
    v = d.value
    if isinstance(v, pd.DataFrame):
        frames.append(v)
    elif hasattr(v, "data"):          # ArrowDataFrame 之类
        try:
            frames.append(pd.DataFrame(v.data))
        except Exception:
            pass
print(f"C/D 页上共 {len(frames)} 张 dataframe")

pool_frames = [f for f in frames
               if "index_group" in f.columns and "name" in f.columns
               and len(f) == len(pool)]
if not pool_frames:
    fail.append(f"C 没渲染出候选池表（行数应 {len(pool)}，列应含 name/index_group）")
else:
    print(f"C 候选池表 {len(pool_frames[0])} 行 × {len(pool_frames[0].columns)} 列，"
          f"列：{list(pool_frames[0].columns)}")

named = [f for f in frames if "name" in f.columns and len(f) <= 12]
if not named:
    fail.append("D 没渲染出贴名的持仓表（应有一张 ≤12 行、带 name 列的表）")
else:
    f = named[0]
    ok = f["name"].notna().mean()
    print(f"D 持仓表 {len(f)} 行，name 非空 {ok:.0%}；样例 "
          f"{f[['code', 'name']].head(3).to_dict('records')}")
    if ok < 0.9:
        fail.append(f"D 持仓表贴名成功率 {ok:.0%} < 90%")
    # 名字必须逐条等于盘上池缓存里那只的名字：防"非空但来源不对"（比如哪天页面上
    # 另有一张带 name 的表被我误当成交集）。结构上的反证是：`_decorate_codes` 一旦
    # 失效，这张表根本不会有 name 列，上面那条 `not named` 就会先红。
    truth = dict(zip(pool["code"].astype(str).str.zfill(6), pool["name"]))
    got = dict(zip(f["code"].astype(str).str.zfill(6), f["name"]))
    bad = {k: (v, truth.get(k)) for k, v in got.items() if v != truth.get(k)}
    if bad:
        fail.append(f"D 贴名与池缓存对不上（页面值, 盘上值）：{bad}")
    else:
        print(f"D 逐条对表通过：{len(got)} 只的 name 与 etf_universe_cache.csv 完全一致")

# ---------- E：#51 选项A 的「方向层集中度」观察项 ----------
# 这一条要同时钉死两件事，所以拆三个子判据：
# E1 页面上真有一张「权重（占净值）」的方向表，且末场那一格是 成长系 70.0%；
# E2 页面那行 markdown 里的三个整段读数（平均/最坏/平均方向数）不是写死的字，
#    而是和盘上 signals_daily.csv 重算出来的一致；
# E3 重算值必须等于 09-28 档位实测记在 `tier_dir_readings.json` 里的那三个数
#    （0.4566 / 1.0000 / 3.7823）。那一组数走的是**另一条数据路径**（全候选打分
#    面板 + `select_weights` 重放），而这里走的是归档 csv + `etf_theme.direction_of`
#    ⇒ 一旦 `etf_theme.py` 那份正则和 `category_map.csv` 的规则漂移，E3 就会红。
# 反证是结构性的：`etf_theme` 若整个失效，方向全落「未分类」，E1 的首行就不是 成长系。
from etf_theme import direction_of  # noqa: E402

TIER_JSON = os.path.join(HERE, "tmp_conc_0927", "tier_dir_readings.json")
theme_frames = [f for f in frames if "权重（占净值）" in f.columns]
if not theme_frames:
    fail.append("E1 页面上没有方向层集中度表（应有一张带「权重（占净值）」列的表）")
else:
    tf = theme_frames[0]
    top = tf.sort_values("权重（占净值）", ascending=False).iloc[0]
    print(f"E1 方向表 {len(tf)} 行，末场最大 = {top['direction']} "
          f"{top['权重（占净值）']:.1%}；全表 "
          f"{[(r['direction'], round(r['权重（占净值）'], 4)) for _, r in tf.iterrows()]}")
    if len(tf) > 11:
        fail.append(f"E1 方向表 {len(tf)} 行 > 11（方向只有 10 个 + 未分类）")
    if abs(top["权重（占净值）"] - 0.700) > 0.001 or top["direction"] != "成长系":
        fail.append(f"E1 末场最大方向 = {top['direction']} "
                    f"{top['权重（占净值）']:.4f}，应为 成长系 0.7000")

    # E2 独立重算（不叫 `_theme_view`，只共用同一把分类函数）
    sig = pd.read_csv(os.path.join(SRC, os.pardir, "data", "results",
                                   "signals_daily.csv"), encoding="utf-8-sig")
    ts_col = sig.columns[0]
    sig[ts_col] = pd.to_datetime(sig[ts_col])
    sig["code"] = sig["code"].astype(str).str.extract(r"(\d{6})")[0]
    sig["weight"] = pd.to_numeric(sig["weight"], errors="coerce")
    sig = sig[sig["code"].notna() & (sig["code"] != "")]
    sig = sig.merge(pool[["code", "name", "index_group"]].astype(str),
                    on="code", how="left")
    sig["direction"] = [direction_of(n, g) for n, g
                        in zip(sig["name"].fillna(""),
                               sig["index_group"].fillna(""))]
    agg = sig.groupby([ts_col, "direction"])["weight"].sum()
    per_max = agg.groupby(level=0).max()
    per_n = agg.groupby(level=0).size()
    mine = {"avg_max": float(per_max.mean()), "worst_max": float(per_max.max()),
            "avg_directions": float(per_n.mean()), "bars": int(len(per_max))}
    print(f"E2 归档 csv 重算：{mine}")
    md = [str(getattr(m, "value", "")) for m in at.markdown]
    line = [x for x in md if "篮子押在几类资产上" in x]
    if not line:
        fail.append("E2 页面上找不到方向层集中度那行 markdown")
    else:
        for key, pat in (("avg_max", f"{mine['avg_max']:.1%}"),
                         ("worst_max", f"{mine['worst_max']:.1%}"),
                         ("avg_directions", f"{mine['avg_directions']:.2f}")):
            if pat not in line[0]:
                fail.append(f"E2 页面 markdown 里没有 {key} 的重算值 {pat}：{line[0][:160]}")

    # E3 与档位实测那条路径对表
    if os.path.exists(TIER_JSON):
        base = json.load(open(TIER_JSON, encoding="utf-8"))["tiers"]["基线"]
        for k, v in (("avg_max_dir_weight", mine["avg_max"]),
                     ("worst_max_dir_weight", mine["worst_max"]),
                     ("avg_directions", mine["avg_directions"])):
            if abs(base[k] - v) > 0.005:
                fail.append(f"E3 {k}：档位实测 {base[k]:.4f} vs 本次重算 {v:.4f}"
                            "（差 >0.005 ⇒ etf_theme 的正则与 category_map 漂移了）")
        if abs(base["bars_scored"] - mine["bars"]) > 5:
            fail.append(f"E3 有仓调仓次数 档位实测 {base['bars_scored']} vs "
                        f"重算 {mine['bars']}")
        else:
            print(f"E3 与档位实测对表通过（三条读数差 ≤0.005，"
                  f"{base['bars_scored']} vs {mine['bars']} 场）")
    else:
        fail.append(f"E3 缺 {TIER_JSON} ⇒ 无法与另一条数据路径对表")


print("\n" + ("\n".join("❌ " + m for m in fail) if fail else "OK"))
sys.exit(1 if fail else 0)
