# -*- coding: utf-8 -*-
"""#14 探源第二棒：可用源的「覆盖度 / 历史深度 / 批量成本」实测，纯只读。

第一棒（`probe_etf_risk_sources_0924.py`）只回答了"哪个接口连得通"：
    份额  : 上交所 fund_etf_scale_sse、深交所 fund_scale_daily_szse
    净值  : 同花顺 fund_etf_spot_ths（只有最新一日快照）
    IOPV / 折溢价: 本机拿不到 → 必须用日线代理
这一棒回答"能不能用"，判据三条：
  1. 覆盖度：抓到的基金代码能盖住主线池 `data/universe_all/`（871 只）的百分之几；
  2. 历史深度：份额能不能回溯到 `EVAL_START=2019-01-01`，还是只有当日快照；
  3. 批量成本：一次全量抓取（含历史补齐）的墙钟时间，决定它进不进日更脚本。
顺带把代理判据要用的量算出来：规模 = 份额 × 单位净值（清盘条款是"连续 20 日基金
资产净值低于 5000 万"），折溢价 = 本地收盘 / 单位净值 - 1。

踩过的三口（本棒据此定的格式）：
  - 上交所 `date` 只吃 `YYYYMMDD`。传 `2026-09-22` 抛的 KeyError 报在内部列索引上
    （"None of [Index([...])]"），看着像接口挂了，其实是实参格式问题，别当断网。
  - 深交所区间抓取**窗口过宽会静默返回 0 行**（不报错）：3 个月有数据、9 个月/整年
    返回空。宽度阶梯是用来找"安全窗口"的，不是用来测吞吐的。
  - 本地镜像文件名是 `<code>_daily.csv`，剥扩展名要剥这一段，只剥 `.csv` 会让代码集
    变成 `159003_daily`，与任何外部源的交集恒为 0 —— 第一棒就把它读成了"覆盖度 0%"。

跑法：仓库根 `/usr/bin/python3.10 -u shell/probe_etf_risk_coverage_0924.py`
"""
import os
import socket
import time

import pandas as pd

socket.setdefaulttimeout(30)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UNIV_DIR = os.path.join(ROOT, "etf", "v1", "data", "universe_all")
SUF = "_daily.csv"
YMD = "%Y%m%d"


def local_panel():
    """读本地镜像每只 ETF 的首末行：code / first / last / close / amount。"""
    rows = []
    for fn in sorted(os.listdir(UNIV_DIR)):
        if not fn.endswith(SUF):
            continue
        code = fn[:-len(SUF)]
        try:
            df = pd.read_csv(os.path.join(UNIV_DIR, fn))
            if len(df) == 0:
                continue
            rows.append((code, str(df["date"].iloc[0]), str(df["date"].iloc[-1]),
                         float(df["close"].iloc[-1]), float(df["amount"].iloc[-1])))
        except Exception:
            continue
    return pd.DataFrame(rows, columns=["code", "first", "last", "close", "amount"])


def code_col(df):
    for c in ("code", "基金代码", "代码"):
        if c in df.columns:
            return c
    return None


def cov(df, codes, label):
    """覆盖率：抓到的代码集合 ∩ 本地镜像代码集。"""
    col = code_col(df)
    if col is None:
        print(f"  [{label}] 没有代码列（列={'|'.join(df.columns)}）→ 无法配对")
        return set()
    got = {str(c).strip().zfill(6) for c in df[col]}
    inter = got & codes
    print(f"  [{label}] 抓到 {len(got)} 个代码 → 与本池 {len(codes)} 只交集 {len(inter)}"
          f"（覆盖 {len(inter) / len(codes):.1%}）")
    return inter


def as_shares(df):
    """归一成 code/shares：同代码多条记录时留**末次**（深市返回的是区间表）。"""
    out = pd.DataFrame()
    out["code"] = df[code_col(df)].astype(str).str.strip().str.zfill(6)
    sc = [c for c in df.columns if "份额" in c]
    out["shares"] = pd.to_numeric(df[sc[0]], errors="coerce") if sc else float("nan")
    dc = [c for c in df.columns if c in ("日期", "统计日期")]
    if dc:
        out = out.assign(_d=df[dc[0]].astype(str)).sort_values("_d").drop(columns="_d")
    return out.dropna(subset=["shares"]).drop_duplicates("code", keep="last")


