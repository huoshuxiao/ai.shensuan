# -*- coding: utf-8 -*-
"""报告融合器"""

import os
from tenacity import retry, stop_after_attempt, wait_exponential
from ..feedback.generation_config import GENERATION_MODELS


class ReportMerger:
    def __init__(self, language="zh"):
        self.language = language
        available = [m for m in GENERATION_MODELS
                     if m.get("enabled", True) and
                     os.environ.get(m["env_key"])]
        self.model = max(available, key=lambda m: m.get("weight", 1.0)) \
            if available else None

    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=2, max=6))
    def _llm_merge(self, reports, raw_data):
        from openai import OpenAI
        system = """你是量化研究主管。综合多份日报草稿，生成最终日报。
要求：提取最佳分析、去重合并、保持 4 段式结构、引用具体数字。"""
        drafts = []
        for i, r in enumerate(reports):
            drafts.append(f"## 草稿 {i+1}（{r.get('model', 'unknown')}）"
                          f"\n\n{r['text']}")
        user = "\n\n---\n\n".join(drafts) + \
               "\n\n---\n\n请生成最终日报。"
        kwargs = {"api_key": os.environ[self.model["env_key"]]}
        if self.model.get("base_url"):
            kwargs["base_url"] = self.model["base_url"]
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=self.model["name"],
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.3)
        return resp.choices[0].message.content

    def _extract_best(self, reports, eval_result):
        ranking = eval_result.get("ranking", [])
        if not ranking:
            return "\n\n---\n\n".join(r["text"] for r in reports[:2])
        sorted_reports = [reports[i] for i in ranking]
        return sorted_reports[0]["text"]

    def merge(self, reports, raw_data, eval_result=None,
              method="llm_merge"):
        if not reports:
            return ""
        if method == "llm_merge" and self.model:
            try:
                return self._llm_merge(reports, raw_data)
            except Exception:
                pass
        return self._extract_best(reports, eval_result or {})


def merge_reports(reports, raw_data, eval_result=None,
                  method="llm_merge"):
    return ReportMerger().merge(reports, raw_data,
                                  eval_result, method)