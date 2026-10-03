# -*- coding: utf-8 -*-
"""实盘数据源实测 第二弹：重叠日逐日对表 + 覆盖率 + 全市场刷新耗时

第一弹（probe_live_sources_0923.py）的教训：只比了每个源的**最后一日**，而新浪/腾讯已经
有 09-23（今天）的完整日线、面板只到 09-22 → 全落在「面板无此日」上，口径一个字没验。
这一弹把比较限制在**重叠日**上，并且把决定成败的三件事各自量化：

A. 价格口径：源的不复权收盘 vs 面板盘面价（$close/$factor），逐日相对差。
   > 0 差 = 两套价不同源或复权算法不同 → 混用会在收益序列上造新台阶，比现有假台阶更糟。
B. 数量单位：源的成交量 vs $volume（面板已核为「手」）。整批 ≈100 说明源给「股」，
   差一个常数倍尚可归一；**逐票散布大就不能用**（量能族只吃 $volume，单位不统一 = 直接污染）。
C. 覆盖率与耗时：随机抽 60 只（含北交所与 68/30 段）数失败率，再按 5677 只外推整轮刷新墙钟。
   这条路能不能日更，看的是这个数，不是「接口能通」。
"""
import socket
import time
from concurrent.futures import ThreadPoolExecutor

socket.setdefaulttimeout(15)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import akshare as ak  # noqa: E402

PANEL = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results/"
         "rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5")
START, END = "20260801", "20260923"

print("读面板…")
t0 = time.time()
p = pd.read_hdf(PANEL)
p = p[[c for c in p.columns if c.startswith("$")]]
p.columns = [c.lstrip("$") for c in p.columns]
p = p[["open", "close", "volume", "factor"]]
last = p.index.get_level_values("datetime").max()
insts = np.array(sorted(p.index.get_level_values("instrument").unique()))
print(f"  {time.time() - t0:.1f}s  {len(insts):,} 只，面板末日 {last.date()}")


def sina(sym):
    d = ak.stock_zh_a_daily(symbol=sym, start_date=START, end_date=END, adjust="")
    return None if d is None or not len(d) else d


def tx(sym):
    d = ak.stock_zh_a_hist_tx(symbol=sym, start_date=START, end_date=END)
    return None if d is None or not len(d) else d


def sym_of(inst):
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}[inst[:2]] + inst[2:]


def norm(d, src):
    """统一成 date/close/vol/amt；腾讯的 amount 字段语义未知，先原样带出再判"""
    if d is None:
        return None
    d = d.copy()
    d["date"] = pd.to_datetime(d["date"] if "date" in d else d.iloc[:, 0])
    for c in ("close", "volume", "amount"):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.rename(columns={"volume": "vol", "amount": "amt"})
    if "vol" not in d:
        d["vol"] = np.nan
    return d[["date", "close", "vol", "amt"]].set_index("date")


def fetch(inst, fn):
    try:
        return norm(fn(sym_of(inst)), fn.__name__)
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:40]}"


def overlap_report(sub, label):
    """重叠日逐日对表：只在面板也有的日子比"""
    price_diffs, vol_ratios, amt_ratios, bad = [], [], [], []
    for inst, d in sub.items():
        if isinstance(d, str) or d is None:
            bad.append((inst, d))
            continue
        try:
            pan = p.xs(inst, level="instrument")
        except KeyError:
            continue
        common = d.index.intersection(pan.index)
        if len(common) < 5:
            bad.append((inst, f"重叠日仅 {len(common)}"))
            continue
        pan = pan.loc[common]
        raw = pan["close"] / pan["factor"].where(pan["factor"] > 0)
        pd_ = (d.loc[common, "close"] / raw - 1).abs()
        vr = d.loc[common, "vol"] / pan["volume"]
        ar = d.loc[common, "amt"] / (raw * pan["volume"])
        price_diffs.append(pd_)
        vol_ratios.append(vr)
        amt_ratios.append(ar)
        print(f"  {inst:10s} 重叠 {len(common):2d} 日  价最大相对差 {pd_.max():.3%}  "
              f"量比中位 {vr.median():8.2f}  额/价×量 中位 {ar.median():10,.1f}")
    if bad:
        print(f"  -- 失败/不可比 {len(bad)}: " + "; ".join(f"{i}({b})" for i, b in bad[:8]))
    if price_diffs:
        allp = pd.concat(price_diffs)
        allv = pd.concat(vol_ratios)
        alla = pd.concat(amt_ratios)
        print(f"  [{label}] 合计 {len(allp):,} 个 (票,日)：价差 max {allp.max():.3%} / "
              f"p99 {allp.quantile(0.99):.3%}；量比 p1~p99 {allv.quantile(0.01):.2f}~"
              f"{allv.quantile(0.99):.2f}；额/价×量 p1~p99 "
              f"{alla.quantile(0.01):,.1f}~{alla.quantile(0.99):,.1f}")


FIXED = ["SH600519", "SH601398", "SZ300750", "SH688981", "SH688256", "SH600028",
         "SZ300506", "BJ871553", "BJ834765"]
rng = np.random.default_rng(20260923)
sample = list(FIXED) + [str(i) for i in rng.choice(insts, size=45, replace=False)]
sample = list(dict.fromkeys(sample))
boards = pd.Series([s[:2] for s in sample]).value_counts().to_dict()
print(f"\n抽样 {len(sample)} 只（{boards}）")

for name, fn in (("新浪", sina), ("腾讯", tx)):
    print("=" * 78)
    print(f"{name}：并发拉取")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(lambda i: (i, fetch(i, fn)), sample))
    el = time.time() - t0
    ok = sum(1 for _, d in res if not isinstance(d, str) and d is not None)
    print(f"  {el:.1f}s / {len(sample)} 只（4 并发）→ 成功 {ok} 只 "
          f"（失败 {len(sample) - ok}）；单只均速 {el / len(sample):.2f}s")
    print(f"  外推 5677 只：4 并发 {el / len(sample) * 5677 / 60:.1f} 分钟"
          f"（同 09-22 ETF 全市场拉取的串行/并发折算口径）")
    bj = [(i, d) for i, d in res if i.startswith("BJ")]
    bj_ok = sum(1 for _, d in bj if not isinstance(d, str) and d is not None)
    print(f"  北交所抽样 {len(bj)} 只，成功 {bj_ok}")
    overlap_report(dict(res), name)

print("=" * 78)
print("东财（第一弹已 FAIL，此处只复核一次以确认不是偶发）")
t0 = time.time()
try:
    d = ak.stock_zh_a_hist(symbol="600519", period="daily", start_date=START,
                           end_date=END, adjust="")
    print(f"  ok {time.time() - t0:.1f}s rows={len(d)}")
except Exception as e:
    print(f"  FAIL {time.time() - t0:.1f}s {type(e).__name__}: {str(e)[:90]}")
