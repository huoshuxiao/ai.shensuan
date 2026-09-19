# -*- coding: utf-8 -*-
"""生成配置"""

from config import REPORT_DIR

GENERATION_MODELS = [
    {"name": "gpt-4o-mini", "provider": "openai",
     "env_key": "OPENAI_API_KEY", "weight": 1.0,
     "temperature": 0.4, "style": "concise", "enabled": True},
    {"name": "gpt-4o", "provider": "openai",
     "env_key": "OPENAI_API_KEY", "weight": 1.5,
     "temperature": 0.5, "style": "analytical", "enabled": True},
    {"name": "deepseek-chat", "provider": "deepseek",
     "env_key": "DEEPSEEK_API_KEY",
     "base_url": "https://api.deepseek.com",
     "weight": 1.0, "temperature": 0.4,
     "style": "practical", "enabled": True},
    {"name": "qwen-max", "provider": "qwen",
     "env_key": "DASHSCOPE_API_KEY",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "weight": 1.0, "temperature": 0.4,
     "style": "structured", "enabled": True},
]

GENERATION_STRATEGY = {
    "mode": "write_and_vote",
    "n_samples_per_model": 1,
    "min_models_required": 2,
    "blind_eval": True,
    "randomize_order": True,
    "merge_top_k": 2,
    "enable_merge": False,
    "concurrency": 4,
    "timeout_seconds": 60,
}

STYLE_INSTRUCTIONS = {
    "concise": "**额外风格**：每段不超过 3 句，直接给结论。",
    "analytical": "**额外风格**：每段有为什么，引用数字+趋势，用表格对比。",
    "practical": "**额外风格**：每条建议有做什么/何时/预期，用命令式。",
    "structured": "**额外风格**：严格按现状→分析→建议三段式，编号列表。",
    "balanced": "**额外风格**：兼顾简洁与分析，每段 2-3 句。",
}

MERGE_CONFIG = {
    "enabled": False,          # ← 改成 True 才启用融合
    "merge_method": "llm_merge",
    "merge_prompt": "综合以下多份日报的优点，生成一份最终日报。",
    "keep_original": True,
    "merge_top_k": 2,
}

OUTPUT = {
    "save_all_versions": True,
    "save_dir": f"{REPORT_DIR}/multi_gen",
    "final_report_path": f"{REPORT_DIR}/report_daily.md",
    "eval_result_path": f"{REPORT_DIR}/multi_gen_eval.json",
    "archive": True,
}


def get_style_instruction(style):
    return STYLE_INSTRUCTIONS.get(style, "")