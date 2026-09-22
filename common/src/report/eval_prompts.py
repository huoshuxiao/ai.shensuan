# -*- coding: utf-8 -*-
"""评估 Prompt 模板（已内联在 self_evaluator 与 multi_llm_voter）"""

EVAL_SYSTEM_PROMPT_ZH = """你是量化报告审核专家。
评估日报质量，4 维度各 25 分。
输出 JSON 格式。"""


def get_eval_prompt(mode="single", language="zh"):
    return EVAL_SYSTEM_PROMPT_ZH