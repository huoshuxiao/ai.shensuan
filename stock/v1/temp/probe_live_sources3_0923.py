# -*- coding: utf-8 -*-
"""实盘数据源实测 第三弹：成交量单位归一 + 全市场快照能不能一次拿完

第二弹留下两个必须收口的问题：
1. **价已验到机器精度**（腾讯的不复权收盘 vs 面板 $close/$factor，1700 个 (票,日) 相对差
   max 0.000%）—— 但**量的比值在 0.10~106.72 之间乱跳**，不是「源给股、面板给手」那个
   整齐的 100。单位对不上就是整条路废掉：量能族四条构造只吃 $volume，滚动窗口里一旦
   混进两套单位，边界上会造出一个比现有假台阶更狠的台阶。
2. 逐只拉 5677 只按 0.66s/只 要 60+ 分钟。若有**一次请求返回全市场**的快照接口，
   日更就从「一小时任务」变成「几秒任务」，整个方案的形态都不一样。

所以这一弹只做两件事：把单位钉死（同一只票、同一天、三个口径对着看），
和测快照接口的覆盖率/耗时/字段语义。
"""
import socket
import time

socket.setdefaulttimeout(30)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import akshare as ak  # noqa: E402

PANEL = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results/"
         "rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5")
START, END = "20260901", "20260923"
# 故意包含 $factor 失真的两只（中石化 factor>1、寒武纪 142 倍）与一只假台阶样本
FIXED = ["SH600519", "SH601398", "SZ300750", "SH688981", "SH688256", "SH600028",
         "SZ300506", "SZ000799", "SZ000503"]

print("读面板…")
p = pd.read_hdf(PANEL)
p = p[[c for c in p.columns if c.startswith("$")]]
p.columns = [c.lstrip("$") for c in p.columns]
insts = sorted(p.index.get_level_values("instrument").unique())
rng = np.random.default_rng(7)
pool = list(dict.fromkeys(FIXED + [str(i) for i in rng.choice(np.array(insts), 12, replace=False)]))
print(f"  {len(pool)} 只（含北交所抽样 {[i for i in pool if i.startswith('BJ')] or '无'}）")

print("=" * 78)
print("① 逐日对表：面板($volume/手) vs 新浪 vs 腾讯")
t0 = time.time()
for inst in pool:
    sym = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[inst[:2]] + inst[2:]
    pan = p.xs(inst, level="instrument")
    try:
        s = ak.stock_zh_a_daily(symbol=sym, start_date=START, end_date=END, adjust="")
    except Exception:
        s = None
    try:
        t = ak.stock_zh_a_hist_tx(symbol=sym, start_date=START, end_date=END)
    except Exception as e:
        t = None
    line = [f"{inst:9s}"]
    if s is not None and len(s):
        s["date"] = pd.to_datetime(s["date"])
        m = s.set_index("date").join(pan[["volume", "close", "factor"]], how="inner",
                                     rsuffix="_p")
        m["raw"] = m["close_p"] / m["factor"]
        line.append(f"新浪 量/面板={float((m['volume'] / m['volume_p']).median()):7.2f} "
                    f"额/(价×面板量)={float((m['amount'] / (m['raw'] * m['volume_p'])).median()):7.2f} "
                    f"价差={float((m['close'] / m['raw'] - 1).abs().max()):.2e}")
    else:
        line.append("新浪 无数据")
    if t is not None and len(t):
        t["date"] = pd.to_datetime(t["date"])
        mt = t.set_index("date").join(pan[["volume", "close", "factor"]], how="inner",
                                      rsuffix="_p")
        mt["raw"] = mt["close_p"] / mt["factor"]
        cols = " ".join(f"{c}={float(mt[c].median()):.3g}" for c in t.columns
                        if c != "date" and pd.api.types.is_numeric_dtype(mt[c]))
        line.append(f"| 腾讯 [{cols}]")
    print("  " + "  ".join(line))
print(f"  耗时 {time.time() - t0:.1f}s / {len(pool)} 只")

print("=" * 78)
print("② 一次请求拿全市场：新浪现货快照 stock_zh_a_spot")
t0 = time.time()
try:
    spot = ak.stock_zh_a_spot()
    el = time.time() - t0
    print(f"  ok {el:.1f}s  rows={len(spot):,}  cols={list(spot.columns)}")
    spot["代码"] = spot["代码"].astype(str)
    pre = spot["代码"].str[:2].value_counts().to_dict()
    print(f"  前缀分布 {pre}")
    print(spot[spot["代码"].isin([{"SH": "sh", "SZ": "sz", "BJ": "bj"}[i[:2]] + i[2:]
                                   for i in pool])]
          [["代码", "名称", "最新价", "成交量", "成交额"]].to_string(index=False))
except Exception as e:
    print(f"  FAIL {time.time() - t0:.1f}s {type(e).__name__}: {str(e)[:120]}")

print("=" * 78)
print("③ 快照 vs 逐只日线是否同一套数（同一只票同一天的三方对表）")
try:
    for inst in ["SH600519", "SH600028", "SZ300506"]:
        sym = {"SH": "sh", "SZ": "sz"}[inst[:2]] + inst[2:]
        d = ak.stock_zh_a_daily(symbol=sym, start_date=START, end_date=END, adjust="")
        d["date"] = pd.to_datetime(d["date"])
        last = d.iloc[-1]
        pan = p.xs(inst, level="instrument")
        dt = last["date"]
        pr = pan.loc[dt] if dt in pan.index else None
        sp = spot[spot["代码"] == sym]
        print(f"  {inst}  新浪日线 {dt.date()}: 价 {float(last['close']):.2f} "
              f"量 {float(last['volume']):,.0f} 额 {float(last['amount']):,.0f}")
        if len(sp):
            r = sp.iloc[0]
            print(f"      新浪快照            : 价 {float(r['最新价']):.2f} "
                  f"量 {float(r['成交量']):,.0f} 额 {float(r['成交额']):,.0f}")
        print(f"      面板({dt.date()}) "
              + (f": 复权收 {pr['close']:.2f} factor {pr['factor']:.4f} "
                 f"盘面 {pr['close'] / pr['factor']:.2f} 量(手) {pr['volume']:,.0f}"
                 if pr is not None else ": 无此日"))
except Exception as e:
    print(f"  FAIL {type(e).__name__}: {str(e)[:120]}")
