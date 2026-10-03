# -*- coding: utf-8 -*-
"""#22 实跑验证驱动：真入口跑一轮全量，只关掉与本次改动无关的因子归因。

为什么要驱动脚本而不是直接 `python etf/v1/src/main.py`：本轮要验的是
`stage_lifecycle` 有没有把 walk-forward 的合并样本外 DSR 喂进重挖触发器
（`main.py` 20:51 的改动，上一轮 18:35 起跑的进程没赶上），而 `因子归因`
实测 8943s（`置换采样 200/200 已评估子集 127`）占整轮六成、且与这条改动
无关。只在本进程里把 `FACTOR_ATTRIBUTION["enabled"]` 置 False，
**config.py 一个字节都不改**，其余阶段一律走生产真入口、真数据、真账本。

已知会被这一轮真实执行的东西（用户选定方案 1）：账本 consecutive=1、
last_dsr=0.004061、last_pbo=0.657143 ⇒ 本轮两腿同恶化 ⇒ 连续 2/2 ⇒ 触发
`AutoReminingLoop`（remining_engines=registry+genetic，不碰 RD-Agent），
因子库可能自动产生 [factor-lib] commit。
"""

import os
import sys

V1 = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                  "etf", "v1")
SRC = os.path.join(V1, "src")
os.chdir(SRC)                      # 与生产入口一致：CWD = etf/v1/src
sys.path.insert(0, SRC)

import _bootstrap  # noqa: E402,F401  必须先于项目模块导入
import config

config.FACTOR_ATTRIBUTION["enabled"] = False
print("=" * 60)
print("  #22 实跑验证驱动 | 唯一改动：因子归因关在本进程")
print(f"  FACTOR_ATTRIBUTION.enabled = {config.FACTOR_ATTRIBUTION['enabled']}"
      f"（进程内，配置文件未改）")
print(f"  AUTO_REMINING.enabled      = {config.AUTO_REMINING['enabled']}")
print(f"  TRIGGER_LOGIC              = mode={config.TRIGGER_LOGIC['mode']} "
      f"consecutive_rounds={config.TRIGGER_LOGIC['consecutive_rounds']} "
      f"dsr_threshold={config.TRIGGER_LOGIC['dsr_threshold']}")
print("=" * 60)

import main  # noqa: E402

assert main.FACTOR_ATTRIBUTION is config.FACTOR_ATTRIBUTION, \
    "main 里拿到的必须是同一个 dict 对象，否则关掉的是别人的配置"

sys.exit(main.main())
