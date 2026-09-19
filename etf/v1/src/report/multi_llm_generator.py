# -*- coding: utf-8 -*-
"""多 LLM 生成 + 投票"""

import os
import json
import glob
import random
import numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_API_KEY_ENV
from generation_config import (
    GENERATION_MODELS, GENERATION_STRATEGY,
    get_style_instruction, MERGE_CONFIG, OUTPUT,
)
from report_prompts import get_system_prompt
from report_templates import ReportTemplate


class SingleLLMGenerator:
    def __init__(self, model_cfg, language="zh"):
        self.model = model_cfg
        self.language = language
        self.api_key = os.environ.get(model_cfg["env_key"], "")
        self.enabled = bool(self.api_key)

    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=2, max=6))
    def _call(self, messages, temperature):
        from openai import OpenAI
        kwargs = {"api_key": self.api_key}
        if self.model.get("base_url"):
            kwargs["base_url"] = self.model["base_url"]
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=self.model["name"], messages=messages,
            temperature=temperature)
        return resp.choices[0].message.content

    def generate(self, raw_data, report_type="daily"):
        if not self.enabled:
            return {"model": self.model["name"], "success": False,
                    "error": f"缺少 {self.model['env_key']}",
                    "text": ""}
        base_system = get_system_prompt(report_type, self.language)
        style = get_style_instruction(
            self.model.get("style", "balanced"))
        system = base_system + "\n" + style
        user = ReportTemplate.build_daily_prompt(raw_data)
        try:
            text = self._call([
                {"role": "system", "content": system},
                {"role": "user", "content": user}],
                self.model.get("temperature", 0.4))
            return {"model": self.model["name"],
                    "style": self.model.get("style", "balanced"),
                    "weight": self.model.get("weight", 1.0),
                    "success": True, "text": text,
                    "text_length": len(text),
                    "generated_at": datetime.now().isoformat()}
        except Exception as e:
            return {"model": self.model["name"], "success": False,
                    "error": str(e), "text": ""}


