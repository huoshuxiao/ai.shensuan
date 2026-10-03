# -*- coding: utf-8 -*-
"""ETF 特有风险数据探源（#14 前置，纯只读、零主线依赖）

要回答的问题：本机能不能拿到 ETF 的**份额 / 规模 / 单位净值 / IOPV / 折溢价**，
拿到的是哪一档频率（实时 / 日 / 季）、一次调用覆盖多少只、耗时多少。
拿不到就必须在主线里改用日线代理（成交额分档滑点 + 连续低量剔除 + 对指数残差），
所以这份探源报告直接决定 #14 的后半段怎么写。

东财系（`*_em`）在股票线已实测 HTTP 不可用，这里仍列上：一是接口清单要全，
二是「此刻仍然不可用」这件事本身需要每次重新取证，不能靠记忆。

用法：/usr/bin/python3.10 shell/probe_etf_risk_sources_0924.py
"""

import io
import re
import socket
import time
import traceback

import pandas as pd

socket.setdefaulttimeout(25)      # 单源最多 25s，别让整个探源挂在死接口上

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 40)

ROWS = []


def probe(tag, fn, sample_cols=None, note=""):
    """跑一个数据源，记录 状态/形状/列名/关键列是否存在/耗时。

        关键列用正则匹配而不是精确列名：各源同一含义的列名写法不一
        （份额 vs 最新份额 vs 实收基金；折溢价 vs 折价率 vs 溢价率），
        探源阶段要的是"有没有这个信息"，不是"叫什么名字"。
    """
    t0 = time.time()
    try:
        df = fn()
    except Exception as e:
        ROWS.append({"源": tag, "状态": f"失败 {type(e).__name__}",
                     "行数": 0, "耗时s": round(time.time() - t0, 1),
                     "关键列": "", "列": "", "备注": note or str(e)[:60]})
        print(f"[{tag}] 失败：{type(e).__name__}: {str(e)[:120]}")
        return None
    dt = time.time() - t0
    if not isinstance(df, pd.DataFrame):
        ROWS.append({"源": tag, "状态": f"非表 {type(df).__name__}", "行数": 0,
                     "耗时s": round(dt, 1), "关键列": "", "列": "", "备注": note})
        print(f"[{tag}] 返回 {type(df)}，不是 DataFrame")
        return df
    cols = [str(c) for c in df.columns]
    joined = "|".join(cols)
    keys = {}
    for k, pat in [("份额", r"份额|实收基金|总份额"),
                   ("规模", r"规模|净资产|基金规模|最新规模"),
                   ("净值", r"净值|单位净值|NAV|nv"),
                   ("IOPV", r"IOPV|参考净值|实时净值"),
                   ("折溢价", r"折价|溢价|折溢|偏离")]:
        hit = [c for c in cols if re.search(pat, c, re.I)]
        if hit:
            keys[k] = hit
    ktxt = "、".join(f"{k}:{'/'.join(v)}" for k, v in keys.items()) or "无"
    ROWS.append({"源": tag, "状态": "OK", "行数": len(df), "耗时s": round(dt, 1),
                 "关键列": ktxt, "列": "、".join(cols)[:220], "备注": note})
    print(f"[{tag}] OK {len(df)} 行 × {len(cols)} 列，{dt:.1f}s")
    print(f"    列：{joined[:300]}")
    print(f"    关键列：{ktxt}")
    if sample_cols and len(df):
        keep = [c for c in sample_cols if c in cols]
        print(df[keep or cols].head(3).to_string())
    return df


def main():
    import akshare as ak
    print(f"akshare {ak.__version__}\n")

    # ---- 实时截面：一次调用拿全市场（东财含 IOPV/折价率，最值得先验）----
    probe("fund_etf_spot_em(东财实时)", lambda: ak.fund_etf_spot_em(),
          sample_cols=["代码", "名称", "最新价", "IOPV实时净值", "折价率", "成交量", "成交额"],
          note="实时全市场，若可用则折溢价零成本")
    probe("fund_etf_spot_ths(同花顺实时)", lambda: ak.fund_etf_spot_ths(),
          note="备选实时源，同花顺通常有反爬")

    # ---- 交易所官方份额（日频，直接对应"清盘风险 5000 万线"）----
    probe("fund_etf_scale_sse(上交所份额)", lambda: ak.fund_etf_scale_sse(date="20260922"),
          note="上交所官方基金份额，日频")
    probe("fund_etf_scale_szse(深交所份额)", lambda: ak.fund_etf_scale_szse(date="20260922"),
          note="深交所官方基金份额，日频")
    probe("fund_scale_daily_szse(深交所规模)", lambda: ak.fund_scale_daily_szse(
        start_date="20260901", end_date="20260922", symbol="ETF"),
        note="深交所按日规模")

    # ---- 新浪系：份额/规模/净值 ----
    probe("fund_etf_category_sina(新浪ETF列表)", lambda: ak.fund_etf_category_sina(symbol="ETF基金"),
          note="新浪全市场快照（股票线实测新浪可达）")
    probe("fund_scale_open_sina(新浪开放基金规模)", lambda: ak.fund_scale_open_sina(symbol="开放基金"),
          note="按报告期披露的规模")
    probe("fund_etf_fund_info_em(东财净值历史 510300)", lambda: ak.fund_etf_fund_info_em(
        fund="510300", start_date="20260101", end_date="20260922"),
        note="单只单位净值序列，折溢价要的就是它")
    probe("fund_etf_dividend_sina(新浪分红)", lambda: ak.fund_etf_dividend_sina(),
          note="分红/拆分：份额变动的另一种来源")

    # ---- 汇总 ----
    res = pd.DataFrame(ROWS)
    print("\n" + "=" * 100)
    print("探源汇总")
    print("=" * 100)
    print(res[["源", "状态", "行数", "耗时s", "关键列"]].to_string(index=False))
    print("\n可用源（OK 且行数>0）：")
    ok = res[(res["状态"] == "OK") & (res["行数"] > 0)]
    print(ok[["源", "行数", "耗时s", "关键列"]].to_string(index=False) if len(ok) else "  （无）")
    print("\n按关键信息归类：")
    for k in ["份额", "规模", "净值", "IOPV", "折溢价"]:
        hit = ok[ok["关键列"].str.contains(k, na=False)]["源"].tolist()
        print(f"  {k:6s}: {'、'.join(hit) if hit else '本机拿不到 → 必须用日线代理'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
