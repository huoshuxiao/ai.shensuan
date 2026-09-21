# -*- coding: utf-8 -*-
"""PBO (Probability of Backtest Overfitting) via CSCV

组合对称交叉验证（Bailey et al. 2017）：把回测期切成 S 段，
任取一半做样本内(IS)、另一半做样本外(OOS)。若在 IS 上最优的配置
经常在 OOS 上排名垫底，则当前"最优选择"大概率是挑参数的运气。"""

import itertools
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from config import PBO as PBO_CFG


def _sharpe(returns):
    """逐 bar 夏普 mean/std；样本过少或零波动记 -inf（永不入选最优）"""
    r = returns[~np.isnan(returns)]
    if len(r) < 5:
        return -np.inf
    s = r.std(ddof=1)
    if s < 1e-12:
        return -np.inf
    return r.mean() / s


def build_returns_matrix(equity_dict, common_index=None):
    """净值字典 -> 对齐的收益矩阵（列=配置，行=时间）。
    全 NaN 行删除，单列缺失填 0（视作空仓）。"""
    rets = {name: eq.pct_change() for name, eq in equity_dict.items()}
    df = pd.DataFrame(rets)
    if common_index is not None:
        df = df.reindex(common_index)
    df = df.dropna(how="all").fillna(0.0)
    return df


def cscv_pbo(returns_df, n_splits=None, max_combinations=None):
    """CSCV 主流程：
    1) 时间轴均分 n_splits 段（强制偶数，IS/OOS 各半，对称性）；
    2) 枚举 C(n_splits, half) 个划分（超过 max_combinations 随机采样）；
    3) 每个划分：IS 上选夏普最优配置 n*，看它在 OOS 全体的
       相对排名 ω̂ = rank(n*)/(N+1) ∈ (0,1)；
    4) logit(ω̂) = ln(ω̂/(1-ω̂))；
    5) PBO = P(logit ≤ 0) = OOS 排名掉到中位数以下的划分占比。
    PBO < 0.5 通过；越接近 1 说明 IS 最优 ≈ OOS 最差（纯过拟合）。"""
    n_splits = n_splits or PBO_CFG["n_splits"]
    max_combinations = max_combinations or PBO_CFG["max_combinations"]

    if n_splits % 2 == 1:
        n_splits -= 1
    half = n_splits // 2
    T, N = returns_df.shape
    if T < n_splits * 5 or N < 3:
        return {"pbo": 1.0, "error": "样本或策略不足", "passed": False}

    segments = np.array_split(np.arange(T), n_splits)
    all_combos = list(itertools.combinations(range(n_splits), half))
    if len(all_combos) > max_combinations:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(all_combos), max_combinations, replace=False)
        all_combos = [all_combos[i] for i in idx]

    logits, oos_ranks = [], []
    values = returns_df.values

    for combo in all_combos:
        is_idx = np.concatenate([segments[i] for i in combo])
        oos_combo = [i for i in range(n_splits) if i not in combo]
        oos_idx = np.concatenate([segments[i] for i in oos_combo])

        # 样本内冠军：IS 上夏普最大的配置下标
        is_sharpe = np.array([_sharpe(values[is_idx, j]) for j in range(N)])
        n_star = int(np.argmax(is_sharpe))

        oos_sharpe = np.array([_sharpe(values[oos_idx, j]) for j in range(N)])
        if np.all(np.isinf(oos_sharpe)):
            continue

        ranks = rankdata(oos_sharpe)                    # OOS 全排名
        rank_n_star = ranks[n_star] / (N + 1)           # 冠军的相对排名 ω̂
        oos_ranks.append(rank_n_star)
        eps = 1e-6                                      # 防 logit 发散
        r = min(max(rank_n_star, eps), 1 - eps)
        logits.append(np.log(r / (1 - r)))

    if not logits:
        return {"pbo": 1.0, "error": "无有效切分", "passed": False}

    logits = np.array(logits)
    pbo = float((logits <= 0).mean())                   # ω̂ ≤ 0.5 的频率
    return {"pbo": pbo, "logits_mean": float(logits.mean()),
            "logits_std": float(logits.std()),
            "n_combinations": len(logits),
            "oos_rank_median": float(np.median(oos_ranks)),
            "passed": pbo < 0.5, "logits": logits.tolist()}


def collect_config_equities(pool, universe, factor_sets, risk_param_sets,
                            strategy_cls, backtester_cls, max_configs=None,
                            all_ts=None, strategy_kwargs=None):
    """为 PBO 组装"配置族"净值：因子集 × 风控参数 的笛卡尔积，
    每个组合跑一次回测，列名 {因子集名}_{风控参数名}；
    上限 max_configs 截断（组合数爆炸时只评估前若干组）。

    factor_sets / risk_param_sets 可直接传 dict（{配置名: 取值}），此时用键
    命名（如 base、daily_stop_loss=-0.05），PBO 结果里能看出是哪一档参数在
    样本内称王；传 list 则退回下标 F{i} / R{i}。
    strategy_kwargs：构造策略的额外入参（如研究侧 factor_weights），缺了它
    配置族的信号与实际采用的加权方式不一致；
    all_ts：信号时间轴，默认取历史最长的标的——池按成交额排序，首位常是
    近年新 ETF，用它会把整族配置都截到同一段短历史上。"""
    max_configs = max_configs or PBO_CFG["n_configs"]
    equity_dict = {}
    if all_ts is None:
        ref_code = max(pool, key=lambda c: len(pool[c]))
        all_ts = pool[ref_code].index
    count = 0

    def _items(seq, prefix):
        if isinstance(seq, dict):
            return [(str(k), v) for k, v in seq.items()]
        return [(f"{prefix}{i}", v) for i, v in enumerate(seq)]

    for fkey, factors in _items(factor_sets, "F"):
        if not factors:
            continue
        strategy = strategy_cls(factors, pool, universe,
                                **(strategy_kwargs or {}))
        signals = strategy.generate_signals(all_ts)
        for rkey, risk_params in _items(risk_param_sets, "R"):
            if count >= max_configs:
                break
            try:
                bt = backtester_cls(pool, universe, risk_params)
                res = bt.run(signals)
                equity_dict[f"{fkey}_{rkey}"] = res["equity"]["equity"]
                count += 1
            except Exception:
                continue
    return equity_dict