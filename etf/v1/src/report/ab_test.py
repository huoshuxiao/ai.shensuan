# -*- coding: utf-8 -*-
"""Prompt A/B 测试"""

import os
import json
import shutil
import random
import numpy as np
import re
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_API_KEY_ENV, LLM_MODEL
from eval_prompts import get_eval_prompt
from multi_llm_voter import MultiLLMVoter


class PromptABTest:
    def __init__(self, live_data_dir="live_data", language="zh"):
        self.dir = live_data_dir
        self.language = language
        self.voter = MultiLLMVoter(language=language)
        self.backup_dir = "prompt_backups"
        os.makedirs(self.backup_dir, exist_ok=True)

    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=2, max=6))
    def _generate(self, prompt, raw_data):
        from openai import OpenAI
        from report_templates import ReportTemplate
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        user = ReportTemplate.build_daily_prompt(raw_data)
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "system", "content": prompt},
                      {"role": "user", "content": user}],
            temperature=0.4)
        return resp.choices[0].message.content

    def _blind_compare(self, report_a, report_b, raw_data):
        if not self.voter.available:
            return {}
        swap = random.random() < 0.5
        first, second = (report_b, report_a) if swap else (report_a, report_b)
        system = get_eval_prompt("single", self.language)
        scores_first, scores_second = [], []
        for model in self.voter.available:
            try:
                from openai import OpenAI
                kwargs = {"api_key": os.environ.get(model["env_key"])}
                if model.get("base_url"):
                    kwargs["base_url"] = model["base_url"]
                client = OpenAI(**kwargs)
                r1 = self._score_one(client, model, system, first, raw_data)
                r2 = self._score_one(client, model, system, second, raw_data)
                scores_first.append(r1)
                scores_second.append(r2)
            except Exception:
                continue
        if not scores_first or not scores_second:
            return {}
        avg_first = np.mean([s["total"] for s in scores_first])
        avg_second = np.mean([s["total"] for s in scores_second])
        avg_a, avg_b = (avg_second, avg_first) if swap else (avg_first, avg_second)
        diff = avg_a - avg_b
        winner = "tie" if abs(diff) < 2 else ("A" if diff > 0 else "B")
        return {"winner": winner, "score_a": round(float(avg_a), 1),
                "score_b": round(float(avg_b), 1),
                "diff": round(float(diff), 1)}

    def _score_one(self, client, model, system, report, raw_data):
        user = (f"# 日报\n{report[:4000]}\n\n# 原始数据\n```json\n"
                f"{json.dumps(raw_data, ensure_ascii=False)[:2500]}\n```")
        resp = client.chat.completions.create(
            model=model["name"],
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.3,
            response_format={"type": "json_object"})
        return json.loads(resp.choices[0].message.content)

    def run(self, prompt_a, prompt_b, n_samples=5):
        print(f"\n  🧪 A/B 测试（{n_samples} 样本）")
        import glob
        raw_files = sorted(glob.glob(
            f"{self.dir}/feedback_report*.json"))[-n_samples:]
        if not raw_files:
            return {}
        results = []
        for i, f in enumerate(raw_files, 1):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    raw = json.load(fp)
                print(f"\n  [{i}/{len(raw_files)}] {os.path.basename(f)}")
                report_a = self._generate(prompt_a, raw)
                report_b = self._generate(prompt_b, raw)
                r = self._blind_compare(report_a, report_b, raw)
                if r:
                    r["file"] = os.path.basename(f)
                    results.append(r)
                    print(f"    Winner: {r['winner']} "
                          f"(A={r['score_a']}, B={r['score_b']})")
            except Exception as e:
                print(f"    ⚠️ {e}")
        summary = self._summarize(results)
        self._save(results, summary)
        return {"results": results, "summary": summary}

    @staticmethod
    def _summarize(results):
        if not results:
            return {}
        wins_a = sum(1 for r in results if r["winner"] == "A")
        wins_b = sum(1 for r in results if r["winner"] == "B")
        ties = sum(1 for r in results if r["winner"] == "tie")
        avg_a = np.mean([r["score_a"] for r in results])
        avg_b = np.mean([r["score_b"] for r in results])
        overall = "A" if wins_a > wins_b else \
                  "B" if wins_b > wins_a else "tie"
        return {"n_samples": len(results), "wins_a": wins_a,
                "wins_b": wins_b, "ties": ties,
                "avg_a": round(float(avg_a), 1),
                "avg_b": round(float(avg_b), 1),
                "overall_winner": overall}

    def _save(self, results, summary):
        os.makedirs(self.dir, exist_ok=True)
        with open(f"{self.dir}/ab_test_result.json", "w",
                  encoding="utf-8") as f:
            json.dump({"generated_at": datetime.now().isoformat(),
                       "results": results, "summary": summary},
                      f, ensure_ascii=False, indent=2)
        lines = ["# 🧪 Prompt A/B 测试报告", "",
                 f"> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "",
                 "## 📊 汇总", "",
                 f"| 指标 | 值 |", "|------|-----|",
                 f"| 样本数 | {summary['n_samples']} |",
                 f"| A 胜 | {summary['wins_a']} |",
                 f"| B 胜 | {summary['wins_b']} |",
                 f"| 平局 | {summary['ties']} |",
                 f"| A 平均分 | {summary['avg_a']} |",
                 f"| B 平均分 | {summary['avg_b']} |",
                 f"| 总冠军 | **{summary['overall_winner']}** |", ""]
        with open(f"{self.dir}/ab_test_report.md", "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n  ✅ A/B 报告: {self.dir}/ab_test_report.md")

    def apply_winner(self, winner_prompt):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        src = "feedback/report_prompts.py"
        if os.path.exists(src):
            shutil.copy(src,
                        f"{self.backup_dir}/report_prompts_ab_{ts}.py")
        with open(src, "r", encoding="utf-8") as f:
            content = f.read()
        new_block = f'SYSTEM_PROMPT_ZH = """{winner_prompt}"""'
        content = re.sub(r'SYSTEM_PROMPT_ZH\s*=\s*""".*?"""',
                         new_block, content, flags=re.DOTALL)
        with open(src, "w", encoding="utf-8") as f:
            f.write(content)
        print("  ✅ 已应用获胜 Prompt")
        return True


