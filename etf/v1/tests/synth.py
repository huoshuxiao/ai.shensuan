# -*- coding: utf-8 -*-
"""测试用合成行情：确定性几何随机游走，不依赖任何网络数据源。"""

import numpy as np
import pandas as pd


# walk_forward_run 的训练段长度门槛（backtest/walk_forward.py 里 `len(df) > 240`）
TRAIN_GATE_BARS = 240


def wf_pool_bars(margin=2):
    """喂给 walk-forward 测试的最小时间轴长度（按当前分折配置反解，再留 margin 倍余量）。

    长度不够时 `walk_forward_run` 会**每折都跳过**（训练段 ≤240 bar 无标的可用），
    测试拿空 folds 静默通过或报莫名其妙的 KeyError。09-24 把 train_ratio
    从 0.7 定档到 0.5 时，写死 1200 bar 的夹具就整批退化成这样 ——
    门槛与 ratio 都是配置，所以这里按配置算，不写死。"""
    from config import WALK_FORWARD
    n_splits = WALK_FORWARD["n_splits"]
    ratio = WALK_FORWARD["train_ratio"]
    window = int((TRAIN_GATE_BARS + 1) / ratio) + 2
    return window * n_splits * margin


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
