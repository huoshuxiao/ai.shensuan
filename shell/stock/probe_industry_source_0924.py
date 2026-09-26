# -*- coding: utf-8 -*-
"""行业源选路探针：东财能不能用 + 新浪∪深市官方到底覆盖多少（09-24）

`fetch_industry_map.py` 第一跑被自己的守卫拦下了：新浪 49 个板块只给到 **2990 只**，
而全市场 5567 只 ⇒ 少于 4000 就拒绝落盘。这个探针回答两件事，好让下一步不是瞎猜：

 1. 东财那条源在本机到底通不通（`stock_board_industry_name_em` 只要 1 次请求就能判，
    之前不可用的是它的行情类接口，未必同一条命）。
 2. **新浪 ∪ 深市官方** 的并集，对「可投池 5225 只」和「待买入 50 只」各覆盖多少。
    剩下的缺口按代码段拆开看（沪市主板 / 科创板 / 创业板 / 北交所），
    才知道「未知」这一类会不会正好吃掉某一整个市场板块。
"""
import os
import sys
import socket

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, os.path.join(SRC, "data"))
sys.path.insert(0, SRC)

import _bootstrap  # noqa: E402,F401
import pandas as pd

socket.setdefaulttimeout(60)

SIG = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results/"
       "daily_signal")
sig = pd.read_csv(os.path.join(SIG, "signal_20260923.csv"), dtype={"code": str})
buy = pd.read_csv(os.path.join(SIG, "buy_20260923.csv"), dtype={"code": str})
pool, keep = set(sig["code"]), set(sig.loc[sig["keep"], "code"])
b50 = set(buy["code"])
print(f"[口径] 可投池 {len(pool)}、保留池 {len(keep)}、待买入 {len(b50)}")


def seg(code):
    p, d = code[:2], code[2:]
    return {"SH60": "沪市主板", "SH68": "科创板", "SZ00": "深市主板",
            "SZ30": "创业板"}.get(p + d[:2], "北交所")


print("\n===== 1 东财 board_industry_name_em =====")
try:
    import akshare as ak
    d = ak.stock_board_industry_name_em()
    print(f"OK 东财行业板块 {len(d)} 个，列 {d.columns.tolist()}")
    print(d.head(3).to_string())
except Exception as e:
    print(f"FAIL {type(e).__name__}: {str(e)[:140]}")

print("\n===== 2 新浪 ∪ 深市官方 覆盖率 =====")
import fetch_industry_map as F     # noqa: E402  复用生产入口的取数函数，不另写一份

sina = F.fetch_sina()
sz = F.fetch_sz()
m = sina[["inst", "行业"]].rename(columns={"行业": "新浪行业"}).merge(
    sz, on="inst", how="outer")
m["有行业"] = m["新浪行业"].notna() | m["证监会行业"].notna()
have = set(m.loc[m["有行业"], "inst"])
m.to_csv("/tmp/industry_partial_0924.csv", index=False, encoding="utf-8-sig")

for tag, s in (("可投池", pool), ("保留池", keep), ("待买入 50", b50)):
    cov = sum(1 for c in s if c in have)
    print(f"  {tag:10s} {len(s):5d} 只 → 有行业 {cov:5d}（{cov / len(s):.1%}）")

miss = pd.Series([seg(c) for c in pool if c not in have]).value_counts()
allp = pd.Series([seg(c) for c in pool]).value_counts()
print("\n  按代码段看缺口（可投池）：")
for k in allp.index:
    print(f"    {k:6s} 池内 {allp[k]:5d} 只、缺 {int(miss.get(k, 0)):5d} 只"
          f"（{miss.get(k, 0) / allp[k]:.1%}）")
m5 = pd.Series([seg(c) for c in b50 if c not in have]).value_counts()
print(f"\n  待买入 50 只里缺行业的：{int(m5.sum())} 只 {dict(m5)}")
