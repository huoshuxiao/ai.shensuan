"""「每折拉长时间跨度」买到什么、修幸存者入口值多少 —— 只读几何探针，判据/配置一行不改。

问的三个问题：
  Q1  把每折的时间跨度拉长（少要折 / 提高 train_ratio / 起点后移），挖掘层宽度和
      **日均截面厚度**分别涨到多少？
  Q2  IC 那把尺子（daily_cs_ic 的 MIN_CS=30 ⇒ 一天不足 30 只就整天丢掉）在每种几何下
      **有多少天是有牙的**？只报"多少只标的过了 240 根闸"是不够的 —— 20 只摊在 8 年里，
      每一天仍然只有个位数。
  Q3  候选池若改成**点时挑法**（在该折训练段起点当天，用生产阈值从 871 只全市场镜像重挑
      top-100），Q1/Q2 变成多少 ⇒ 修幸存者入口到底买不买得到宽度。

⚠️ 水位（引用读数时必须一起念）：
  · Q3 的点时池**没做「每指数一只代表」去重** —— 871 只的名称/指数归属本机不存在
    （名称来自 akshare 东财现货 `fund_etf_spot_em`，该源本机不可达；风险面板三份 csv
    只有 code 没有名称）。不去重 ⇒ 同一根指数的多只 ETF 一起进池 ⇒ **Q3 的日均厚度是
    被共线标的撑虚的上界**。
  · 871 只镜像 = 「今天还查得到」的集合 ⇒ 已退市 ETF 不在里面 ⇒ Q3 只修「按今日成交额
    挑池」这一层，「今日还存在」那层要真点时宇宙才有解。
  · 本探针只算几何与厚度，**不算 DSR**：门槛是 (T, N) 的函数，N 要真挖才知道。

用法：/usr/bin/python3.10 etf/v1/temp/fold_geometry_probe_0930.py
"""
import os
import sys
import math

HERE = os.path.dirname(os.path.abspath(__file__))
BYPASS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(BYPASS, "src"))
import _bootstrap  # noqa: E402

import pandas as pd                                        # noqa: E402
import walk_forward as WF                                   # noqa: E402
from config import (FREQ, ETF_FILTER, BACKTEST_START,       # noqa: E402
                    CACHE_DIR, WALK_FORWARD)
from etf_universe import get_universe                       # noqa: E402

GATE = 240                       # walk_forward.py:93 训练段长度闸
MIN_CS = 30                      # daily_cs_ic 的逐日截面最薄天数（<此值整天丢）
MIRROR_DIR = os.path.join(os.path.dirname(CACHE_DIR), "universe_all")
SUFFIX = f"_{FREQ}.csv"
ORIGINAL = dict(WALK_FORWARD)
GEOS = [(1, 0.5), (2, 0.5), (3, 0.5), (4, 0.5), (2, 0.7), (3, 0.7), (6, 0.5)]


def read_ro(path):
    try:
        df = pd.read_csv(path, usecols=["date", "close", "amount"],
                         parse_dates=["date"])
    except Exception:
        return None
    df = df.dropna(subset=["close"])
    df = df[~df["date"].duplicated(keep="last")].sort_values("date")
    df = df.set_index("date").loc[BACKTEST_START:]
    return df if len(df) else None


def load_all(codes):
    """缓存→镜像 优先级直读，不碰 DataLoader（它会回源网络并写回 cache）。"""
    panel = {}
    for c in codes:
        for d in (CACHE_DIR, MIRROR_DIR):
            p = os.path.join(d, f"{c}{SUFFIX}")
            if os.path.exists(p):
                df = read_ro(p)
                if df is not None:
                    panel[c] = df
                break
    return panel


def pit_pool(panel, asof, top=None):
    """asof 当天按生产阈值重挑池子：上市满 min_list_days、年化波动与 20 日中位成交额
    达标 ⇒ 按中位成交额取前 top。
    ⚠️ 波动必须**年化**（×√252）后再比：production `etf_universe.measure` 就是年化的，
    直接拿日线 std 去比 0.05 等于把门槛抬 ~16 倍（09-30 本探针第一版就栽在这里，
    2015 年那一格只剩 3 只、把 510050 这类大ETF挡在门外）。"""
    top = top or ETF_FILTER["max_count"]
    listed_before = asof - pd.Timedelta(days=ETF_FILTER["min_list_days"])
    scored = []
    for c, df in panel.items():
        if df.index[0] > listed_before:
            continue                      # asof 当天还没上市满一年
        hist = df.loc[:asof]
        if len(hist) < ETF_FILTER["vol_window"]:
            continue
        amt = hist["amount"].tail(ETF_FILTER["amount_window"]).median()
        vol = (hist["close"].pct_change().tail(ETF_FILTER["vol_window"]).std()
               * math.sqrt(252))
        if amt != amt or vol != vol:
            continue
        if amt < ETF_FILTER["min_avg_amount"] or vol < ETF_FILTER["min_ann_vol"]:
            continue
        scored.append((c, float(amt)))
    scored.sort(key=lambda x: -x[1])
    return [c for c, _ in scored[:top]]


