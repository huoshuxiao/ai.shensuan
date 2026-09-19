# -*- coding: utf-8 -*-
"""季度/年度复盘"""

import os
import json
import glob
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_MODEL, LLM_API_KEY_ENV, REPORT_DIR


QUARTERLY_PROMPT = """你是量化投资总监。写季度报告给投委会。
结构：摘要/业绩/系统演化/关键发现/风险/下季战略/长期观察。
长度 2000-3000 字。"""


ANNUAL_PROMPT = """你是量化投资总监。写年度总结给高管+投资人。
结构：摘要/业绩/演化时间线/报告质量/十大发现/风险/明年战略/反思。
长度 3000-5000 字。"""


class PeriodReviewGenerator:
    def __init__(self, live_data_dir=None):
        # 输入（月报 metrics/archive）与输出均在报告目录
        self.dir = live_data_dir or REPORT_DIR
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

    def generate(self, period="quarterly", n_months=3):
        print(f"\n  📅 生成 {period} 报告（{n_months} 月）...")
        files = sorted(glob.glob(
            f"{self.dir}/archive/monthly_*.json"))[-n_months:]
        if not files:
            files = sorted(glob.glob(
                f"{self.dir}/monthly_metrics*.json"))[-n_months:]
        data = []
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data.append(json.load(fp))
            except Exception:
                continue
        if not data:
            print("  ❌ 无月报数据")
            return ""
        system = QUARTERLY_PROMPT if period == "quarterly" else ANNUAL_PROMPT
        user = f"# {period} 数据（{len(data)} 份月报）\n"
        for m in data:
            eq = m.get("equity", {})
            user += (f"- {m.get('generated_at', '')[:7]}: "
                     f"收益 {eq.get('total_return', 0):.2f}%\n")
        user += f"\n请写 {period} 报告。"
        if self.enabled:
            try:
                text = self._chat([
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}])
            except Exception as e:
                text = f"LLM 失败: {e}"
        else:
            text = f"# {period} 报告（模板）\n\n需配置 OPENAI_API_KEY"
        suffix = datetime.now().strftime("%Y") if period == "annual" else \
            f"{datetime.now().year}Q{(datetime.now().month-1)//3+1}"
        filename = f"report_{period}_{suffix}.md"
        os.makedirs(f"{self.dir}/archive", exist_ok=True)
        with open(f"{self.dir}/{filename}", "w",
                  encoding="utf-8") as f:
            f.write(text)
        with open(f"{self.dir}/archive/{filename}", "w",
                  encoding="utf-8") as f:
            f.write(text)
        print(f"  ✅ {period} 报告: {self.dir}/{filename}")
        return text


def generate_quarterly_review(n_months=3):
    return PeriodReviewGenerator().generate("quarterly", n_months)


def generate_annual_review(n_months=12):
    return PeriodReviewGenerator().generate("annual", n_months)