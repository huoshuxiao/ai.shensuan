# -*- coding: utf-8 -*-
"""ETF 线 · 只跑 RD-Agent(Q) + 本地 LLM 两支的实跑驱动（09-25）

分支裁剪全部落在 etf/v1/src/config/config.py 末尾的「分支裁剪」段，本脚本不再
在进程内改任何开关——只负责：①把生效值原样打进日志头（判据改动必须能被事后
从产物反推），②起 main.main()。

与上一轮 shell/i22_trigger_e2e_0924.py 的区别：那轮的裁剪是进程内 patch（只为
关掉 5h 的归因），配置文件的真值没动；本轮裁剪进了 config.py，所以这里改成
**断言校验**而不是设置。断言失败就直接退出，不允许"配置没生效也照跑"。

用法（必须在仓库根，日志路径按 etf/v1/temp/ 往回三级算根）
    cd <repo root> && /usr/bin/python3.10 -u etf/v1/temp/i23_official_llm_0925.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "etf", "v1", "src")
os.chdir(SRC)                      # 生产入口约定：CWD = etf/v1/src
sys.path.insert(0, SRC)
import _bootstrap  # noqa: F401
import config

EXPECT_OFF = ["GENETIC", "GENETIC_MULTI_OBJECTIVE", "LLM_GENETIC_HYBRID",
              "ORTHO_LLM", "JOINT_LLM", "LLM_RESEARCH_PLANNER",
              "FACTOR_ATTRIBUTION"]
EXPECT_ON = ["MULTI_SOURCE", "FACTOR_LIBRARY", "FACTOR_CLUSTERING",
             "WALK_FORWARD", "AUTO_REMINING", "TRIGGER_LOGIC"]

print("=" * 60)
print("  ETF 线 | 只跑 RD-Agent + 本地 LLM 两支（09-25 定档）")
print(f"  MULTI_SOURCE.sources = {config.MULTI_SOURCE['sources']}")
print(f"  外层 timeout {config.MULTI_SOURCE['timeout_seconds']}s"
      f"（子进程 RDAGENT_TIMEOUT_SEC={config.RDAGENT_TIMEOUT_SEC}s）")
print(f"  WALK_FORWARD.fold_engines      = {config.WALK_FORWARD['fold_engines']}")
print(f"  AUTO_REMINING.remining_engines = {config.AUTO_REMINING['remining_engines']}")
for k in EXPECT_OFF:
    print(f"  {k:24s} enabled={config.__dict__[k]['enabled']}")
print("=" * 60)

bad = [k for k in EXPECT_OFF if config.__dict__[k]["enabled"]]
bad += [k for k in EXPECT_ON if not config.__dict__[k]["enabled"]]
# 引擎清单的键名两链不同（折内叫 fold_engines、重挖叫 remining_engines），
# 必须逐键各比一次。写成「一边的值 != 一个二维列表」会恒为 True（拿一维
# 清单去比二维清单），健康配置也会被断言拦死——判据本身先要成立。
for key, sub in (("WALK_FORWARD", "fold_engines"),
                 ("AUTO_REMINING", "remining_engines")):
    if config.__dict__[key].get(sub) != ["registry", "multi_source"]:
        bad.append(f"{key}[{sub}]={config.__dict__[key].get(sub)}")
assert not bad, f"配置未按预期生效，拒绝开跑: {bad}"

import main  # noqa: E402
# main 是 `from config import X`，拿到的是同一个 dict 对象；不是同一个对象就说明
# 有一方改了副本，上面所有断言对真正跑起来的进程都不成立。
for k in ("MULTI_SOURCE", "WALK_FORWARD", "AUTO_REMINING", "FACTOR_ATTRIBUTION"):
    assert getattr(main, k) is config.__dict__[k], f"main.{k} 与 config.{k} 不是同一对象"
print("  ✅ 开关生效值已校验（含 main 侧同一对象引用）\n")

sys.exit(main.main())
