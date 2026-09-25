# -*- coding: utf-8 -*-
"""容器/LLM 自述读数（只加读数，不参与任何判据）

09-25 边界核查收回了四道闸（风控区间、外来 IC 重算、提示词回滚、库来源标记），
剩下三处的判据实现都在 `common/src`，本线不改它们，只把它们的**自述**搬到看板上
并标明"这不是判据"：

1. `decision_tally`：RD-Agent 容器每轮的 `Final Decision`（CoSTEER 判的是**代码
   实现**对不对，不是因子 alpha），只存在于子进程日志文本里，`common/src` 从不读它；
2. `harvest_vs_library`：容器自报 IC 与本池重算后库内 IC 的并排对照（差多少一眼可见，
   准入用的永远是后者，见 `main.recount_foreign_ic`）；
3. `save_self_eval_dims`：日报自评的四维分数 —— 共享层只落 `final_score/grade`
   （`self_evaluator.py:136-143`），四维在内存里算完就丢，这里在本线补一份落盘，
   并且**记录这一轮端点在不在**：`mode` 的判据只是
   `endpoint_enabled = bool(key) or bool(LLM_BASE_URL)`，而本线默认就配了
   `LLM_BASE_URL`（`etf/v1/.env`）⇒ 常态是 `llm`；`template` 只在端点被清空时出现
   （那时四维恒 20、grade 恒 A）。**`llm` 不等于分数可信**：09-25 13:41 实测
   `qwen2.5:7b` 把 `EVAL_PROMPT` 里的示例 JSON 逐字吐回（四维 22/18/20/21、
   total 81），所以这一栏是"谁给的数"，不是"数对不对"。
"""

import os
import re
import json
import glob
from datetime import datetime

import pandas as pd

# 容器结论行的原文形如 `This implementation is SUCCESS.` / `... is FAIL.`
_DECISION_RE = re.compile(r"This implementation is (SUCCESS|FAIL)\b")


def _fmt_mtime(path):
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime(
        "%Y-%m-%d %H:%M")


def decision_tally(log_dir, pattern="loop_*.log"):
    """数出每场容器循环日志里的 SUCCESS/FAIL 次数，按落盘时间倒序。

    只在 `log_dir` 里找：这里是本线产物目录（`RDAGENT_OUTPUT_DIR`），如果谁把子进程
    stdout 重定向到别处（例如临时驱动脚本的日志），那个场次的结论**不在这张表里**
    —— 所以返回行数不等于跑过的场次数，宁少报不误报。"""
    rows = []
    for path in glob.glob(os.path.join(log_dir or "", pattern)):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                hits = _DECISION_RE.findall(f.read())
        except OSError:
            continue
        rows.append({"log": os.path.basename(path),
                     "ended": _fmt_mtime(path),
                     "decision_success": hits.count("SUCCESS"),
                     "decision_fail": hits.count("FAIL"),
                     "n_decisions": len(hits)})
    rows.sort(key=lambda r: os.path.getmtime(
        os.path.join(log_dir, r["log"])), reverse=True)
    return rows


def harvest_vs_library(harvest_path, lib_index_path):
    """容器自报 IC ↔ 库内（本池重算后）IC 的并排表。

    自报值取 `factors.json` 的 `mean_ic`（回收时三种键名都读过，见
    `official_rdagent.py:377-380`），库内值取 `factor_library_index.json` 的 `ic`。
    两者不同名或因子已下架时留空，不做插值 —— 这张表只用来"看见差多少"。"""
    if not (harvest_path and os.path.exists(harvest_path)):
        return pd.DataFrame()
    with open(harvest_path, "r", encoding="utf-8") as f:
        harvest = json.load(f)
    if isinstance(harvest, dict):
        harvest = harvest.get("factors", [])
    lib = {}
    if lib_index_path and os.path.exists(lib_index_path):
        with open(lib_index_path, "r", encoding="utf-8") as f:
            lib = json.load(f)
    rows = []
    for i, item in enumerate(harvest or []):
        name = item.get("name") or f"official_{i}"
        rec = lib.get(name) or {}
        self_ic = item.get("mean_ic")
        lib_ic = rec.get("ic")
        rows.append({
            "因子": name,
            "容器自报 IC": self_ic,
            "库内 IC（本池重算）": lib_ic,
            "差值": (None if self_ic is None or lib_ic is None
                    else round(float(lib_ic) - float(self_ic), 6)),
            "库内来源": rec.get("source", "不在库"),
            "库内更新时间": rec.get("last_seen", "—")})
    return pd.DataFrame(rows)


def self_report_mtime(harvest_path):
    """自报产物的落盘时间；文件不在就是"没有可对照的自报产物"，不猜。"""
    if harvest_path and os.path.exists(harvest_path):
        return _fmt_mtime(harvest_path)
    return "—"


DIM_KEYS = ("accuracy", "completeness", "actionability", "logic")


def save_self_eval_dims(result, endpoint_on, path, keep_days=90):
    """把自评四维分数落到本线 report 目录，并写明**每一天**的评法。

    为什么要合并写：共享层 `SelfEvaluator._save`（`self_evaluator.py:136-147`）是
    **整表覆写**，日更只评当日 1 份时会把 `self_eval_detail.csv` 从 20 行冲成 1 行。
    这里按 `date` 做 upsert（同日新覆盖旧、旧日期保留、最多留 `keep_days` 天），
    日更才有可累积的历史；旧文件读不动（截断/手改坏）时整份重写，而不是挡住今天的落盘。

    `mode` 记的是**端点在不在**，不是分数对不对：`endpoint_enabled =
    bool(key) or bool(LLM_BASE_URL)`，本线 `.env` 默认配了 `LLM_BASE_URL` ⇒
    常态是 `llm`。只有把端点清空才会 `endpoint_on=False`，那时共享层走
    `_fallback_llm`（`self_evaluator.py:82-87`）：四维恒 20、total 恒 80、
    grade 恒 A —— 那是模板不是模型给的数，所以这一栏必须**跟着每一天一起落**
    （顶层那份只代表本批），否则哪天端点断过、第二天就看不出来了。
    """
    rows = []
    for r in (result or {}).get("results") or []:
        scores = ((r.get("llm_score") or {}).get("scores")) or {}
        row = {"date": r.get("date", ""),
               "final_score": r.get("final_score"),
               "grade": r.get("grade"),
               "llm_total": (r.get("llm_score") or {}).get("total"),
               "objective": (r.get("objective") or {}).get(
                   "objective_score", r.get("objective")),
               "mode": "llm" if endpoint_on else "template"}
        for k in DIM_KEYS:
            row[k] = scores.get(k)
        rows.append(row)

    merged = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for old_row in (json.load(f).get("detail") or []):
                    if old_row.get("date"):
                        merged[old_row["date"]] = old_row
        except (OSError, ValueError):
            merged = {}
    for row in rows:
        if row["date"]:
            merged[row["date"]] = row
    detail = [merged[d] for d in sorted(merged)[-keep_days:]]

    payload = {"mode": "llm" if endpoint_on else "template",
               "endpoint_on": bool(endpoint_on),
               "written_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "n_batch": len(rows),
               "n_days_total": len(detail),
               "summary": (result or {}).get("summary") or {},
               "detail": detail}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload
