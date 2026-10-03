"""折内宽度 4 只到底是「市场没长出来」还是「池子挑法」—— 只读探针，判据/配置一行不改。

现场：09-30 重建的折表里，三折训练段可用标的 = 4 / 17 / 69（/100）。
折 1 那行权益曲线新旧两场逐字节相同 ⇒ 需要知道这是不是「4 只面板上挑谁都买同一批」。

四张读数（全部现量，边算边打）：
  M1  现状：本线 100 只候选池在每折训练段/测试段各有多少只可用（复现 4/17/69 作身份闸）
  M2  天花板：**全市场 871 只镜像**在同一段里最多能给出多少只 ⇒ 区分「物理没有」与「没挑到」
  M3  点时反事实：把准入闸（20 日成交额中位数 ≥min_avg_amount、60 日年化波动 ≥min_ann_vol）
      搬到**该折训练段末尾**那一天去量，能剩下几只 —— 即「池子完全改成点时挑法」值多少宽度
  M4  折起点后移：全局起点从 2010 往后挪到 2013/2015/2017/2019/2021，每折宽度和合并样本外
      bar 数怎么变 —— 「少要早折」这条路的 trade-off

⚠️ 口径限制（读数自带的水位）：
  · 871 只镜像是 akshare「今天还查得到」的集合 ⇒ 已退市 ETF 不在里面 ⇒ M2/M3 是**被幸存者
    偏差抬高上界**的天花板，不是真点时宇宙。
  · M3 没做「每指数一只代表」去重（871 只的名称/指数归属不在这份数据里）⇒ 真实点时池会比
    M3 更窄。
  · 本探针**不走 DataLoader**：它会在缓存陈旧时回源网络、并把镜像写回 cache（生产写盘）。
    这里用同样的「缓存→镜像」优先级直接读盘，一个字节都不写。

用法：/usr/bin/python3.10 etf/v1/temp/fold_breadth_probe_0930.py
"""
import os
import sys
import math

HERE = os.path.dirname(os.path.abspath(__file__))
BYPASS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(BYPASS, "src"))
import _bootstrap  # noqa: E402

import pandas as pd                                        # noqa: E402
from config import (FREQ, ETF_FILTER, BACKTEST_START,      # noqa: E402
                    CACHE_DIR)
from etf_universe import get_universe                      # noqa: E402
from walk_forward import make_splits                       # noqa: E402
GATE = 240                       # walk_forward.py:93 的训练段长度闸（>240 根 bar）
UNIVERSE_ALL_DIR = os.path.join(os.path.dirname(CACHE_DIR), "universe_all")
MIRROR_SUFFIX = f"_{FREQ}.csv"


def read_csv_ro(path):
    """按 data_loader._standardize 的口径直读一份日线 csv（只留 close/amount）。"""
    try:
        df = pd.read_csv(path, usecols=["date", "close", "amount"],
                         parse_dates=["date"])
    except Exception:
        return None
    df = df.dropna(subset=["close"])
    df = df[~df["date"].duplicated(keep="last")].sort_values("date")
    df = df.set_index("date").loc[BACKTEST_START:]
    return df if len(df) else None


def load_panel_ro(codes):
    """缓存→镜像 优先级的只读加载；返回 {code: df} 和来源统计。"""
    panel, from_cache, from_mirror, missing = {}, 0, 0, []
    for c in codes:
        cp = os.path.join(CACHE_DIR, f"{c}{MIRROR_SUFFIX}")
        mp = os.path.join(UNIVERSE_ALL_DIR, f"{c}{MIRROR_SUFFIX}")
        df = read_csv_ro(cp) if os.path.exists(cp) else None
        if df is not None:
            from_cache += 1
        elif os.path.exists(mp):
            df = read_csv_ro(mp)
            if df is not None:
                from_mirror += 1
        if df is None:
            missing.append(c)
            continue
        panel[c] = df
    return panel, from_cache, from_mirror, missing


def bars_in(df, a, b):
    return int(len(df.loc[a:b]))


def pit_median_amount(df, asof, window):
    tail = df["amount"].loc[:asof].tail(window)
    return float(tail.median()) if len(tail) else float("nan")


