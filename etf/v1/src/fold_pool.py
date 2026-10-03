# -*- coding: utf-8 -*-
"""折内点时候选池（WP-2，只在本线的样本外验证里用）

要修的泄漏入口在 `common/src/data/etf/etf_universe.py:build()`：它按**今天**的
20 日成交额中位数把 871 只截成 100 只，这 100 只再原样喂给每一折。于是折 1
（2010~2014）挖因子时的"可投宇宙"其实是"活到 2026 年且今天还很活跃的那批"
⇒ 幸存者 + 前视双重泄漏，样本外收益里有一部分是"事后才知道谁会活下来"给的。

这里的做法：每折的候选池只用**该折训练段内**的信息挑——
逐 bar 判三闸（与 `ETF_FILTER` 同源）：上市满 `min_list_days` 自然日、
近 `amount_window` 日成交额中位数 ≥ `min_avg_amount`、近 `vol_window` 日
**年化**波动 ≥ `min_ann_vol`（⚠️ 必须年化再比，拿日频 std 直接比会严 √252≈16 倍，
09-30 上午那份几何探针就是这么把早折池子量成 0 只的）；
一段里过闸天数占比 ≥ `min_share` 才算入选，按**段内**成交额中位数排序截 `max_codes`。

两根水位（读数必须带着走，别当成真点时宇宙）：
1. 871 只镜像是 akshare「今天还查得到」的集合 ⇒ 已退市/清盘的 ETF 不在里面，
   这一层修的是"挑法"，修不了"死掉的样本压根没有"。
2. 本轮按用户裁定**不做「每指数一只代表」去重** ⇒ 同指数姊妹基金一起进池，
   宽度里含共线重复（同日实测：871 只按同花顺名称分组得 852 组 ⇒ 去重几乎不咬，
   但早年华夏/易方达 50 那批会撞）。

实测五档对照见 `temp/fold_pit_pool_probe_0930.py`（09-30，2 折几何）：
  折 1  现状 7 只 → 点时(≥50%) 6 只        ← 早折宽度是物理天花板，挑法救不动
  折 2  现状 54 只 → 点时 100 只（截断前 113）
        日均厚 37.5 → 92.6，「≥30 只可算 IC 的天数」69.2% → 100%
⇒ 这一改动买到的不是"早折变肥"，是**晚折的截面尺子第一次全长齐牙**，
   外加把"谁会活到今天"这道未来信息从折内池子里拿掉。
"""

import os

import pandas as pd
from config import (BACKTEST_START, BACKTEST_END, ETF_FILTER, FREQ,
                    UNIVERSE_ALL_DIR)

GATE_BARS = 240          # walk_forward.py:119 同款训练段长度闸
MIN_CS = 30              # daily_cs_ic 的截面尺子有牙门槛


def mirror_codes():
    """全市场日线镜像里有哪些代码（这就是本机能拿到的最宽宇宙）"""
    try:
        return sorted(fn[:-len(f"_{FREQ}.csv")] for fn in os.listdir(UNIVERSE_ALL_DIR)
                      if fn.endswith(f"_{FREQ}.csv"))
    except OSError:
        return []


def tradable_mask(df):
    """逐 bar 的点时可投布尔（口径见模块 docstring）"""
    amt = df["amount"].rolling(ETF_FILTER["amount_window"]).median()
    vol = (df["close"].pct_change()
           .rolling(ETF_FILTER["vol_window"]).std() * (252 ** 0.5))
    age_days = (df.index - df.index[0]).days
    return ((age_days >= ETF_FILTER["min_list_days"])
            & (amt >= ETF_FILTER["min_avg_amount"])
            & (vol >= ETF_FILTER["min_ann_vol"]))


def select_pit_pool(panel, tr_s, tr_e, min_share=0.5, max_codes=100):
    """返回 (入选代码表, 读数 dict)：挑池只用 [tr_s, tr_e] 这一段，不碰未来。

    排序键用**段内**成交额中位数（点时），不是"今天"的中位数 ⇒ 截断本身也不再前视。"""
    cand, shares, amts = [], {}, {}
    for code, df in panel.items():
        win = df.loc[tr_s:tr_e]
        if len(win) <= GATE_BARS:        # 与训练段长度闸同一条
            continue
        m = tradable_mask(df).loc[tr_s:tr_e]
        share = float(m.mean()) if len(m) else 0.0
        if share < min_share:
            continue
        cand.append(code)
        shares[code] = share
        amts[code] = float(pd.to_numeric(win["amount"], errors="coerce").median())
    cand.sort(key=lambda c: -amts[c])
    sel = cand[:max_codes]
    thick = pd.concat([panel[c].loc[tr_s:tr_e]["close"] for c in sel],
                      axis=1).notna().sum(axis=1) if sel else pd.Series(dtype=int)
    return sel, {
        "点时池_截断前": len(cand),
        "点时池_入选": len(sel),
        "日均厚": round(float(thick.mean()), 1) if len(sel) else 0.0,
        "≥30只天%": round(float((thick >= MIN_CS).mean() * 100), 1) if len(sel) else 0.0,
        "过闸天数占比中位": round(float(pd.Series([shares[c] for c in sel]).median()), 3)
        if sel else 0.0,
    }


class FoldUniverse:
    """折内点时宇宙：给策略层用的最小替身，只会回答"这一天谁能买"。

    `ETFUniverse.get_tradable_at` 只看 list_date、且限定在**今日挑出的 100 只**里，
    折内换池子必须换一个会读这段历史的对象 ⇒ 这里用"该标的本段首根 bar + 上市满
    min_list_days"作同日可判的代理（镜像里没有落盘每只的上市日，首根 bar 是本段
    数据能给出的最诚实的下界，只会**偏晚**、不会把没上市的代码放进来）。"""

    def __init__(self, panel, codes, name="点时池"):
        self.name = name
        self._first = {c: panel[c].index[0] for c in codes if len(panel[c])}
        self._codes = sorted(self._first)
        self.universe = pd.DataFrame({"code": self._codes})

    def get_tradable_at(self, date):
        cutoff = pd.Timestamp(date) - pd.Timedelta(
            days=ETF_FILTER["min_list_days"])
        return [c for c in self._codes if self._first[c] <= cutoff]


def load_wide_panel(loader, pool):
    """把镜像里那些**不在今日池**的标的按生产同一条加载路径补进来。

    走 `DataLoader` 而不是自己 read_csv：复权口径、时间列标准化、区间裁剪都跟
    主线逐字一致，折内挖出来的因子与主链吃的必须是同一种面板。
    返回 {code: df}（含原池），加载失败的代码只记账不致命。"""
    extra = [c for c in mirror_codes() if c not in pool]
    print(f"  点时池补数：今日池 {len(pool)} 只 + 镜像独有 {len(extra)} 只"
          f"（走缓存→镜像优先级，不回源网络）")
    wide = dict(pool)
    got = loader.load_pool(extra)
    wide.update(got)
    missing = len(extra) - len(got)
    print(f"  点时池补数完成：可用 {len(wide)} 只 / 镜像独有取到 {len(got)} 只"
          f"{f' / 缺 {missing} 只' if missing else ''}")
    return wide
