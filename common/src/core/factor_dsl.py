# -*- coding: utf-8 -*-
"""因子 DSL：LLM 生成 / 遗传编程产出的表达式只能使用这些算子。

两类符号在 safe_eval 中的取用形态不同：
- 列名（close/open/.../returns）在求值环境里绑定为该 ETF 的实际
  Series，表达式直接当向量用（GP 终端即依赖此形态）；
- 算子（ma/std/delta/...）保持函数调用，第一个参数传序列
  （df 类算子如 ma(df, n) 例外，收 DataFrame）。
"""

import numpy as np
import pandas as pd

# 算子字典。FACTOR_DSL 里的列名条目为 df 取数 lambda，
# 会在 safe_eval 的环境构造中被同名真实 Series 覆盖（保留仅作注册表）。
FACTOR_DSL = {
    # ---- 行情列（OHLCV 与衍生收益率） ----
    "close":  lambda df: df["close"],          # 收盘价
    "open":   lambda df: df["open"],           # 开盘价
    "high":   lambda df: df["high"],           # 最高价
    "low":    lambda df: df["low"],            # 最低价
    "volume": lambda df: df["volume"],         # 成交量
    # fill_method=None：默认 'pad' 会先把停牌 NaN 填成前值再算收益，
    # 造出「复牌日 0% 收益 + 次日跳空」的假收益序列（A 股全市场样本里
    # 停牌很常见）。无缺口的数据（ETF 日线）两种写法结果完全一致。
    "returns": lambda df: df["close"].pct_change(fill_method=None),   # 日收益率 r_t = P_t/P_{t-1} - 1
    # ---- 价格类窗口算子（作用于 close） ----
    "ma":     lambda df, n: df["close"].rolling(int(n)).mean(),    # 简单移动平均
    "std":    lambda df, n: df["close"].rolling(int(n)).std(),     # 价格窗口标准差
    "max":    lambda df, n: df["close"].rolling(int(n)).max(),     # 窗口最高收盘
    "min":    lambda df, n: df["close"].rolling(int(n)).min(),     # 窗口最低收盘
    # ---- 通用序列变换（作用于任意 Series s） ----
    "delay":  lambda s, n: s.shift(int(n)),                        # 滞后 n 期（防未来函数关键件）
    "delta":  lambda s, n: s.diff(int(n)),                         # 一阶差分 Δs = s_t - s_{t-n}
    "ts_sum": lambda s, n: s.rolling(int(n)).sum(),                # 窗口求和
    "ts_mean": lambda s, n: s.rolling(int(n)).mean(),              # 窗口均值
    "ts_std": lambda s, n: s.rolling(int(n)).std(),                # 窗口标准差
    "abs":    lambda s: s.abs(),                                   # 绝对值
    "log":    lambda s: np.log(s.clip(lower=1e-9)),                # 自然对数（截负值防 log(0)）
    "sign":   lambda s: np.sign(s),                                # 符号函数 ∈ {-1,0,1}
    # 窗口分位：当前值在过去 n 期内的百分位排名 ∈ (0, 1]
    "rank":   lambda s, n: s.rolling(int(n)).apply(
                  lambda x: pd.Series(x).rank(pct=True).iloc[-1],
                  raw=False),
}


def safe_eval(expr: str, df: pd.DataFrame) -> pd.Series:
    """在受限命名空间中求值因子表达式，返回因子值 Series。

    eval 仅暴露 {np, pd, df} + FACTOR_DSL 算子 + 裸名列 Series，
    屏蔽全部 builtins，防止 LLM 生成表达式执行任意代码。

    裸名列（close/open/.../returns）必须绑定为实际 Series 而非
    lambda——GP / 多目标 GP / LLM 模板产出的表达式（如
    `ts_mean(returns, 20) / ts_std(returns, 20)`）直接按变量使用它们；
    放在 **FACTOR_DSL 之后正是为了覆盖其中的同名取数 lambda。
    """
    env = {"df": df, "np": np, "pd": pd, **FACTOR_DSL,
           "open": df["open"], "high": df["high"], "low": df["low"],
           "close": df["close"], "volume": df["volume"],
           "returns": df["close"].pct_change(fill_method=None)}
    try:
        result = eval(expr, {"__builtins__": {}}, env)
        if isinstance(result, pd.Series):
            return result
        return pd.Series(result, index=df.index)
    except Exception as e:
        raise ValueError(f"表达式求值失败: {expr} -> {e}")


def safe_spearman(a: pd.Series, b: pd.Series) -> float:
    """两序列 Spearman 秩相关，常量输入短路返回 0 且抑制告警。

    滚动窗口/GP 子树常产出常量序列（如 sign() 饱和、全 NaN 残段），
    scipy 对常量输入抛 ConstantInputWarning 并返回 nan——逐万次调用
    会淹没日志。所有 IC/相关性计算统一走本函数。"""
    import warnings
    if a.nunique() <= 1 or b.nunique() <= 1:
        return 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return a.corr(b, method="spearman")


def spearman_corr_matrix(wide: pd.DataFrame) -> pd.DataFrame:
    """DataFrame 逐列 Spearman 相关矩阵（常量列抑制告警，
    对应位置由 pandas 返回 nan）"""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return wide.corr(method="spearman")


def compute_ic(factor: pd.Series, forward_ret: pd.Series) -> float:
    """IC（信息系数）：因子值与下一期收益的 Spearman 秩相关。

    衡量截面预测力：|IC| > 0.02 一般认为有效（日频）。
    样本 < 30 或任一输入为常量列时无统计意义，直接返回 0。
    """
    df = pd.concat([factor, forward_ret], axis=1).dropna()
    if len(df) < 30:
        return 0.0
    return safe_spearman(df.iloc[:, 0], df.iloc[:, 1])
