# -*- coding: utf-8 -*-
"""内置因子库

每个因子函数接收单只 ETF 的 OHLCV DataFrame，返回与 index 对齐的
Series（值越大代表越看多）。窗口参数以 bar 为单位（日线=天）。
"""

import numpy as np
import pandas as pd


def momentum(df, window=20):
    """动量：近 window 期收益率，M_t = P_t / P_{t-window} - 1。
    强者恒强逻辑：过去涨得多的继续买入。"""
    return df["close"].pct_change(window)


def reversal(df, window=5):
    """反转：短期收益取负，M_t = -(P_t / P_{t-window} - 1)。
    博弈逻辑：近 window 期跌得越多越看多（均值回复）。"""
    return -df["close"].pct_change(window)


def volatility(df, window=20):
    """低波动：日收益率 window 期标准差取负，σ = -std(r_t, window)。
    低波异象：波动率低的标的风险调整后表现更好，故取负号看多低波。"""
    return -df["close"].pct_change().rolling(window).std()


def ma_ratio(df, short=5, long=20):
    """均线比：短均线相对长均线的偏离，(MA_short / MA_long - 1)。
    >0 表示短期趋势强于长期趋势（多头排列程度）。"""
    ma_s = df["close"].rolling(short).mean()
    ma_l = df["close"].rolling(long).mean()
    return ma_s / ma_l - 1


def rsi(df, window=14):
    """RSI 相对强弱（Wilder 均值版），映射到 [-1, 1]：
    RS = mean(gain, w) / mean(loss, w)，RSI = 100 - 100/(1+RS)，
    返回 (RSI - 50) / 50。正值偏超买、负值偏超卖；
    在截面对比中作为强弱打分使用。"""
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / (loss + 1e-9)  # 防零除：全跌时无涨跌幅退化为 0 附近
    return (100 - 100 / (1 + rs) - 50) / 50


def volume_ratio(df, window=20):
    """量比：当日成交量 / window 期均量，V_t = vol_t / mean(vol, w)。
    >1 放量（关注度上升），<1 缩量。"""
    return df["volume"] / df["volume"].rolling(window).mean()


def price_position(df, window=20):
    """价格分位：收盘价在 window 期高低价区间的位置，
    Pos = 2*(C - min(L,w)) / (max(H,w) - min(L,w)) - 1 ∈ [-1, 1]。
    +1=区间顶部（突破），-1=区间底部（超跌）。"""
    high = df["high"].rolling(window).max()
    low = df["low"].rolling(window).min()
    return 2 * (df["close"] - low) / (high - low + 1e-9) - 1


# 注册表：因子名 -> 取数 lambda。参数（窗口）编码在名字里，
# 如 ma_ratio_5_20 = ma_ratio(short=5, long=20)
FACTOR_REGISTRY = {
    "momentum_20":      lambda df: momentum(df, 20),
    "momentum_10":      lambda df: momentum(df, 10),
    "reversal_5":       lambda df: reversal(df, 5),
    "volatility_20":    lambda df: volatility(df, 20),
    "ma_ratio_5_20":    lambda df: ma_ratio(df, 5, 20),
    "ma_ratio_10_30":   lambda df: ma_ratio(df, 10, 30),
    "rsi_14":           lambda df: rsi(df, 14),
    "volume_ratio_20":  lambda df: volume_ratio(df, 20),
    "price_position_20": lambda df: price_position(df, 20),
}

# 与上面**逐条配对**的 DSL 表达式（10-06 用户裁「第 2 问＝丙：不改闸，改发射端」）。
#
# 为什么要有这张表：`expr` 不是注释——`run_live.py:193` 会拿库里存的 `expr` 重新求值
# 给实盘打分，而库里的 `ic` 是用**左边那台 lambda** 量出来的；同时
# `etf/v1/src/main.py` 的写库腿 `f.get("expr", "")` 在没有这张表时一律落空串，
# 于是这些名字在库里挂着空表达式、被 `run_live.py:156` 的非空过滤直接挡在实盘门外。
#
# 每一条都必须与对应 lambda **逐位相同**（不是"差不多"）。对拍尺＝
# `etf/v1/temp/registry_expr_equivalence_1006.py`：真池子＋人造停牌缺口池各拍一遍，
# 外加三支负对照（窗口差一档 / 防零除少一个）。取形的规矩是**按构造相同优先**：
# 前四条写成方法链（与 lambda 同一台 pandas 调用）而不是 DSL 惯用形
# `close / delay(close, 20) - 1`——后者在真池子上与 lambda 逐位相同，但在有停牌缺口的
# 数据上就分家（DSL 的 `returns` 写 `fill_method=None`，lambda 用 pandas 默认的 `'pad'`），
# 那是"今天相等"不是"按构造相等"。夹具 `test_registry_expr_1006.py` 把这条对拍常驻。
FACTOR_EXPR = {
    "momentum_20": "close.pct_change(20)",
    "momentum_10": "close.pct_change(10)",
    "reversal_5": "-close.pct_change(5)",
    "volatility_20": "-close.pct_change().rolling(20).std()",
    "ma_ratio_5_20": "ma(close, 5) / ma(close, 20) - 1",
    "ma_ratio_10_30": "ma(close, 10) / ma(close, 30) - 1",
    "rsi_14": "(100 - 100 / (1 + ts_mean(delta(close, 1).clip(lower=0), 14) / "
              "(ts_mean((-delta(close, 1)).clip(lower=0), 14) + 1e-9)) - 50) / 50",
    "volume_ratio_20": "volume / ts_mean(volume, 20)",
    "price_position_20": "2 * (close - ts_min(low, 20)) / "
                         "(ts_max(high, 20) - ts_min(low, 20) + 1e-9) - 1",
}

