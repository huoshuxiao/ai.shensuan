# -*- coding: utf-8 -*-
"""LLM 生成 SHAP 解释"""

import os
import json
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_SHAP_EXPLAINER as CFG, LLM_MODEL, LLM_API_KEY_ENV


EXPLAIN_SYSTEM_PROMPT_ZH = """你是资深量化因子研究员。根据因子的衰减归因数据，输出：

1. **衰减原因**（2-3 句）
2. **市场机制**（1-2 句）
3. **行动建议**（1-2 条）

要求：说人话，引用具体数字，建议具体。150-250 字。

输出 Markdown：
## 衰减原因
...
## 市场机制
...
## 行动建议
...
"""


def _build_prompt(factor, explain_result):
    lines = [f"### 因子 `{factor.get('name', '')}`",
             f"- 表达式: `{factor.get('expr', '')}`",
             f"- IC: {factor.get('mean_ic', 0):+.4f}",
             f"- ICIR: {factor.get('icir', 0):+.3f}", ""]
    top_lags = explain_result.get("top_lags")
    if top_lags is not None and not top_lags.empty:
        lines.append("**最重要的 lag**:")
        for _, row in top_lags.iterrows():
            lines.append(f"- `{row['feature']}`: {row['importance']:.4f}")
        lines.append("")
    root = explain_result.get("root_cause", "")
    if root:
        lines.append(f"**规则判断**: {root}")
        lines.append("")
    ic = explain_result.get("ic_series")
    if ic is not None and len(ic) > 0:
        lines.append("**IC 序列（最近 10 点）**:")
        lines.append(str(ic.tail(10).round(4).tolist()))
    return "\n".join(lines)


class LLMShapExplainer:
    def __init__(self):
        self.api_key = os.environ.get(LLM_API_KEY_ENV, "")
        self.enabled = bool(self.api_key)
        self.cache = {}

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def _chat(self, messages):
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages, temperature=0.4)
        return resp.choices[0].message.content

    def explain_one(self, factor, explain_result):
        if not self.enabled:
            return self._fallback(factor, explain_result)
        cache_key = factor.get("name", "")
        if cache_key in self.cache:
            return self.cache[cache_key]
        prompt = _build_prompt(factor, explain_result)
        try:
            text = self._chat([
                {"role": "system",
                 "content": EXPLAIN_SYSTEM_PROMPT_ZH},
                {"role": "user", "content": prompt}])
            self.cache[cache_key] = text
            return text
        except Exception as e:
            print(f"    ⚠️ LLM 失败: {e}")
            return self._fallback(factor, explain_result)

    def _fallback(self, factor, explain_result):
        root = explain_result.get("root_cause", "未判断")
        return (f"## 衰减原因\n因子 `{factor.get('name', '')}` IC 下降。\n\n"
                f"## 市场机制\n{root}\n\n"
                f"## 行动建议\n- 建议观察 3 天再决定\n")


def generate_llm_report(factors, explanation_results, save=True):
    print("\n========== LLM 生成 SHAP 解释 ==========")
    if not CFG["enabled"]:
        return {}
    explainer = LLMShapExplainer()
    to_explain = factors[:CFG["max_factors"]]
    report = {}
    for i, f in enumerate(to_explain, 1):
        name = f["name"]
        if name not in explanation_results:
            continue
        print(f"  [{i}/{len(to_explain)}] {name}")
        report[name] = explainer.explain_one(f,
                                              explanation_results[name])
    if save and report:
        _save(report)
    return report


def _save(report):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = ["# LLM 因子衰减归因报告", "",
             f"> {now} | 共 {len(report)} 个因子", "", "---", ""]
    for name, text in report.items():
        lines.append(f"## `{name}`")
        lines.append("")
        lines.append(text)
        lines.append("")
        lines.append("---")
        lines.append("")
    # llm_shap_explainer.py _save 内
    os.makedirs(os.path.dirname(CFG["report_path"]) or ".", exist_ok=True)
    with open(CFG["report_path"], "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(CFG["report_path"].replace(".md", ".json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 报告: {CFG['report_path']}")