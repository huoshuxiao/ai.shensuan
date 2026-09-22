# -*- coding: utf-8 -*-
"""sys.path 引导（股票线）：本线目录先挂，common/ 内核后挂。

与 etf/v1/src/_bootstrap.py 同构：全项目走裸模块名导入，所以两线的
区分点就是「先挂谁」——`from config import X` 在此解析到
stock/v1/src/config/config.py，而 common/src 里的 DSL / 因子库 /
RD-Agent 驱动 / 报告模块两线共用。
"""

import os
import sys

_SRC = os.path.dirname(os.path.abspath(__file__))
# 仓库根：_SRC = <repo>/stock/v1/src
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_SRC)))
_COMMON = os.path.join(_REPO, "common", "src")

# 本线目前只有 config（研究线：RD-Agent 产因子 + A 股截面评估，
# 策略/回测/实盘暂时复用 ETF 线，等股票线要独立回测时再在此加目录）
_OWN = ("config",)
# 共享内核：整个 common/src 也挂上，log_kit / config_base 等根级模块在此解析
_SHARED = ("", "core", "optimizer", "feedback", "report", "view")

for _d in _OWN:
    _p = os.path.join(_SRC, _d)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
for _d in _SHARED:
    _p = os.path.join(_COMMON, _d) if _d else _COMMON
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
