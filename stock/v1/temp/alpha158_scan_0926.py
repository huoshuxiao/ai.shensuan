# -*- coding: utf-8 -*-
"""选项C+B 第二步：Alpha158 全市场扫描的分块驱动（8 条/批 × 20 批，断点续跑）

为什么要分块而不是 157 条一口气扫：单条最贵的 `rank` 实测 211s、一轮 8 条批次约 900s，
157 条合起来 4~5 小时。一把梭的风险是任何一次 OOM / 断电就全丢；8 条一批、**每批落一次盘**
⇒ 崩了最多丢一批（15 分钟），而每批多付的 `load_panel` 只要 9~13s（20 批共 ~4 分钟，
占总量 1.3%）⇒ 分块几乎不要钱。范式抄 `etf/v1/src/run_etf_factor_eval.py:133`。

尺子只有环 1 那一份：面板、前向收益、逐日截面 IC、四窗口「稳不稳」统计全部 import，
**没有第二份实现**。
- `load_panel` / `evaluate` ← `run_ashare_factor_eval.py:65/:137`
- `stability_stats` / `check_same_ruler` ← `run_ashare_rolling_ic.py:137/:209`

一次扫描同时出两张表（这是 ㉝ 里量出来的免费午餐）：`evaluate(..., series_sink=)`
在扫面板那一遍里就把逐日 IC 序列留下来 ⇒ 样本内 IC 和「稳不稳」共用同一遍，
不必再扫第二次（第二次实测 698~738s ≈ 再扫一遍面板）。

判据：每批跑完立刻过 **判据 A**（同一次运行内，序列求平均必须逐位等于环 1 当场算的
指标行，|差| ≤ 1e-15；它 raise SystemExit ⇒ 本批不落盘，下一批重跑）。
**读数 B** 只在「本批因子名与环 1 归档有交集」时才念得出来，Alpha158 的 157 条花名
与在库 21 条**完全不重名** ⇒ 本扫描给不出 B，这是预期的空集而不是失败。

只写自己目录下的新文件（`stock/v1/temp/tmp_alpha158/scan/`），**不碰任何权威产物**：
`ashare_factor_eval.csv` / 三个 `ashare_rolling*.csv` / factors.json 一律只读。
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "..", "stock", "v1", "src"))
sys.path.insert(0, SRC)
os.chdir(SRC)

from run_ashare_factor_eval import load_panel, evaluate          # noqa: E402
from run_ashare_rolling_ic import stability_stats, check_same_ruler  # noqa: E402

EXPR_JSON = os.path.join(HERE, "tmp_alpha158", "alpha158_exprs_0926.json")
OUT_BASE = os.path.join(HERE, "tmp_alpha158", "scan")
# 抽样跑（--stride>0）的读数**不是全市场口径**，绝不能和全市场产物混在一棵目录里 ——
# 否则 --resume 会把 299 只的数当成已完成、永久跳过那几条。所以目录按 stride 分开。
OUT_DIR = OUT_BASE
METRICS = os.path.join(OUT_DIR, "alpha158_metrics.csv")
STABILITY = os.path.join(OUT_DIR, "alpha158_stability.csv")
COST = os.path.join(OUT_DIR, "alpha158_batch_cost.csv")
DAILY_DIR = os.path.join(OUT_DIR, "daily")

METRIC_COLS = ["name", "expr", "status", "n_stocks", "ts_ic_mean", "ts_icir",
               "cs_ic_mean", "cs_icir", "cs_rank_ic_mean", "cs_rank_icir",
               "cs_ic_win_rate", "n_days"]


def set_paths(stride):
    """抽样口径与全市场口径**各一棵目录**，不让续跑逻辑把抽样的数当成全市场的数"""
    global OUT_DIR, METRICS, STABILITY, COST, DAILY_DIR
    OUT_DIR = OUT_BASE + ("" if stride <= 0 else f"_sample{stride}")
    METRICS = os.path.join(OUT_DIR, "alpha158_metrics.csv")
    STABILITY = os.path.join(OUT_DIR, "alpha158_stability.csv")
    COST = os.path.join(OUT_DIR, "alpha158_batch_cost.csv")
    DAILY_DIR = os.path.join(OUT_DIR, "daily")
    print(f"[目录] {OUT_DIR}", flush=True)


def append_csv(path, rows, cols, key="name"):
    """按 `key` 列幂等追加：同键重跑以新行为准（断点续跑 / 换口径重扫都靠这一条）"""
    new = pd.DataFrame(rows)
    for c in cols:
        if c not in new.columns:
            new[c] = np.nan
    new = new[cols]
    if os.path.exists(path) and os.path.getsize(path) > 0:
        old = pd.read_csv(path, dtype={key: str})
        old = old[~old[key].isin(set(new[key]))]
        new = pd.concat([old, new], ignore_index=True)
    new.to_csv(path, index=False)
    return len(new)


def done_names():
    if not os.path.exists(METRICS):
        return set()
    return set(pd.read_csv(METRICS)["name"].astype(str))


def rss_gb():
    try:
        with open(f"/proc/{os.getpid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return float("nan")


def run_batch(pool, tasks, tag):
    """一批表达式：扫面板 -> 判据 A -> 落三样（指标行 / 稳不稳 / 逐日 IC 宽表）"""
    t0 = time.time()
    sink = {}
    rows = evaluate(pool, tasks, series_sink=sink)
    scan_s = time.time() - t0

    stats = []
    for r in rows:
        name = r["name"]
        if name not in sink:
            stats.append({"name": name, "status": r.get("status", "无逐日序列"), "n_days": 0})
            continue
        st = stability_stats(name, sink[name]["rank"], sink[name]["pearson"])
        st["expr"] = r["expr"]
        stats.append(st)
    summary = pd.DataFrame(stats)
    check_same_ruler(summary, rows, sink)          # 不过就 SystemExit，本批不落盘

    n_m = append_csv(METRICS, rows, METRIC_COLS)
    n_s = append_csv(STABILITY, stats, list(summary.columns))
    daily = pd.DataFrame({**{f"rank|{k}": v["rank"] for k, v in sink.items()},
                          **{f"pearson|{k}": v["pearson"] for k, v in sink.items()}}).sort_index()
    daily.index.name = "date"
    daily.to_csv(os.path.join(DAILY_DIR, f"{tag}.csv"))
    cost = {"batch": tag, "n_expr": len(tasks), "names": "|".join(t["name"] for t in tasks),
            "scan_s": round(scan_s, 1), "per_expr_s": round(scan_s / max(len(tasks), 1), 1),
            "peak_rss_gb": round(rss_gb(), 2),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    append_csv(COST, [cost], list(cost.keys()), key="batch")
    print(f"  [{tag}] {len(tasks)} 条　{scan_s:.0f}s（单条均 {cost['per_expr_s']:.0f}s）"
          f"　RSS {cost['peak_rss_gb']}GB　累计 metrics={n_m} stability={n_s}", flush=True)
    return scan_s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stride", type=int, default=0,
                    help=">0 时按等间隔抽样标的（代价 smoke 用；0=全市场）")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--only", type=int, default=0, help="只跑前 N 批（smoke 用；0=全部）")
    ap.add_argument("--pick", default="",
                    help="只跑指定批号（逗号分隔，1 起）；smoke 用来点「含新算子的那几批」")
    ap.add_argument("--rerun", action="store_true", help="忽略已有 metrics，重跑全部批")
    args = ap.parse_args()

    recs = json.load(open(EXPR_JSON, encoding="utf-8"))
    tasks = [{"name": r["name"], "expr": r["expr"]} for r in recs]
    batches = [tasks[i:i + args.batch_size] for i in range(0, len(tasks), args.batch_size)]
    pick = {int(x) for x in args.pick.split(",") if x.strip()}
    print(f"[计划] {len(tasks)} 条 ÷ {args.batch_size} = {len(batches)} 批"
          + (f"　--pick {sorted(pick)}" if pick else "")
          + f"　表达式来自 {EXPR_JSON}", flush=True)

    set_paths(args.stride)
    full_pool = load_panel()
    if args.stride > 0:
        pool = dict(list(full_pool.items())[::args.stride])
        med_s = int(np.median([len(v) for v in pool.values()]))
        med_f = int(np.median([len(v) for v in full_pool.values()]))
        print(f"[抽样] stride={args.stride} ⇒ {len(pool)} 只／全市场 {len(full_pool)} 只　"
              f"行数中位 {med_s} vs 全表 {med_f}（比值 {med_s/med_f:.2f}）", flush=True)
        assert med_s / med_f > 0.9, f"抽样中位数 {med_s} 太短 ⇒ 代价读数会低估"
        del full_pool
    else:
        pool = full_pool
        del full_pool

    os.makedirs(DAILY_DIR, exist_ok=True)
    skip = set() if args.rerun else done_names()
    if skip:
        print(f"[续跑] metrics 已有 {len(skip)} 条 ⇒ 这些表达式不再扫", flush=True)

    t_all, n_run = time.time(), 0
    for bi, batch in enumerate(batches, 1):
        tag = f"b{bi:02d}"
        if pick and bi not in pick:
            continue
        todo = [t for t in batch if t["name"] not in skip]
        if not todo:
            print(f"  [{tag}] {len(batch)} 条已完成，跳过", flush=True)
            continue
        if args.only and bi > args.only:
            print(f"  [{tag}] 到 --only {args.only} 为止，停在这里", flush=True)
            break
        n_run += 1
        run_batch(pool, todo, tag)

    print(f"\n[总计] 本进程实跑 {n_run} 批、{time.time()-t_all:.0f}s ⇒ 累计 metrics "
          f"{len(done_names())}/{len(tasks)} 条　总墙钟含 load_panel {time.time()-t_all:.0f}s",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
