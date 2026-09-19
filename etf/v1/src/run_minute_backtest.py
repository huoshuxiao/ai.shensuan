# -*- coding: utf-8 -*-
"""分钟线回测专用入口"""

import os

# 必须在导入 config / main 之前设置，config.py 会读取该环境变量
os.environ["ETF_FREQ"] = "1min"

import _bootstrap  # noqa: E402,F401
from main import main as run_main  # noqa: E402


if __name__ == "__main__":
    run_main()
