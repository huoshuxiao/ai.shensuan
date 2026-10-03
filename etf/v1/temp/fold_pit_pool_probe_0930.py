"""WP-2 量闸：把「按今日成交额挑 100 只」换成点时挑法，每折能多出多少宽度？
只读探针，判据/配置/生产文件一行不改。

现场：09-30 已定档 2 折（`config.py:161`），折 1 的现状池只有 4 只可挖。
幸存者泄漏的入口在 `etf_universe.build()`：它用**今天**的 20 日成交额中位数
把 871 只截成 100 只，再把这个池子原样喂给所有折 ⇒ 折 1（2010~2014）挖因子时
读到的"可投宇宙"其实是"活到今天且今天还很活跃的那批"。

五种挑法在同一折窗口上并列量（边算边打）：
  A 现状     本线 100 只候选池里训练段 >240 根的          （身份闸：必须复现生产读数）
  B 镜像上限 871 只镜像里训练段 >240 根的                 （物理天花板）
  C 滚动≥50% 窗口内**过半交易日**都过闸的点时池           ← 候选接线口径
  D 滚动≥20% 窗口内**两成交易日**都过闸的点时池           （更松一档的对照）
  E 段末点时 只在训练段**末尾那一天**过闸                 （09-30 上午试过的那版：早折反而更瘦）
闸的口径与 `ETF_FILTER` 同源：上市满 min_list_days 自然日、近 amount_window 日
成交额中位数 ≥ min_avg_amount、近 vol_window 日**年化**波动 ≥ min_ann_vol。

⚠️ 读数水位（这几条探针自己消不掉，报数必须一起报）：
  · 871 只镜像是 akshare「今天还查得到」的集合 ⇒ 已退市/清盘的 ETF 不在里面
    ⇒ B/C/D/E 全是**被幸存者偏差抬高过的上界**，修的是"池子挑法"这一半，
      修不了"死掉的样本根本没有"那一半。
  · 本轮按用户裁定**不做「每指数一只代表」去重** ⇒ 同指数的姊妹基金会一起进池，
    C/D 的宽度里有一部分是共线重复，不是真独立标的（同日实测：871 只按同花顺
    名称分组得到 852 组 ⇒ 去重几乎不咬，但早年的华夏/易方达 50 那批会撞）。
  · 本探针**不走 DataLoader**：它会在缓存陈旧时回源网络并把镜像写回 cache（生产写盘）。
    这里直接读盘，一个字节都不写。

用法：/usr/bin/python3.10 etf/v1/temp/fold_pit_pool_probe_0930.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BYPASS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(BYPASS, "src"))
import _bootstrap  # noqa: E402

import numpy as np                                        # noqa: E402
import pandas as pd                                        # noqa: E402
from config import (FREQ, ETF_FILTER, BACKTEST_START,      # noqa: E402
                    CACHE_DIR, WALK_FORWARD)
from etf_universe import get_universe                      # noqa: E402
from walk_forward import make_splits                       # noqa: E402

GATE = 240                       # walk_forward.py:93 的训练段长度闸（>240 根 bar）
MIN_CS = 30                      # 截面尺子有牙的天数门槛（daily_cs_ic）
UNIVERSE_ALL_DIR = os.path.join(os.path.dirname(CACHE_DIR), "universe_all")
SUFFIX = f"_{FREQ}.csv"


def read_csv_ro(path):
    """直读一份日线 csv，只留判定要用的两列（与 fold_breadth_probe 同口径）"""
    try:
        df = pd.read_csv(path, usecols=["date", "close", "amount"],
                         parse_dates=["date"])
    except Exception:
        return None
    df = df.dropna(subset=["close"])
    df = df[~df["date"].duplicated(keep="last")].sort_values("date")
    df = df.set_index("date").loc[BACKTEST_START:]
    return df if len(df) else None


def load_mirror_ro():
    """缓存→镜像 优先级的只读全量加载；返回 {code: df}"""
    codes = sorted(fn[:-len(SUFFIX)] for fn in os.listdir(UNIVERSE_ALL_DIR)
                   if fn.endswith(SUFFIX))
    panel = {}
    for c in codes:
        df = read_csv_ro(os.path.join(CACHE_DIR, f"{c}{SUFFIX}"))
        if df is None:
            df = read_csv_ro(os.path.join(UNIVERSE_ALL_DIR, f"{c}{SUFFIX}"))
        if df is not None:
            panel[c] = df
    return panel, codes


def tradable_mask(df):
    """逐 bar 的点时可投布尔：上市满 N 自然日 + 近 20 日成交额中位数 + 近 60 日年化波动。

    ⚠️ 波动必须**年化**再比：`min_ann_vol=0.05` 是年化口径，拿日频 std 直接比会严
    约 √252≈16 倍（09-30 上午那份几何探针就是这么把早折池子量成 0 只的）。"""
    amt = df["amount"].rolling(ETF_FILTER["amount_window"]).median()
    ret = df["close"].pct_change()
    vol = ret.rolling(ETF_FILTER["vol_window"]).std() * math_sqrt252()
    first = df.index[0]
    age_days = (df.index - first).days
    return ((age_days >= ETF_FILTER["min_list_days"])
            & (amt >= ETF_FILTER["min_avg_amount"])
            & (vol >= ETF_FILTER["min_ann_vol"]))


def math_sqrt252():
    return 252 ** 0.5


def slice_len(df, a, b):
    return int(len(df.loc[a:b]))


_MASK = {}


def mask_of(panel, c):
    """逐代码缓存：一份镜像的闸位只算一次（探针里三个口径共用）"""
    if c not in _MASK:
        _MASK[c] = tradable_mask(panel[c])
    return _MASK[c]


def readings(panel, tr_s, tr_e, sel):
    """给一组入选代码，返回（宽度, 日均厚, ≥30只天%, 段内 bar 数中位）

    日均厚按「当天在训段内且有 bar」的入选代码数逐日统计 ⇒ 与生产折表同尺。"""
    series = [panel[c].loc[tr_s:tr_e]["close"] for c in sel
              if len(panel[c].loc[tr_s:tr_e])]
    if not series:
        return 0, 0.0, 0.0, 0.0
    thick = pd.concat(series, axis=1).notna().sum(axis=1)
    return (len(sel), float(thick.mean()),
            float((thick >= MIN_CS).mean() * 100),
            float(np.median([slice_len(panel[c], tr_s, tr_e) for c in sel])))


def main():
    print("=" * 78)
    print("  点时挑池 五档并列（只读）")
    print("=" * 78)
    uni = get_universe()
    cur = uni.universe["code"].astype(str).tolist()
    panel, codes = load_mirror_ro()
    print(f"  现状候选池 {len(cur)} 只 / 全市场镜像 {len(codes)} 只"
          f" / 直读成功 {len(panel)} 只")
    ref = panel[max(panel, key=lambda c: len(panel[c]))].index
    splits = make_splits(ref)
    print(f"  时间轴 {ref[0]:%Y-%m-%d} ~ {ref[-1]:%Y-%m-%d}（{len(ref)} bar）"
          f"  几何 n_splits={WALK_FORWARD['n_splits']} "
          f"train_ratio={WALK_FORWARD['train_ratio']}"
          f"  MIN_CS={MIN_CS}\n")

    for i, (tr_s, tr_e, te_s, te_e) in enumerate(splits, 1):
        print(f"◆ 折 {i}  训练段 {tr_s:%Y-%m-%d} ~ {tr_e:%Y-%m-%d}"
              f"  测试段 {te_s:%Y-%m-%d} ~ {te_e:%Y-%m-%d}")
        in_win = [c for c in panel if slice_len(panel[c], tr_s, tr_e) > GATE]
        a = [c for c in cur if c in panel and slice_len(panel[c], tr_s, tr_e) > GATE]
        share = {}
        for c in in_win:
            m = mask_of(panel, c).loc[tr_s:tr_e]
            share[c] = float(m.mean()) if len(m) else 0.0
        c_sel = [c for c, s in share.items() if s >= 0.5]
        d_sel = [c for c, s in share.items() if s >= 0.2]
        # E 取"训练段末尾那一天"过闸：只能落在真实 bar 上，所以用 asof 而不是硬索引
        e_sel = [c for c in in_win
                 if bool(mask_of(panel, c).asof(tr_e))]
        rows = [("A 现状100池", a), ("B 镜像上限", in_win),
                ("C 滚动≥50%", c_sel), ("D 滚动≥20%", d_sel),
                ("E 段末点时", e_sel)]
        print(f"  {'挑法':<12}{'宽度':>6}{'日均厚':>9}{'≥30只天%':>10}"
              f"{'段内bar中位':>12}{'新面孔':>8}{'踢出':>7}")
        for tag, sel in rows:
            if not sel:
                print(f"  {tag:<12}{0:>6}{'-':>9}{'-':>10}{'-':>12}{'-':>8}{'-':>7}")
                continue
            n, th, pct, bars = readings(panel, tr_s, tr_e, sel)
            new = len(set(sel) - set(cur))
            out = len(set(cur) & set(in_win) - set(sel))
            print(f"  {tag:<12}{n:>6}{th:>9.1f}{pct:>10.1f}{bars:>12.0f}"
                  f"{new:>8}{out:>7}")
        print(f"  共线水位：本折入选 {len(c_sel)} 只里有 "
              f"{len([c for c in c_sel if c not in cur])} 只不在现状池"
              f"（未做「每指数一只代表」去重 ⇒ 宽度含共线重复）\n")

    print("探针结束（未写任何生产文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
