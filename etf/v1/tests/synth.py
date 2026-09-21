# -*- coding: utf-8 -*-
"""测试用合成行情：确定性几何随机游走，不依赖任何网络数据源。"""

import numpy as np
import pandas as pd


def make_daily_pool(n_codes=3, n_bars=1200, seed=7, start="2015-01-05",
                    codes=None):
    """构造确定性的合成日线池（几何随机游走 + OHLCV）。

    代码默认用 5103xx 前缀，避开 513/511/518 的 T+0 分支，
    以便测到日线的 T+1 约束。
    """
    rng = np.random.default_rng(seed)
    codes = codes or [f"51030{i}" for i in range(n_codes)]
    idx = pd.bdate_range(start, periods=n_bars)
    pool = {}
    for k, code in enumerate(codes):
        drift = 0.0003 + 0.0002 * k
        r = rng.normal(drift, 0.012, n_bars)
        close = 10.0 * np.cumprod(1 + r)
        open_ = close * (1 + rng.normal(0, 0.002, n_bars))
        high = np.maximum(open_, close) * (
            1 + np.abs(rng.normal(0, 0.003, n_bars)))
        low = np.minimum(open_, close) * (
            1 - np.abs(rng.normal(0, 0.003, n_bars)))
        volume = rng.integers(1_000_000, 5_000_000,
                              n_bars).astype(float)
        pool[code] = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close,
             "volume": volume}, index=idx)
    return pool


def make_flat_pool(code="510300", n_bars=60, price=10.0,
                   start="2023-01-03"):
    """恒定价格的单标的池：用来精确核对成本与净值恒等式。"""
    idx = pd.bdate_range(start, periods=n_bars)
    df = pd.DataFrame({"open": price, "high": price, "low": price,
                       "close": price, "volume": 1_000_000.0}, index=idx)
    return {code: df}, idx
