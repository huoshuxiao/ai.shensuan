# -*- coding: utf-8 -*-
"""ETF 特有风险数据探源实测（0924）

要回答三个能不能接：清盘风险（份额/规模）、折溢价（IOPV/NAV）、跟踪误差（标的指数日线），
外加「有了 NAV 历史能不能自算」。样本固定三只：
  510300 沪深300ETF（宽基, sh） / 513100 纳指ETF（跨境, sh） / 511990 华宝现金添益A（货币, sh）
深市样本用 159915 创业板ETF 补（szse 族接口只覆盖深市）。

规矩（0923 踩过）：
- 东财 *_em 一族预期不可用，各只测 1 次记录失败原因，不浪费重试；
- 所有调用包 socket.setdefaulttimeout；
- 单接口失败快速跳过，重试 <=2。
"""
import socket
import time
import traceback

socket.setdefaulttimeout(25)  # 必须在 import akshare 之前生效，兜住任何挂起
import pandas as pd  # noqa: E402
import akshare as ak  # noqa: E402

pd.set_option("display.max_columns", 40)
pd.set_option("display.width", 220)

SAMPLES = {"510300": "sh510300", "513100": "sh513100", "511990": "sh511990"}
RESULTS = []


def probe(name, fn, retries=2, show=4, note=""):
    """真实调用一个接口，记录：可用性/耗时/行数/字段。返回 df 或 None。"""
    for attempt in range(1, retries + 1):
        t0 = time.time()
        try:
            df = fn()
            el = time.time() - t0
            nrows = 0 if df is None else len(df)
            cols = [] if df is None else list(getattr(df, "columns", []))
            RESULTS.append((name, "OK", f"{el:.1f}s", nrows, cols, note))
            print(f"\n### {name}  OK  rows={nrows}  {el:.1f}s")
            if cols:
                print(f"  字段: {cols}")
            if df is not None and nrows:
                print(df.head(show).to_string(index=False))
            return df
        except Exception as e:
            el = time.time() - t0
            msg = f"{type(e).__name__}: {str(e)[:150]}"
            print(f"\n### {name}  FAIL(try {attempt})  {el:.1f}s  {msg}")
            if attempt == retries:
                RESULTS.append((name, "FAIL", f"{el:.1f}s", 0, [msg], note))
                return None
            time.sleep(1.0)
    return None


