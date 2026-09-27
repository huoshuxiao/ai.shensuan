# -*- coding: utf-8 -*-
"""截面组合改造（#12）的真实端到端验证跑。

与平时手跑的差别只在：把外部依赖（RD-Agent / 本地 LLM / 遗传编程 / 归因）
关掉，让主线跑完整条链——池构建 → 数据加载 → 注册表因子 → 信号长表 →
组合回测 → DSR → 落盘。策略与回测本体一行不改。

用法（工作区根目录）：
    /usr/bin/python3.10 shell/e2e_portfolio_daily_0924.py          # 只跑到回测+DSR
    /usr/bin/python3.10 shell/e2e_portfolio_daily_0924.py full     # 带上验证与报告
"""
import os
import sys

os.environ.setdefault("ETF_RDAGENT_OFFICIAL_FALLBACK", "false")
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "etf", "v1", "src")
sys.path.insert(0, _SRC)
import _bootstrap  # noqa: E402,F401  挂好裸模块导入路径

import config  # noqa: E402

# 外部依赖与重型阶段一律关掉（只影响本进程）
OFF = ["MULTI_SOURCE", "GENETIC", "GENETIC_MULTI_OBJECTIVE",
       "LLM_GENETIC_HYBRID", "LLM_RESEARCH_PLANNER", "LLM_CROSSOVER",
       "LLM_MUTATION", "ADAPTIVE_MUTATION", "JOINT_LLM", "ORTHO_LLM",
       "RL_WEIGHT", "LLM_SHAP_EXPLAINER", "ANIMATION_EXPORT",
       "ANIMATION_SHAP", "PARETO_ANIMATION", "DECAY_PREDICT",
       "DECAY_EXPLAIN", "FACTOR_ATTRIBUTION", "AUTO_REMINING",
       "PBO_TIMELINE", "DYNAMIC_WEIGHTS", "EXTENDED_OBJECTIVES"]
for name in OFF:
    cfg = getattr(config, name, None)
    if isinstance(cfg, dict):
        cfg["enabled"] = False

if len(sys.argv) < 2 or sys.argv[1] != "full":
    for name in ("WALK_FORWARD", "STRATEGY_PBO", "MULTI_STRATEGY",
                 "STRATEGY_LIFECYCLE", "FACTOR_DECAY"):
        cfg = getattr(config, name, None)
        if isinstance(cfg, dict):
            cfg["enabled"] = False
else:
    config.WALK_FORWARD["fold_engines"] = ["registry"]

print(f"  [e2e] RD-Agent official={config.RDAGENT_USE_OFFICIAL_FALLBACK} "
      f"walk_forward={config.WALK_FORWARD['enabled']} "
      f"strategy_pbo={config.STRATEGY_PBO['enabled']} "
      f"multi_strategy={config.MULTI_STRATEGY['enabled']} "
      f"attribution={config.FACTOR_ATTRIBUTION['enabled']}")
print(f"  [e2e] 池 max_count={config.ETF_FILTER['max_count']} "
      f"top_k={config.PORTFOLIO['top_k']} "
      f"max_turnover={config.PORTFOLIO['max_turnover']} "
      f"lot_size={config.PORTFOLIO['lot_size']}")

import main  # noqa: E402

main.main()
