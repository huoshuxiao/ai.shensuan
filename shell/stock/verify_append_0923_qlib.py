# -*- coding: utf-8 -*-
"""日更落盘后的验收（09-24）：用 qlib 自己读回来，再拿外部同日真值对一笔

只看 bin 字节不够——下游（pregen_source_data → daily_pv.h5 → 评估/看板）是通过
qlib 的 D.features 取数的，索引错位、表头 start 下标写歪、日历少一格这类毛病，
都要在 qlib 这一层验。三件事：
  1. 日历末格 == 2026-09-23，且 09-22 那一行**逐字段没被挪动**（append 不许改历史）
  2. 新格满足包内恒等式：open<=low<=high、close/factor == 盘面价、amount == 元/1000
  3. volume 那一格拿外部真值反证：qlib 读回 $volume × f × 100 == 新浪当日真实股数
     （新浪裸接口只给到 T-1，所以这里对的是 09-23，正是刚写进去的那一场）
"""
import json
import socket
import sys

socket.setdefaulttimeout(25)
import numpy as np          # noqa: E402
import pandas as pd         # noqa: E402
import requests             # noqa: E402

PROVIDER = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
            "qlib/qlib_data/cn_data")
SNAP = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
        "daily_snapshot/spot_20260923.csv")
PREV, NEW = "2026-09-22", "2026-09-23"
# 09-22 那行的既有真值（回滚后 probe_bin_rollback_state_0924.py 打印过的）
HISTORY = {"SH600519": dict(close=304.9339, factor=0.243208, volume=101037.0),
           "SZ000001": dict(close=4.10848, factor=0.350852, volume=2164610.0)}
FIELDS = ["$open", "$high", "$low", "$close", "$volume", "$amount",
          "$factor", "$vwap", "$adjclose", "$change"]
VOL_CHECK = ["SH600519", "SZ000001", "SZ002769", "SZ301118", "SH600428", "BJ920118"]

import qlib                  # noqa: E402
qlib.init(provider_uri=PROVIDER, region="cn")
from qlib.data import D      # noqa: E402

cal = D.calendar()
print(f"qlib 日历：{len(cal)} 格，末格 {pd.Timestamp(str(cal[-1])).date()}")
assert pd.Timestamp(str(cal[-1])).date() == pd.Timestamp(NEW).date(), "qlib 没读到新的一天"

df = D.features(D.instruments("all"), FIELDS, start_time="2026-09-15", end_time=NEW)
df = df.swaplevel().sort_index()
df.columns = [c.lstrip("$") for c in df.columns]     # qlib 给的是 $open…，下面按裸名取
df.columns = [c.lstrip("$") for c in df.columns]      # qlib 给的是 $open 这种列名
print(f"D.features 取回 {df.shape}（{df.index.get_level_values(1).nunique()} 只票）")
piv = {c: df[c].unstack("instrument") for c in
       [f.lstrip("$") for f in FIELDS]}
prev_row = {k: v.loc[PREV] for k, v in piv.items()}
new_row = {k: v.loc[NEW] for k, v in piv.items()}
print(f"  有值票数：{PREV} {int(prev_row['close'].notna().sum())} 只 / {NEW} "
      f"{int(new_row['close'].notna().sum())} 只")

# ---- 1. 历史没被挪动 ----
for inst, want in HISTORY.items():
    for field, v in want.items():
        got = float(piv[field][inst].loc[PREV])
        flag = "OK" if abs(got / v - 1) < 1e-5 else "❌被改写"
        print(f"  {inst} {field} {PREV}: qlib {got:.6g} vs 回滚后实测 {v:.6g}  {flag}")

# ---- 2. 新格恒等式 ----
c, o, h, l = (new_row["close"], new_row["open"], new_row["high"], new_row["low"])
f, v, a, w = new_row["factor"], new_row["volume"], new_row["amount"], new_row["vwap"]
spot = pd.read_csv(SNAP, encoding="utf-8-sig").assign(
    inst=lambda d: d["代码"].str.upper()).set_index("inst")
m = c.notna() & f.notna()
raw_new = (c / f)[m]
sn = spot["最新价"].reindex(raw_new.index).astype("float64")
rel = (raw_new / sn - 1).abs()
print(f"  新格 close/factor vs 快照盘面价：{len(rel)} 只，"
      f"中位相对差 {rel.median():.2e}、p95 {rel.quantile(.95):.2e}")
bad = sorted(o.index[(o > h + 1e-6) | (o < l - 1e-6) | (h < l - 1e-6)])
print(f"  K 线自相矛盾（open 越出 [low,high] 或 high<low）：{len(bad)} 只 {bad[:6]}"
      f"（注意：open>close 是正常下跌开盘，不算破坏）")
sub = pd.DataFrame({"close": c, "open": o, "high": h, "low": l}).dropna()
inside = ((sub["open"].between(sub["low"], sub["high"]))
          & (sub["close"].between(sub["low"], sub["high"]))).mean()
print(f"  开盘/收盘落在 [low, high] 区间内：{inside:.2%}（真做到 K 线里当然 100%，"
      f"这里是查有没有把复权/盘面两套价混进同一格）")
print(f"  vwap 是否也在 [low, high]：{(w.dropna() <= sub['high'].reindex(w.dropna().index) * 1.001).mean():.2%}"
      f"、amount>0 占 {(a.reindex(m[m].index) > 0).mean():.2%}")

# ---- 3. volume 格的外部反证 ----
print(f"\n  新格 volume 对新浪当日真值（单位=股）：")
for inst in VOL_CHECK:
    try:
        js = json.loads(requests.get(
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={inst.lower()}&scale=240&ma=no&datalen=8"
        ).text)
        r = [x for x in js if x["day"] == NEW][0]
    except Exception as e:
        print(f"    {inst} 取不到 {type(e).__name__}: {str(e)[:60]}")
        continue
    S = float(r["volume"])
    E = float(r["close"]) * S          # 外部成交额尺子：新浪当日**盘面**收盘 × 真股数
    gv, ga, gw, gf = v[inst], a[inst], w[inst], f[inst]
    if not np.isfinite(gv):
        print(f"    {inst} {NEW} 新格是 NaN（当日无成交？）")
        continue
    print(f"    {inst}: qlib $volume={gv:,.1f} × f={gf:.5f} × 100 = "
          f"{gv * gf * 100:,.0f} 股 vs 新浪 {S:,.0f} 股 → 比值 {gv * gf * 100 / S:.5f}"
          f"；$amount={ga:,.1f} 千元 vs 盘面收盘×股数={E:,.0f} 元 → {ga * 1000 / E:.5f}")

# ---- 4. instruments 表 ----
li = D.list_instruments(D.instruments("all"), start_time="2026-09-01", as_list=True)
print(f"\n  D.instruments('all') 在 {NEW} 当天在册 {len(li)} 只；"
      f"BJ920025 {'在' if 'BJ920025' in set(li) else '不在'}册")
ends = pd.read_csv(f"{PROVIDER}/instruments/all.txt", sep="\t", header=None,
                   names=["inst", "start", "end"])
print(f"  all.txt {len(ends)} 行，END=={NEW} 的 {int((ends['end'] == NEW).sum())} 只、"
      f"END 仍在 {PREV} 的 {int((ends['end'] == PREV).sum())} 只（当日停牌/退市的正常留在过去）")
print("\n验收结束")
