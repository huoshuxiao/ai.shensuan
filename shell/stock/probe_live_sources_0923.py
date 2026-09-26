# -*- coding: utf-8 -*-
"""实盘数据源可用性实测（一次性核查，先探再动主线代码）

要回答的三个问题，按重要性排：
1. **研究面板旧不旧**：qlib bin 的最后一日 vs 今天差几个交易日 —— 决定「接实盘」是不是刚需。
2. **口径对不对得上**：公开源的**不复权**收盘价 vs 面板的盘面价（$close/$factor），
   成交量 vs $volume（面板单位已核为「手」）。对不上就整条路不能用，差一根 K 线都不行。
3. **单位与字段**：东财给的是 手 还是 股？有没有直接给成交额？
   —— 若有真成交额，`amount20` 就有机会从「数据集内部相对量」升级成可对外引用的市场量。

akshare 的请求不带 socket 超时（ETF 线注释里已踩过），所以这里全局设默认超时，
否则一个挂死的连接会把探测变成守夜。
"""
import os
import socket
import sys
import time

socket.setdefaulttimeout(20)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

PANEL = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results/"
         "rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5")

# 跨板块抽样：主板/创业板/科创板/北交所 + 两只已知 $factor 失真最狠的（中石化 factor>1、
# 寒武纪 raw/adj 142 倍），如果连它们都对得上，说明盘面价这条路站得住
# （后两个代码的名字未核实，只作板块与失真样本用，不引用名称）
SAMPLES = {
    "SH600519": "贵州茅台", "SH601398": "工商银行", "SZ300750": "宁德时代",
    "SH688981": "中芯国际", "SH688256": "寒武纪", "SH600028": "中国石化",
    "SZ300506": "假台阶最狠样本", "BJ871553": "北交所样本",
}


def timed(label, fn):
    t0 = time.time()
    try:
        r = fn()
        print(f"  [{label}] ok  {time.time() - t0:5.1f}s  rows={len(r) if r is not None else 0}")
        return r
    except Exception as e:
        print(f"  [{label}] FAIL {time.time() - t0:5.1f}s  {type(e).__name__}: {str(e)[:110]}")
        return None


def em_daily(code6):
    import akshare as ak
    return ak.stock_zh_a_hist(symbol=code6, period="daily", start_date="20260801",
                              end_date="20260923", adjust="")


def sina_daily(sym):
    import akshare as ak
    return ak.stock_zh_a_daily(symbol=sym, start_date="20260801", end_date="20260923",
                              adjust="")


def tx_daily(sym):
    import akshare as ak
    return ak.stock_zh_a_hist_tx(symbol=sym, start_date="20260801", end_date="20260923")


def sina_sym(inst):
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}[inst[:2]] + inst[2:]


print("=" * 78)
print("① 面板新鲜度")
TODAY = pd.Timestamp.today().normalize()
t0 = time.time()
raw = pd.read_hdf(PANEL)
raw = raw[[c for c in raw.columns if c.startswith("$")]]   # 别名列与 $ 列同值，先丢一边
raw.columns = [c.lstrip("$") for c in raw.columns]
print(f"读面板 {time.time() - t0:.1f}s  shape={raw.shape}  cols={list(raw.columns)}")
px = raw[["open", "close", "volume", "factor"]]
last = px.index.get_level_values("datetime").max()
print(f"面板最后一日 = {last.date()}   今天 = {TODAY.date()}   "
      f"日历差 {(TODAY - last).days} 天")
recent = sorted(px.index.get_level_values("datetime").unique())[-12:]
print("面板最后 12 个交易日:", ", ".join(d.strftime("%m-%d") for d in recent))
print(f"最后一日有行情的票: {int(px.xs(last, level='datetime')['close'].notna().sum()):,}")

print("=" * 78)
print("② 网络与三个候选源（同一批票，东财→新浪→腾讯）")
import akshare as ak  # noqa: E402
print(f"akshare {ak.__version__}")

spot = timed("东财快照 spot_em", lambda: ak.stock_zh_a_spot_em())
if spot is not None:
    print(f"    全市场行数 {len(spot):,}  列样例: {list(spot.columns)[:8]}")

rows = []
for inst, nm in SAMPLES.items():
    c6 = inst[2:]
    d_em = timed(f"东财 {inst}", lambda c=c6: em_daily(c))
    d_sina = timed(f"新浪 {inst}", lambda s=sina_sym(inst): sina_daily(s))
    d_tx = timed(f"腾讯 {inst}", lambda s=sina_sym(inst): tx_daily(s))
    for tag, d in (("东财", d_em), ("新浪", d_sina), ("腾讯", d_tx)):
        if d is None or not len(d):
            continue
        cols = {c.lower(): c for c in d.columns}
        date_c = next((cols[k] for k in ("日期", "date") if k in cols), d.columns[0])
        dd = d.copy()
        dd["date"] = pd.to_datetime(dd[date_c].astype(str))
        for kn, out in (("收盘", "close"), ("close", "close"), ("开盘", "open"), ("open", "open"),
                        ("成交量", "vol"), ("volume", "vol"), ("成交额", "amt"), ("amount", "amt")):
            if kn in cols and out not in dd:
                dd[out] = pd.to_numeric(dd[cols[kn]], errors="coerce")
        try:
            pan = px.xs(inst, level="instrument")
        except KeyError:
            pan = None
        for dt in sorted(dd["date"])[-1:]:
            r = dd[dd["date"] == dt].iloc[-1]
            if pan is None or dt not in pan.index:
                rows.append(dict(inst=inst, src=tag, date=dt.date(), close=r.get("close"),
                                 vol=r.get("vol"), amt=r.get("amt"),
                                 panel_close=np.nan, panel_raw=np.nan, panel_vol=np.nan,
                                 note="面板无此日"))
                continue
            pr = pan.loc[dt]
            raw_px = pr["close"] / pr["factor"] if pr["factor"] > 0 else np.nan
            rows.append(dict(inst=inst, src=tag, date=dt.date(),
                             close=float(r.get("close", np.nan)),
                             vol=float(r.get("vol", np.nan)),
                             amt=float(r.get("amt", np.nan)),
                             panel_close=float(pr["close"]), panel_raw=float(raw_px),
                             panel_vol=float(pr["volume"]),
                             note=""))

print("=" * 78)
print("③ 口径对照（源的不复权收盘 vs 面板盘面价 = $close/$factor；成交量 vs $volume）")
out = pd.DataFrame(rows)
if len(out):
    out["价差%"] = (out["close"] / out["panel_raw"] - 1) * 100
    out["量比"] = out["vol"] / out["panel_vol"]
    out["额/盘面价×量"] = out["amt"] / (out["panel_raw"] * out["panel_vol"])
    print(out[["inst", "src", "date", "close", "panel_raw", "价差%",
               "vol", "panel_vol", "量比", "amt", "额/盘面价×量", "note"]].to_string(
        index=False, float_format=lambda v: f"{v:,.4f}", na_rep="-"))
print("\n判读：'价差%' 应≈0；'量比' 应≈1（同为手）或≈100（源给股）；"
      "'额/盘面价×量' ≈100 说明面板的 ×100 规则与源的成交额自洽")
