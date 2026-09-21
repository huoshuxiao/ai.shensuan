# -*- coding: utf-8 -*-
"""LLM 因子生成 Agent"""

import os
import json
import numpy as np
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential
from config import (
    LLM_MODEL, LLM_API_KEY_ENV, MAX_LOOPS,
    HYPOTHESIS_PER_LOOP, IC_THRESHOLD,
)
from factor_dsl import safe_eval, compute_ic
from factor_naming import cn_name
from llm_client import (make_openai_client, endpoint_enabled,
                        describe_endpoint)


SYSTEM_PROMPT = """你是量化因子研究员。生成分钟线 ETF 因子表达式。

可用算子：
- 数据列: close, open, high, low, volume, returns
- 滚动: ma(df, n), std(df, n), max(df, n), min(df, n), rank(s, n)
- 序列: delay(s, n), delta(s, n), ts_sum(s, n), ts_mean(s, n), ts_std(s, n)
- 数学: abs(s), log(s), sign(s)
- n 为整数，建议 5~240

输出 JSON:
{"factors":[{"name":"xxx","expr":"表达式","reason":"逻辑"}]}

要求：
1. 表达式一行 Python，用 df 作输入
2. 例如: "delta(close, 5) / (ma(df, 20) + 1e-9)"
3. 绝不能用未来数据（shift(-1) / future / lookahead）
4. 生成 {n} 个因子，避免与历史重复
"""


class LLMClient:
    def __init__(self):
        self.api_key = os.environ.get(LLM_API_KEY_ENV, "")
        self.enabled = endpoint_enabled(self.api_key)
        if self.enabled:
            print(f"  LLM 端点: {describe_endpoint()}")

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def chat(self, messages):
        client = make_openai_client(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages, temperature=0.7,
            response_format={"type": "json_object"})
        return resp.choices[0].message.content


class LLMFactorAgent:
    def __init__(self, pool):
        self.pool = pool
        self.llm = LLMClient()
        self.history = []
        self.knowledge_base = []

    def _fallback(self, n):
        tpl = [
            {"name": "mom_5", "expr": "delta(close, 5) / (ma(df, 20) + 1e-9)"},
            {"name": "mom_20", "expr": "delta(close, 20) / (ma(df, 60) + 1e-9)"},
            {"name": "vol_20", "expr": "-ts_std(returns, 20)"},
            {"name": "vol_60", "expr": "-ts_std(returns, 60)"},
            {"name": "ma_cross", "expr": "ma(df, 5) / (ma(df, 20) + 1e-9) - 1"},
            {"name": "rsi_like", "expr": "ts_sum(returns.clip(lower=0), 14) / (ts_sum(returns.abs(), 14) + 1e-9)"},
            {"name": "vol_ratio", "expr": "volume / (ts_mean(volume, 20) + 1e-9)"},
            {"name": "price_pos", "expr": "(close - min(df, 20)) / (max(df, 20) - min(df, 20) + 1e-9)"},
        ]
        return tpl[:n]

    def _generate(self, feedback=""):
        if not self.llm.enabled:
            return self._fallback(HYPOTHESIS_PER_LOOP)
        history_str = json.dumps(self.history[-10:], ensure_ascii=False)
        user = (f"历史因子IC：{history_str}\n"
                f"上轮反馈：{feedback or '无'}\n"
                f"请生成 {HYPOTHESIS_PER_LOOP} 个新因子。")
        try:
            content = self.llm.chat([
                {"role": "system",
                 "content": SYSTEM_PROMPT.format(n=HYPOTHESIS_PER_LOOP)},
                {"role": "user", "content": user}])
            return json.loads(content).get("factors", [])
        except Exception as e:
            print(f"  ⚠️ LLM 生成失败: {e}")
            return self._fallback(HYPOTHESIS_PER_LOOP)

    def _evaluate(self, fd):
        name, expr = fd["name"], fd["expr"]
        ics, impl = [], {}
        first_err = None
        for code, df in self.pool.items():
            try:
                f = safe_eval(expr, df)
                fr = df["close"].pct_change().shift(-1)
                ic = compute_ic(f, fr)
                if not np.isnan(ic):
                    ics.append(ic)
                    impl[code] = {"factor": f, "ic": ic}
            except Exception as e:
                if first_err is None:
                    first_err = e
                continue
        if not ics:
            if first_err is not None:
                print(f"  ⚠️ {name} 全部标的求值失败: "
                      f"{type(first_err).__name__}: {first_err}")
            return {"name": name, "mean_ic": 0.0, "icir": 0.0, "pass": False}
        mean_ic = float(np.mean(ics))
        std_ic = float(np.std(ics)) if len(ics) > 1 else 1.0
        icir = mean_ic / (std_ic + 1e-9)
        return {"name": name, "expr": expr, "mean_ic": mean_ic,
                "icir": icir,
                "pass": abs(mean_ic) >= IC_THRESHOLD, "impl": impl}

    def run(self):
        print("\n========== LLM 因子生成 Agent ==========")
        loops = MAX_LOOPS
        if not self.llm.enabled:
            # 模板是确定性的：多轮循环只会逐字节重复同一批表达式，
            # 无 LLM 时只跑一轮
            print("  ℹ️ 未配置 LLM，使用内置模板（确定性，单次循环）")
            loops = 1
        for loop in range(loops):
            print(f"\n--- Loop {loop + 1}/{loops} ---")
            feedback = ""
            if self.history:
                avg = np.mean([h["ic"] for h in self.history])
                feedback = f"上轮平均 IC={avg:+.4f}"
            for fd in self._generate(feedback):
                r = self._evaluate(fd)
                flag = "✅" if r["pass"] else "❌"
                print(f"  {r['name']:15s} IC={r['mean_ic']:+.4f} "
                      f"ICIR={r['icir']:+.3f} {flag}  [{cn_name(r['name'], fd.get('expr', ''))}]")
                self.history.append({"name": r["name"],
                                     "ic": r["mean_ic"],
                                     "expr": fd.get("expr", "")})
                if r["pass"] and not any(
                        k["name"] == r["name"] for k in self.knowledge_base):
                    self.knowledge_base.append(r)
        self.knowledge_base.sort(key=lambda x: abs(x["mean_ic"]),
                                  reverse=True)
        return self.knowledge_base