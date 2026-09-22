# -*- coding: utf-8 -*-
"""LLM 生成研究计划"""

import os
import json
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_MODEL, LLM_API_KEY_ENV, RESULTS_DIR
from llm_client import (make_openai_client, endpoint_enabled)


RESEARCH_PROMPT = """你是量化研究主管。根据团队当前状态，制定未来 {days} 天研究计划。

输出 JSON:
{"plan": [{"title": "方向", "rationale": "为什么",
"candidate_exprs": ["expr1"], "priority": "high",
"expected_ic_gain": 0.01}], "summary": "整体策略"}
"""


class LLMResearchPlanner:
    def __init__(self):
        self.api_key = os.environ.get(LLM_API_KEY_ENV, "")
        self.enabled = endpoint_enabled(self.api_key)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def _chat(self, messages):
        client = make_openai_client(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages, temperature=0.7,
            response_format={"type": "json_object"})
        return resp.choices[0].message.content

    def generate_plan(self, days=3):
        if not self.enabled:
            return self._fallback(days)
        context = self._gather_context()
        user = f"当前状态：\n{context}\n\n请制定 {days} 天研究计划。"
        try:
            content = self._chat([
                {"role": "system",
                 "content": RESEARCH_PROMPT.format(days=days)},
                {"role": "user", "content": user}])
            plan = json.loads(content)
            plan["generated_at"] = datetime.now().isoformat()
            plan["days"] = days
            return plan
        except Exception as e:
            print(f"  ⚠️ 计划生成失败: {e}")
            return self._fallback(days)

    def _gather_context(self):
        lines = []
        try:
            from factor_library import get_library
            lib = get_library()
            lines.append("### 因子库 Top 10")
            for name in lib.get_top(10):
                f = lib.factors.get(name, {})
                lines.append(f"- {name}: IC={f.get('ic', 0):+.4f}")
        except Exception:
            pass
        return "\n".join(lines) or "（无上下文）"

    def _fallback(self, days):
        return {"plan": [{"title": "提升因子稳定性",
                          "rationale": "近期多个因子衰减",
                          "candidate_exprs": [
                              "ts_std(returns, 20) / (ts_mean(volume, 20) + 1e-9)"],
                          "priority": "high",
                          "expected_ic_gain": 0.008}],
                "summary": "聚焦稳定性", "generated_at":
                datetime.now().isoformat(), "days": days}

    def save_plan(self, plan,
                  path=f"{RESULTS_DIR}/research_plan.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        md_path = path.replace(".json", ".md")
        lines = [f"# 研究计划 ({plan.get('days', 3)} 天)", "",
                 f"> {plan.get('generated_at', '')}", "",
                 f"**策略**: {plan.get('summary', '')}", ""]
        for i, item in enumerate(plan.get("plan", []), 1):
            lines.append(f"## {i}. {item.get('title', '')} "
                         f"[{item.get('priority', '')}]")
            lines.append("")
            lines.append(f"**理由**: {item.get('rationale', '')}")
            lines.append("")
            for expr in item.get("candidate_exprs", []):
                lines.append(f"```python\n{expr}\n```")
            lines.append("")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


def generate_research_plan(days=3):
    planner = LLMResearchPlanner()
    plan = planner.generate_plan(days)
    if plan:
        planner.save_plan(plan)
    return plan