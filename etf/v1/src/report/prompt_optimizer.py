# -*- coding: utf-8 -*-
"""Prompt 迭代器"""

import os
import re
import json
import shutil
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_MODEL, LLM_API_KEY_ENV, DATA_DIR
from llm_client import (make_openai_client, endpoint_enabled)


class PromptOptimizer:
    def __init__(self):
        self.api_key = os.environ.get(LLM_API_KEY_ENV, "")
        self.enabled = endpoint_enabled(self.api_key)
        self.backup_dir = os.path.join(DATA_DIR, "prompt_backups")
        os.makedirs(self.backup_dir, exist_ok=True)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def _chat(self, messages):
        client = make_openai_client(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages, temperature=0.4,
            response_format={"type": "json_object"})
        return resp.choices[0].message.content

    def optimize(self, summary):
        if not self.enabled:
            return {}
        current = self._load_current()
        if not current:
            return {}
        user = (f"当前 Prompt:\n{current[:2000]}\n\n"
                f"高频问题: {summary.get('top_issues', [])}\n\n"
                f"请输出改进后的完整 Prompt。\n"
                f'JSON: {{"improved_prompt": "...", "changes": []}}')
        try:
            content = self._chat([
                {"role": "system",
                 "content": "你是 Prompt 工程专家。"},
                {"role": "user", "content": user}])
            result = json.loads(content)
            self._backup()
            self._apply(result["improved_prompt"])
            print(f"  ✅ Prompt 已优化: {result.get('changes', [])}")
            return result
        except Exception as e:
            print(f"  ⚠️ 优化失败: {e}")
            return {}

    def _load_current(self):
        path = "report/report_prompts.py"
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r'SYSTEM_PROMPT_ZH\s*=\s*"""(.*?)"""',
                      content, re.DOTALL)
        return m.group(1) if m else ""

    def _backup(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        src = "report/report_prompts.py"
        if os.path.exists(src):
            shutil.copy(src,
                        f"{self.backup_dir}/report_prompts_{ts}.py")

    def _apply(self, new_prompt):
        path = "report/report_prompts.py"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        new_block = f'SYSTEM_PROMPT_ZH = """{new_prompt}"""'
        content = re.sub(r'SYSTEM_PROMPT_ZH\s*=\s*""".*?"""',
                         new_block, content, flags=re.DOTALL)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def optimize_prompt(summary_path=None):
    from config import REPORT_DIR
    if summary_path is None:
        summary_path = f"{REPORT_DIR}/self_eval_summary.json"
    if not os.path.exists(summary_path):
        return {}
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
    return PromptOptimizer().optimize(summary)