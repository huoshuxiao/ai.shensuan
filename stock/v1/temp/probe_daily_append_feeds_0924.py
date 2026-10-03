# -*- coding: utf-8 -*-
"""日更 append 的数据源体检（09-24，#78 的前提）

要「收盘后一个脚本把日线 append 进 bin」，得先有一条能拿到**当日全市场 OHLC +
成交额**、且**代码/单位/对齐**都和现有 bin 咬得上的来源。本脚本按四步对看，
不写任何生产数据：

    1. 腾讯 tx 单票日线（stock_zh_a_hist_tx）：列名、单位、更新到哪一天
    2. 新浪 bulk 快照（stock_zh_a_spot）：一次请求能否覆盖全市场（含北交所）、
       成交量是「股」还是「手」、时间戳显示的是哪一个交易日
    3. tx 的收盘价 vs 快照的「最新价/昨收」：同一票两个源对不对得上
    4. bin 自己的尾巴：raw = $close/$factor 在末日（09-22）是否等于 tx 的 09-22 收盘
       —— 这一条才是 append 的落点判据：新一天必须与 bin 的最后一格严丝合缝

第 4 步刻意直接读 .day.bin（第 0 个元素是起始下标，其后逐日对齐），
不经过 h5，因为 bin 才是被写的那个文件。
"""
import socket
import sys
import time

socket.setdefaulttimeout(45)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import akshare as ak  # noqa: E402

BIN = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
       "qlib/qlib_data/cn_data")
# 跨板块抽样：沪主板 / 深主板 / 创业板 / 科创 / 北交所
SAMPLE = ["SH600519", "SZ000001", "SZ300750", "SH688981", "BJ832566"]
SINCE, UNTIL = "20260901", "20260924"


def read_bin(code, field):
    """ qlib bin: float32[]，[0]=起始日历下标，其后逐日对齐到该标的自己的末日 """
    cal = [ln.strip() for ln in open(f"{BIN}/calendars/day.txt") if ln.strip()]
    a = np.fromfile(f"{BIN}/features/{code.lower()}/{field}.day.bin", dtype="<f4")
    start = int(a[0])
    return pd.Series(a[1:], index=pd.to_datetime(cal[start:start + len(a) - 1]))


print("=" * 78)
print("1) 腾讯 tx 单票日线")
tx = {}
for code in SAMPLE:
    sym = code[0].lower() + code[2:]
    try:
        df = ak.stock_zh_a_hist_tx(symbol=sym, start_date=SINCE, end_date=UNTIL,
                                   adjust="")
        tx[code] = df
        print(f"  {code}: {len(df)} 行 列={list(df.columns)} 末日="
              f"{df.iloc[-1, 0] if len(df) else '-'}")
    except Exception as e:
        print(f"  {code}: 失败 {type(e).__name__}: {e}")
    time.sleep(0.5)
if tx:
    print("  样例（第一只前 3 行）:")
    print(tx[list(tx)[0]].head(3).to_string(index=False))

print("=" * 78)
print("2) 新浪 bulk 快照 stock_zh_a_spot")
t0 = time.time()
try:
    spot = ak.stock_zh_a_spot()
    print(f"  {time.time() - t0:.1f}s / {len(spot)} 行  列={list(spot.columns)}")
    spot["board"] = spot["代码"].str[:2].str.upper()
    print("  板块分布：" + "  ".join(f"{k} {v}"
                                    for k, v in spot["board"].value_counts().items()))
    print("  时间戳分布（取前 3 个值）：" + "  ".join(
        map(str, spot["时间戳"].unique()[:3])))
    # 成交量单位：成交额/成交量 ÷ 最新价 ≈ 1 ⇒ 单位是「股」；≈100 ⇒ 「手」
    v = (spot["成交额"] / spot["成交量"] / spot["最新价"]).dropna()
    print(f"  成交额/成交量/最新价：中位 {v.median():.4f}  p05 {v.quantile(.05):.4f}"
          f"  p95 {v.quantile(.95):.4f}   ⇒ 单位「{'股' if 0.8 < v.median() < 1.25 else '?'}」")
    v2 = spot[spot["最新价"] > 0].copy()
    v2["chg"] = v2["最新价"] / v2["昨收"] - 1
    print(f"  快照涨跌幅越界检查：|最新价/昨收-1| > 11% 的有 "
          f"{int(v2['chg'].abs().gt(.11).sum())} 只（>11% 多半是当日除权）")
    spot = spot.set_index("代码")
except Exception as e:
    print(f"  失败 {type(e).__name__}: {e}")
    spot = None

print("=" * 78)
print("3) tx 收盘 vs 快照（同票两源对看）")
if spot is not None:
    for code, df in tx.items():
        sym = code.lower()
        if sym not in spot.index:
            print(f"  {code}: 快照里没有这个代码（格式不匹配？）")
            continue
        row = spot.loc[sym] if not isinstance(spot.loc[sym], pd.DataFrame) else spot.loc[sym].iloc[0]
        last = df.iloc[-1]
        print(f"  {code}: tx 末日 {last['date']} 收 {last['close']} | 快照 最新价 "
              f"{row['最新价']} 昨收 {row['昨收']} 今开 {row['今开']} 量 {row['成交量']} "
              f"额 {row['成交额']}")

print("=" * 78)
print("4) bin 的尾巴：raw = $close/$factor 与 tx / 快照对不对得上")
cal = [ln.strip() for ln in open(f"{BIN}/calendars/day.txt") if ln.strip()]
print(f"  bin 日历 {cal[0]} ~ {cal[-1]}（{len(cal)} 天）")
for code in SAMPLE:
    try:
        c = read_bin(code, "close")
        f = read_bin(code, "factor")
    except FileNotFoundError as e:
        print(f"  {code}: 没有 bin（{e}）")
        continue
    raw = c / f
    line = [f"  {code}: bin 末日 {c.index[-1].date()} raw收盘 {raw.iloc[-1]:.2f}"
            f" close {c.iloc[-1]:.2f} factor {f.iloc[-1]:.4f}"]
    d = tx.get(code)
    if d is not None and len(d):
        d = d.assign(date=pd.to_datetime(d["date"])).set_index("date")
        hit = d.loc[c.index[-1], "close"] if c.index[-1] in d.index else None
        line.append(f"| tx 同日 {hit}")
    print(" ".join(map(str, line)))

print("=" * 78)
print("附：其他 bulk 候选源是否可用（只探可达性，不抓全量）")
for name, fn in [("东财 stock_zh_a_hist(em)",
                  lambda: ak.stock_zh_a_hist(symbol="600519", period="daily",
                                             start_date="20260901", end_date="20260924")),
                 ("交易日历 tool_trade_date_hist_sina",
                  lambda: ak.tool_trade_date_hist_sina())]:
    try:
        t0 = time.time()
        df = fn()
        print(f"  {name}: OK {len(df)} 行 / {time.time() - t0:.1f}s")
    except Exception as e:
        print(f"  {name}: 失败 {type(e).__name__}: {str(e)[:80]}")

sys.exit(0)
