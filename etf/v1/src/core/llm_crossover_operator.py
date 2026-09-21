# -*- coding: utf-8 -*-
"""LLM 融合交叉"""

import os
import json
from collections import OrderedDict
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_CROSSOVER as CFG, LLM_MODEL, LLM_API_KEY_ENV
from llm_client import (make_openai_client, endpoint_enabled)


CROSSOVER_SYSTEM_PROMPT = """你是量化因子研究员。给定两个父代因子表达式，生成"融合"表达式。

要求：
1. 融合两者核心逻辑（如 A 的动量 + B 的波动率）
2. 不简单相加，要有逻辑组合
3. 一行 Python 表达式，用 df 输入
4. 可用算子: close, open, high, low, volume, returns, ma, std, max, min, delay, delta, ts_sum, ts_mean, ts_std, abs, log, sign

输出 JSON:
{"expr": "融合表达式", "logic": "融合逻辑（1-2 句）"}
"""


class LLMCrossoverOperator:
    def __init__(self):
        self.api_key = os.environ.get(LLM_API_KEY_ENV, "")
        self.enabled = endpoint_enabled(self.api_key)
        self.cache = OrderedDict()
        self.n_calls = 0
        self.calls_this_gen = 0
        self.history = []

    def _cache_get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def _cache_put(self, key, val):
        self.cache[key] = val
        if len(self.cache) > CFG["cache_size"]:
            self.cache.popitem(last=False)

    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=2, max=8))
    def _chat(self, messages):
        client = make_openai_client(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages,
            temperature=CFG["temperature"],
            response_format={"type": "json_object"})
        return resp.choices[0].message.content

    def crossover(self, parent_a_expr, parent_b_expr,
                  metrics_a, metrics_b):
        if self.calls_this_gen >= CFG["max_llm_calls_per_gen"]:
            return None, None
        if not self.enabled:
            return None, None
        key = f"{parent_a_expr}|{parent_b_expr}"
        cached = self._cache_get(key)
        if cached:
            return cached

        parts = []
        if CFG["include_parents"]:
            parts.append(f"父代 A: `{parent_a_expr}`")
            parts.append(f"父代 B: `{parent_b_expr}`")
        if CFG["include_metrics"]:
            parts.append(
                f"A 指标: IC={metrics_a.get('abs_ic', 0):.4f}, "
                f"换手={metrics_a.get('turnover', 0):.3f}")
            parts.append(
                f"B 指标: IC={metrics_b.get('abs_ic', 0):.4f}, "
                f"换手={metrics_b.get('turnover', 0):.3f}")
        parts.append("请生成融合表达式。")
        try:
            content = self._chat([
                {"role": "system", "content": CROSSOVER_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(parts)}])
            data = json.loads(content)
            expr = data.get("expr", "").strip()
            logic = data.get("logic", "")
            if not expr or "df" not in expr:
                return None, None
            self.n_calls += 1
            self.calls_this_gen += 1
            self._cache_put(key, (expr, logic))
            self.history.append({"parent_a": parent_a_expr,
                                 "parent_b": parent_b_expr,
                                 "new": expr, "logic": logic})
            return expr, logic
        except Exception as e:
            print(f"    ⚠️ LLM 交叉失败: {e}")
            return None, None

    def reset_generation(self):
        self.calls_this_gen = 0

    def get_history_df(self):
        import pandas as pd
        if not self.history:
            return pd.DataFrame()
        return pd.DataFrame(self.history)


def smart_crossover(node_a, node_b, metrics_a, metrics_b, rng, llm_op):
    from factor_genetic import crossover as random_crossover, ExprNode
    if rng.random() < CFG["llm_prob"] and llm_op.enabled:
        expr_a = node_a.value if node_a.op == "raw" else node_a.to_string()
        expr_b = node_b.value if node_b.op == "raw" else node_b.to_string()
        new_expr, logic = llm_op.crossover(
            expr_a, expr_b, metrics_a, metrics_b)
        if new_expr:
            child = ExprNode("raw", value=new_expr)
            if CFG["fallback_to_random"]:
                _, c2 = random_crossover(node_a, node_b, rng)
                return child, c2
            return child, child.clone()
    if CFG["fallback_to_random"]:
        return random_crossover(node_a, node_b, rng)
    return node_a.clone(), node_b.clone()