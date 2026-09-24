# -*- coding: utf-8 -*-
"""ETF 风险字段日更采集器：份额（沪+深）与单位净值（同花顺）逐日累积成历史。

一句话：清盘线与折溢价这两项 ETF 特有风险的判据，在 09-24 之前**只有"今天"这一个
截面** —— 探源实测（`shell/probe_etf_risk_coverage_0924.py`，输出已归档为同名 .log）
给出三条硬事实：
    份额·沪  `fund_etf_scale_sse(date=)`   逐日快照，可回溯到 2018-12（覆盖本池 55.5%）
    份额·深  `fund_scale_daily_szse(...)`  逐日区间，2019-03 起有数（覆盖 44.5%，与沪市**并集 100%**）
    净值     `fund_etf_spot_ths()`         **只有最新一日**，无历史（覆盖 100%，0.8s）
所以"份额 + 净值"两条腿里，净值这条**只能从今天开始攒**。本模块就是那台攒历史的
机器：每天 ~5 秒的三次批量请求，换一份永久可回溯的 `data/risk/` 长表。

判据与公式
----------
    资产规模    A_{t,i}   = shares_{t,i} × nav_{t,i}            （元）
    净值代理    nav_{t,i} ≈ close_{t,i}                          （无净值历史的过去日期）
                          实测成立：(份额×本地前复权收盘)/(份额×净值) 的
                          P5~P95 = 0.991~1.023，中位 1.004 ⇒ 收盘是合格净值代理，
                          过去的规模能用「交易所份额 × 已有日线收盘」回算
    清盘条款    连续 CLEAR_DAYS=60 个交易日 A_{t,i} < CLEAR_LINE=5000 万元
                ⇒ 基金管理人须发起清盘/合并程序（合同条款，非交易所规则）
    折溢价      prem_{t,i} = close_{t,i} / nav_{t,i} - 1          （需净值，只能向前攒）

为什么存长表而不是宽表
----------------------
三个源的日期覆盖互不对齐（沪市月末才有、深市逐日、净值只有快照日），拼成宽表就得
先把空洞填掉，而**空洞本身就是信息**（那天这个源的份额没披露）。故每源各存一张
长表 `date,code,...`，宽矩阵由 `load_*_matrix()` 在读取时 pivot 出来，缺失保持 NaN。

只增不改（append-only）是硬约定
--------------------------------
`append_long()` 只做三件事：并入本次抓到的行、按 (date, code) 去重、按 (date, code)
排序。同一 (date, code) 再来一次时**以新抓的为准**（源方偶尔回补/更正数字），但
历史里已有的其它行一条都不会少 —— 落盘前有一道 `assert` 守着。原子写（临时文件 +
`os.replace`）：日更脚本会被 cron 打断，半截 CSV 会把后面所有天的读取都带崩。

用法
----
    cd etf/v1/src && /usr/bin/python3.10 data/fetch_etf_risk_panel.py --daily
    # 回补份额历史（净值没有历史可回补，跳过是设计如此不是漏抓）
    ... data/fetch_etf_risk_panel.py --backfill-shares 2019-01-01 2026-09-24
    ... data/fetch_etf_risk_panel.py --report          # 只读，看攒到哪了

坑（都实测踩过，写在这是为了让下一个人不必再踩）
------------------------------------------------
1. 沪市 `date=` 只吃 `YYYYMMDD`。传带横杠的 `2026-09-22` 会在 akshare 内部抛
   KeyError，长得**和接口停机一模一样** —— 本模块统一在入口处把日期压成 8 位。
2. 深市区间静默失败：窗口 ≥9 个月返回 **0 行**（不报错），6 个月返回**恰好 65535
   行**（2^16−1，被服务端截断）。⇒ 一次最多取 3 个月，本模块的 `_month_windows()`
   就是把这条规则写死成代码。
3. 代码列宽度不一（`159003` 与 `sh159003` 混着出现过），一律 `zfill(6)` 后再比较；
   与本池 `data/universe_all/*_daily.csv` 对代码时要剥掉 `_daily` 后缀。
"""

