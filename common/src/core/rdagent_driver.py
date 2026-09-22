# -*- coding: utf-8 -*-
"""RD-Agent(Q) factor 循环驱动 —— 只在 conda 环境子进程内运行

本文件不得 import 本项目任何模块（环境内没有管线依赖），只依赖
rdagent/pyqlib。跑完后从最新会话 pickle 回收因子（名字/LaTeX 原式/
factor.py 代码/IC 指标），翻译为管线 DSL 后写成 output_dir 下的
factors.json，供管线侧（official_rdagent._recover_factors）读取。

用法: python rdagent_driver.py --out <dir> [--loops 5]"""

import argparse
import json
import os
import pickle
import re
import traceback

# factor_name -> 管线 factor_dsl 表达式。rdagent 产出的是 pandas 代码
# 与 LaTeX 公式，管线侧 safe_eval 只认 DSL 函数集（ma/std/ts_sum...），
# 故在环回传前翻译；翻译不了的只回传原文供人工复核。
# 名字口径并不稳定：有的轮次给模板名 `5_day_SMA`，有的给自然语言
# `5-day Simple Moving Average of Volume`，故先按紧凑模板查表，未命中
# 再按「窗口天数 + 作用列 + 统计量」三元组解析
# 注：ma/std/max/min 的第一参数是 df（作用于 close），与其他源口径一致；
# 作用列/统计量在名字里，需要作用在任意序列时改用通用算子（ts_mean 等）
_DSL_BY_NAME = {
    "SMA": lambda n: f"ma(df,{n})",
    "STD": lambda n: f"std(df,{n})",
    "MAX": lambda n: f"max(df,{n})",
    "MIN": lambda n: f"min(df,{n})",
    # 成交量加权均价 VWAP_n = Σ(vol·close) / Σ(vol)
    "VWAP": lambda n: f"ts_sum(volume*close,{n})/ts_sum(volume,{n})",
}
_GENERIC_OPS = {
    "SMA": "ts_mean", "STD": "ts_std", "MAX": "ts_max", "MIN": "ts_min",
}
_WINDOW_RE = re.compile(r"(\d+)\s*[-_ ]?\s*(?:day|d)", re.I)
_COLUMN_RE = re.compile(r"\b(volume|vol|close|price|open|high|low)\b", re.I)
_OP_RE = re.compile(
    r"\b(SMA|Simple Moving Average|EMA|VWAP|STD|Standard Deviation|"
    r"MAX|Maximum|MIN|Minimum)\b", re.I)
_OP_ALIAS = {"simple moving average": "SMA", "standard deviation": "STD",
             "ema": "EMA", "maximum": "MAX", "minimum": "MIN"}


def _expr_from_task(task):
    name = (getattr(task, "factor_name", "") or "").strip()
    key = name.upper().replace(" ", "_")
    if key in _DSL_BY_NAME:
        return _DSL_BY_NAME[key](name.split("_")[0])
    m = re.match(r"^(\d+)_day_(SMA|STD|MAX|MIN|VWAP)$", key, re.I)
    if m and m.group(2) in _DSL_BY_NAME:
        return _DSL_BY_NAME[m.group(2)](m.group(1))
    # 自然语言名：三元组都解析出来才翻译，否则宁可回传空串交人工复核
    w, c, o = _WINDOW_RE.search(name), _COLUMN_RE.search(name), _OP_RE.search(name)
    if not (w and c and o):
        return ""
    op = _OP_ALIAS.get(o.group(1).lower(), o.group(1).upper())
    n, col = w.group(1), c.group(1).lower()
    col = {"vol": "volume", "price": "close"}.get(col, col)
    if op == "EMA":
        return ""                      # DSL 无指数加权算子，不硬凑
    if op == "VWAP":
        return _DSL_BY_NAME["VWAP"](n)
    return f"{_GENERIC_OPS[op]}({col},{n})" if col != "close" \
        else _DSL_BY_NAME[op](n)


_METRIC_KEYS = {
    # 本管线口径 -> qlib 回测指标名
    "mean_ic": ("IC",),
    "icir": ("ICIR",),
    "rank_ic": ("Rank IC",),
    "rank_icir": ("Rank ICIR",),
}
_METRIC_RE = {
    # qlib 的指标输出既可能是 "IC = 0.03" 也可能是表格行 "IC     0.03"
    "mean_ic": re.compile(r"(?<![A-Za-z_])IC\s*(?:=|:)?\s*(-?[\d.]+)"),
    "icir": re.compile(r"ICIR\s*(?:=|:)?\s*(-?[\d.]+)"),
}


def _metrics_from_result(result):
    """从 running 步产物取回测指标

    qlib 的指标挂在 experiment.result 上（pandas Series，键就是 IC/ICIR/
    Rank IC/Rank ICIR），而 str(experiment) 里没有这些数字 —— 早先只对字符串
    跑正则，09-22 那轮 running 真出了 IC=0.029 仍被回收成 0.0。故先按对象取，
    取不到再退回字符串正则（running 抛异常时会话里只剩一段文本）。"""
    out = {k: 0.0 for k in _METRIC_KEYS}
    res = getattr(result, "result", None)
    if res is not None and hasattr(res, "get"):
        for key, qlib_names in _METRIC_KEYS.items():
            for name in qlib_names:
                try:
                    val = res.get(name)
                except Exception:
                    val = None
                if val is not None:
                    out[key] = float(val)
                    break
        if any(out.values()):
            return out
    text = result if isinstance(result, str) else str(result)
    for key, pat in _METRIC_RE.items():
        m = pat.search(text or "")
        if m:
            out[key] = float(m.group(1))
    return out


