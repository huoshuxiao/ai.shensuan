# -*- coding: utf-8 -*-
"""pytest 基座：目录隔离 + 裸模块名导入 + 真实数据/网络零依赖。

必须在导入任何项目模块之前改环境变量：config.py 在 import 期就解析
ETF_DATA_DIR 并 makedirs，事后再改无效。所有测试因此只写临时目录，
不会污染 data/cache、data/library、data/results。
"""

import os
import sys
import tempfile

import pytest

V1_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(V1_ROOT, "src")

_TMP_ROOT = tempfile.mkdtemp(prefix="etf-v1-test-")
os.environ["ETF_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")
os.environ["ETF_REPORT_DIR"] = os.path.join(_TMP_ROOT, "report")
os.environ["ETF_LOG_DIR"] = os.path.join(_TMP_ROOT, "log")
os.environ["ETF_FREQ"] = "daily"
# 测试永不触外部端：LLM 与 RD-Agent 一律视为不可用
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ETF_LLM_BASE_URL", None)
# official 源在 ETF 线默认打开（见 config），但一轮循环 ≈50min 且要
# conda+docker 沙箱，测试环境一律显式关闭，防止任何用例误拉起子进程
os.environ["ETF_RDAGENT_OFFICIAL_FALLBACK"] = "false"
# 规模闸在生产里默认开（5 亿，见 etf_admission.MIN_SCALE），但它要读 `data/risk/`
# 的份额长表 —— 而本文件的 `ETF_DATA_DIR` 已经指到临时空目录，闸门就会在
# `load_scale_matrix` 里 SystemExit。测试测的是其余几道闸的语义，一律关掉；
# 规模闸自己的语义在 test_etf_admission 第 12 节用**内存假面板**显式覆盖。
os.environ["ETF_MIN_SCALE"] = "0"

sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402,F401

import config  # noqa: E402

# 关掉因子库 git 自动提交：save_markdown 会向 FACTOR_LIBRARY_GIT.git_dir
# （真实仓库根）提交，测试绝不能有这种副作用
config.FACTOR_LIBRARY_GIT["enabled"] = False
config.FACTOR_LIBRARY_GIT["auto_commit"] = False

from synth import make_daily_pool  # noqa: E402

TEST_TMP = _TMP_ROOT


@pytest.fixture
def daily_pool():
    return make_daily_pool()
