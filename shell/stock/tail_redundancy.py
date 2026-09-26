# -*- coding: utf-8 -*-
"""一次性核查（不落库、不写正式产物）：成交量尾部统计 MAX/MIN(Volume,N)
与已在库的量能族（SMA/STD/MOM/ratio）在全市场截面上是否携带独立信息。

判据来自 rdagent 自己的去重逻辑 scenarios/qlib/developer/factor_runner.py
deduplicate_new_factors：新因子与 SOTA 因子的逐日截面相关均值 >= 0.99 即被判为
重复并 Skip loop。所以这里算的就是同一件事 —— 若候选与在库因子的平均截面
秩相关已接近 0.9+，那把它写进 CANDIDATE LIST 等于送一轮空转。

**已被 stock/v1/src/run_ashare_redundancy_check.py 取代，数字不可再引用**：
本脚本是「日内秩 + 全样本一次 Pearson」的池化近似，而 rdagent 逐日算相关再对日
取均值、且用**原始值**不作秩变换。两个口径在 0.99 阈值附近给出不同结论——这里
报 MAX(Volume,20)=0.991（像是会被判重），权威口径实为 0.9836（不会）。留这个
文件只作为 09-23 提示词那次改错的来路，判重一律以新入口的输出为准。
"""
import sys

sys.path.insert(0, "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/common/src/core")

import numpy as np
import pandas as pd

H5 = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results/"
      "rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5")
START = "2015-01-01"
SAMPLE = 1500


def is_index(code):
    ex, num = code[:2], code[2:]
    return (ex == "SH" and num.startswith("000")) or (ex == "SZ" and num.startswith("399"))


def main():
    raw = pd.read_hdf(H5, key="data")
    cols = {c.lstrip("$"): c for c in raw.columns}
    vol = raw[cols["volume"]]
    dt = raw.index.get_level_values("datetime")
    inst = raw.index.get_level_values("instrument")
    keep = (dt >= pd.Timestamp(START)) & (~inst.isin({i for i in inst.unique() if is_index(i)}))
    vol = vol.loc[keep]
    codes = sorted({c for c in inst.unique() if not is_index(c)})[:SAMPLE]
    vol = vol[vol.index.get_level_values("instrument").isin(set(codes))]
    del raw
    print(f"面板 {vol.shape}")

    # 逐标的算各构造，堆成 {name: Series(datetime, instrument)}
    defs = {}
    for grp, sub in vol.groupby(level="instrument", sort=False):
        s = sub.droplevel("instrument").sort_index().astype("float32")
        for n in (5, 10, 20):
            defs.setdefault(f"SMA(V,{n})", {})[grp] = s.rolling(n).mean()
            defs.setdefault(f"STD(V,{n})", {})[grp] = s.rolling(n).std()
            defs.setdefault(f"MAX(V,{n})", {})[grp] = s.rolling(n).max()
            defs.setdefault(f"MIN(V,{n})", {})[grp] = s.rolling(n).min()
        for n in (5, 20):
            defs.setdefault(f"MOM(V,{n})", {})[grp] = s / s.shift(n) - 1
    long = {k: pd.concat(v, names=["instrument", "datetime"]).swaplevel().sort_index()
            .astype("float64") for k, v in defs.items()}
    del defs

    names = list(long)
    base = pd.DataFrame(long).dropna()
    print(f"成对样本 {base.shape}，逐日截面中位数 {int(base.groupby(level='datetime').size().median())}")

    # 逐日截面 Spearman 相关，再对日取均值
    ranked = base.groupby(level="datetime").rank()
    print("\n===== 与在库量能族的平均截面秩相关（绝对值；>=0.99 会被 rdagent 判重复） =====")
    ref = [n for n in names if not n.startswith(("MAX", "MIN"))]
    tgt = [n for n in names if n.startswith(("MAX", "MIN"))]
    for t in tgt:
        row = []
        for r in ref:
            c = np.corrcoef(ranked[t], ranked[r])[0, 1]
            row.append(f"{r}={c:+.3f}")
        print(f"{t:10s} " + "  ".join(row))
    print("\n===== 尾部统计自身：MAX/MIN 各窗口之间的相关 =====")
    for i, a in enumerate(tgt):
        for b in tgt[i + 1:]:
            print(f"{a} vs {b} = {np.corrcoef(ranked[a], ranked[b])[0, 1]:+.3f}")


if __name__ == "__main__":
    main()
