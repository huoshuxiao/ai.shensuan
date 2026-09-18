# -*- coding: utf-8 -*-
"""LLM 调正交化阈值"""

import os
import json
import numpy as np
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential
from config import (
    ORTHO_LLM, LLM_MODEL, LLM_API_KEY_ENV, ORTHOGONAL,
)
from factor_orthogonal import orthogonalize_factors


ORTHO_SYSTEM_PROMPT = """你是因子组合优化专家。建议下一组因子正交化参数。

参数：
- corr_threshold: 0.3~0.9（越小越严格）
- method: "gram_schmidt" | "pca" | "none"

目标：最大化 DSR，同时避免因子数过少。

输出 JSON:
{"corr_threshold": 0.6, "method": "gram_schmidt", "reason": "..."}
"""


class LLMOrthoOptimizer:
    def __init__(self):
        self.api_key = os.environ.get(LLM_API_KEY_ENV, "")
        self.enabled = bool(self.api_key)
        self.history = []

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def _chat(self, messages):
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages, temperature=0.3,
            response_format={"type": "json_object"})
        return resp.choices[0].message.content

    def suggest(self, prev, prev_metrics):
        if not self.enabled:
            return None
        user = (f"上一组参数：{json.dumps(prev, ensure_ascii=False)}\n"
                f"表现：DSR={prev_metrics.get('dsr', 0):.4f}, "
                f"夏普={prev_metrics.get('sharpe_annual', 0):.3f}, "
                f"因子数={prev_metrics.get('n_factors', 0)}\n"
                f"历史：{json.dumps(self.history[-3:], ensure_ascii=False)}\n"
                f"请给出下一组。")
        try:
            content = self._chat([
                {"role": "system", "content": ORTHO_SYSTEM_PROMPT},
                {"role": "user", "content": user}])
            data = json.loads(content)
            t = float(data.get("corr_threshold", 0.7))
            lo, hi = ORTHO_LLM["threshold_range"]
            t = max(lo, min(hi, t))
            m = data.get("method", "gram_schmidt")
            if m not in ORTHO_LLM["methods"]:
                m = "gram_schmidt"
            return {"corr_threshold": t, "method": m}
        except Exception as e:
            print(f"    ⚠️ LLM 建议失败: {e}")
            return None


def optimize_orthogonalization(raw_factors, evaluate_fn, max_rounds=None):
    print("\n========== LLM 调正交化参数 ==========")
    max_rounds = max_rounds or ORTHO_LLM["max_rounds"]
    llm = LLMOrthoOptimizer()
    if not llm.enabled:
        print("  ℹ️ 未配置 LLM，走默认")
        factors = orthogonalize_factors(raw_factors)
        return {"metrics": evaluate_fn(factors), "params": {},
                "factors": factors}

    current = {"corr_threshold": ORTHOGONAL["corr_threshold"],
               "method": ORTHOGONAL["method"]}
    best = {"metrics": {"dsr": -1}, "params": current, "factors": None}

    for rnd in range(max_rounds):
        print(f"\n  轮 {rnd + 1}/{max_rounds}  {current}")
        factors = orthogonalize_factors(
            raw_factors, threshold=current["corr_threshold"],
            method=current["method"])
        if not factors:
            break
        try:
            metrics = evaluate_fn(factors)
        except Exception as e:
            print(f"    ⚠️ 评估失败: {e}")
            break
        metrics["n_factors"] = len(factors)
        dsr = metrics.get("dsr", 0)
        print(f"    DSR={dsr:.4f}  因子={len(factors)}")
        llm.history.append({"params": current, "dsr": dsr})
        if dsr > best["metrics"].get("dsr", -1):
            best = {"metrics": metrics, "params": dict(current),
                    "factors": factors}
        nxt = llm.suggest(current, metrics)
        if not nxt or nxt == current:
            break
        current = nxt
    print(f"\n  ✅ 最优: {best['params']}  "
          f"DSR={best['metrics'].get('dsr', 0):.4f}")
    return best