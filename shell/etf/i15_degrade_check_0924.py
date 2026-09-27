# -*- coding: utf-8 -*-
"""#15 第四项（配置族退化出声）的真实跑批检查

只跑 `stage_validation` 里的「策略级 PBO」一环，用三种口径各逼出一条退化
路径，看新告警是否真打印、退化后是否仍按伪变体族出数：
  A 前置条件不满足（use_config_family=False）
  B 回测成功档 <3（max_family_configs=2）
  C 档数缩水（monkeypatch collect_config_equities 少回两档）
不跑 walk-forward / 时间线 / 多策略（省 15 分钟），落盘改指 /tmp，
不覆盖主线产物。

用法：/usr/bin/python3.10 shell/i15_degrade_check_0924.py
"""
import json
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "etf", "v1", "src")
sys.path.insert(0, _SRC)
import _bootstrap  # noqa: E402,F401

import config  # noqa: E402
config.RDAGENT_USE_OFFICIAL_FALLBACK = False
for name in ("WALK_FORWARD", "PBO_TIMELINE", "MULTI_STRATEGY"):
    getattr(config, name)["enabled"] = False

from data_loader import DataLoader  # noqa: E402
from etf_universe import get_universe  # noqa: E402
from dsr import TrialCounter  # noqa: E402
import main  # noqa: E402

main.PBO_RESULT_FILE = "/tmp/i15_degrade_pbo.json"

uni = get_universe()
pool = DataLoader(freq="daily").load_pool(uni.universe["code"].tolist())
ref = max(pool, key=lambda c: len(pool[c]))
all_ts = pool[ref].index

from strategy import IntradayRotationStrategy  # noqa: E402

factors = main.mine_factors(pool)
weights = main.compute_factor_weights(factors, pool)
strategy = IntradayRotationStrategy(factors, pool, uni,
                                    factor_weights=weights)
bt = main.get_backtester(pool, uni, config.RISK_CONTROL)
eq = bt.run(strategy.generate_signals(all_ts))["equity"]["equity"]
print(f"[harness] 因子 {len(factors)} 个 | 净值 {len(eq)} bar")


def once(tag):
    print(f"\n===== {tag} =====")
    main.stage_validation(factors, pool, uni, all_ts, eq, TrialCounter(),
                          factors=factors, weights=weights,
                          risk_params=config.RISK_CONTROL)
    r = json.load(open(main.PBO_RESULT_FILE, encoding="utf-8"))
    print(f"  [harness] 落盘 config_family={r['config_family']} "
          f"n_configs={r['n_configs']} pbo={r['pbo']:.4f} "
          f"passed={r['passed']}")


config.STRATEGY_PBO["use_config_family"] = False
once("A 前置条件不满足")

config.STRATEGY_PBO["use_config_family"] = True
config.STRATEGY_PBO["max_family_configs"] = 2
once("B 回测成功档 <3")

config.STRATEGY_PBO["max_family_configs"] = 12
import pbo as pbo_mod  # noqa: E402

real = pbo_mod.collect_config_equities


def fake(*a, **k):
    got = real(*a, **k)
    drop = list(got)[1:3]
    print(f"  [harness] 故意丢掉 {drop}")
    return {k2: v for k2, v in got.items() if k2 not in drop}


pbo_mod.collect_config_equities = fake
once("C 档数缩水")
pbo_mod.collect_config_equities = real