def fold_readings(panel, codes, tr_s, tr_e, te_s, te_e):
    """一折五张读数：挖掘层宽度 / 训练段交易日数 / 日均厚度 / ≥MIN_CS 天占比 / 测试段有 bar 的天数。"""
    sub = {c: panel[c].loc[tr_s:tr_e] for c in codes if c in panel}
    if not sub:
        return 0, 0, 0.0, 0.0, 0
    width = sum(1 for df in sub.values() if len(df) > GATE)
    days = sorted(set().union(*[set(df.index) for df in sub.values()]))
    occ = pd.DataFrame({c: df["close"].notna().astype("int8")
                        for c, df in sub.items()})
    thick = occ.sum(axis=1).reindex(pd.DatetimeIndex(days)).fillna(0)
    test_days = sorted(set().union(*[set(panel[c].loc[te_s:te_e].index)
                                     for c in codes if c in panel]))
    return (width, len(days), float(thick.mean()),
            float((thick >= MIN_CS).mean() * 100.0), len(test_days))


def main():
    print("=" * 78)
    print("  折几何 × 池挑法 探针（只读）")
    print("=" * 78)
    universe = get_universe()
    cur_codes = universe.universe["code"].tolist()
    mirror_codes = sorted(fn[:-len(SUFFIX)] for fn in os.listdir(MIRROR_DIR)
                          if fn.endswith(SUFFIX))
    panel = load_all(sorted(set(cur_codes) | set(mirror_codes)))
    print(f"候选池 {len(cur_codes)} 只 / 全市场镜像 {len(mirror_codes)} 只 / "
          f"直读盘 {len(panel)} 只")

    ref = max(cur_codes, key=lambda c: len(panel[c]))
    all_ts = panel[ref].index
    print(f"时间轴参考 {ref}: {all_ts[0]:%Y-%m-%d} ~ {all_ts[-1]:%Y-%m-%d} "
          f"({len(all_ts)} bar)   MIN_CS={MIN_CS}（一天不足 30 只 ⇒ IC 尺子当天无定义）")

    for start_year in (2010, 2015):
        ts = all_ts[all_ts >= pd.Timestamp(f"{start_year}-01-01")]
        print("\n" + "=" * 78)
        print(f"全局起点 {ts[0]:%Y-%m-%d}（{len(ts)} bar）")
        print("=" * 78)
        for scheme in ("cur", "pit"):
            print(f"\n  ◆ 候选池口径："
                  f"{'现状池（按今日成交额挑 100 只）' if scheme == 'cur' else '点时池（每折训练段起点重挑 100 只，未去重）'}")
            print("    几何(折,训练比)  折  训练窗                    宽度  "
                  "训练日  日均厚  ≥30天%  测试bar")
            for n_splits, train_ratio in GEOS:
                WF.WALK_FORWARD["n_splits"] = n_splits
                WF.WALK_FORWARD["train_ratio"] = train_ratio
                splits = WF.make_splits(ts)
                if not splits:
                    print(f"    ({n_splits},{train_ratio}) ⇒ 无折")
                    continue
                for i, (tr_s, tr_e, te_s, te_e) in enumerate(splits):
                    codes = (pit_pool(panel, tr_s) if scheme == "pit"
                             else cur_codes)
                    w, nd, mb, pct, tb = fold_readings(panel, codes, tr_s,
                                                       tr_e, te_s, te_e)
                    tag = f"({n_splits},{train_ratio})" if i == 0 else ""
                    print(f"    {tag:<15} {i + 1:<3} {tr_s:%Y-%m-%d}~{tr_e:%Y-%m-%d}"
                          f"  {w:>5} {nd:>6} {mb:>7.1f} {pct:>6.1f} {tb:>7}",
                          flush=True)
    WF.WALK_FORWARD.clear()
    WF.WALK_FORWARD.update(ORIGINAL)
    print(f"\n复原检查：WALK_FORWARD 现在 n_splits="
          f"{WF.WALK_FORWARD['n_splits']} train_ratio="
          f"{WF.WALK_FORWARD['train_ratio']} embargo="
          f"{WF.WALK_FORWARD['embargo_bars']}（应为 3 / 0.5 / {ORIGINAL['embargo_bars']}）")
    print("探针结束（未写任何生产文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
