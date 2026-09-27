# -*- coding: utf-8 -*-
"""#33 端到端验读数：真取数、真走 self_evaluator，只把产物写到临时目录。

为什么不直接跑 `run_feedback.py --self-eval`：那条入口会先执行 `run_feedback()`
整批（覆写 report/feedback_report.md 与 data/live/ 若干产物），而 `SelfEvaluator
._save()` 又会重写 report/self_eval_summary.json —— 本轮要的只是"读数能不能取到"，
所以在这里显式把 report_dir 指到临时目录，生产产物一个字节都不动。
"""
import os
import sys
import json
import tempfile

# 本探针要的是"无端点时走模板"这条确定路径，显式摘掉端点变量，免得顺手去敲
# 用户那台在跑的本地 LLM（同 tests/conftest.py 的做法）
for _k in ("OPENAI_API_KEY", "ETF_LLM_BASE_URL", "LITELLM_PROXY_API_KEY",
           "LITELLM_BASE_URL"):
    os.environ.pop(_k, None)

_SRC = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", "etf", "v1", "src"))
sys.path.insert(0, _SRC)
import _bootstrap  # noqa: F402,E402
from config import (LIVE_DATA_DIR, RDAGENT_OUTPUT_DIR, FACTOR_LIBRARY,
                    REPORT_DIR)
from llm_selfreport import (decision_tally, harvest_vs_library,
                            self_report_mtime, save_self_eval_dims)
from self_evaluator import SelfEvaluator

TMP = tempfile.mkdtemp(prefix="i33_readings_")
SNAPSHOT_FILES = ("self_eval_summary.json", "self_eval_detail.csv")
before = {f: os.path.getmtime(f"{REPORT_DIR}/{f}")
          for f in SNAPSHOT_FILES if os.path.exists(f"{REPORT_DIR}/{f}")}

print("== ① 容器 Final Decision 自报计数 ==")
for row in decision_tally(RDAGENT_OUTPUT_DIR):
    print("   ", row)
print("    目录不存在时的形状:", decision_tally("/nonexistent/here"))

print("\n== ② 容器自报 IC ↔ 库内重算 IC ==")
h = f"{RDAGENT_OUTPUT_DIR}/factors.json"
df = harvest_vs_library(h, FACTOR_LIBRARY["index_path"])
print(f"    自报产物 {h} 落盘 {self_report_mtime(h)} → {len(df)} 行")
print(df.to_string(index=False))
print("    文件缺失时:", harvest_vs_library("/nonexistent/factors.json", None))

print("\n== ③ 自评四维（真走 SelfEvaluator，产物写临时目录） ==")
ev = SelfEvaluator(live_data_dir=LIVE_DATA_DIR, report_dir=TMP)
res = ev.eval_batch(days=20)
print(f"    endpoint_on={ev.enabled} → 评法应为 template（无端点）")
payload = save_self_eval_dims(res, ev.enabled, f"{TMP}/self_eval_dims.json")
print("    mode =", payload["mode"], "| 行数 =", len(payload["detail"]))
print("    首行 =", json.dumps(payload["detail"][0], ensure_ascii=False)
      if payload["detail"] else "    无快照可评")
with open(f"{TMP}/self_eval_dims.json", encoding="utf-8") as f:
    print("    落盘字节 =", len(f.read()))

after = {f: os.path.getmtime(f"{REPORT_DIR}/{f}")
         for f in SNAPSHOT_FILES if os.path.exists(f"{REPORT_DIR}/{f}")}
print("\n== 生产产物核对（应无变化） ==")
print("    ", "未改动 ✅" if before == after else f"被改了 ❌ {before} → {after}")
print("    临时目录:", TMP)
