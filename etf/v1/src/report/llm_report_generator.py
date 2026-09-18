# -*- coding: utf-8 -*-
"""LLM 报告生成"""

import os
import json
import re
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_MODEL, LLM_API_KEY_ENV


DAILY_PROMPT = """你是量化交易主管。根据实盘反馈数据，写一份给交易员看的每日报告。

要求：
1. 开头一句话总结状态（健康/需关注/需干预）
2. 3-5 个关键发现，按紧急程度排序
3. 具体行动建议（谁、做什么、何时）
4. 风险提示

风格：说人话，引用具体数字，建议可执行。长度 300-500 字。

输出 Markdown：
## 📊 一句话总结
## 🔍 关键发现
## ✅ 行动建议
## ⚠️ 风险提示
"""


class LLMReportGenerator:
    def __init__(self):
        self.api_key = os.environ.get(LLM_API_KEY_ENV, "")
        self.enabled = bool(self.api_key)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def _chat(self, messages, temperature=0.4):
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages,
            temperature=temperature)
        return resp.choices[0].message.content

    def generate_daily(self, report):
        print("\n  📝 生成日报...")
        user = f"# 反馈数据\n```json\n{json.dumps(report, ensure_ascii=False, indent=2)[:3000]}\n```\n请写日报。"
        if self.enabled:
            try:
                text = self._chat([
                    {"role": "system", "content": DAILY_PROMPT},
                    {"role": "user", "content": user}])
            except Exception as e:
                text = self._fallback(report)
        else:
            text = self._fallback(report)
        os.makedirs("live_data", exist_ok=True)
        with open("live_data/report_daily.md", "w",
                  encoding="utf-8") as f:
            f.write(text)
        return text

    def generate_summary(self, report):
        sp = report.get("slippage", {})
        st = report.get("strategy", {})
        return (f"🟡 实盘日报\n\n"
                f"实盘夏普 {st.get('live_sharpe', 0):.2f}，"
                f"滑点 {sp.get('avg_slippage', 0)*100:.3f}%。\n\n"
                f"建议：检查滑点，必要时更新参数。")

    def _fallback(self, report):
        return ("## 📊 一句话总结\nLLM 未配置，使用模板。\n\n"
                "## 🔍 关键发现\n（需配置 OPENAI_API_KEY）\n\n"
                "## ✅ 行动建议\n检查 feedback_report.json\n\n"
                "## ⚠️ 风险提示\n无")


def generate_daily_report(report_path="live_data/feedback_report.json"):
    if not os.path.exists(report_path):
        return ""
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)
    return LLMReportGenerator().generate_daily(report)


def generate_summary(report_path="live_data/feedback_report.json"):
    if not os.path.exists(report_path):
        return ""
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)
    return LLMReportGenerator().generate_summary(report)