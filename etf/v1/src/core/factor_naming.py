# -*- coding: utf-8 -*-
"""因子中文名 + 指标口径解释（统一供挖掘打印/因子库/看板/LLM 报告使用）"""

import re

# 内置注册表因子（core/factors.py FACTOR_REGISTRY）
BUILTIN_CN = {
    "momentum_20": "20日动量",
    "momentum_10": "10日动量",
    "reversal_5": "5日反转",
    "volatility_20": "20日低波动",
    "ma_ratio_5_20": "均线比(5日/20日)",
    "ma_ratio_10_30": "均线比(10日/30日)",
    "rsi_14": "14日RSI相对强弱",
    "volume_ratio_20": "20日量比",
    "price_position_20": "20日价格分位",
}

# LLM 未配置时的内置模板因子（core/llm_factor_agent.py _fallback）
TEMPLATE_CN = {
    "mom_5": "5日价格动量(模板)",
    "mom_20": "20日价格动量(模板)",
    "vol_20": "20日低波动(模板)",
    "vol_60": "60日低波动(模板)",
    "ma_cross": "均线交叉(5日/20日)",
    "rsi_like": "RSI类强弱(14日)",
    "vol_ratio": "20日量比(模板)",
    "price_pos": "20日价格位置(模板)",
}

# 挖掘来源前缀 -> 中文系列名（后跟数字编号）
PREFIX_CN = {
    "gp": "遗传规划因子",
    "mogp": "多目标遗传规划因子",
    "hybrid": "LLM-遗传混合因子",
    "official": "RD-Agent 因子",
    "plan": "研究计划因子",
}

# 表达式 DSL 算子 -> 中文（core/factor_dsl.py FACTOR_DSL）
EXPR_TOKEN_CN = {
    "close": "收盘价", "open": "开盘价", "high": "最高价", "low": "最低价",
    "volume": "成交量", "returns": "收益率",
    "ma": "均线", "std": "标准差", "max": "区间最高", "min": "区间最低",
    "delay": "滞后", "delta": "差分", "ts_sum": "滚动求和",
    "ts_mean": "滚动均值", "ts_std": "滚动标准差", "rank": "滚动分位",
    "abs": "绝对值", "log": "对数", "sign": "符号",
}

# 带窗口数字的通用命名模式：xxx_12 -> 中文
_WINDOW_PATTERNS = [
    (r"^momentum_(\d+)$", r"\1日动量"),
    (r"^mom_(\d+)$", r"\1日动量"),
    (r"^reversal_(\d+)$", r"\1日反转"),
    (r"^rev_(\d+)$", r"\1日反转"),
    (r"^volatility_(\d+)$", r"\1日低波动"),
    (r"^vol_(\d+)$", r"\1日低波动"),
    (r"^rsi_(\d+)$", r"\1日RSI"),
    (r"^volume_ratio_(\d+)$", r"\1日量比"),
    (r"^price_position_(\d+)$", r"\1日价格分位"),
    (r"^ma_ratio_(\d+)_(\d+)$", r"均线比(\1日/\2日)"),
]

# IC = Information Coefficient，信息系数，用来衡量因子预测未来收益的能力。
# 指标口径说明：(字段, 中文名, 解读)
METRIC_GLOSSARY = [
    ("mean_ic", "平均信息系数 IC",
     "因子值与下一期收益的 Spearman 秩相关均值。|IC|>0.02 有初步预测力，"
     ">0.05 较强；符号表示方向（负值反向有效）。"),
    ("icir", "信息比率 ICIR",
     "mean IC / IC 标准差，衡量预测力稳定性。|ICIR|>0.5 可用，>1 优秀。"),
    ("turnover", "换手率",
     "单根 bar 因子截面排名变化比例。换手越高，手续费与滑点侵蚀越大，"
     "日线策略宜 <0.3。"),
    ("max_corr", "最大相关性",
     "与库内其他因子的最大截面相关。接近 1 说明信息冗余，聚类去重会剔除。"),
    ("stability", "稳定性",
     "IC 在时间上的一致性得分（0~1），越高越不易段外失效。"),
    ("simplicity", "简洁度",
     "表达式树节点数越少得分越高。简单因子过拟合风险更低。"),
    ("dsr", "去偏夏普比 DSR",
     "考虑多次试验挖掘偏差后的夏普比，>0 表示显著优于随机。"),
    ("pbo", "回测过拟合概率 PBO",
     "样本内最优策略在样本外垫底的概率，<0.5 较可信，越低越好。"),
]


def _prefix_cn(name: str):
    m = re.match(r"^([a-z]+)_0*(\d+)$", name)
    if m and m.group(1) in PREFIX_CN:
        return f"{PREFIX_CN[m.group(1)]}{int(m.group(2)) + 1}号"
    if name.startswith("plan_"):
        return "研究计划因子"
    return None


def translate_expr(expr: str) -> str:
    """把 DSL 表达式粗翻译成中文读法（用于无标准名时的兜底描述）"""
    if not expr:
        return ""
    text = expr
    text = re.sub(r"df\[[\"'](close|open|high|low|volume)[\"']\]",
                  lambda m: EXPR_TOKEN_CN[m.group(1)], text)
    text = re.sub(r"\bdf\b", "数据", text)
    for tok in ("ts_mean", "ts_std", "ts_sum", "delta", "delay", "rank",
                "ma", "std", "max", "min", "abs", "log", "sign",
                "returns", "volume", "close", "open", "high", "low"):
        cn = EXPR_TOKEN_CN[tok]
        text = re.sub(rf"\b{tok}\b", cn, text)
    return text


def cn_name(name: str, expr: str = "") -> str:
    """因子中文名：注册表/模板 > 窗口模式 > 来源前缀 > 表达式翻译 > 原名"""
    if not name:
        return ""
    if name in BUILTIN_CN:
        return BUILTIN_CN[name]
    if name in TEMPLATE_CN:
        return TEMPLATE_CN[name]
    for pattern, tpl in _WINDOW_PATTERNS:
        m = re.match(pattern, name)
        if m:
            return m.expand(tpl)
    by_prefix = _prefix_cn(name)
    if by_prefix:
        return by_prefix
    if expr:
        return translate_expr(expr)[:40]
    return name