def _harvest_from_sessions(out_dir):
    """从最新 rdagent 会话 pickle 回收因子

    会话目录 log/<时间戳>/__session__/<loop>/<step>_<name> 里每个 pickle
    都是完整 FactorRDLoop（含 loop_prev_out 全部中间产物）。running 步
    抛 FactorEmptyError 时 result/feedback 为 None，但 coding 步已产出
    经 CoSTEER 演化验证的 factor.py，因子本体仍值得回收。"""
    sessions = []
    log_dir = os.path.join(out_dir, "log")
    for d in os.listdir(log_dir) if os.path.isdir(log_dir) else []:
        s = os.path.join(log_dir, d, "__session__")
        if os.path.isdir(s):
            sessions.append(s)
    if not sessions:
        return []
    latest = max(sessions, key=os.path.getmtime)
    files = []
    for loop_dir in os.listdir(latest):
        ld = os.path.join(latest, loop_dir)
        if os.path.isdir(ld):
            files += [os.path.join(ld, f) for f in os.listdir(ld)]
    if not files:
        return []
    # 文件名前缀是 dump 序号，取最后一次 dump = 最全状态
    latest_file = max(files, key=lambda p: (int(os.path.basename(p).split("_")[0]),
                                            os.path.getmtime(p)))
    with open(latest_file, "rb") as f:
        loop = pickle.load(f)
    found = []
    for _li, step_out in getattr(loop, "loop_prev_out", {}).items():
        if not isinstance(step_out, dict):
            continue
        exp = step_out.get("coding")
        result = step_out.get("running")
        if exp is None or not hasattr(exp, "sub_tasks"):
            # coding 全部演化失败时该步是 None，但假设阶段（direct_exp_gen
            # .exp_gen）已经给出任务定义（factor_name + LaTeX 原式）。管线
            # 侧要的是「定义」不是那份坏 pandas，故回退到原始任务照样回收。
            gen = step_out.get("direct_exp_gen")
            exp = gen.get("exp_gen") if isinstance(gen, dict) else None
            if exp is None or not hasattr(exp, "sub_tasks"):
                continue
        metrics = _metrics_from_result(result)
        workspaces = getattr(exp, "sub_workspace_list", [])
        for i, task in enumerate(exp.sub_tasks):
            code = ""
            if i < len(workspaces):
                code = workspaces[i].file_dict.get("factor.py", "") \
                    if hasattr(workspaces[i], "file_dict") else ""
            found.append({
                "name": getattr(task, "factor_name", f"official_{i}"),
                "expr": _expr_from_task(task),
                "formulation": getattr(task, "factor_formulation", ""),
                "code": code,
                **metrics,
            })
    print(f"[driver] 会话 {os.path.basename(os.path.dirname(latest))}"
          f" 回收 {len(found)} 个因子")
    return found


def _harvest_latest_factors(out_dir):
    """优先读会话 pickle，退回扫描 JSON 类产物"""
    found = _harvest_from_sessions(out_dir)
    seen = {f["name"] for f in found}
    for root, _dirs, files in os.walk(out_dir):
        for fn in files:
            if fn.endswith(".json") and fn != "factors.json":
                p = os.path.join(root, fn)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                items = data if isinstance(data, list) \
                    else data.get("factors", []) if isinstance(data, dict) \
                    else []
                for it in items:
                    if isinstance(it, dict) and (
                            "expr" in it or "expression" in it) \
                            and it.get("name") not in seen:
                        found.append(it)
                        seen.add(it.get("name"))
    # 每轮只回收当轮会话的因子，直接覆写会把历轮攒下的定义一起抹掉
    # （09-22 连续两轮把 12 个覆成 2 个）。同名以本轮为主，只把本轮缺失
    # 的字段（如沙箱实测过的 code、IC 指标）从旧条目补回来。
    prev_path = os.path.join(out_dir, "factors.json")
    if os.path.exists(prev_path):
        try:
            with open(prev_path, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = []
        for old in prev if isinstance(prev, list) else []:
            if not isinstance(old, dict) or not old.get("name"):
                continue
            mine = next((x for x in found if x.get("name") == old["name"]), None)
            if mine is None:
                found.append(old)
                seen.add(old["name"])
            else:
                for k, v in old.items():
                    if v and not mine.get(k):
                        mine[k] = v
    if found:
        with open(os.path.join(out_dir, "factors.json"), "w",
                  encoding="utf-8") as f:
            json.dump(found, f, ensure_ascii=False, indent=2)
        print(f"[driver] 回收 {len(found)} 个因子表达式 -> factors.json")
    else:
        print("[driver] 未在产物目录发现可回收的因子")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="产物/工作目录")
    ap.add_argument("--loops", type=int, default=5,
                    help="factor 循环轮数（16G 内存下建议 ≤5）")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    # RD-Agent(Q) 的模型配置走工作目录 .env（LITELLM_CHAT_MODEL 等，
    # 与官方 CLI 的 load_dotenv(".env") 行为一致）
    from dotenv import load_dotenv
    load_dotenv(os.path.join(args.out, ".env"))

    from rdagent.app.qlib_rd_loop.factor import main as factor_main
    print(f"[driver] rdagent factor 循环启动 loop_n={args.loops}")
    try:
        factor_main(loop_n=args.loops)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
    _harvest_latest_factors(args.out)
    print("[driver] 完成")


if __name__ == "__main__":
    main()
