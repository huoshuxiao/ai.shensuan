# -*- coding: utf-8 -*-
"""sys.path 引导：让各层模块可以裸模块名导入（from config import ...）

所有入口脚本在导入任何项目模块之前先 `import _bootstrap`。

两条线（ETF / 股票）共用一个 common/ 内核，靠这里的挂载顺序而不是包名区分：
**本线目录先挂、common 后挂**。于是同名模块本线优先（如 core/factor_attribution
只在 ETF 线、config 各自一份），而 common 里的通用模块两线直接可见。
关键副作用是 `from config import X` 在两条线进程里解析到各自的 config.py，
这就是分线的配置开关（见 common/src/config_base.py 的说明）。
"""

import os
import sys

_SRC = os.path.dirname(os.path.abspath(__file__))
# 仓库根：_SRC = <repo>/etf/v1/src
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_SRC)))
_COMMON = os.path.join(_REPO, "common", "src")

# 本线专属：config/data/strategy/backtest/live 只有 ETF 线有，
# core/feedback 是「本线残留 + common 主体」同名双目录，故本线必须在前
_OWN = ("config", "data", "core", "strategy", "backtest", "live", "feedback")
# 共享内核：整个 common/src 也挂上，log_kit / config_base 这类根级模块在此解析
_SHARED = ("", "core", "optimizer", "feedback", "report", "view")

for _d in _OWN:
    _p = os.path.join(_SRC, _d)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
for _d in _SHARED:
    _p = os.path.join(_COMMON, _d) if _d else _COMMON
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
