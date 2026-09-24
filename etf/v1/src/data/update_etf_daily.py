# -*- coding: utf-8 -*-
"""ETF 线日线日更：把全市场镜像、主线池缓存推到最新交易日，并按市场报告新鲜度

为什么需要这一步
----------------
本线的日线数据有三个面，此前各自为政、各自停在不同的一天（09-24 实测）：
    data/universe_all/   871 只全市场镜像，沪市止于 09-22、深市止于 09-21
    data/cache/*_daily   主线池 20 只的逐只缓存，止于 09-18
    data/risk/           份额/净值长表（`fetch_etf_risk_panel.py`），止于 09-23
而 `DataLoader.load()` 见到缓存就**原样返回**，所以回测会一直算到 09-18 那条
线上而无人出声——日历不往前走，所有指标都在算同一截历史（股票线 09-24 的同款
坑，见 `stock/v1/src/data/update_qlib_bin_daily.py` 的开头）。本模块把这三面
一条命令推齐，并留下按市场的滞后读数（#18 的量化依据）。

追加的判据：重叠段必须逐格对得上
--------------------------------
镜像里存的是 akshare **前复权**（qfq）价，而前复权锚在**最新一天**：中途一次
分红就会把整条历史重新缩放。新浪源是**不复权**、腾讯源口径又与东财不同（实测
510300 在腾讯重叠段最大相对差 0.41，是整段缩放而非单日跳空）。所以不能"拉到数
据就往后贴"——那样贴出来的台阶是永久的，会污染之后每一次回测。规则：

    取源后先在【与镜像重叠的日期】上比收盘价，
    rel = max_t |close_src,t / close_mirror,t − 1|        （t ∈ 重叠日期）
    rel ≤ APPEND_TOL（1e-6）⇒ 同口径，只贴 last_date 之后的新行
    rel >  APPEND_TOL        ⇒ 换下一个源；全都不合格就跳过这只并登记

只贴新行、不改动历史任何一格；写完校验「新文件行数 ≥ 旧文件行数」且旧日期集合
是新日期集合的子集，不满足就退回原文件（原子写：临时文件 + `os.replace`，
日更会被 cron 打断，半截 CSV 会把之后每一天的读取都带崩）。

用法（工作区根目录，全部走 /usr/bin/python3.10）
------------------------------------------------
    cd etf/v1/src && /usr/bin/python3.10 data/update_etf_daily.py            # 三面推齐
    ... data/update_etf_daily.py --report                          # 只读，看滞后分布
    ... data/update_etf_daily.py --codes 510300 159915 --verbose  # 冒烟
    ... data/update_etf_daily.py --skip-risk --dump-bin            # 顺带重导 qlib bin
"""

import argparse
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _bootstrap  # noqa: F401  裸模块名导入（from config import ...）的前提

from config import (  # noqa: E402
    CACHE_DIR, DATA_SOURCES, UNIVERSE_ALL_DIR,
    RISK_NAV_THS, RISK_SHARES_SSE, RISK_SHARES_SZSE,
)
from data_loader import DataLoader, _last_date  # noqa: E402

APPEND_TOL = 1e-6        # 重叠段收盘相对差容差：小于它就是同一个复权锚
TAIL_CHECK_BARS = 60     # 口径校验看重叠段的最后这么多格（分红会挪整段）


def _read_csv(path):
    df = pd.read_csv(path)
    col = "date" if "date" in df.columns else "datetime"
    df[col] = pd.to_datetime(df[col])
    return df.sort_values(col).drop_duplicates(subset=[col], keep="last")


