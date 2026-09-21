# -*- coding: utf-8 -*-
"""策略中文名 + 策略指标口径解释"""

import re

ORTHO_CN = {
    "gram_schmidt": "GS正交化",
    "pca": "PCA正交化",
    "none": "原始因子",
}

WEIGHT_CN = {
    "equal": "等权",
    "ic_ir": "ICIR加权",
    "risk_parity": "风险平价",
}

COMBINE_CN = {
    "equal": "等权组合",
    "risk_parity": "风险平价组合",
    "ic_weighted": "夏普加权组合",
    "lifecycle": "生命周期加权组合",
}

# multi_summary.csv 各列口径
STRATEGY_METRIC_GLOSSARY = [
    ("总收益", "区间总收益率",
     "回测期初到期末的净值累计涨幅，未扣挖掘偏差。"),
    ("年化", "年化收益率",
     "总收益折算为一年的复合增速，便于跨区间比较。"),
    ("夏普", "夏普比率",
     "年化收益 / 年化波动。>1 可用，>2 优秀；这里是原始夏普。"),
    ("回撤", "最大回撤",
     "净值从峰到谷的最大跌幅，衡量最坏持有体验；越小越好。"),
    ("交易次数", "成交笔数",
     "回测期内买入+卖出的总次数，配合总收益看费用侵蚀。"),
    ("胜率", "单笔盈利占比",
     "盈利笔数 / 总笔数。胜率略高于 50% 且盈亏比为正即可盈利。"),
    ("PBO", "回测过拟合概率",
     "样本内选出的策略在样本外垫底的概率。<0.5 较可信，越低越好。"),
]

_NAME_RE = re.compile(
    r"^S(?P<idx>\d+)_(?P<ortho>[a-z_]+?)_sl(?P<sl>[\d.]+)_(?P<weight>\w+)$")


def strategy_cn(name: str) -> str:
    """S3_gram_schmidt_sl0.02_ic_ir -> 策略3·GS正交化·日止损2%·ICIR加权"""
    if not name:
        return ""
    m = _NAME_RE.match(name)
    if m:
        ortho = ORTHO_CN.get(m.group("ortho"), m.group("ortho"))
        weight = WEIGHT_CN.get(m.group("weight"), m.group("weight"))
        sl = float(m.group("sl")) * 100
        sl_txt = f"{sl:g}%"
        return f"策略{m.group('idx')}·{ortho}·日止损{sl_txt}·{weight}"
    if name.startswith("[组合]"):
        mode = name.split("]", 1)[1].strip()
        return COMBINE_CN.get(mode, f"组合·{mode}")
    return name
