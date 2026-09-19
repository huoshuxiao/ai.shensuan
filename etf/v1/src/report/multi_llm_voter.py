# -*- coding: utf-8 -*-
"""多 LLM 投票"""

import os
import json
import numpy as np
from datetime import datetime
from collections import Counter
from tenacity import retry, stop_after_attempt, wait_exponential
from eval_prompts import get_eval_prompt
from config import LIVE_DATA_DIR, REPORT_DIR


DEFAULT_MODELS = [
    {"name": "gpt-4o-mini", "provider": "openai",
     "env_key": "OPENAI_API_KEY", "weight": 1.0},
    {"name": "gpt-4o", "provider": "openai",
     "env_key": "OPENAI_API_KEY", "weight": 1.5},
    {"name": "deepseek-chat", "provider": "deepseek",
     "env_key": "DEEPSEEK_API_KEY",
     "base_url": "https://api.deepseek.com", "weight": 1.0},
    {"name": "qwen-max", "provider": "qwen",
     "env_key": "DASHSCOPE_API_KEY",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "weight": 1.0},
]


class MultiLLMVoter:
    def __init__(self, models=None, language="zh"):
        self.models = models or DEFAULT_MODELS
        self.language = language
        self.available = [m for m in self.models
                          if os.environ.get(m["env_key"])]
        print(f"  🗳️ 可用 LLM: {[m['name'] for m in self.available]}")

    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=2, max=6))
    def _call_one(self, model, messages):
        from openai import OpenAI
        kwargs = {"api_key": os.environ.get(model["env_key"])}
        if model.get("base_url"):
            kwargs["base_url"] = model["base_url"]
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=model["name"], messages=messages, temperature=0.3,
            response_format={"type": "json_object"})
        return resp.choices[0].message.content

    def evaluate(self, report_text, raw_data):
        if not self.available:
            return {}
        system = get_eval_prompt("single", self.language)
        user = (f"# 日报\n{report_text[:4000]}\n\n"
                f"# 原始数据\n```json\n"
                f"{json.dumps(raw_data, ensure_ascii=False)[:3000]}\n```")
        votes, details = [], []
        for model in self.available:
            try:
                content = self._call_one(model, [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}])
                r = json.loads(content)
                r["_model"] = model["name"]
                r["_weight"] = model.get("weight", 1.0)
                votes.append(r)
                details.append({"model": model["name"],
                                "total": r.get("total", 0)})
                print(f"    ✅ {model['name']}: {r.get('total', 0)}")
            except Exception as e:
                print(f"    ❌ {model['name']}: {e}")
        if not votes:
            return {}
        agg = self._aggregate(votes)
        return {"votes": votes, "details": details,
                "aggregated": agg, "n_voters": len(votes),
                "evaluated_at": datetime.now().isoformat()}

    @staticmethod
    def _aggregate(votes):
        totals = [v.get("total", 0) for v in votes]
        weights = [v.get("_weight", 1.0) for v in votes]
        weighted = np.average(totals, weights=weights)
        median = float(np.median(totals))
        dims = ["accuracy", "completeness", "actionability", "logic"]
        dim_scores = {}
        for d in dims:
            vals = [v.get("scores", {}).get(d, 0) for v in votes]
            dim_scores[d] = float(np.average(vals, weights=weights))
        consistency = max(0.0, min(1.0, 1 - (np.std(totals) / 50)))
        all_weak = []
        for v in votes:
            all_weak.extend(v.get("weaknesses", []))
        weak_cnt = Counter(w[:30] for w in all_weak)
        return {"total_weighted": round(weighted, 1),
                "total_median": round(median, 1),
                "grade": MultiLLMVoter._grade(median),
                "dimensions": {k: round(v, 1)
                               for k, v in dim_scores.items()},
                "consistency": round(consistency, 3),
                "n_voters": len(votes),
                "top_weaknesses": [k for k, _ in weak_cnt.most_common(5)]}

    @staticmethod
    def _grade(score):
        if score >= 90:
            return "A+"
        elif score >= 80:
            return "A"
        elif score >= 70:
            return "B"
        elif score >= 60:
            return "C"
        return "D"


class VotingSelfEvaluator:
    def __init__(self, live_data_dir=None, language="zh",
                 report_dir=None):
        self.dir = live_data_dir or LIVE_DATA_DIR
        self.out = report_dir or REPORT_DIR
        self.voter = MultiLLMVoter(language=language)
        self.history = []

    def eval_single(self, report_text, raw_data):
        voting = self.voter.evaluate(report_text, raw_data)
        if not voting:
            return {}
        agg = voting["aggregated"]
        from eval_metrics import EvalMetrics
        obj = EvalMetrics.compute_objective_score(report_text, raw_data)
        final = agg["total_median"] * 0.7 + obj["objective_score"] * 0.3
        return {"final_score": round(final, 1),
                "grade": MultiLLMVoter._grade(final),
                "voting": voting, "objective": obj,
                "consistency": agg["consistency"],
                "n_voters": voting["n_voters"]}

    def eval_batch(self, days=20):
        import glob
        files = sorted(glob.glob(
            f"{self.dir}/report_daily*.json"))[-days:]
        if not files:
            return {}
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
                if r:
                    r["date"] = data.get("generated_at", "")[:10]
                    results.append(r)
                    print(f"    [{i}/{len(files)}] {r['date']}: "
                          f"{r['final_score']} "
                          f"(一致性 {r['consistency']})")
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
        consis = [r["consistency"] for r in results]
        return {"n_reports": len(results),
                "avg_score": round(float(np.mean(scores)), 1),
                "median_score": round(float(np.median(scores)), 1),
                "avg_consistency": round(float(np.mean(consis)), 3),
                "n_controversial": sum(1 for c in consis if c < 0.6)}

    def _save(self, results, summary):
        import pandas as pd
        os.makedirs(self.out, exist_ok=True)
        pd.DataFrame([{"date": r["date"],
                       "final_score": r["final_score"],
                       "grade": r["grade"],
                       "consistency": r["consistency"]}
                      for r in results]).to_csv(
            f"{self.out}/voting_eval_detail.csv",
            index=False, encoding="utf-8-sig")
        with open(f"{self.out}/voting_eval_summary.json", "w",
                  encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)


def run_voting_evaluation(days=20):
    return VotingSelfEvaluator().eval_batch(days=days)