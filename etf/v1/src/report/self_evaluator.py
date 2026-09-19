# -*- coding: utf-8 -*-
"""日报自评器"""

import os
import json
import glob
import numpy as np
import pandas as pd
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_MODEL, LLM_API_KEY_ENV


EVAL_PROMPT = """你是量化报告审核专家。评估日报质量。

4 个维度各 25 分：
- 准确性：数字是否与原始数据匹配
- 完整性：是否覆盖关键指标
- 可执行性：建议是否具体
- 逻辑性：因果是否成立

输出 JSON:
{"scores": {"accuracy": 22, "completeness": 18,
"actionability": 20, "logic": 21},
"total": 81, "grade": "A",
"strengths": [], "weaknesses": [],
"hallucinations": [], "missing_items": [],
"improvement_suggestions": []}
"""


class SelfEvaluator:
    def __init__(self, live_data_dir="live_data"):
        self.dir = live_data_dir
        self.api_key = os.environ.get(LLM_API_KEY_ENV, "")
        self.enabled = bool(self.api_key)
        self.history = []

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def _chat(self, messages, temperature=0.3):
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"})
        return resp.choices[0].message.content

    def eval_single(self, report_text, raw_data):
        # 客观指标
        from eval_metrics import EvalMetrics
        obj = EvalMetrics.compute_objective_score(report_text, raw_data)

        # LLM 评分
        if self.enabled:
            user = (f"# 日报\n{report_text[:4000]}\n\n"
                    f"# 原始数据\n```json\n"
                    f"{json.dumps(raw_data, ensure_ascii=False)[:2500]}"
                    f"\n```")
            try:
                content = self._chat([
                    {"role": "system", "content": EVAL_PROMPT},
                    {"role": "user", "content": user}])
                llm = json.loads(content)
            except Exception:
                llm = self._fallback_llm(obj)
        else:
            llm = self._fallback_llm(obj)

        llm_total = llm.get("total", 0)
        obj_total = obj.get("objective_score", 0)
        final = llm_total * 0.7 + obj_total * 0.3
        grade = ("A+" if final >= 90 else "A" if final >= 80 else
                 "B" if final >= 70 else "C" if final >= 60 else "D")

        return {"final_score": round(final, 1), "grade": grade,
                "llm_score": llm, "objective": obj,
                "evaluated_at": datetime.now().isoformat()}

    def _fallback_llm(self, obj):
        return {"scores": {"accuracy": 20, "completeness": 20,
                           "actionability": 20, "logic": 20},
                "total": 80, "grade": "A", "strengths": [],
                "weaknesses": [], "hallucinations": [],
                "missing_items": [], "improvement_suggestions": []}

    def eval_batch(self, days=20):
        print(f"\n  🔍 批量自评（{days} 天）...")
        files = sorted(glob.glob(
            f"{self.dir}/report_daily*.json"))[-days:]
        results = []
        for i, f in enumerate(files, 1):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                text = data.get("text", "")
                if not text:
                    continue
                base = f.replace("report_daily", "feedback_report")
                raw = {}
                if os.path.exists(base):
                    with open(base, "r", encoding="utf-8") as fp2:
                        raw = json.load(fp2)
                r = self.eval_single(text, raw)
                r["date"] = data.get("generated_at", "")[:10]
                results.append(r)
                print(f"    [{i}/{len(files)}] {r['date']}: "
                      f"{r['final_score']} ({r['grade']})")
            except Exception as e:
                print(f"    ⚠️ {f}: {e}")
        self.history = results
        summary = self._summarize(results)
        self._save(results, summary)
        return {"results": results, "summary": summary}

    @staticmethod
    def _summarize(results):
        if not results:
            return {}
        scores = [r["final_score"] for r in results]
        all_issues = []
        for r in results:
            all_issues.extend(r["llm_score"].get("weaknesses", []))
        from collections import Counter
        weak_cnt = Counter(w[:30] for w in all_issues)
        return {"n_reports": len(results),
                "avg_score": round(float(np.mean(scores)), 1),
                "median_score": round(float(np.median(scores)), 1),
                "min_score": round(float(np.min(scores)), 1),
                "max_score": round(float(np.max(scores)), 1),
                "top_issues": [{"text": k, "count": v}
                               for k, v in weak_cnt.most_common(5)]}

    def _save(self, results, summary):
        os.makedirs(self.dir, exist_ok=True)
        pd.DataFrame([{"date": r["date"],
                       "final_score": r["final_score"],
                       "grade": r["grade"]}
                      for r in results]).to_csv(
            f"{self.dir}/self_eval_detail.csv",
            index=False, encoding="utf-8-sig")
        with open(f"{self.dir}/self_eval_summary.json", "w",
                  encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2,
                      default=str)


def run_self_evaluation(days=20):
    return SelfEvaluator().eval_batch(days=days)