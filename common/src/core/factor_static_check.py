# -*- coding: utf-8 -*-
"""因子表达式的静态尺子：语法体检 + 未来函数硬拒。

10-01 从 `hypothesis_roles` 原样提出（判据一行未改，只换落点）：那把尺子原本
只有多角色定稿闸一个调用点，而那条闸默认关 ⇒ "IC 过线 → 写库" 那条生产路
**没有任何静态检查**，结果 `delay(max(high, 5), -1)` 这种读下一根 K 线的写法
带着被抬高的 IC 进了库（10-01 实测虚高 +0.0203/+0.0261，见
`etf/v1/temp/lookahead_price_1001.py`）。写库单点 `factor_library.upsert` 也要用
它，但不能因此把 LLM 依赖（tenacity / llm_client）拖进落盘路径，故独立成模块。

现在两个调用点共用这一把：
- `hypothesis_roles.HypothesisGate._finalize`（IC 闸之前，默认关）
- `factor_library.FactorLibrary.upsert`（写库之前，默认开，开关
  `FACTOR_LIBRARY["static_gate"]` / 环境变量 `<线>_LIBRARY_STATIC_GATE`）
"""

import ast

from factor_dsl import FACTOR_DSL

# safe_eval 的命名空间就是这些名字；多一个都不放行（LLM 输出要进 eval()，
# 白名单比黑名单可靠：黑名单永远漏，df/np/pd 都在环境里，属性链能爬出去）
_ALLOWED_NAMES = set(FACTOR_DSL) | {"df", "np", "pd"}

# 禁止出现的语法节点：推导式/lambda/赋值都能绕出更好的求值面
_BANNED_NODES = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                 ast.GeneratorExp, ast.Assign, ast.Await, ast.NamedExpr)


def _negative(node) -> bool:
    """字面负数，或 `0 - n` 这种减出负数的写法"""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return isinstance(node.operand, ast.Constant) and node.operand.value > 0
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
        and node.value < 0


def _call_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def check_lookahead(tree) -> str:
    """返回 '' 表示通过，否则返回命中原因（未来函数）"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = _call_name(node)
            # delay/delta 的窗口位；.shift(...) 的位移位
            win_idx = {"delay": 1, "delta": 1}.get(fname)
            if fname == "shift":
                win_idx = 0
            if win_idx is not None and len(node.args) > win_idx \
                    and _negative(node.args[win_idx]):
                return f"{fname} 用了负窗口"
        # 只钉「往回看」：正的 shift(1) 就是 delay，合法；不再按属性名一刀切，
        # 否则合法滞后会被误拒（那才是真会把好因子挡在门外的错）
    return ""


def check_expr(expr: str) -> str:
    """表达式静态体检：'' 通过，否则返回拒因"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return f"语法错误: {e.msg}"
    for node in ast.walk(tree):
        if isinstance(node, _BANNED_NODES):
            return f"非法语法节点 {type(node).__name__}"
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            return f"未知名字 {node.id}"
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return f"非法属性 {node.attr}"
        if isinstance(node, ast.Call):
            fname = _call_name(node)
            kw = {k.arg for k in node.keywords}
            if kw - {"lower", "upper", "fill_method", "abs", "ddof"}:
                return f"{fname} 带了未授权参数"
    return check_lookahead(tree)
