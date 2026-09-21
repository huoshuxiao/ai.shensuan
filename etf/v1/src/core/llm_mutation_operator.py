# -*- coding: utf-8 -*-
"""LLM 变异算子"""

import os
import json
from collections import OrderedDict
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_MUTATION as CFG, LLM_MODEL, LLM_API_KEY_ENV
from llm_client import (make_openai_client, endpoint_enabled)


MUTATION_SYSTEM_PROMPT = """你是量化因子研究员。给定一个因子表达式及其弱点，生成新的变体。

要求：
1. 保留核心逻辑（动量/反转/波动率）
2. 针对弱点做改进
3. 一行 Python 表达式，用 df 输入
4. 可用算子: close, open, high, low, volume, returns, ma(df,n), std(df,n), max(df,n), min(df,n), delay(s,n), delta(s,n), ts_sum(s,n), ts_mean(s,n), ts_std(s,n), abs(s), log(s), sign(s)

输出 JSON:
{"expr": "新表达式", "reason": "改进逻辑"}
"""


class LLMMutationOperator:
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

    @staticmethod
    def analyze_weakness(metrics):
        weaknesses = []
        if metrics.get("abs_ic", 0) < 0.02:
            weaknesses.append(f"IC 偏低 ({metrics.get('abs_ic', 0):.4f})")
        if metrics.get("turnover", 0) > 0.3:
            weaknesses.append(f"换手过高 ({metrics.get('turnover', 0):.3f})")
        if metrics.get("max_corr", 0) > 0.6:
            weaknesses.append(f"相关性高 ({metrics.get('max_corr', 0):.2f})")
        if metrics.get("stability", 1.0) < 0.3:
            weaknesses.append("稳定性不足")
        return "; ".join(weaknesses) if weaknesses else "无明显弱点"

    def mutate(self, parent_expr, parent_metrics):
        if self.calls_this_gen >= CFG["max_llm_calls_per_gen"]:
            return parent_expr
        if not self.enabled:
            return parent_expr
        weakness = (self.analyze_weakness(parent_metrics)
                    if CFG["include_weakness"] else "")
        cache_key = f"{parent_expr}|{weakness}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        parts = []
        if CFG["include_parent"]:
            parts.append(f"父代表达式: `{parent_expr}`")
        if weakness:
            parts.append(f"弱点: {weakness}")
        parts.append("请生成改进变体。")
        try:
            content = self._chat([
                {"role": "system", "content": MUTATION_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(parts)}])
            data = json.loads(content)
            new_expr = data.get("expr", "").strip()
            if not new_expr or "df" not in new_expr:
                return parent_expr
            self.n_calls += 1
            self.calls_this_gen += 1
            self._cache_put(cache_key, new_expr)
            self.history.append({"parent": parent_expr,
                                 "new": new_expr,
                                 "weakness": weakness,
                                 "reason": data.get("reason", "")})
            return new_expr
        except Exception as e:
            print(f"    ⚠️ LLM 变异失败: {e}")
            return parent_expr

    def reset_generation(self):
        self.calls_this_gen = 0

    def get_history_df(self):
        import pandas as pd
        if not self.history:
            return pd.DataFrame()
        return pd.DataFrame(self.history)


def smart_mutate(node, metrics, rng, max_depth, llm_op):
    from factor_genetic import mutate as random_mutate
    if rng.random() < CFG["llm_prob"] and llm_op.enabled:
        parent_expr = node.value if node.op == "raw" else node.to_string()
        new_expr = llm_op.mutate(parent_expr, metrics)
        if new_expr != parent_expr:
            from factor_genetic import ExprNode
            return ExprNode("raw", value=new_expr)
    return random_mutate(node, rng, max_depth)