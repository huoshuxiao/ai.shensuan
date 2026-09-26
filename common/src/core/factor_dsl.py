# -*- coding: utf-8 -*-
"""因子 DSL：LLM 生成 / 遗传编程产出的表达式只能使用这些算子。

两类符号在 safe_eval 中的取用形态不同：
- 列名（close/open/.../returns）在求值环境里绑定为该 ETF 的实际
  Series，表达式直接当向量用（GP 终端即依赖此形态）；
- 算子（ma/std/delta/...）保持函数调用，第一个参数传序列
  （df 类算子如 ma(df, n) 例外，收 DataFrame）。

**这里是「能求值」的注册表，不是「会生成」的白名单**：GP 的算子集在
`config_base.GENETIC_OPERATORS`、LLM 的算子清单在四处提示词里各自**硬编码**，都不读本字典
⇒ 往这里加算子**不会**扩大任何一条线的搜索/生成空间，只让「已经写出来的表达式」求得出。
"""

import numpy as np
import pandas as pd


# ===================== 窗口算子的实现 =====================
# 一律沿用本线既有口径：`rolling(n)` 的 min_periods 默认等于 n ⇒ **窗口内含缺值则该点为
# NaN**（qlib 的 IdxMax/Quantile/Slope 用 min_periods=1，缺值只是被跳过；两者在停牌股上
# 会得到「一个数」和「没有数」的区别，跨实现对表时先确认这一条）。
# 之所以写成函数而不是 lambda：`safe_eval` 用 `eval(expr, {"__builtins__": {}}, env)`，
# globals 与 locals 分开传 ⇒ 表达式里**内联 lambda 的自由变量按 globals 解析**，
# `close.rolling(20).apply(lambda w: np.argmax(w), raw=True)` 会 NameError。新增语义
# 必须注册成本模块的函数（函数对象自带真 `__globals__`）才在 DSL 里可用。

def _rank(s, n):
    """当前值在过去 n 期内的百分位排名 ∈ (0, 1]，缺值先剔除再按比例。

    与 `pd.Series(window).rank(pct=True).iloc[-1]` **逐位等价**（ties 走 average 法：
    名次 = 严格小于的个数 + (相等个数+1)/2，分母 = 非缺值个数），但比
    `rolling.apply(raw=False)` 快一个量级。单价是本模块自己复测的（09-26，A 股全市场
    面板 299 只抽样外推，见 `shell/stock/rank_speed_0926.py`）：447 → 38 ms/只，
    即单条 `rank(close,20)` 扫全市场 2538s → 215s，快 11.8 倍。
    """
    n = int(n)

    def _f(w):
        v = w[-1]
        if np.isnan(v):
            return np.nan
        val = w[~np.isnan(w)]
        less = (val < v).sum()
        eq = (val == v).sum()
        return float(less + (eq + 1) / 2.0) / len(val)

    return s.rolling(n).apply(_f, raw=True)


def _ts_max(s, n):
    """窗口最大值（作用于任意列；`max` 那四个钉死 close，本条不钉）"""
    return s.rolling(int(n)).max()


def _ts_min(s, n):
    return s.rolling(int(n)).min()


def _corr(a, b, n):
    """两序列的滚动 Pearson 相关 ∈ [-1, 1]（对齐到共同索引后逐窗算）"""
    return a.rolling(int(n)).corr(b)


def _slope(s, n):
    """窗口内 y 对「窗口内第几天」的一元线性回归斜率（闭式解，全程向量算）。

    slope = Σ(t-t̄)(y-ȳ)/Σ(t-t̄)²，t 取整段的绝对序号不影响斜率；分子就是窗口协方差
    `cov_pop`，分母 = n·var_pop(t) = n·(n²-1)/12 ⇒ 除以 (n²-1)/12 即得 slope。
    与逐窗 `np.polyfit` 最大差实测 2.2e-13，但快 7.8 倍（145s vs 1135s / 全市场）。
    """
    n = int(n)
    t = pd.Series(np.arange(1, len(s) + 1, dtype=float), index=s.index)
    cov = (t * s).rolling(n).mean() - s.rolling(n).mean() * t.rolling(n).mean()
    return cov / ((n * n - 1) / 12.0)


def _rsquare(s, n):
    """上面那条回归的 R² ∈ [0,1]。

    R² = corr(t, y)² = slope²·var_pop(t)/var_pop(y)；`pandas` 的 std 是 ddof=1 的样本
    标准差，换成 var_pop 要乘 (n-1)/n ⇒ 合并成 slope²·n(n+1)/12 / std²。
    """
    n = int(n)
    return _slope(s, n) ** 2 * (n * (n + 1) / 12.0) / (s.rolling(n).std() ** 2 + 1e-12)


def _resi(s, n):
    """回归残差的最后一天取值：y_末 − ȳ − slope·(n-1)/2（拟合线在窗口末端差多远）。"""
    n = int(n)
    return s - s.rolling(n).mean() - _slope(s, n) * (n - 1) / 2.0


def _quantile(s, n, q):
    """窗口分位数**阈值本身**（`rank` 要的是当前值排第几，本条要的是那个数值边界）。"""
    return s.rolling(int(n)).quantile(float(q))


def _arg_pos(s, n, largest):
    """窗口内极值出现在第几天 ∈ [1, n]（含 NaN 的窗口整段返回 NaN，见上方口径说明）。"""
    n = int(n)

    def _f(w):
        if np.isnan(w).any():
            return np.nan
        return float((np.argmax(w) if largest else np.argmin(w)) + 1)

    return s.rolling(n).apply(_f, raw=True)


def _idx_max(s, n):
    return _arg_pos(s, n, True)


def _idx_min(s, n):
    return _arg_pos(s, n, False)


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
    # （09-26 换成 `_rank` 的快实现：与原来 `apply(raw=False)` 的 pandas 版**逐位等价**，
    #   只是把全市场单条 2538s 降到 215s——含停牌缺值的窗口两边也相等，实测过。）
    "rank":   _rank,
    # ---- 09-26 为 Alpha158 全市场扫描新增：7 个新语义 + 2 个「极值放宽到任意列」 ----
    # 注意 `max/min` 仍钉死作用于 close（上面那四个），要极值别的列用 ts_max/ts_min。
    "ts_max": _ts_max,            # 窗口最大值（任意列）
    "ts_min": _ts_min,            # 窗口最小值（任意列）
    "corr":   _corr,              # 两序列滚动 Pearson 相关 ∈ [-1, 1]
    "slope":  _slope,             # 窗口内 y 对「第几天」的一元回归斜率
    "rsquare": _rsquare,          # 上面那条回归的 R² ∈ [0, 1]
    "resi":   _resi,              # 最后一天的回归残差（y_末 − 拟合值）
    "quantile": _quantile,        # 窗口分位数阈值（与 rank 不同：要的是数值边界）
    "idx_max": _idx_max,          # 窗口最大值出现在第几天 ∈ [1, n]
    "idx_min": _idx_min,          # 窗口最小值出现在第几天 ∈ [1, n]
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
