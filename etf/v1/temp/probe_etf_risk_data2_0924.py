# -*- coding: utf-8 -*-
"""ETF 风险数据探源·第二弹（0924）

第一弹留下的必须钉死的口子：
1. fund_open_fund_info_em 居然 OK（fund.eastmoney.com 子域通, 与 push2 域不同命）——
   但 head 显示日期是周间隔。NAV 到底是日频还是周频? 513100/511990 有没有?
2. fund_etf_spot_ths(date=历史) 疑似返回当日的单位净值 ⇒ 全市场日频 NAV 历史可回溯?
   拿两只样本、隔几天的日期对表验证。
3. fund_etf_scale_sse(date=历史) 的 date 参数是筛选还是只回最新?
4. 国证 index_hist_cni / 申万 index_analysis_daily_sw 首测失败, 换参数再试一次(各1次)。
5. 补第一弹 ⑤ 崩掉的本地日线代理段。
"""
import socket
import time

socket.setdefaulttimeout(25)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import akshare as ak  # noqa: E402

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)


def run(name, fn, retries=1):
    for a in range(1, retries + 1):
        t0 = time.time()
        try:
            df = fn()
            print(f"\n### {name}  OK  rows={len(df):,}  {time.time()-t0:.1f}s")
            return df
        except Exception as e:
            print(f"\n### {name}  FAIL(try{a})  {time.time()-t0:.1f}s  {type(e).__name__}: {str(e)[:130]}")
            if a < retries:
                time.sleep(1)
    return None


print("=" * 78)
print("① fund_open_fund_info_em: NAV 频率/覆盖 (510300/513100/511990)")
for code in ("510300", "513100", "511990"):
    df = run(f"open_fund_info_em {code} 单位净值走势",
             lambda c=code: ak.fund_open_fund_info_em(symbol=c, indicator="单位净值走势"))
    if df is not None:
        d = pd.to_datetime(df["净值日期"])
        gap = d.diff().dt.days
        print(f"  {code}: {d.min().date()}..{d.max().date()}  间隔分布 {gap.value_counts().head(3).to_dict()}")
        print(df.tail(3).to_string(index=False))
        t0 = time.time()
        df2 = run(f"open_fund_info_em {code} 累计净值走势",
                  lambda c=code: ak.fund_open_fund_info_em(symbol=c, indicator="累计净值走势"))
        if df2 is not None:
            d2 = pd.to_datetime(df2.iloc[:, 0])
            print(f"  累计净值: {d.min().date()}.. 间隔 {d2.diff().dt.days.value_counts().head(3).to_dict()}  {time.time()-t0:.1f}s")

print("=" * 78)
print("② fund_etf_spot_ths(date=) 历史回溯验证: 隔日 NAV 应与 open_fund_info_em 对得上")
ths1 = run("spot_ths(date=20260918)", lambda: ak.fund_etf_spot_ths(date="20260918"))
ths2 = run("spot_ths(date=20260922)", lambda: ak.fund_etf_spot_ths(date="20260922"))
if ths1 is not None and ths2 is not None:
    for c in ("510300", "513100"):
        a = ths1[ths1["基金代码"].astype(str) == c]
        b = ths2[ths2["基金代码"].astype(str) == c]
        print(f"  {c}: 09-18 当前-单位净值={a['当前-单位净值'].values}  09-22 当前-单位净值={b['当前-单位净值'].values}")

print("=" * 78)
print("③ fund_etf_scale_sse(date=历史) 是否返回当日期末份额")
for dt in ("20240102", "20260922"):
    df = run(f"fund_etf_scale_sse(date={dt})", lambda x=dt: ak.fund_etf_scale_sse(date=x))
    if df is not None:
        r = df[df["基金代码"].astype(str) == "510300"]
        print(f"  统计日期列唯一值: {df['统计日期'].astype(str).unique()[:3]}  510300: {r.to_string(index=False)}")