import argparse
import datetime as dt
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _bootstrap  # noqa: F401  裸模块名导入（from config import ...）的前提

from config import (RISK_DIR, RISK_NAV_THS, RISK_SHARES_SSE,   # noqa: E402
                    RISK_SHARES_SZSE, UNIVERSE_ALL_DIR)

# ========== 落盘位置与表结构（路径全部来自 config，ETF_RISK_DIR 可整目录改走） =====
# 采集器**只写这三张长表**：规模、折溢价、清盘线都是派生量，一律现算不落盘
# （要用当日收盘价与净值，存成文件就是存一份会过期的真相）。派生逻辑在
# `etf_admission.py`（裁判链与日更共用），本模块只负责把源攒下来。
SHARES_SSE_OUT = RISK_SHARES_SSE
SHARES_SZSE_OUT = RISK_SHARES_SZSE
NAV_THS_OUT = RISK_NAV_THS

# 采集窗口/阈值里凡是**判据**的都归 `etf_admission.py` 所有（清盘线是它的
# `CLEAR_DAYS`/`CLEAR_LINE`）。本模块只留源协议层面的常数 —— 否则同一个数在两处
# 各写一遍，漂移了没人知道，这是股票线用一次返工换来的教训。
SZSE_MAX_MONTHS = 3        # 深市单次区间的上限，超过就静默返 0 行 / 截断
SSE_DATE_FMT = "%Y%m%d"    # 沪市只吃这个格式，见模块 docstring 坑 1

SHARES_KEY = ["date", "code"]
SHARES_COLS = ["date", "code", "mkt", "shares"]
NAV_COLS = ["date", "code", "nav", "nav_date", "nav_growth"]


# ==================== 长表读写 ====================

def read_long(path):
    """读一张长表；文件不存在就返回带正确列与 dtype 的空表（调用方不必判存在）。"""
    dtypes = {"date": "datetime64[ns]", "code": "object", "mkt": "object",
              "shares": "float64", "nav": "float64", "nav_date": "object",
              "nav_growth": "float64"}
    cols = (SHARES_COLS if "shares" in os.path.basename(path) else NAV_COLS)
    if not os.path.exists(path):
        return pd.DataFrame({c: pd.Series(dtype=dtypes[c]) for c in cols})
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def append_long(path, new, key=SHARES_KEY, cols=None):
    """把本次抓取并入长表，**同一 key 以新值覆盖、其余历史一条不少**，原子落盘。

        out = dedupe_by_key(concat(old, new)) ，且 len(out) >= len(old)
    覆盖而非丢弃的理由：交易所会回补/更正某一天的份额；但"这次没抓到"绝不能等于
    "那天没有" —— 所以只覆盖本次真正抓到的 key，其余原样留着。这道不变式由下面的
    assert 守住：任何一次运行都不可能让历史变短。
    """
    if new is None or len(new) == 0:
        return read_long(path)
    new = new.copy()
    new["date"] = pd.to_datetime(new["date"])
    new["code"] = new["code"].astype(str).str.zfill(6)
    if cols:
        new = new[[c for c in cols if c in new.columns]]
    old = read_long(path)
    out = pd.concat([old, new], ignore_index=True)
    out = out.drop_duplicates(subset=key, keep="last")
    out = out.sort_values(key).reset_index(drop=True)
    assert len(out) >= len(old), f"{path}: 并入后比原表短，历史被改写"
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    return out


def has_table(path):
    """这张长表是否已有至少一行数据（消费方用它决定"跳过"还是"报错"）。

    只读前两行就返回：55 万行的深市份额表用 `read_long` 判空要白读一遍磁盘。
    表头存在但没有数据行 —— 那种状态真实存在（抓到 0 行的那次运行），必须算"没有"。
    """
    if not os.path.exists(path):
        return False
    with open(path) as f:
        f.readline()
        return bool(f.readline().strip())