def sub(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------- ① 份额/规模
sub("① ETF 份额/规模（清盘风险判据）")

# 交易所批量：上交所 ETF 产品规模（最新一期，全批量）
sse = probe("fund_etf_scale_sse(批量,最新)", lambda: ak.fund_etf_scale_sse(date="20260922"))
if sse is not None:
    for c in SAMPLES:
        r = sse[sse.astype(str).apply(lambda x: x.str.contains(c, regex=False)).any(axis=1)]
        print(f"  样本 {c}: {len(r)} 行")
        if len(r):
            print(r.to_string(index=False))

# 深交所批量份额
szse = probe("fund_etf_scale_szse(批量,份额)", lambda: ak.fund_etf_scale_szse())
if szse is not None:
    r = szse[szse.astype(str).apply(lambda x: x.str.contains("159915", regex=False)).any(axis=1)]
    print(f"  样本 159915: {len(r)} 行")
    if len(r):
        print(r.to_string(index=False))

# 深交所日频规模（历史序列？关键探）——先 1 天，成功再拉 20 天看逐标的是否可筛
d1 = probe("fund_scale_daily_szse(1天,ETF)",
           lambda: ak.fund_scale_daily_szse(start_date="20260918", end_date="20260918", symbol="ETF"))
if d1 is not None and len(d1):
    for s in ("159915", "159901"):
        r = d1[d1.astype(str).apply(lambda x: x.str.contains(s, regex=False)).any(axis=1)]
        print(f"  {s}: {len(r)} 行")
        if len(r):
            print(r.to_string(index=False))
    probe("fund_scale_daily_szse(20天,ETF,耗时参照)",
          lambda: ak.fund_scale_daily_szse(start_date="20260820", end_date="20260918", symbol="ETF"),
          show=2)

# 新浪开放式基金规模（历史披露序列, 批量按类型）
probe("fund_scale_open_sina(股票型基金)", lambda: ak.fund_scale_open_sina(symbol="股票型基金"), show=5)
# 同花顺 ETF 实时行情批量（看有无 份额/规模/溢价 字段 + date 参数能否回溯历史）
ths = probe("fund_etf_spot_ths(批量)", lambda: ak.fund_etf_spot_ths())
if ths is not None:
    for c in SAMPLES:
        r = ths[ths.astype(str).apply(lambda x: x.str.contains(c, regex=False)).any(axis=1)]
        print(f"  样本 {c}: {len(r)} 行")
        if len(r):
            print(r.to_string(index=False))
    if ths is not None and "日期" in [str(x) for x in ths.columns] or True:
        pass
    probe("fund_etf_spot_ths(date=20260915 回溯测试)", lambda: ak.fund_etf_spot_ths(date="20260915"))

# 东财批量快照（含最新份额字段?  预期 FAIL, 只测 1 次钉死）
probe("fund_etf_spot_em(东财,预期不可用)", lambda: ak.fund_etf_spot_em(), retries=1)

# ---------------------------------------------------------------- ② IOPV / 折溢价
sub("② IOPV / 参考净值 / 折溢价")
cat = probe("fund_etf_category_sina(ETF基金,批量)", lambda: ak.fund_etf_category_sina(symbol="ETF基金"))
if cat is not None:
    for c in SAMPLES:
        r = cat[cat.astype(str).apply(lambda x: x.str.contains(c, regex=False)).any(axis=1)]
        print(f"  样本 {c}: {len(r)} 行")
        if len(r):
            print(r.to_string(index=False))

# 指数成分/行情类接口里 sina ETF 快照有无 IOPV 已经上面看过字段；
# 再看腾讯族有没有 ETF 现货（0923 证腾讯日线可用）
probe("fund_etf_hist_sina(510300,日线核对)", lambda: ak.fund_etf_hist_sina(symbol=SAMPLES["510300"]), show=2)

# ---------------------------------------------------------------- ③ NAV 历史
sub("③ ETF 单位净值 NAV 历史")
probe("fund_etf_fund_info_em(东财,预期不可用)",
      lambda: ak.fund_etf_fund_info_em(fund="510300", start_date="20250101", end_date="20260924"),
      retries=1)
probe("fund_open_fund_info_em(东财,预期不可用)",
      lambda: ak.fund_open_fund_info_em(symbol="510300", indicator="单位净值走势"), retries=1)
probe("fund_info_ths(510300 基本信息)", lambda: ak.fund_info_ths(symbol="510300"))
probe("fund_etf_dividend_sina(510300 分红)", lambda: ak.fund_etf_dividend_sina())

# ---------------------------------------------------------------- ④ 标的指数日线
sub("④ 标的指数日线（beta 中性用）")
probe("index_csindex_all(中证指数列表,批量)", lambda: ak.index_csindex_all(), show=3)
t0 = time.time()
csi_a = probe("stock_zh_index_hist_csindex(000928 全历史)",
              lambda: ak.stock_zh_index_hist_csindex(symbol="000928",
                                                     start_date="20100101", end_date="20260924"),
              show=2)
print(f"  ^ 单次全历史 {time.time() - t0:.1f}s")
if csi_a is not None:
    probe("stock_zh_index_hist_csindex(H30199 主题/行业样本)",
          lambda: ak.stock_zh_index_hist_csindex(symbol="H30199",
                                                 start_date="20100101", end_date="20260924"),
          show=2)
probe("stock_zh_index_daily(sh000300 新浪)", lambda: ak.stock_zh_index_daily(symbol="sh000300"), show=2)
probe("stock_zh_index_daily(csi930903? 新浪csi前缀试探)",
      lambda: ak.stock_zh_index_daily(symbol="csi930903"), retries=1, show=2)
probe("index_hist_cni(399975 国证证券)",
      lambda: ak.index_hist_cni(symbol="399975", start_date="20240101", end_date="20260924"), show=2)
probe("index_hist_sw(801030 申万化工)", lambda: ak.index_hist_sw(symbol="801030", period="day"), show=2)
probe("index_analysis_daily_sw(行业指数,短窗)",
      lambda: ak.index_analysis_daily_sw(symbol="行业指数", start_date="20260901", end_date="20260918"),
      show=3)
probe("stock_zh_index_spot_sina(指数现货批量)", lambda: ak.stock_zh_index_spot_sina(), show=3)

# ---------------------------------------------------------------- ⑤ 本地日线代理可行性
sub("⑤ 本地日线代理（无网络）")
import numpy as np  # noqa: E402
import os  # noqa: E402

BASE = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/data"
etf = pd.read_csv(f"{BASE}/universe_all/510300_daily.csv", parse_dates=["date"]).set_index("date")
idx = pd.read_csv(f"{BASE}/index_cache/SH000300.csv", parse_dates=[0], index_col=0)
ir = idx.iloc[:, idx.columns.get_loc(idx.columns[0]) if False else "close"] if "close" in idx.columns else idx.iloc[:, -1]
m = etf.join(ir.rename("idx_close"), how="inner")
m["r_etf"] = np.log(m["close"]).diff()
m["r_idx"] = np.log(m["idx_close"]).diff()
m = m.dropna()
beta = np.polyfit(m["r_idx"], m["r_etf"], 1)
resid = m["r_etf"] - (beta[0] * m["r_idx"] + beta[1])
te_ann = resid.std(ddof=1) * np.sqrt(252)
print(f"  510300 vs SH000300: n={len(m)}  beta={beta[0]:.3f}  年化残差(跟踪误差代理)={te_ann*100:.2f}%")
# 跨境样本 513100 对 SH000300 无意义, 演示流动性代理分布
amts = {}
for f in os.listdir(f"{BASE}/universe_all"):
    if not f.endswith("_daily.csv"):
        continue
    s = pd.read_csv(f"{BASE}/universe_all/{f}", usecols=["amount"])["amount"].tail(20)
    amts[f[:6]] = float(s.median())
ser = pd.Series(amts)
print(f"  871只近20日中位成交额(元): p1={ser.quantile(.01):.2e} p5={ser.quantile(.05):.2e} "
      f"p50={ser.quantile(.5):.2e}; 最迷你10只: {ser.nsmallest(10).index.tolist()}")
print(f"  低于1000万元/日 的只数: {(ser < 1e7).sum()} / {len(ser)}")

# ---------------------------------------------------------------- 汇总
sub("实测结论汇总")
print(f"{'接口':52s} {'状态':4s} {'耗时':8s} {'行数':7s} 备注")
for name, st, el, n, cols, note in RESULTS:
    print(f"{name:52s} {st:4s} {el:8s} {n:<7} {note} {'; '.join(map(str, cols))[:110]}")
print("\nDONE probe_etf_risk_data_0924")
