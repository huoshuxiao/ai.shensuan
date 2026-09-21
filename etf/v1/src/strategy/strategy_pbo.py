# -*- coding: utf-8 -*-
"""策略级 PBO（回测过拟合概率, López de Prado CSCV 框架）

因子级 PBO 衡量"参数挑出来的最优因子是不是运气"；本模块衡量
"这条策略净值曲线本身是不是过拟合"——配置族默认取真实搜索过的参数组合
（build_risk_config_family × collect_config_equities），拿不到配置族时
退化为同一收益序列的降权伪变体，再跑组合对称交叉验证。"""

import numpy as np
import pandas as pd
from config import STRATEGY_PBO, RISK_SEARCH_SPACE
from pbo import cscv_pbo


def build_risk_config_family(base_risk, search_space=None, max_configs=None):
    """由基线风控参数 + 研究搜索空间构造"一次只动一维"(OFAT) 的配置族。

    全笛卡尔积是 Π|space| = 3^5 = 243 档，逐档回测不现实；OFAT 只保留基线在
    每个维度上的邻域，配置数 = 1 + Σᵢ(|spaceᵢ| - 1)，仍覆盖"挑参数"这一步
    所在的选择空间。基线本身必须在族内——它就是样本内被选中的那个配置。
    返回 {配置名: risk_params}，配置名直接进 PBO 结果便于溯源。"""
    space = search_space or RISK_SEARCH_SPACE
    max_configs = max_configs or STRATEGY_PBO.get("max_family_configs", 12)
    family = {"base": dict(base_risk)}
    # 基线在每个维度上的邻域先剔除自身取值，再按维度轮换入族：
    # 一旦触到 max_configs 就截断，轮换保证每维都至少贡献一档，
    # 否则靠后的维度会被整族丢弃，配置族便不再覆盖"挑参数"的搜索空间。
    dims = [(k, [v for v in vs if v != base_risk.get(k)])
            for k, vs in space.items()]
    for i in range(max((len(vs) for _, vs in dims), default=0)):
        for k, vs in dims:
            if len(family) >= max_configs:
                return family
            if i < len(vs):
                p = dict(base_risk)
                p[k] = vs[i]
                family[f"{k}={vs[i]}"] = p
    return family


def _make_pseudo_configs(rets, n_configs=6):
    """构造伪配置族：q ∈ [0, 0.3] 均匀取 n_configs 档，
    q=0 为原始收益；q>0 时把波动率最高的 q 分位 bar 收益置 0
    （相当于"波动率过滤阈值"不同的策略变体）。
    这些变体共享同一真实历史，PBO 高说明结论对任意微小变体都翻转。"""
    vol = rets.rolling(60).std()
    vol = vol.fillna(vol.median())
    configs = {}
    for i, q in enumerate(np.linspace(0.0, 0.3, n_configs)):
        if q == 0:
            configs[f"cfg_q{q:.2f}"] = rets.values
        else:
            thresh = vol.quantile(1 - q)
            mask = (vol <= thresh).values
            configs[f"cfg_q{q:.2f}"] = rets.where(mask, 0.0).values
    return configs


def strategy_level_pbo(equity, n_splits=None, max_combinations=None,
                       configs=None):
    """净值序列 -> 配置收益矩阵 -> CSCV 求 PBO。

    configs：预先算好的收益矩阵（列=配置、行=bar，如
    `build_returns_matrix(collect_config_equities(...))`）。传真实配置族时
    PBO 才包含"从这些配置里挑最优"的选择偏差；缺省时退化为同一条净值的
    伪变体，只能衡量结论对微小扰动的敏感性。
    passed: PBO < pbo_threshold（默认 0.5），越小越可信。"""
    n_splits = n_splits or STRATEGY_PBO["n_splits"]
    max_combinations = max_combinations or STRATEGY_PBO["max_combinations"]
    min_bars = STRATEGY_PBO["min_equity_bars"]
    rets = equity.pct_change().dropna()
    if len(rets) < min_bars:
        return {"pbo": 1.0, "error": "样本不足", "passed": False,
                "config_family": None, "n_configs": 0}
    if configs is None:
        rets_df = pd.DataFrame(_make_pseudo_configs(rets))
        family = "pseudo"
    else:
        rets_df = pd.DataFrame(configs).dropna(how="all")
        family = "real"
    if rets_df.shape[1] < 3:
        return {"pbo": 1.0, "error": "配置不足", "passed": False,
                "config_family": family, "n_configs": int(rets_df.shape[1])}
    result = cscv_pbo(rets_df, n_splits=n_splits,
                      max_combinations=max_combinations)
    result["passed"] = result.get("pbo", 1.0) < STRATEGY_PBO["pbo_threshold"]
    result["config_family"] = family
    result["n_configs"] = int(rets_df.shape[1])
    return result


def filter_strategies_by_pbo(strategies, min_pbo_pass=2):
    """多策略池过拟合过滤器：仅保留 PBO 达标的策略；
    若达标数 < min_pbo_pass，按 PBO 从小到大补足保底数量
    （防止全拒导致组合层无策略可用）。"""
    print("\n========== 策略级 PBO 过滤 ==========")
    pbo_results, filtered = {}, {}
    for name, s in strategies.items():
        r = strategy_level_pbo(s["equity"]["equity"])
        pbo_results[name] = r
        flag = "✅" if r.get("passed") else "❌"
        print(f"  {name:35s} PBO={r.get('pbo', 1):.4f} {flag}")
        if r.get("passed"):
            filtered[name] = s
    if len(filtered) < min_pbo_pass and strategies:
        ranked = sorted(pbo_results.items(),
                        key=lambda x: x[1].get("pbo", 1.0))
        for name, _ in ranked[:min_pbo_pass]:
            filtered[name] = strategies[name]
    print(f"  过滤后: {len(filtered)}/{len(strategies)}")
    return {"filtered": filtered, "pbo_results": pbo_results}