def load_shares_matrix():
    """两张份额长表 → 宽矩阵（date × code，单位：份）。沪市是月末/抽样日、深市逐日。"""
    frames = []
    for p in (SHARES_SSE_OUT, SHARES_SZSE_OUT):
        d = read_long(p)
        if len(d):
            frames.append(d)
    if not frames:
        return pd.DataFrame()
    s = pd.concat(frames, ignore_index=True).drop_duplicates(SHARES_KEY, keep="last")
    return s.pivot_table(index="date", columns="code", values="shares", aggfunc="last")


def load_nav_matrix():
    """净值长表 → 宽矩阵（date × code，单位：元/份）。只有抓到快照的那些日子有行。"""
    d = read_long(NAV_THS_OUT)
    if not len(d):
        return pd.DataFrame()
    return d.pivot_table(index="date", columns="code", values="nav", aggfunc="last")


# ==================== 抓取 ====================

def _yyyymmdd(d):
    """任意可解析的日期 → 沪市要的 8 位数字串（坑 1）。"""
    return pd.Timestamp(d).strftime(SSE_DATE_FMT)


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _col(df, kw):
    """按关键字找列名：交易所改列名（加前缀/改词）时只想改一处映射，不想改判据。"""
    for c in df.columns:
        if kw in str(c):
            return c
    raise KeyError(f"列 {kw} 不在 {list(df.columns)} 里")


def fetch_shares_sse(date):
    """上交所某交易日的全部 ETF 份额快照 → (date, code, mkt='SH', shares)。

    "该日无披露"的实现方式是抛 KeyError 而不是回空表（akshare 在空 DataFrame 上做
    列重索引，实测 2026-09-24 尚未发布时报 `KeyError: "None of [Index(['序号', ...`，
    而已发布的 09-23 正常返回 912 行）。这里把它翻译成空表，好让调用方往前找一天；
    列名映射失败（`_col` 的 KeyError）**不**在此吞掉 —— 那是接口真改了，必须炸出来。
    """
    import akshare as ak
    try:
        df = ak.fund_etf_scale_sse(date=_yyyymmdd(date))
    except KeyError as ex:
        print(f"  [沪 {date}] 该日无披露：{str(ex)[:60]}")
        return pd.DataFrame(columns=SHARES_COLS)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=SHARES_COLS)
    out = pd.DataFrame({
        "date": pd.Timestamp(date).normalize(),
        "code": df[_col(df, "基金代码")].astype(str).str.zfill(6),
        "mkt": "SH",
        "shares": _num(df[_col(df, "份额")]),
    })
    return out.dropna(subset=["shares"]).query("shares > 0")


def fetch_shares_szse(start, end):
    """深交所区间内的逐日 ETF 份额 → (date, code, mkt='SZ', shares)。窗口 ≤3 个月。"""
    import akshare as ak
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    if (e - s).days > SZSE_MAX_MONTHS * 31:
        raise ValueError(f"深市单次窗口上限 {SZSE_MAX_MONTHS} 个月，"
                         f"{s.date()}~{e.date()} 会被静默截断")
    df = ak.fund_scale_daily_szse(start_date=s.strftime("%Y%m%d"),
                                  end_date=e.strftime("%Y%m%d"), symbol="ETF")
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=SHARES_COLS)
    out = pd.DataFrame({
        "date": pd.to_datetime(df[_col(df, "日期")].astype(str)),
        "code": df[_col(df, "基金代码")].astype(str).str.zfill(6),
        "mkt": "SZ",
        "shares": _num(df[_col(df, "份额")]),
    })
    return out.dropna(subset=["shares"]).query("shares > 0")