class MultiLLMGenerator:
    def __init__(self, language="zh", live_data_dir="live_data"):
        self.language = language
        self.dir = live_data_dir
        self.config = GENERATION_STRATEGY
        self.output_cfg = OUTPUT
        self.models = [m for m in GENERATION_MODELS
                       if m.get("enabled", True) and
                       os.environ.get(m["env_key"])]
        print(f"  🤖 可用生成模型: "
              f"{[m['name'] for m in self.models]}")

    def parallel_generate(self, raw_data, report_type="daily"):
        print(f"\n  🚀 并发生成（{len(self.models)} 个模型）...")
        results = []
        with ThreadPoolExecutor(
                max_workers=self.config["concurrency"]) as executor:
            futures = {}
            for model in self.models:
                gen = SingleLLMGenerator(model, self.language)
                futures[executor.submit(
                    gen.generate, raw_data, report_type)] = model["name"]
            for future in as_completed(
                    futures,
                    timeout=self.config["timeout_seconds"] * 2):
                name = futures[future]
                try:
                    r = future.result(
                        timeout=self.config["timeout_seconds"])
                    results.append(r)
                    status = "✅" if r["success"] else "❌"
                    print(f"    {status} {name}: "
                          f"{r.get('text_length', 0)} 字符")
                except Exception as e:
                    print(f"    ❌ {name}: {e}")
                    results.append({"model": name, "success": False,
                                    "error": str(e), "text": ""})
        return results

    def blind_evaluate(self, reports, raw_data):
        success = [r for r in reports if r.get("success")]
        if len(success) < 2:
            return {}
        print(f"\n  🎭 盲评（{len(success)} 份）...")
        report_ids = list(range(len(success)))
        random.shuffle(report_ids)
        scores = {i: [] for i in report_ids}
        for model in self.models:
            if not os.environ.get(model["env_key"]):
                continue
            for idx in report_ids:
                report = success[idx]
                if report["model"] == model["name"]:
                    continue
                try:
                    score = self._evaluate_one(model, report["text"],
                                                raw_data)
                    if score:
                        scores[idx].append({
                            "evaluator": model["name"],
                            "total": score.get("total", 0)})
                except Exception:
                    continue
        aggregated = {}
        for idx, evals in scores.items():
            if not evals:
                continue
            totals = [e["total"] for e in evals]
            aggregated[idx] = {
                "n_evaluators": len(evals),
                "avg_score": round(float(np.mean(totals)), 1),
                "std_score": round(float(np.std(totals)), 2)}
        ranked = sorted(aggregated.items(),
                        key=lambda x: -x[1]["avg_score"])
        return {"scores": aggregated,
                "ranking": [r[0] for r in ranked],
                "top_idx": ranked[0][0] if ranked else None}

    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=2, max=6))
    def _evaluate_one(self, model, report_text, raw_data):
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

    def generate_and_vote(self, raw_data, report_type="daily"):
        print(f"\n  {'=' * 50}")
        print(f"  🤖 多 LLM 生成 + 投票")
        print(f"  {'=' * 50}")
        reports = self.parallel_generate(raw_data, report_type)
        success = [r for r in reports if r.get("success")]
        if not success:
            return {}
        if len(success) == 1:
            return {"final": success[0], "all_reports": reports,
                    "evaluation": {}, "mode": "single"}
        eval_result = self.blind_evaluate(reports, raw_data) \
            if self.config["blind_eval"] else {}
        if eval_result and eval_result.get("top_idx") is not None:
            final = success[eval_result["top_idx"]]
            print(f"\n  🏆 胜出: {final['model']} "
                  f"(avg "
                  f"{eval_result['scores'][eval_result['top_idx']]['avg_score']})")
        else:
            final = max(success, key=lambda r: r.get("weight", 1.0))
        result = {"final": final, "all_reports": reports,
                  "evaluation": eval_result,
                  "mode": "multi_write_vote",
                  "generated_at": datetime.now().isoformat()}

        # ========== 融合（可选） ==========
        merged = self._merge_top_k(success, eval_result, raw_data)
        if merged:
            result["merged"] = merged
            print(f"  🔀 融合自 {merged['sources']}")

        self._save_all(result, raw_data)
        return result

    def _merge_top_k(self, reports, eval_result, raw_data):
        """融合 Top K 报告（可选）"""
        if not MERGE_CONFIG.get("enabled"):
            return None
        try:
            from report_merger import ReportMerger
            merger = ReportMerger(self.language)
            ranking = eval_result.get("ranking",
                                    [])[:MERGE_CONFIG.get("merge_top_k", 2)]
            if len(ranking) < 2:
                return None
            top_reports = [reports[i] for i in ranking]
            text = merger.merge(top_reports, raw_data, eval_result)
            if text:
                return {"model": "merged",
                        "text": text,
                        "sources": [r["model"] for r in top_reports],
                        "generated_at": datetime.now().isoformat()}
        except Exception as e:
            print(f"    ⚠️ 融合失败: {e}")
        return None
    
    def _save_all(self, result, raw_data):
        save_dir = self.output_cfg["save_dir"]
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(os.path.dirname(
            self.output_cfg["final_report_path"]), exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.output_cfg["save_all_versions"]:
            for r in result["all_reports"]:
                if not r.get("success"):
                    continue
                name = r["model"].replace("/", "_")
                with open(os.path.join(save_dir,
                                        f"{ts}_{name}.md"), "w",
                        encoding="utf-8") as f:
                    f.write(r["text"])

        final_path = self.output_cfg["final_report_path"]
        header = [
            "<!-- 多 LLM 生成 -->",
            f"<!-- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} -->",
            f"<!-- 胜出: {result['final']['model']} -->", ""]

        # 优先用融合版
        if result.get("merged"):
            content = result["merged"]["text"]
            header.insert(3, f"<!-- 融合自: {result['merged']['sources']} -->")
        else:
            content = result["final"]["text"]

        with open(final_path, "w", encoding="utf-8") as f:
            f.write("\n".join(header) + content)
        with open(self.output_cfg["eval_result_path"], "w",
                encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"  ✅ 最终报告: {final_path}")


def run_multi_llm_generation(report_path=None,
                              report_type="daily"):
    if report_path is None:
        report_path = "live_data/feedback_report.json"
    if not os.path.exists(report_path):
        return {}
    with open(report_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return MultiLLMGenerator().generate_and_vote(raw, report_type)


def run_multi_llm_generation_batch(n_days=5):
    results = []
    files = sorted(glob.glob(
        "live_data/feedback_report*.json"))[-n_days:]
    for f in files:
        with open(f, "r", encoding="utf-8") as fp:
            raw = json.load(fp)
        r = MultiLLMGenerator().generate_and_vote(raw)
        if r:
            results.append({"file": f, "winner": r["final"]["model"],
                            "n_models": len([x for x in r["all_reports"]
                                             if x.get("success")])})
    return results