def append_tail(path, new_df, tail_check_bars=TAIL_CHECK_BARS):
    """把 new_df 里晚于现有末日的行贴进 CSV；同口径校验不过就拒绝。

    返回 (状态, 贴入行数, 重叠最大相对差)。状态：
      ok=贴入 / uptodate=源没有更新的行 / mismatch=重叠段对不上（源与镜像不同
      复权锚）/ empty=源返回空。任何情况下**都不改动已有行**。"""
    old = _read_csv(path)
    tcol = old.columns[0]
    if new_df is None or new_df.empty:
        return "empty", 0, float("nan")
    new_df = new_df.copy()
    if not isinstance(new_df.index, pd.DatetimeIndex):
        new_df = new_df.reset_index()
        if tcol not in new_df.columns:
            return "empty", 0, float("nan")
    else:
        new_df = new_df.reset_index()
    new_df[tcol] = pd.to_datetime(new_df[tcol])
    overlap = old[old[tcol].isin(set(new_df[tcol]))]
    if overlap.empty:
        return "empty", 0, float("nan")
    # 只比尾部：整段比对会把「上市首日不同源的价格差」也算进来，那不是口径问题
    tail = overlap.tail(tail_check_bars)
    src_close = new_df.set_index(tcol)["close"]
    rel = (float(np.abs(tail["close"].values
                        / src_close.loc[tail[tcol]].values - 1).max())
           if len(tail) else float("nan"))
    if not (rel <= APPEND_TOL):
        return "mismatch", 0, rel
    fresh = new_df[new_df[tcol] > old[tcol].max()]
    if fresh.empty:
        return "uptodate", 0, rel
    cols = [c for c in old.columns if c in fresh.columns]
    merged = pd.concat([old, fresh[cols]], ignore_index=True) \
              .sort_values(tcol).drop_duplicates(subset=[tcol], keep="last")
    if len(merged) < len(old) or not set(old[tcol]) <= set(merged[tcol]):
        return "mismatch", 0, rel          # 行数/日期集合退化，宁可不写
    tmp = path + ".tmp"
    merged.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)
    # 报"净增行"而不是源行数：源自身带重复日（实测尾部会重复一格）时虚报会误导
    return "ok", len(merged) - len(old), rel


def _refresh_one(args):
    """子进程入口：一只代码贴一次。返回 (code, status, added, rel, src)"""
    code, path, sources = args
    loader = DataLoader(freq="daily")
    tried = []
    for src in sources:
        try:
            df = loader._load_daily(code, src)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        status, added, rel = append_tail(path, df)
        if status in ("ok", "uptodate"):
            return code, status, added, rel, src
        tried.append(f"{src}(rel={rel:.1e})")
    return code, "mismatch", 0, float("nan"), "|".join(tried) or "no-source"


def market_of(code: str) -> str:
    """沪市基金 5/6/9 开头，其余归深市（与 data_loader._sina_symbol 同规则）"""
    return "沪" if str(code).startswith(("5", "6", "9")) else "深"


def refresh_mirror(codes=None, workers=4, sources=None, verbose=False):
    """全市场镜像 data/universe_all/ 逐只追加，返回统计 dict"""
    sources = list(sources or DATA_SOURCES["daily"])
    files = sorted(f for f in os.listdir(UNIVERSE_ALL_DIR)
                   if f.endswith("_daily.csv"))
    todo = [f for f in files
            if codes is None or f.split("_")[0] in set(codes)]
    jobs = [(f.split("_")[0], os.path.join(UNIVERSE_ALL_DIR, f), sources)
            for f in todo]
    return _run_jobs(jobs, workers, "全市场镜像", verbose)


def refresh_pool_cache(codes=None, workers=4, sources=None, verbose=False):
    """主线池逐只缓存 data/cache/<code>_daily.csv 同步追加。

    这些文件是 `DataLoader.load()` 的第一优先级，日更漏了它们就等于回测停在
    旧的一天（09-24 实测 20 只全止于 09-18）。"""
    sources = list(sources or DATA_SOURCES["daily"])
    files = sorted(f for f in os.listdir(CACHE_DIR) if f.endswith("_daily.csv"))
    todo = [f for f in files
            if codes is None or f.split("_")[0] in set(codes)]
    jobs = [(f.split("_")[0], os.path.join(CACHE_DIR, f), sources)
            for f in todo]
    return _run_jobs(jobs, workers, "主线池缓存", verbose)


def _run_jobs(jobs, workers, label, verbose=False):
    stat, rel_max, bad, added_total = {}, 0.0, [], 0
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=max(workers, 1)) as ex:
        for i, (code, st, n, rel, src) in enumerate(
                ex.map(_refresh_one, jobs), 1):
            stat[st] = stat.get(st, 0) + 1
            added_total += n
            if st == "mismatch":
                bad.append(f"{code}:{src}")
            elif not math.isnan(rel) and rel > rel_max:
                rel_max = rel
            if verbose and st == "ok":
                print(f"    {code} +{n} 行（源 {src}）")
            if i % 200 == 0 or i == len(jobs):
                print(f"    {label} {i}/{len(jobs)}，用时 "
                      f"{time.time() - t0:.0f}s，失败 {len(bad)}")
    out = {"n": len(jobs), "stat": stat, "added": added_total,
           "rel_max": rel_max, "bad": bad, "secs": time.time() - t0}
    print(f"  [{label}] {len(jobs)} 只 "
          + " / ".join(f"{k}={v}" for k, v in sorted(stat.items()))
          + f"；贴入 {added_total} 行，重叠残差上界 {rel_max:.1e}，"
            f"用时 {out['secs']:.0f}s")
    if bad:
        print(f"  ⚠️ {len(bad)} 只因口径对不上未追加："
              f"{bad[:10]}{'…' if len(bad) > 10 else ''}")
    return out


