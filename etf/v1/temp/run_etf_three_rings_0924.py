# -*- coding: utf-8 -*-
"""一次性驱动器：按顺序真跑 ETF 准入链三环入口，把 stdout 原样打出来。

放在仓库根 shell/（一次性脚本的家），**不放进 etf/v1/src**，也不改任何主线文件。
它只做两件事：把 CWD 切到 etf/v1/src（入口脚本假定那里是工作目录）、把该目录挂上
sys.path（入口脚本第一句 `import _bootstrap` 需要），然后 runpy 执行入口。

用法：/usr/bin/python3.10 -u shell/run_etf_three_rings_0924.py factor|portfolio|redundancy
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "etf", "v1", "src")
NAMES = {"factor": "run_etf_factor_eval.py",
         "portfolio": "run_etf_portfolio_eval.py",
         "redundancy": "run_etf_redundancy_check.py"}

which = (sys.argv[1] if len(sys.argv) > 1 else "factor").lower()
target = NAMES.get(which) or (which if which.endswith(".py") else None)
if not target:
    raise SystemExit(f"未知目标 {which}，可选 {sorted(NAMES)}")

sys.path.insert(0, SRC)
os.chdir(SRC)
print(f"[drive] CWD={SRC}\n[drive] {target}")
runpy.run_path(os.path.join(SRC, target), run_name="__main__")
