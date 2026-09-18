# -*- coding: utf-8 -*-
"""内置因子库"""

import numpy as np
import pandas as pd


def momentum(df, window=20):
    return df["close"].pct_change(window)


def reversal(df, window=5):
    return -df["close"].pct_change(window)


def volatility(df, window=20):
    return -df["close"].pct_change().rolling(window).std()


def ma_ratio(df, short=5, long=20):
    ma_s = df["close"].rolling(short).mean()
    ma_l = df["close"].rolling(long).mean()
    return ma_s / ma_l - 1


def rsi(df, window=14):
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / (loss + 1e-9)
    return (100 - 100 / (1 + rs) - 50) / 50


def volume_ratio(df, window=20):
    return df["volume"] / df["volume"].rolling(window).mean()


def price_position(df, window=20):
    high = df["high"].rolling(window).max()
    low = df["low"].rolling(window).min()
    return 2 * (df["close"] - low) / (high - low + 1e-9) - 1


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