# -*- coding: utf-8 -*-
"""拉全市场 ETF 日线到 `data/universe_all/`，只给 qlib dump / RD-Agent(Q) 循环用

为什么要单独一份：主线轮动池是 `ETF_FILTER.max_count` 截断后的 20 只「每指数最早
上市代表」，够做组合轮动，但喂给 RD-Agent 不够 —— qlib 的 running 步是横截面回归
（Alpha158 + LGB），20 只池里日均只有约 7.7 只当日有行情，截面 IC 基本被噪声淹没。
全市场（剔货币/短融/同业存单/理财与 511 债券段后约 1.6 千只）逐只拉日线实测
0.38 秒/只、串行约 10 分钟，成本可接受，于是循环那侧改用全市场池。

本目录不进主线：轮动池读 `data/cache/etf_universe_cache.csv`，`data/universe_all/`
只被 `dump_qlib_bin.py` 扫（全仓库仅此一处 glob `*_daily.csv`）。

用法：
    cd etf/v1/src && /usr/bin/python3.10 data/fetch_etf_universe_all.py
    # 默认即断点续拉：目录里已存在且非空的 CSV 直接跳过，失败标的自动重试 3 轮
    ... data/fetch_etf_universe_all.py --limit 50        # 冒烟
"""

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _bootstrap  # noqa: F401  裸模块名导入（from config import ...）的前提

from config import ETF_FILTER, UNIVERSE_ALL_DIR, BACKTEST_START
from data_loader import DataLoader
from etf_universe import ETFUniverse

# 与回测侧同构的列，dump 直接按列名读，不做二次改名
COLS = ["open", "high", "low", "close", "volume", "amount"]
MIN_BARS = 60          # 少于 60 根 K 线的基金没资格进 bin（rolling 窗都填不满）


def _mature(code: str, df: pd.DataFrame) -> bool:
    """成立满 min_list_days（默认 365 天）才入池：新基金建仓期收益不代表有效域"""
    if len(df) < MIN_BARS:
        return False
    age_days = (pd.Timestamp.now().normalize()
                - pd.Timestamp(df.index[0]).normalize()).days
    return age_days >= ETF_FILTER.get("min_list_days", 365)


def candidate_codes() -> list:
    """全市场现货列表 → 与主线同源的过滤规则（关键词/代码段/流动性）"""
    df = ETFUniverse().fetch_all()
    name = df["name"].astype(str)
    keep = pd.Series(True, index=df.index)
    for kw in ETF_FILTER["exclude_keywords"]:
        keep &= ~name.str.contains(kw, na=False)
    for pfx in ETF_FILTER.get("exclude_prefixes", []):
        keep &= ~df["code"].str.startswith(pfx)
    keep &= df["amount"] >= ETF_FILTER["min_avg_amount"]
    df = df[keep]
    print(f"  候选：全市场 {len(df)} 只（已剔货币/债/理财关键词、"
          f"{ETF_FILTER.get('exclude_prefixes')} 段、"
          f"成交额 < {ETF_FILTER['min_avg_amount']:.0e}）")
    return df["code"].tolist()


def fetch_one(code: str) -> tuple:
    """单只：东财→新浪→腾讯降级拉日线并落 CSV，返回 (code, 状态, 行数)

    状态含义：cached=本目录已有 / ok=新落盘 / immature=成立未满 1 年 /
    short=K 线不足 / empty=三个源都没数据 / error=抛异常"""
    path = os.path.join(UNIVERSE_ALL_DIR, f"{code}_daily.csv")
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return code, "cached", 0
    loader = DataLoader(freq="daily")
    for src in ("em", "sina", "tx"):
        try:
            df = loader._load_daily(code, src)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df.loc[BACKTEST_START:]
        if not _mature(code, df):
            return code, ("short" if len(df) < MIN_BARS else "immature"), len(df)
        out = df[[c for c in COLS if c in df.columns]].copy()
        out.index.name = "date"
        out.to_csv(path, encoding="utf-8-sig")
        return code, "ok", len(out)
    return code, "empty", 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4,
                    help="并发进程数（东财会限流，>6 收益转负）")
    ap.add_argument("--limit", type=int, default=0, help="只拉前 N 只（冒烟）")
    ap.add_argument("--rounds", type=int, default=3,
                    help="整池跑完后对失败标的的重试轮数")
    args = ap.parse_args()

    os.makedirs(UNIVERSE_ALL_DIR, exist_ok=True)
    codes = candidate_codes()
    if args.limit:
        codes = codes[:args.limit]
    todo = [c for c in codes
            if not (os.path.exists(os.path.join(UNIVERSE_ALL_DIR,
                                                f"{c}_daily.csv"))
                    and os.path.getsize(os.path.join(UNIVERSE_ALL_DIR,
                                                     f"{c}_daily.csv")) > 1024)]
    print(f"  目标目录 {UNIVERSE_ALL_DIR}：待拉 {len(todo)}/{len(codes)} 只，"
          f"{args.workers} 进程")
    t0 = time.time()
    stat, failed = {}, []
    for rnd in range(args.rounds):
        batch = todo if rnd == 0 else failed
        if not batch:
            break
        if rnd:
            wait = 30 * rnd
            print(f"  第 {rnd + 1} 轮重试 {len(batch)} 只，先歇 {wait}s 避限流")
            time.sleep(wait)
        failed = []
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, (code, st, n) in enumerate(ex.map(fetch_one, batch), 1):
                stat[st] = stat.get(st, 0) + 1
                if st in ("error", "empty"):
                    failed.append(code)
                if i % 100 == 0 or i == len(batch):
                    print(f"    {i}/{len(batch)} 已处理，"
                          f"用时 {time.time() - t0:.0f}s，失败 {len(failed)}")
    files = len([f for f in os.listdir(UNIVERSE_ALL_DIR)
                 if f.endswith("_daily.csv")])
    print(f"[完成] 目录内日线 {files} 只；本轮 "
          + " / ".join(f"{k}={v}" for k, v in sorted(stat.items()))
          + (f"；仍失败 {len(failed)} 只：{failed[:10]}…" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