def freshness_report() -> pd.DataFrame:
    """只读体检：按市场统计镜像/池缓存/风险长表各自的末日与落后天数。"""
    rows = []
    batch = DataLoader(freq="daily")._mirror_batch_end()
    end = pd.Timestamp(batch) if batch else pd.Timestamp.today().normalize()
    for label, folder, suffix in (("全市场镜像", UNIVERSE_ALL_DIR, "_daily.csv"),
                                  ("主线池缓存", CACHE_DIR, "_daily.csv")):
        for mkt in ("沪", "深"):
            lasts = [pd.Timestamp(_last_date(os.path.join(folder, f)))
                     for f in os.listdir(folder)
                     if f.endswith(suffix)
                     and market_of(f.split("_")[0]) == mkt]
            if not lasts:
                continue
            lag = (end - max(lasts)).days
            rows.append({
                "数据面": label, "市场": mkt, "只数": len(lasts),
                "末日": max(lasts).date().isoformat(),
                "落后交易日": int(len(pd.bdate_range(max(lasts) + pd.Timedelta(days=1),
                                                     end))),
                "备注": f"批末 {batch} 落后 {lag} 天" if label == "全市场镜像" else "",
            })
    try:
        from fetch_etf_risk_panel import read_long
        for label, path in (("份额·沪", RISK_SHARES_SSE),
                            ("份额·深", RISK_SHARES_SZSE),
                            ("单位净值", RISK_NAV_THS)):
            df = read_long(path)
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            rows.append({"数据面": f"风险面板·{label}", "市场": "-",
                         "只数": int(df["code"].nunique()),
                         "末日": str(pd.Timestamp(df["date"].max()).date()),
                         "落后交易日": int(len(pd.bdate_range(
                             pd.Timestamp(df["date"].max())
                             + pd.Timedelta(days=1), end))),
                         "备注": f"{len(df)} 行长表"})
    except Exception as e:
        print(f"  ⚠️ 风险面板未纳入体检: {type(e).__name__}: {e}")
    return pd.DataFrame(rows)


def print_report():
    df = freshness_report()
    with pd.option_context("display.unicode.east_asian_width", True,
                           "display.width", 160):
        print(df.to_string(index=False))
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="*", default=None,
                    help="只处理这些代码（冒烟）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--source", nargs="*", default=None,
                    help=f"覆盖取数源顺序，默认 {DATA_SOURCES['daily']}")
    ap.add_argument("--skip-risk", action="store_true",
                    help="不跑份额/净值日更采集器")
    ap.add_argument("--dump-bin", action="store_true",
                    help="顺带重导 qlib bin（RD-Agent 侧用）")
    ap.add_argument("--report", action="store_true", help="只读体检后退出")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.report:
        print_report()
        return 0

    print(f"===== ETF 日线日更 | 源顺序 {args.source or DATA_SOURCES['daily']} =====")
    t0 = time.time()
    m = refresh_mirror(args.codes, args.workers, args.source, args.verbose)
    c = refresh_pool_cache(args.codes, args.workers, args.source, args.verbose)
    if not args.skip_risk:
        try:
            from fetch_etf_risk_panel import collect_daily
            collect_daily()
        except Exception as e:
            print(f"  ⚠️ 风险面板日更失败（不阻断日线日更）: "
                  f"{type(e).__name__}: {e}")
    if args.dump_bin:
        # dump_qlib_bin.main() 自带 argparse，必须另起进程，否则会吃到本脚本的 argv
        r = subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "dump_qlib_bin.py")],
            capture_output=True, text=True)
        tail = (r.stdout or "").strip().splitlines()
        print(tail[-1] if r.returncode == 0 and tail
              else f"  ⚠️ qlib bin 重导失败: {(r.stderr or '')[-200:]}")

    print("\n----- 日更后新鲜度 -----")
    print_report()
    print(f"\n[完成] 总用时 {time.time() - t0:.0f}s｜镜像贴入 {m['added']} 行、"
          f"池缓存 {c['added']} 行｜口径不合 "
          f"{len(m['bad']) + len(c['bad'])} 只")
    return 0


if __name__ == "__main__":
    sys.exit(main())
