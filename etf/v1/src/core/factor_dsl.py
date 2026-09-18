# -*- coding: utf-8 -*-
"""因子 DSL：LLM 生成的表达式只能使用这些算子"""

import numpy as np
import pandas as pd

FACTOR_DSL = {
    "close":  lambda df: df["close"],
    "open":   lambda df: df["open"],
    "high":   lambda df: df["high"],
    "low":    lambda df: df["low"],
    "volume": lambda df: df["volume"],
    "returns": lambda df: df["close"].pct_change(),
    "ma":     lambda df, n: df["close"].rolling(int(n)).mean(),
    "std":    lambda df, n: df["close"].rolling(int(n)).std(),
    "max":    lambda df, n: df["close"].rolling(int(n)).max(),
    "min":    lambda df, n: df["close"].rolling(int(n)).min(),
    "delay":  lambda s, n: s.shift(int(n)),
    "delta":  lambda s, n: s.diff(int(n)),
    "ts_sum": lambda s, n: s.rolling(int(n)).sum(),
    "ts_mean": lambda s, n: s.rolling(int(n)).mean(),
    "ts_std": lambda s, n: s.rolling(int(n)).std(),
    "abs":    lambda s: s.abs(),
    "log":    lambda s: np.log(s.clip(lower=1e-9)),
    "sign":   lambda s: np.sign(s),
    "rank":   lambda s, n: s.rolling(int(n)).apply(
                  lambda x: pd.Series(x).rank(pct=True).iloc[-1],
                  raw=False),
}


def safe_eval(expr: str, df: pd.DataFrame) -> pd.Series:
    env = {"df": df, "np": np, "pd": pd, **FACTOR_DSL}
    try:
        result = eval(expr, {"__builtins__": {}}, env)
        if isinstance(result, pd.Series):
            return result
        return pd.Series(result, index=df.index)
    except Exception as e:
        raise ValueError(f"表达式求值失败: {expr} -> {e}")


def compute_ic(factor: pd.Series, forward_ret: pd.Series) -> float:
    df = pd.concat([factor, forward_ret], axis=1).dropna()
    if len(df) < 30:
        return 0.0
    return df.iloc[:, 0].corr(df.iloc[:, 1], method="spearman")