def fetch_nav_ths():
    """同花顺净值快照 → (date, code, nav, nav_date, nav_growth)。无历史可回溯。"""
    import akshare as ak
    df = ak.fund_etf_spot_ths()
    nav_c, date_c = _col(df, "最新-单位净值"), _col(df, "最新-交易日")
    growth_c = next((c for c in df.columns if "增长率" in str(c)), None)
    d = pd.to_datetime(df[date_c].astype(str), errors="coerce")
    out = pd.DataFrame({
        # 以净值自己的日期为键：抓到旧净值就存成旧日期，绝不冒充今天更新过
        "date": d.dt.normalize(),
        "code": df["基金代码"].astype(str).str.zfill(6),
        "nav": _num(df[nav_c]),
        "nav_date": df[date_c].astype(str),
        "nav_growth": _num(df[growth_c]) if growth_c else pd.NaT,
    })
    return out.dropna(subset=["date", "nav"]).query("nav > 0")


# ==================== 补历史 ====================

def _month_windows(start, end, months=SZSE_MAX_MONTHS):
    """把区间切成不超过 `months` 个月的连续窗口（深市硬上限，见坑 2）。"""
    s, e = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    out = []
    cur = s
    while cur <= e:
        nxt = min(cur + pd.DateOffset(months=months) - pd.Timedelta(days=1), e)
        out.append((cur, nxt))
        cur = nxt + pd.Timedelta(days=1)
    return out


def _month_ends(start, end):
    """区间内每个月的最后一个日历日 —— 沪市回补的取样点（那天是否披露由源决定）。"""
    s, e = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    out, ym = [], (s.year, s.month)
    while pd.Timestamp(year=ym[0], month=ym[1], day=1) <= e:
        nxt = (pd.Timestamp(year=ym[0] + (ym[1] == 12),
                            month=1 if ym[1] == 12 else ym[1] + 1, day=1))
        out.append(min(nxt - pd.Timedelta(days=1), e))
        ym = (nxt.year, nxt.month)
    return out


def backfill_shares(start, end, sleep=0.3):
    """回补份额历史：深市按 ≤3 个月区间、沪市按月末取样。净值不在此列（无历史）。"""
    t0 = time.time()
    n_sz = n_sh = 0
    wins = _month_windows(start, end)
    for i, (s, e) in enumerate(wins, 1):
        try:
            df = fetch_shares_szse(s, e)
            append_long(SHARES_SZSE_OUT, df)
            n_sz += len(df)
            print(f"  [深 {i}/{len(wins)}] {s.date()}~{e.date()} 抓到 {len(df)} 行")
        except Exception as ex:
            print(f"  [深 {i}/{len(wins)}] {s.date()}~{e.date()} 失败 "
                  f"{type(ex).__name__}: {str(ex)[:70]}")
        time.sleep(sleep)
    dates = _month_ends(start, end)
    for i, d in enumerate(dates, 1):
        try:
            df = fetch_shares_sse(d)
            if len(df):
                append_long(SHARES_SSE_OUT, df)
                n_sh += len(df)
                print(f"  [沪 {i}/{len(dates)}] 月末 {d.date()} 抓到 {len(df)} 行")
            else:
                print(f"  [沪 {i}/{len(dates)}] 月末 {d.date()} 无披露（非交易日/未发布）")
        except Exception as ex:
            print(f"  [沪 {i}/{len(dates)}] {d.date()} 失败 "
                  f"{type(ex).__name__}: {str(ex)[:70]}")
        time.sleep(sleep)
    print(f"[回补] 深 {n_sz} 行、沪 {n_sh} 行，用时 {time.time() - t0:.0f}s")
    return n_sz, n_sh