def run_prompt_ab_test(prompt_a=None, prompt_b=None, n_samples=5):
    if prompt_a is None:
        from report_prompts import SYSTEM_PROMPT_ZH
        prompt_a = SYSTEM_PROMPT_ZH
    if prompt_b is None:
        backups = sorted([f for f in os.listdir("prompt_backups")
                          if f.startswith("report_prompts_")])
        if not backups:
            return {}
        with open(f"prompt_backups/{backups[-1]}",
                  "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r'SYSTEM_PROMPT_ZH\s*=\s*"""(.*?)"""',
                      content, re.DOTALL)
        if m:
            prompt_b = m.group(1)
        else:
            return {}
    tester = PromptABTest()
    return tester.run(prompt_a, prompt_b, n_samples)


def auto_ab_test_and_apply(n_samples=5):
    result = run_prompt_ab_test(n_samples=n_samples)
    if not result:
        return {}
    winner = result["summary"]["overall_winner"]
    if winner == "B":
        print("\n  🎉 B 版胜出，自动应用...")
        backups = sorted([f for f in os.listdir("prompt_backups")
                          if f.startswith("report_prompts_")])
        if backups:
            with open(f"prompt_backups/{backups[-1]}",
                      "r", encoding="utf-8") as f:
                content = f.read()
            m = re.search(r'SYSTEM_PROMPT_ZH\s*=\s*"""(.*?)"""',
                          content, re.DOTALL)
            if m:
                PromptABTest().apply_winner(m.group(1))
    return result