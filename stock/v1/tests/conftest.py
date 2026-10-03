# -*- coding: utf-8 -*-
"""pytest 基座（股票线）：目录隔离 + 裸模块名导入 + 真实数据/网络零依赖。

口径抄 ETF 线（`etf/v1/tests/conftest.py`），换成本线的 env 前缀 `STOCK_`：
`config.py` 在 import 期就调 `config_base.build()` 解析目录并 makedirs，
**事后再改 env 无效**，所以所有覆盖必须发生在导入任何项目模块之前。

被指到临时目录的四根（缺一根就会写进生产）：
  STOCK_DATA_DIR        → 研究产物 / 账本 / RD-Agent 工作区都从它派生
  STOCK_BASE_DATA_DIR   → 09-29 起基础行情与 qlib bin 在此（CACHE_DIR/QLIB_DATA_DIR 由它派生）
  STOCK_REPORT_DIR      → 报告落盘
  STOCK_LOG_DIR         → 日志落盘
再加一根 `STOCK_DAILY_H5`：生产面板 789MB / 15,176,062 行，测试**不许**读它
（指到一个不存在的临时路径，谁误读就在第一次取数时炸出来，而不是静默慢一整轮）。

网络与 LLM：本线目前无 LLM 调用点（见 AGENT.md §3），但仍显式摘掉相关 env，
防止哪天接线后用真端点跑测试；交易所日历（akshare）在需要它的用例里由
monkeypatch 注入假日历，**测试一律不发请求**。
"""

import atexit
import os
import pathlib
import shutil
import sys
import tempfile

V1_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(V1_ROOT, "src")

_TMP_ROOT = tempfile.mkdtemp(prefix="stock-v1-test-")
os.environ["STOCK_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")
os.environ["STOCK_BASE_DATA_DIR"] = os.path.join(_TMP_ROOT, "base_data")
os.environ["STOCK_REPORT_DIR"] = os.path.join(_TMP_ROOT, "report")
os.environ["STOCK_LOG_DIR"] = os.path.join(_TMP_ROOT, "log")
# 指向一个**不存在**的路径：任何用例真去读生产面板都会当场失败，而不是悄悄变慢
os.environ["STOCK_DAILY_H5"] = os.path.join(_TMP_ROOT, "no_such_panel.h5")
os.environ["STOCK_FACTORS_JSON"] = os.path.join(_TMP_ROOT, "factors.json")

for _k in list(os.environ):
    if _k.startswith("STOCK_LLM_") or _k in ("OPENAI_API_KEY", "OLLAMA_HOST"):
        os.environ.pop(_k, None)

# 跑一次留一枚临时目录 ⇒ 09-29 连跑 15 次就攒了 15 个；收尾自己扫掉
atexit.register(shutil.rmtree, _TMP_ROOT, ignore_errors=True)

sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402,F401  裸模块名导入的挂载器（与生产同一份）

import config  # noqa: E402

TEST_TMP = _TMP_ROOT
# SRC = <repo>/stock/v1/src ⇒ 上溯三层是仓库根（与 _bootstrap 里同一个算法）
REPO_ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.dirname(SRC))))
