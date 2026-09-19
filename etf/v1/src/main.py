# -*- coding: utf-8 -*-
"""研究主流程（支持日线 + 分钟线）"""

import json
import numpy as np
import pandas as pd
import _bootstrap  # noqa: F401  必须先于项目模块导入
from config import (
    FREQ, DSR, MULTI_STRATEGY, RISK_BUDGET,
    MULTI_SOURCE, FREQ_MAP, LOOKBACK_BARS,
)
from etf_universe import get_universe
from data_loader import DataLoader
from factors import FACTOR_REGISTRY
from factor_dsl import compute_ic
from factor_orthogonal import orthogonalize_factors
from risk_budget import compute_factor_weights
from strategy import IntradayRotationStrategy
from backtest import get_backtester, backtest_with_params
from dsr import deflated_sharpe_ratio, annualize_sharpe, TrialCounter
from frequency_adapter import get_adapter


def mine_factors(pool):
    print("\n========== 因子挖掘 ==========")
    factors = []
    for name, fn in FACTOR_REGISTRY.items():
        impl, ics = {}, []
        for code, df in pool.items():
            try:
                f = fn(df)
                fr = df["close"].pct_change().shift(-1)
                ic = compute_ic(f, fr)
                if not np.isnan(ic):
                    impl[code] = {"factor": f, "ic": ic}
                    ics.append(ic)
            except Exception:
                continue
        if impl:
            mean_ic = float(np.mean(ics))
            std_ic = float(np.std(ics)) if len(ics) > 1 else 1.0
            factors.append({"name": name, "mean_ic": mean_ic,
                            "icir": mean_ic / (std_ic + 1e-9),
                            "impl": impl, "source": "simple"})
            print(f"  {name:20s} IC={mean_ic:+.4f}")
    factors = [f for f in factors
               if abs(f["mean_ic"]) >= 0.005]
    factors.sort(key=lambda x: abs(x["mean_ic"]), reverse=True)
    print(f"  入池因子: {len(factors)}")
    return factors


def main():
    adapter = get_adapter()
    print("=" * 60)
    print(f"  ETF 量化系统 | 频率={FREQ} "
          f"({'日线' if not adapter.is_intraday else '分钟线'})")
    print(f"  回看窗口: {LOOKBACK_BARS} bar "
          f"(≈{LOOKBACK_BARS / adapter.bars_per_day:.1f} 天)")
    print(f"  年化系数: {adapter.bars_per_year}")
    print("=" * 60)

    # 1. ETF 池
    print("\n[1/7] 构建 ETF 池...")
    universe = get_universe()
    codes = universe.universe["code"].tolist()

    # 2. 数据
    print(f"\n[2/7] 加载 {FREQ} 数据...")
    loader = DataLoader(freq=FREQ)
    pool = loader.load_pool(codes)
    if not pool:
        print("❌ 无数据")
        return

    tc = TrialCounter()
    tc.reset()

    # 3. 因子
    print("\n[3/7] 因子挖掘...")
    raw_factors = mine_factors(pool)
    tc.add(len(raw_factors))
    if not raw_factors:
        print("❌ 无因子")
        return

    # 4. 正交化
    print("\n[4/7] 正交化...")
    factors = orthogonalize_factors(raw_factors) or raw_factors

    # 5. 信号
    print("\n[5/7] 生成信号...")
    weights = compute_factor_weights(factors, pool)
    ref_code = next(iter(pool))
    all_ts = pool[ref_code].index

    strategy = IntradayRotationStrategy(
        factors, pool, universe, factor_weights=weights)
    signals = strategy.generate_signals(all_ts)
    print(f"  bar 数: {len(signals)}")

    # 6. 回测
    print("\n[6/7] 回测...")
    bt = get_backtester(pool, universe)
    result = bt.run(signals)

    # DSR
    eq = result["equity"]["equity"]
    rets = eq.pct_change().dropna().values
    n_trials = DSR.get("n_trials") or tc.get()
    dsr_res = deflated_sharpe_ratio(rets, n_trials=n_trials)
    dsr_res["sharpe_annual"] = adapter.annualize_sharpe(
        dsr_res.get("sr_observed", 0))

    # 7. 保存
    print("\n[7/7] 保存...")
    result["equity"].to_csv(f"equity_{FREQ}.csv",
                             encoding="utf-8-sig")
    result["trades"].to_csv(f"trades_{FREQ}.csv", index=False,
                             encoding="utf-8-sig")
    signals.to_csv(f"signals_{FREQ}.csv", encoding="utf-8-sig")
    pd.DataFrame([dsr_res]).to_csv(f"dsr_{FREQ}.csv",
                                    index=False,
                                    encoding="utf-8-sig")
    # 兼容旧路径
    result["equity"].to_csv("equity.csv", encoding="utf-8-sig")
    result["trades"].to_csv("trades.csv", index=False,
                             encoding="utf-8-sig")
    signals.to_csv("signals.csv", encoding="utf-8-sig")
    pd.DataFrame([dsr_res]).to_csv("dsr.csv", index=False,
                                    encoding="utf-8-sig")

    with open(f"optimized_params_{FREQ}.json", "w",
              encoding="utf-8") as f:
        json.dump({"freq": FREQ, "n_factors": len(factors),
                   "factor_weights": weights,
                   "n_trials": n_trials,
                   "bars_per_year": adapter.bars_per_year},
                  f, ensure_ascii=False, indent=2, default=str)

    print("\n" + "=" * 60)
    print("  绩效")
    print("=" * 60)
    for k, v in result["stats"].items():
        print(f"  {k:10s}: {v}")
    print(f"\n  DSR: {dsr_res.get('dsr', 0):.4f}")
    print("\n🎉 完成！")


if __name__ == "__main__":
    main()