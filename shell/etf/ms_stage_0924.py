# -*- coding: utf-8 -*-
"""只跑到「多策略并行」那一段的真实验证跑（#12 收尾用）。

改动的两处是 run_multi_strategy 的出口读数与「策略不入场」的报错，都要在真实
多策略链上看见才算验证过。与 e2e_portfolio_daily_0924.py full 的差别只有：
关掉 walk-forward（那段 15 分钟与本次改动无关），保留策略级 PBO 过滤，
让 5 条策略配置各自过一遍正交化 → 信号 → 组合回测。

用法（工作区根目录）：
    /usr/bin/python3.10 -u shell/ms_stage_0924.py
"""
import os
import sys

os.environ.setdefault("ETF_RDAGENT_OFFICIAL_FALLBACK", "false")
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "etf", "v1", "src")
sys.path.insert(0, _SRC)
import _bootstrap  # noqa: E402,F401

import config  # noqa: E402

OFF = ["MULTI_SOURCE", "GENETIC", "GENETIC_MULTI_OBJECTIVE",
       "LLM_GENETIC_HYBRID", "LLM_RESEARCH_PLANNER", "LLM_CROSSOVER",
       "LLM_MUTATION", "ADAPTIVE_MUTATION", "JOINT_LLM", "ORTHO_LLM",
       "RL_WEIGHT", "LLM_SHAP_EXPLAINER", "ANIMATION_EXPORT",
       "ANIMATION_SHAP", "PARETO_ANIMATION", "DECAY_PREDICT",
       "DECAY_EXPLAIN", "FACTOR_ATTRIBUTION", "AUTO_REMINING",
       "PBO_TIMELINE", "DYNAMIC_WEIGHTS", "EXTENDED_OBJECTIVES",
       "WALK_FORWARD", "STRATEGY_LIFECYCLE", "FACTOR_DECAY"]
for name in OFF:
    cfg = getattr(config, name, None)
    if isinstance(cfg, dict):
        cfg["enabled"] = False

config.MULTI_STRATEGY["enabled"] = True
config.STRATEGY_PBO["enabled"] = True

print(f"  [ms_stage] multi={config.MULTI_STRATEGY['enabled']} "
      f"strategy_pbo={config.STRATEGY_PBO['enabled']} "
      f"walk_forward={config.WALK_FORWARD['enabled']}")

import main  # noqa: E402

main.main()
