# -*- coding: utf-8 -*-
"""换模型后的端到端一枪：真走共享层注入 + `qwen3.5:9b`，产物只落临时目录。

为什么不直接用 `run_feedback.py --self-eval`：那个入口在第 112 行**无条件**先跑
`run_feedback()` 整批（覆写 `report/feedback_report.md` 与 `data/live/` 若干产物），
本轮要的只是"闸门有没有真的加进请求体、9b 能不能出可解析四维"。
与 i33 的差别：i33 显式摘掉端点变量专测模板分支，这里**保留** `.env`，
所以 LLM_PATH 上每一环都是生产口径 —— `SelfEvaluator._chat`（不传 max_tokens）
→ `make_openai_client` → `_apply_request_defaults` 注入 → ollama `/v1` → 9b。
"""
import json
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "etf", "v1", "src")))
import _bootstrap  # noqa: F402,E402
from config import LIVE_DATA_DIR, REPORT_DIR  # noqa: E402
from core.llm_client import describe_endpoint  # noqa: E402
from llm_selfreport import save_self_eval_dims  # noqa: E402
from self_evaluator import SelfEvaluator  # noqa: E402

TMP = tempfile.mkdtemp(prefix="i38_switch_")
WATCH = ("self_eval_summary.json", "self_eval_detail.csv",
         "feedback_report.md")


def main():
    print(describe_endpoint(), flush=True)
    before = {}
    for f in WATCH:
        p = os.path.join(REPORT_DIR, f)
        before[f] = (os.path.getmtime(p) if os.path.exists(p) else None)

    ev = SelfEvaluator(live_data_dir=LIVE_DATA_DIR, report_dir=TMP)
    print(f"endpoint_enabled={ev.enabled} | 模型来自 config | 产物目录={TMP}",
          flush=True)
    t0 = time.time()
    result = ev.eval_batch(days=2)
    dt = time.time() - t0
    print(f"eval_batch(days=2) 用时 {dt:.1f}s", flush=True)
    for r in (result or {}).get("results") or []:
        sc = ((r.get("llm_score") or {}).get("scores")) or {}
        print(json.dumps({
            "date": r.get("date"), "final_score": r.get("final_score"),
            "grade": r.get("grade"),
            "llm_total": (r.get("llm_score") or {}).get("total"),
            "objective": (r.get("objective") or {}).get("objective_score"),
            "四维": [sc.get(k) for k in
                     ("accuracy", "completeness", "actionability", "logic")]},
            ensure_ascii=False), flush=True)
    if result:
        save_self_eval_dims(result, ev.enabled,
                            os.path.join(TMP, "self_eval_dims.json"))

    print("\n生产产物 mtime 核对：", flush=True)
    dirty = 0
    for f in WATCH:
        p = os.path.join(REPORT_DIR, f)
        now = os.path.getmtime(p) if os.path.exists(p) else None
        same = now == before[f]
        dirty += 0 if same else 1
        print(f"  {f}: {'未改动 ✅' if same else '被写了 ❌'}", flush=True)
    print(f"{'✅ 生产目录零写入' if dirty == 0 else '❌ 有生产文件被改动'}"
          f" | 临时产物 {sorted(os.listdir(TMP))}", flush=True)


if __name__ == "__main__":
    main()
