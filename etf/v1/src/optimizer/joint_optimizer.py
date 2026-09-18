# -*- coding: utf-8 -*-
"""LLM 联合优化（正交化 + 风控）"""

import os
import json
import numpy as np
from tenacity import retry, stop_after_attempt, wait_exponential
from config import JOINT_LLM, ORTHOGONAL, RISK_CONTROL, \
    LLM_MODEL, LLM_API_KEY_ENV
from factor_orthogonal import orthogonalize_factors


JOINT_SYSTEM_PROMPT = """你是量化策略优化专家。同时优化两组参数：

【A. 因子正交化】
- corr_threshold: 0.3~0.9
- method: "gram_schmidt" | "pca" | "none"

【B. 风控】
- daily_stop_loss, max_drawdown_stop, cooldown_days,
  single_position_max, min_bars_between_trades, max_trades_per_day

目标：最大化 DSR，PBO < 0.5。

输出 JSON:
{"ortho": {"corr_threshold": 0.6, "method": "gram_schmidt"},
 "risk": {...}, "reason": "..."}
"""


class LLMJointOptimizer:
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

    def suggest(self, prev_ortho, prev_risk, metrics):
        if not self.enabled:
            return None
        user = (f"上一组正交化: {json.dumps(prev_ortho)}\n"
                f"上一组风控: {json.dumps(prev_risk)}\n"
                f"表现: DSR={metrics.get('dsr', 0):.4f}, "
                f"PBO={metrics.get('pbo', 1):.4f}, "
                f"夏普={metrics.get('sharpe_annual', 0):.3f}\n"
                f"请给出下一组。")
        try:
            content = self._chat([
                {"role": "system", "content": JOINT_SYSTEM_PROMPT},
                {"role": "user", "content": user}])
            data = json.loads(content)
            ortho = data.get("ortho", {})
            risk = data.get("risk", {})
            t = max(0.3, min(0.9, float(ortho.get("corr_threshold", 0.7))))
            m = ortho.get("method", "gram_schmidt")
            if m not in ("gram_schmidt", "pca", "none"):
                m = "gram_schmidt"
            return {"ortho": {"corr_threshold": t, "method": m},
                    "risk": {**RISK_CONTROL, **risk}}
        except Exception as e:
            print(f"    ⚠️ LLM 建议失败: {e}")
            return None


def joint_optimize(raw_factors, evaluate_fn, max_rounds=None):
    print("\n========== LLM 联合优化 ==========")
    max_rounds = max_rounds or JOINT_LLM["max_rounds"]
    llm = LLMJointOptimizer()
    if not llm.enabled:
        print("  ℹ️ 未配置 LLM，走默认参数")
        factors = orthogonalize_factors(raw_factors)
        metrics = evaluate_fn(factors, RISK_CONTROL)
        return {"ortho": {"corr_threshold": 0.7, "method": "gram_schmidt"},
                "risk": RISK_CONTROL, "metrics": metrics,
                "factors": factors}

    cur_ortho = {"corr_threshold": ORTHOGONAL["corr_threshold"],
                 "method": ORTHOGONAL["method"]}
    cur_risk = dict(RISK_CONTROL)
    best = {"metrics": {"dsr": -1}, "ortho": cur_ortho,
            "risk": cur_risk, "factors": None}

    for rnd in range(max_rounds):
        print(f"\n  轮 {rnd + 1}/{max_rounds}")
        factors = orthogonalize_factors(
            raw_factors, threshold=cur_ortho["corr_threshold"],
            method=cur_ortho["method"])
        if not factors:
            break
        try:
            metrics = evaluate_fn(factors, cur_risk)
        except Exception as e:
            print(f"    ⚠️ 评估失败: {e}")
            break
        dsr = metrics.get("dsr", 0)
        pbo = metrics.get("pbo", 1.0)
        print(f"    DSR={dsr:.4f}  PBO={pbo:.4f}")
        score = dsr if pbo < 0.5 else dsr - 0.5
        best_score = best["metrics"].get("dsr", -1)
        if best["metrics"].get("pbo", 1.0) >= 0.5:
            best_score -= 0.5
        if score > best_score:
            best = {"metrics": metrics, "ortho": dict(cur_ortho),
                    "risk": dict(cur_risk), "factors": factors}
        nxt = llm.suggest(cur_ortho, cur_risk, metrics)
        if not nxt:
            break
        if nxt["ortho"] == cur_ortho and nxt["risk"] == cur_risk:
            break
        cur_ortho, cur_risk = nxt["ortho"], nxt["risk"]

    return best