print("=" * 78)
print("④ 换参数重试国证/申万")
df = run("index_hist_cni(399001 默认区间附近)",
         lambda: ak.index_hist_cni(symbol="399001", start_date="20230114", end_date="20240114"))
if df is None:
    df = run("index_all_cni(国证列表,批量)", lambda: ak.index_all_cni())
    if df is not None:
        print(df.head(2).to_string(index=False))
df = run("index_analysis_daily_sw(市场表征)",
         lambda: ak.index_analysis_daily_sw(symbol="市场表征", start_date="20260901", end_date="20260918"))
df = run("index_realtime_sw(一级行业)", lambda: ak.index_realtime_sw(symbol="一级行业"))
if df is not None:
    print(f"  字段: {list(df.columns)[:12]}")

print("=" * 78)
print("⑤ csindex 逐指数全历史耗时样本 x5 (行业/主题混样)")
codes = ["000928", "H30199", "000813", "930707", "H30374"]
t0 = time.time()
ok = 0
for c in codes:
    d = run(f"csindex {c}", lambda x=c: ak.stock_zh_index_hist_csindex(symbol=x,
                                                                       start_date="20100101",
                                                                       end_date="20260924"),
            retries=1)
    if d is not None:
        ok += 1
        print(f"  {c}: {len(d)} 行  日期 {d['日期'].min()}..{d['日期'].max()}")
print(f"  {ok}/{len(codes)} 成功, 总耗时 {time.time()-t0:.1f}s")
# ETF→指数映射: index_csindex_all 的 跟踪产品 列能否反查 (抽样 510300 所属)
li = run("index_csindex_all(重取,映射用)", lambda: ak.index_csindex_all())
if li is not None:
    print(f"  可跟踪指数数(跟踪产品=是): {(li['跟踪产品']=='是').sum()} / {len(li)}")
    print(li[li["指数简称"].astype(str).str.contains("半导体|软件|军工|医药|红|科技|芯片", na=False)]
          [["指数代码", "指数简称", "指数类别", "跟踪产品"]].head(8).to_string(index=False))

print("=" * 78)
print("⑥ 本地日线代理（无网络, 补第一弹崩掉的段）")
BASE = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/data"
etf = pd.read_csv(f"{BASE}/universe_all/510300_daily.csv", parse_dates=["date"]).set_index("date")
idx = pd.read_csv(f"{BASE}/index_cache/SH000300.csv")
dcol = idx.columns[0]
idx["date"] = pd.to_datetime(idx["date"].astype(str).str[:10])
idx = idx.set_index("date")
print(f"  index_cache 列: {list(idx.columns)}")
ir = idx["close"] if "close" in idx.columns else idx.iloc[:, -1]
m = etf[["close"]].join(ir.rename("idx_close"), how="inner")
m["r_etf"] = np.log(m["close"]).diff()
m["r_idx"] = np.log(m["idx_close"]).diff()
m = m.dropna()
b = np.polyfit(m["r_idx"], m["r_etf"], 1)
resid = m["r_etf"] - (b[0] * m["r_idx"] + b[1])
print(f"  510300 vs SH000300: n={len(m)}  beta={b[0]:.3f}  年化残差(跟踪误差代理)={resid.std(ddof=1)*np.sqrt(252)*100:.2f}%")
# 溢价代理: 若有 NAV 历史就能真算; 无 NAV 时对宽基残差即含溢价漂移
import os  # noqa: E402
amts = {}
for f in os.listdir(f"{BASE}/universe_all"):
    if not f.endswith("_daily.csv"):
        continue
    s = pd.read_csv(f"{BASE}/universe_all/{f}", usecols=["amount"])["amount"].tail(20)
    amts[f[:6]] = float(s.median())
ser = pd.Series(amts)
print(f"  {len(ser)}只 近20日中位成交额(元): p1={ser.quantile(.01):.2e} p5={ser.quantile(.05):.2e} p50={ser.quantile(.5):.2e}")
print(f"  <1000万元/日: {(ser<1e7).sum()} 只;  <3000万元/日: {(ser<3e7).sum()} 只")
print("\nDONE probe2")
