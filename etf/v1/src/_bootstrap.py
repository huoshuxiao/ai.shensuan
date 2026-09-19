# -*- coding: utf-8 -*-
"""sys.path 引导：让各层模块可以裸模块名导入（from config import ...）

所有入口脚本在导入任何项目模块之前先 `import _bootstrap`。
"""

import os
import sys

_SRC = os.path.dirname(os.path.abspath(__file__))

for _d in ("config", "data", "core", "strategy", "backtest",
           "optimizer", "live", "feedback", "report", "view"):
    _p = os.path.join(_SRC, _d)
    if _p not in sys.path:
        sys.path.append(_p)