def pit_ann_vol(df, asof, window):
    s = df["close"].loc[:asof].pct_change().dropna().tail(window)
    if len(s) < window // 2:
        return float("nan")
    return float(s.std() * math.sqrt(252))


def main():
    print("=" * 72)
    print("  折内宽度探针（只读）")
    print("=" * 72)

    universe = get_universe()
    pool_codes = universe.universe["code"].tolist()
    print(f"\n候选池 {len(pool_codes)} 只（每指数一只代表 + 按**今日**成交额截断 "
          f"max_count={ETF_FILTER['max_count']}）")

    mirror_codes = sorted(fn[:-len(f"_{FREQ}.csv")]
                          for fn in os.listdir(UNIVERSE_ALL_DIR)
                          if fn.endswith(MIRROR_SUFFIX))
    print(f"全市场镜像 {len(mirror_codes)} 只")

    # ---- 只读加载两份面板 ----
    all_codes = sorted(set(pool_codes) | set(mirror_codes))
    panel, n_cache, n_mirror, missing = load_panel_ro(all_codes)
    print(f"直读盘完成：{len(panel)} 只有数据（缓存 {n_cache} / 镜像 {n_mirror} / "
          f"两处都没有 {len(missing)}）")
    if missing:
        print(f"  ⚠️ 两处都没有的代码（生产会回源网络，本探针看不见）: "
              f"{missing[:10]}{' ...' if len(missing) > 10 else ''}")

    pool = {c: panel[c] for c in pool_codes if c in panel}
    ref_code = max(pool, key=lambda c: len(pool[c]))
    all_ts = pool[ref_code].index
    print(f"时间轴参考 {ref_code}: {all_ts[0]:%Y-%m-%d} ~ {all_ts[-1]:%Y-%m-%d} "
          f"({len(all_ts)} bars)")

    splits = make_splits(all_ts)

    # ---- M1 身份闸：必须逐字复现日志里的 4 / 17 / 69 ----
    print("\n--- M1 现状宽度（挖掘层闸 len>240；交易层=测试段有无 bar）---")
    expect = {1: 4, 2: 17, 3: 69}
    got = {}
    rows = []
    for i, (tr_s, tr_e, te_s, te_e) in enumerate(splits):
        fold = i + 1
        mine_n = sum(1 for c, df in pool.items() if bars_in(df, tr_s, tr_e) > GATE)
        trade_any = sum(1 for c, df in pool.items()
                        if bars_in(df, te_s, te_e) >= 1)
        trade_60 = sum(1 for c, df in pool.items()
                       if bars_in(df, te_s, te_e) >= 60)
        trade_240 = sum(1 for c, df in pool.items()
                        if bars_in(df, te_s, te_e) > GATE)
        got[fold] = mine_n
        rows.append((fold, tr_s, tr_e, te_s, te_e, mine_n,
                     trade_any, trade_60, trade_240))
        print(f"  折 {fold} 训练 {tr_s:%Y-%m-%d}~{tr_e:%Y-%m-%d} "
              f"测试 {te_s:%Y-%m-%d}~{te_e:%Y-%m-%d} | "
              f"挖掘层 {mine_n}/100 | 测试段有bar {trade_any} 只 / "
              f"≥60根 {trade_60} / >240根 {trade_240}", flush=True)
    bad = [f for f in rows if expect.get(f[0]) != f[5]]
    if bad:
        print(f"\n❌ 身份闸未过：折 {[b[0] for b in bad]} 的挖掘层宽度 "
              f"{[b[5] for b in bad]} ≠ 日志 {[expect[b[0]] for b in bad]}"
              f" ⇒ 本探针面板与生产面板不是同一张，全部读数作废")
        return 1
    print("  ✅ 身份闸：4 / 17 / 69 与生产日志逐字一致 ⇒ 下面所有读数读的是同一张面板")

    # ---- M2 天花板：全市场 871 只在同一几何下能给多少 ----
    print("\n--- M2 天花板（把候选池换成全市场 871 只，同一道 >240 闸）---")
    for fold, tr_s, tr_e, te_s, te_e, now_n, _, _, _ in rows:
        ceil_mine = sum(1 for c, df in panel.items()
                        if bars_in(df, tr_s, tr_e) > GATE)
        ceil_trade = sum(1 for c, df in panel.items()
                         if bars_in(df, te_s, te_e) > GATE)
        print(f"  折 {fold}: 挖掘层天花板 {ceil_mine} 只（现状 "
              f"{now_n}）| 测试段有 >240 根 bar 的标的 {ceil_trade} 只", flush=True)

    # ---- M3 点时反事实：把准入闸搬到训练段末尾那天去量 ----
    print("\n--- M3 点时反事实（在每折训练段**末尾**当天，用生产阈值重挑池子）---")
    print(f"  阈值口径：{ETF_FILTER['amount_window']} 日成交额中位数 ≥ "
          f"{ETF_FILTER['min_avg_amount']:,}；{ETF_FILTER['vol_window']} 日年化波动 ≥ "
          f"{ETF_FILTER['min_ann_vol']}；训练段 bar > {GATE}")
    for fold, tr_s, tr_e, te_s, te_e, now_n, _, _, _ in rows:
        asof = tr_e
        keep = []
        for c, df in panel.items():
            if bars_in(df, tr_s, tr_e) <= GATE:
                continue
            amt = pit_median_amount(df, asof, ETF_FILTER["amount_window"])
            vol = pit_ann_vol(df, asof, ETF_FILTER["vol_window"])
            if (amt == amt and amt >= ETF_FILTER["min_avg_amount"]
                    and vol == vol and vol >= ETF_FILTER["min_ann_vol"]):
                keep.append((c, amt))
        keep.sort(key=lambda x: -x[1])
        print(f"  折 {fold}: 点时池 {len(keep)} 只（现状 {now_n} 只，"
              f"多 {len(keep) - now_n} 只）", flush=True)
        if len(keep) <= 12:
            for c, amt in keep:
                first = panel[c].index[0]
                print(f"      {c}  20日中位成交额 {amt/1e8:.2f} 亿 | "
                      f"首根 bar {first:%Y-%m-%d}")

    # ---- M4 折起点后移的 trade-off ----
    print("\n--- M4 全局起点后移：宽度 vs 合并样本外 bar 数（3 折 / train_ratio 0.5）---")
    for start_year in (2010, 2013, 2015, 2017, 2019, 2021):
        ts = all_ts[all_ts >= pd.Timestamp(f"{start_year}-01-01")]
        if len(ts) < 300:
            continue
        sp = make_splits(ts)
        widths, oos_bars = [], 0
        for (tr_s, tr_e, te_s, te_e) in sp:
            widths.append(sum(1 for c, df in pool.items()
                              if bars_in(df, tr_s, tr_e) > GATE))
            oos_bars += int(len(ts[(ts >= te_s) & (ts <= te_e)]))
        print(f"  起点 {ts[0]:%Y-%m-%d}: 各折挖掘层宽度 {widths} | "
              f"合并样本外 {oos_bars} bar", flush=True)

    # ---- M5 折 1 那 4 只是谁：把「挑谁都买同一批」摊开 ----
    print("\n--- M5 折 1 训练段那几只、以及测试段实际能买到几只 ---")
    tr_s, tr_e, te_s, te_e = rows[0][1], rows[0][2], rows[0][3], rows[0][4]
    for c in pool_codes:
        df = panel.get(c)
        if df is None or bars_in(df, tr_s, tr_e) <= GATE:
            continue
        tb = bars_in(df, te_s, te_e)
        amt = pit_median_amount(df, tr_e, ETF_FILTER["amount_window"])
        print(f"  {c} 训练段 {bars_in(df, tr_s, tr_e)} 根 | 测试段 {tb} 根 | "
              f"折末 20日中位成交额 {amt/1e8:.2f} 亿")
    print("  ↑ 若这一列的「测试段」都是同一天出生、同一批名字 ⇒ 4 只面板上 "
          "top-k 的挑选几乎没有分辨力（新旧两场落进同一篮子）")

    print("\n探针结束（未写任何生产文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