def main():
    import akshare as ak
    print(f"akshare {ak.__version__}\n")

    panel = local_panel()
    codes = set(panel["code"])
    first = sorted(pd.to_datetime(panel["first"]))
    last = sorted(pd.to_datetime(panel["last"]))
    print(f"本地镜像 {len(panel)} 只；最新交易日 {last[-1].date()}；"
          f"起点中位 {first[len(first) // 2].date()}、起点 P25 {first[len(first) // 4].date()}\n")

    # ---------- 1. 上交所份额：YYYYMMDD 日期阶梯，测历史深度 ----------
    print("=" * 92)
    print("1. 上交所份额 fund_etf_scale_sse(date='YYYYMMDD') —— 历史深度与覆盖度")
    print("=" * 92)
    sse = {}
    for d in ["20261231", "20260922", "20260630", "20251231", "20241231",
              "20231229", "20221230", "20211231", "20201231", "20191231", "20181228"]:
        t = time.time()
        try:
            df = ak.fund_etf_scale_sse(date=d)
            sse[d] = df
            day = str(df["统计日期"].iloc[0]) if "统计日期" in df else "?"
            print(f"  {d}: OK {len(df)} 行，表内统计日期 {day}，{time.time() - t:.1f}s")
        except Exception as e:
            print(f"  {d}: 失败 {type(e).__name__}: {str(e)[:70]}，{time.time() - t:.1f}s")
    if sse:
        ok = sorted(sse)
        print(f"\n  列：{'|'.join(sse[ok[0]].columns)}")
        print(f"  可用日期 {len(ok)} 个：{ok[0]} … {ok[-1]}")
        for d in (ok[-1], ok[0]):
            cov(sse[d], codes, f"沪市份额 {d}")

    # ---------- 2. 深交所份额：宽度阶梯，找"安全窗口" ----------
    print("\n" + "=" * 92)
    print("2. 深交所份额 fund_scale_daily_szse(start,end,symbol='ETF') —— 静默截断的窗口上限")
    print("=" * 92)
    szse_recent = None
    e = pd.Timestamp("2026-09-22")
    for months in [1, 2, 3, 4, 6, 9, 12]:
        s = e - pd.DateOffset(months=months)
        t = time.time()
        try:
            df = ak.fund_scale_daily_szse(start_date=s.strftime(YMD),
                                          end_date=e.strftime(YMD), symbol="ETF")
            nd = df["日期"].nunique() if "日期" in df else 0
            nc = df["基金代码"].nunique() if "基金代码" in df else 0
            print(f"  近 {months:>2} 个月：{len(df):>6} 行、{nd:>3} 个交易日、{nc:>4} 只代码，{time.time() - t:.1f}s")
            if months == 1 and len(df):
                szse_recent = df
        except Exception as ex:
            print(f"  近 {months:>2} 个月：失败 {type(ex).__name__}: {str(ex)[:70]}，{time.time() - t:.1f}s")
    print("  —— 历史段另测（同一接口在旧年份是否还有数据）")
    for y in [2019, 2021, 2023, 2025]:
        t = time.time()
        try:
            df = ak.fund_scale_daily_szse(start_date=f"{y}0301", end_date=f"{y}0531", symbol="ETF")
            nd = df["日期"].nunique() if "日期" in df else 0
            nc = df["基金代码"].nunique() if "基金代码" in df else 0
            print(f"  {y}-03~05：{len(df):>6} 行、{nd:>3} 个交易日、{nc:>4} 只代码，{time.time() - t:.1f}s")
        except Exception as ex:
            print(f"  {y}-03~05：失败 {type(ex).__name__}: {str(ex)[:70]}，{time.time() - t:.1f}s")
    if szse_recent is not None:
        cov(szse_recent, codes, "深市份额（近 1 个月）")

    # ---------- 3. 同花顺净值快照：覆盖度 + 折溢价量级 ----------
    print("\n" + "=" * 92)
    print("3. 同花顺净值 fund_etf_spot_ths() —— 覆盖度、以及折溢价能不能即算")
    print("=" * 92)
    ths = None
    t = time.time()
    try:
        raw = ak.fund_etf_spot_ths()
        print(f"  OK {len(raw)} 行 × {raw.shape[1]} 列，{time.time() - t:.1f}s")
        nc = [c for c in raw.columns if "最新-单位净值" in c]
        dc = [c for c in raw.columns if "交易日" in c]
        raw = raw.rename(columns={nc[0]: "nav", dc[0]: "nav_date"})
        raw["code"] = raw[code_col(raw)].astype(str).str.strip().str.zfill(6)
        raw["nav"] = pd.to_numeric(raw["nav"], errors="coerce")
        print(f"  净值列 {nc[0]}；日期列取值 {sorted(set(raw['nav_date'].astype(str)))[:3]}；"
              f"净值非空 {raw['nav'].notna().sum()} / {len(raw)}")
        inter = cov(raw, codes, "同花顺净值")
        ths = raw[["code", "nav"]].dropna().drop_duplicates("code")
        m = panel[panel["code"].isin(inter)][["code", "close"]].merge(ths, on="code")
        m = m[m["nav"] > 0]
        m["prem"] = m["close"] / m["nav"] - 1
        if len(m):
            q = m["prem"].quantile([0, .05, .25, .5, .75, .95, 1])
            print(f"  折溢价（本地 {panel['last'].max()} 收盘 / 同花顺净值 - 1，可配 {len(m)} 只）：")
            print("    " + "  ".join(f"{p:.0%}:{v:+.2%}" for p, v in q.items()))
            print(f"    |prem|>1% 占 {(m['prem'].abs() > 0.01).mean():.1%}、"
                  f">3% 占 {(m['prem'].abs() > 0.03).mean():.1%}"
                  f"（净值日与本地收盘日可能错位，故只作量级参考）")
    except Exception as e:
        print(f"  失败 {type(e).__name__}: {str(e)[:120]}，{time.time() - t:.1f}s")

    # ---------- 4. 规模 = 份额 × 净值：清盘线判据的可算性 ----------
    print("\n" + "=" * 92)
    print("4. 规模（份额×单位净值）分布 —— 对照清盘条款阈值 5000 万")
    print("=" * 92)
    parts = []
    if sse:
        s = as_shares(sse[max(sse)]).assign(mkt="SH")
        parts.append(s)
        print(f"  沪市：取 {max(sse)}，{len(s)} 只有效份额")
    if szse_recent is not None:
        z = as_shares(szse_recent).assign(mkt="SZ")
        parts.append(z)
        print(f"  深市：取近 1 个月末次记录，{len(z)} 只有效份额")
    if parts and ths is not None:
        sh = pd.concat(parts, ignore_index=True).drop_duplicates("code")
        j = sh.merge(ths, on="code", how="inner")
        j = j[(j["nav"] > 0) & (j["shares"] > 0)]
        j["aum"] = j["shares"] * j["nav"]
        j = j[j["code"].isin(codes)]
        print(f"  份额×净值可算的本池票：{len(j)} 只 / {len(codes)}（{len(j) / len(codes):.1%}）")
        if len(j):
            q = j["aum"].quantile([0, .05, .1, .25, .5, 1])
            print("    规模分位：" + "  ".join(f"{p:.0%}:{v / 1e8:.2f}亿" for p, v in q.items()))
            print(f"    <5000 万 {(j['aum'] < 5e7).sum()} 只、<2 亿 {(j['aum'] < 2e8).sum()} 只、"
                  f">50 亿 {(j['aum'] > 5e9).sum()} 只")
    else:
        print("  份额或净值没拿到 → 规模只能用日线成交额代理")

    # ---------- 5. 日线代理口径本身有多少信息 ----------
    print("\n" + "=" * 92)
    print("5. 日线代理可用度：最后一日成交额分布（分档滑点 / 连续低量剔除的底料）")
    print("=" * 92)
    amt = panel[panel["amount"] > 0]["amount"]
    q = amt.quantile([0, .05, .1, .25, .5, 1])
    print("   " + "  ".join(f"{p:.0%}:{v / 1e8:.2f}亿" for p, v in q.items()))
    for thr in [1e7, 3e7, 1e8]:
        print(f"   <{thr / 1e8:.1f} 亿：{(panel['amount'] < thr).sum()} / {len(panel)} 只")

    # ---------- 6. 剩下的缺口：IOPV / 净值历史 ----------
    print("\n" + "=" * 92)
    print("6. 缺口确认：IOPV 与净值历史有没有非东财源")
    print("=" * 92)
    t = time.time()
    try:
        df = ak.fund_etf_category_ths(symbol="ETF")
        print(f"  fund_etf_category_ths(ETF): OK {len(df)} 行 × {df.shape[1]} 列，{time.time() - t:.1f}s")
        print(f"    列：{'|'.join(df.columns)}")
        cov(df, codes, "同花顺 ETF 列表")
    except Exception as e:
        print(f"  fund_etf_category_ths: 失败 {type(e).__name__}: {str(e)[:90]}，{time.time() - t:.1f}s")


if __name__ == "__main__":
    main()
