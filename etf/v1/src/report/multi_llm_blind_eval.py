# -*- coding: utf-8 -*-
"""独立盲评器"""

import os
import json
import random
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from tenacity import retry, stop_after_attempt, wait_exponential
from generation_config import GENERATION_MODELS


class BlindEvaluator:
    def __init__(self, language="zh"):
        self.language = language
        self.models = [m for m in GENERATION_MODELS
                       if m.get("enabled", True) and
                       os.environ.get(m["env_key"])]

    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=2, max=6))
    def _eval_one(self, model, report_text, raw_data):
        from openai import OpenAI
        from eval_prompts import get_eval_prompt
        system = get_eval_prompt("single", self.language)
        user = (f"# 日报\n{report_text[:4000]}\n\n# 原始数据\n"
                f"```json\n{json.dumps(raw_data, ensure_ascii=False)[:2500]}\n```")
        kwargs = {"api_key": os.environ.get(model["env_key"])}
        if model.get("base_url"):
            kwargs["base_url"] = model["base_url"]
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=model["name"],
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.3,
            response_format={"type": "json_object"})
        return json.loads(resp.choices[0].message.content)

    def evaluate_reports(self, reports, raw_data):
        if not self.models:
            return {}
        shuffled = list(range(len(reports)))
        random.shuffle(shuffled)
        scores = {i: [] for i in range(len(reports))}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            for model in self.models:
                for idx in shuffled:
                    futures[executor.submit(
                        self._eval_one, model,
                        reports[idx]["text"], raw_data)] = (model["name"], idx)
            for future in as_completed(futures):
                model_name, idx = futures[future]
                try:
                    score = future.result(timeout=60)
                    scores[idx].append({"evaluator": model_name,
                                        "total": score.get("total", 0)})
                except Exception:
                    continue
        aggregated = {}
        for idx, evals in scores.items():
            if not evals:
                continue
            totals = [e["total"] for e in evals]
            aggregated[idx] = {
                "report_id": reports[idx].get("id", idx),
                "author": reports[idx].get("author", ""),
                "avg_score": round(float(np.mean(totals)), 1),
                "consistency": round(max(0.0, min(1.0,
                    1 - np.std(totals) / 30)), 3)}
        ranking = sorted(aggregated.items(),
                         key=lambda x: -x[1]["avg_score"])
        return {"aggregated": aggregated, "ranking": ranking,
                "winner": ranking[0][0] if ranking else None}


def evaluate_multiple_reports(reports, raw_data):
    return BlindEvaluator().evaluate_reports(reports, raw_data)


def evaluate_historical_reports(n_days=5):
    import glob
    gen_files = sorted(glob.glob("live_data/multi_gen/*.json"))
    if not gen_files:
        return {}
    with open(gen_files[-1], "r", encoding="utf-8") as f:
        gen_result = json.load(f)
    reports = []
    for i, r in enumerate(gen_result.get("all_reports", [])):
        if r.get("success"):
            reports.append({"id": i, "text": r["text"],
                            "author": r["model"]})
    if not reports:
        return {}
    raw = {}
    if os.path.exists("live_data/feedback_report.json"):
        with open("live_data/feedback_report.json", "r",
                  encoding="utf-8") as f:
            raw = json.load(f)
    return evaluate_multiple_reports(reports, raw)