def collect_daily(date=None, sleep=0.0):
    """日更一次：深市取「近 3 个月窗口」（自动覆盖今天，且能吃到源方回补）、
    沪市取「指定日/今天」、净值取当前快照。返回三源各新增多少行。

    沪市单独传日期是因为它只有快照语义：`date=` 给哪天就回哪天，没有区间接口。
    """
    today = pd.Timestamp(date or dt.date.today()).normalize()
    t0 = time.time()
    out = {}
    try:
        sz = fetch_shares_szse(today - pd.DateOffset(months=2), today)
        out["深市份额"] = len(append_long(SHARES_SZSE_OUT, sz))
    except Exception as ex:
        out["深市份额"] = f"失败 {type(ex).__name__}"
    try:
        sh = fetch_shares_sse(today)
        if not len(sh):                                  # 当日尚未发布就往前找一天
            for back in range(1, 6):
                sh = fetch_shares_sse(today - pd.Timedelta(days=back))
                if len(sh):
                    break
        n = len(append_long(SHARES_SSE_OUT, sh))
        out["沪市份额"] = (f"{n} 行@{sh['date'].iloc[0].date()}" if n
                          else "0（近 6 日均无披露）")
    except Exception as ex:
        out["沪市份额"] = f"失败 {type(ex).__name__}"
    try:
        out["净值"] = len(append_long(NAV_THS_OUT, fetch_nav_ths()))
    except Exception as ex:
        out["净值"] = f"失败 {type(ex).__name__}"
    print(f"[日更 {today.date()}] " + "  ".join(f"{k}={v}" for k, v in out.items())
          + f"，用时 {time.time() - t0:.1f}s")
    return out


# ==================== 只读体检 ====================

def pool_codes():
    """本池代码全集（剥掉 `_daily` 后缀）：覆盖度一律对着它算，不对着源报的只数。"""
    files = [f for f in os.listdir(UNIVERSE_ALL_DIR) if f.endswith("_daily.csv")] \
        if os.path.isdir(UNIVERSE_ALL_DIR) else []
    return {f[:-len("_daily.csv")] for f in files}


def report():
    """攒到哪了：逐表的行数/日期跨度/对本池覆盖度，以及折溢价与清盘线的可算性。"""
    codes = pool_codes()
    print("=" * 78)
    print(f"ETF 风险字段体检 · 本池 {len(codes)} 只 · 目录 {RISK_DIR}")
    print("=" * 78)
    for p in (SHARES_SSE_OUT, SHARES_SZSE_OUT, NAV_THS_OUT):
        d = read_long(p)
        name = os.path.basename(p)
        if not len(d):
            print(f"  {name:<18s} 空表（还没抓过）")
            continue
        cov = len(set(d["code"]) & codes)
        print(f"  {name:<18s} {len(d):>8d} 行 | {d['date'].min().date()}"
              f" ~ {d['date'].max().date()}（{d['date'].nunique():>4d} 个日期）"
              f" | 覆盖本池 {cov}/{len(codes)} = {cov / max(1, len(codes)):.1%}")
    sm, nm = load_shares_matrix(), load_nav_matrix()
    if len(sm) and len(nm):
        both = sm.index.intersection(nm.index)
        print(f"\n  份额×净值同日可算的日期：{len(both)} 个"
              f"（⇒ 真实口径的规模与折溢价能画多长的曲线）")
    if len(nm):
        print(f"  净值已攒 {nm.index.nunique()} 个交易日 ⇒ 折溢价从 {nm.index.min().date()}"
              f" 起可用；清盘线要攒满 etf_admission.CLEAR_DAYS 个交易日才读得出连续段")
    else:
        print("\n  [提示] 还没有净值行，先跑 --daily")
    return 0


def main():
    ap = argparse.ArgumentParser(description="ETF 份额/净值日更采集器（只增不改）")
    ap.add_argument("--daily", action="store_true", help="抓今天一次（cron 入口）")
    ap.add_argument("--date", default=None, help="覆盖 --daily 的目标日期 YYYY-MM-DD")
    ap.add_argument("--backfill-shares", nargs=2, metavar=("FROM", "TO"),
                    help="回补份额历史（净值无历史可回补）")
    ap.add_argument("--report", action="store_true", help="只读体检")
    args = ap.parse_args()
    if not (args.daily or args.backfill_shares or args.report):
        ap.print_help()
        return 0
    if args.backfill_shares:
        backfill_shares(*args.backfill_shares)
    if args.daily or args.date:
        collect_daily(args.date)
    return report()


if __name__ == "__main__":
    sys.exit(main())
