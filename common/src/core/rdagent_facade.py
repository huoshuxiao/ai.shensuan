# -*- coding: utf-8 -*-
"""RD-Agent 统一入口"""

import os
import numpy as np
from config import (
    RDAGENT_BACKEND, RDAGENT_USE_OFFICIAL_FALLBACK,
)
from factor_dsl import safe_eval


def _official_to_impl(official_factors, pool):
    result = []
    for item in official_factors:
        expr = item.get("expr", "")
        impl = {}
        for code, df in pool.items():
            try:
                f = safe_eval(expr, df)
                impl[code] = {"factor": f, "ic": item["mean_ic"]}
            except Exception:
                continue
        if impl:
            result.append({"name": item["name"],
                           "mean_ic": item["mean_ic"],
                           "icir": item.get("icir", 0.0),
                           "impl": impl})
    return result


def mine_factors(pool):
    print(f"\n  因子挖掘后端: {RDAGENT_BACKEND}")

    if RDAGENT_BACKEND == "official":
        from official_rdagent import try_official_rdagent
        official = try_official_rdagent()
        if official:
            return _official_to_impl(official, pool)
        if not RDAGENT_USE_OFFICIAL_FALLBACK:
            return []

    if RDAGENT_BACKEND in ("llm", "official"):
        from llm_factor_agent import LLMFactorAgent
        agent = LLMFactorAgent(pool)
        if agent.llm.enabled:
            return agent.run()
        print("  ↓ LLM 不可用，降级到内置模板")

    # simple
    from llm_factor_agent import LLMFactorAgent
    agent = LLMFactorAgent(pool)
    agent.llm.enabled = False
    return agent.run()


def mine_factors_multi_source(pool):
    from config import MULTI_SOURCE
    if MULTI_SOURCE.get("enabled", False):
        from multi_source_mining import multi_source_mine
        return multi_source_mine(pool)
    return mine_factors(pool)