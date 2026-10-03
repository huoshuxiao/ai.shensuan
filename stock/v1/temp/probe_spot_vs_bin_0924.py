# -*- coding: utf-8 -*-
"""新浪 bulk 快照 vs 现有 bin 的对齐体检（09-24，#78 落笔前最后一道判据）

一条快照 = 一次请求（实测 24.2s / 5567 行，含北交所 346 只），而单票接口要打通
全市场得几千次请求（09-23 实测 ~62 分钟，且 09-24 探明 sina 单票会返回空 dict、
腾讯 tx 在 akshare 1.18.81 直接 ValueError）。所以日更走 bulk。

但 bulk 快照必须先证明四件事才配当 append 的输入：
    A 它说的是哪一根 K？（凌晨抓到的应是 09-23 收盘那一场）
      判据：快照「昨收」≈ bin 末日(09-22) 的 raw 收盘 = $close/$factor
    B 代码对不对得上：快照 `sh600519` ↔ bin `SH600519`，逐字覆盖率
    C bin 里活着的票有多少在快照里缺席（停牌/退市/新上市都要分开数）
    D 成交额单位与 bin 内 amount 的换算：快照是「元」，bin 的 amount 看起来是
      成交额/1000，vwap 与 amount/volume 差 10 倍 —— append 要写同一套单位
"""
import os
import socket
import sys

socket.setdefaulttimeout(90)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BIN = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
       "qlib/qlib_data/cn_data")
CACHE = "/tmp/spot_0924.csv"
FIELDS = ["open", "high", "low", "close", "volume", "amount", "factor", "vwap"]


def load_spot():
    if os.path.exists(CACHE) and os.path.getsize(CACHE) > 4096:
        df = pd.read_csv(CACHE, encoding="utf-8-sig")
        print(f"用缓存快照 {CACHE}（{len(df)} 行）")
        return df
    import akshare as ak
    df = ak.stock_zh_a_spot()
    df.to_csv(CACHE, index=False, encoding="utf-8-sig")
    print(f"新抓快照 {len(df)} 行 -> {CACHE}")
    return df


spot = load_spot()
spot["code"] = spot["代码"].str.upper()
spot = spot.set_index("code")

cal = [ln.strip() for ln in open(f"{BIN}/calendars/day.txt") if ln.strip()]
last_day = cal[-1]
inst = [ln.split("\t")[0] for ln in open(f"{BIN}/instruments/all.txt") if ln.strip()]
spans = {ln.split("\t")[0]: (ln.split("\t")[1], ln.split("\t")[2])
         for ln in open(f"{BIN}/instruments/all.txt") if ln.strip()}
is_idx = np.array([(c[:2] == "SH" and c[2:].startswith("000"))
                   or (c[:2] == "SZ" and c[2:].startswith("399")) for c in inst])
stocks = [c for c, k in zip(inst, is_idx) if not k]
idx_list = [c for c, k in zip(inst, is_idx) if k]
print(f"bin: 日历末日 {last_day} / {len(inst)} 个标的（指数 {len(idx_list)}：{idx_list}）")

# 活票 = instruments 里 END 落在日历末日的
live = [c for c in stocks if spans[c][1] >= last_day]
dead = [c for c in stocks if spans[c][1] < last_day]
print(f"A/B 之前的底数：股票 {len(stocks)} 只 = 活 {len(live)} + 已停更 {len(dead)}")


def tail(code, field, n=3):
    p = f"{BIN}/features/{code.lower()}/{field}.day.bin"
    if not os.path.exists(p):
        return None
    a = np.fromfile(p, dtype="<f4")
    return a[-n:]


rows = []
for c in live:
    cl, f = tail(c, "close"), tail(c, "factor")
    vo, am = tail(c, "volume"), tail(c, "amount")
    if cl is None or f is None:
        continue
    rows.append({"code": c, "bin_close": cl[-1], "bin_raw": cl[-1] / f[-1],
                 "bin_factor": f[-1], "bin_volume": vo[-1] if vo is not None else np.nan,
                 "bin_amount": am[-1] if am is not None else np.nan})
b = pd.DataFrame(rows).set_index("code")
print(f"读到 bin 尾巴的活票：{len(b)} 只")

common = b.index.intersection(spot.index)
print(f"\n=== B 代码对齐：活票里 {len(common)}/{len(b)} 出现在快照中"
      f"（快照独有 {len(spot.index.difference(b.index))} 个）")

j = b.loc[common].join(spot.loc[common, ["名称", "最新价", "昨收", "成交量", "成交额"]])
j["昨收差"] = j["昨收"] / j["bin_raw"] - 1
for tol in (1e-4, 1e-3, 1e-2):
    print(f"=== A 快照「昨收」≈ bin 末日({last_day}) raw 收盘 的票数"
          f"（容差 {tol:g}）：{int(j['昨收差'].abs().le(tol).sum())}/{len(j)}"
          f"  中位偏离 {j['昨收差'].median():.2e}  p95 {j['昨收差'].abs().quantile(.95):.2e}")
mism = j[j["昨收差"].abs() > 1e-2]
print(f"    偏离 >1% 的 {len(mism)} 只（应当是 09-23 除权/缩股的那批）：")
print(mism[["bin_raw", "昨收", "最新价", "名称"]].head(12).to_string())

print(f"\n=== C 快照缺席的活票：{len(b) - len(common)} 只")
miss = b.index.difference(spot.index)
miss_bj = [c for c in miss if c[:2] == "BJ"]
print(f"    其中北交所 {len(miss_bj)} 只；样例 {list(miss[:8])}")
print(f"    缺席票 bin 末日 volume 是否为 0/NaN："
      f"{b.loc[miss, 'bin_volume'].fillna(0).eq(0).mean():.2%} 为零")

print("\n=== D 单位换算")
j2 = j.dropna(subset=["bin_amount", "bin_volume", "bin_close"])
est_amount = j2["bin_close"] * j2["bin_volume"] * 100      # 复权价×复权手数×100 = 元
print(f"    bin amount ÷ (close×volume×100)：中位 "
      f"{(j2['bin_amount'] / est_amount).median():.4f}"
      f"  p05 {(j2['bin_amount'] / est_amount).quantile(.05):.4f}"
      f"  p95 {(j2['bin_amount'] / est_amount).quantile(.95):.4f}  ⇒ bin amount 单位 = 元/{1 / (j2['bin_amount'] / est_amount).median():,.0f}")
print(f"    快照成交额 中位 {spot['成交额'].median():,.0f} 元（真成交额，单位元）")
vwap = tail("SH600519", "vwap")
print(f"    茅台 bin vwap 尾 {vwap}  vs amount/volume {j2.loc['SH600519', 'bin_amount'] / j2.loc['SH600519', 'bin_volume']:.2f}"
      f"  vs 真均价 {j2.loc['SH600519', 'bin_close'] / j2.loc['SH600519', 'bin_factor']:.2f}")

print("\n=== 新上市：快照里有、bin 里没有的代码")
new = sorted(set(spot.index) - set(inst))
print(f"    {len(new)} 只：{new[:15]}")
