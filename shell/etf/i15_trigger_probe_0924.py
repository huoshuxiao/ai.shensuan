# -*- coding: utf-8 -*-
"""#15 收尾探针（只读）：修好的 DSR/PBO 喂进重挖触发器会判成什么。

触发器本体 `common/src/optimizer/trigger_logic.py` 在共享层（股票线同用），
本探针**一行不改**它，只把两类数字送进 `check()` 看输出：
  1. 今天真实族跑出的 DSR/PBO（读 data/results 落盘文件，不重跑流水线）
  2. shell/i15_degrade_check_0924.py 逼出的伪变体族 PBO

状态文件走临时目录：`ReminingState` 默认写 data/cache/remining_state.json，
那正是生产下一轮读的账本，探针不能污染。
"""
import os
import sys
import json
import tempfile

_TMP = tempfile.mkdtemp(prefix="i15-trigger-")
os.environ["ETF_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["ETF_FREQ"] = "daily"
os.environ["ETF_RDAGENT_OFFICIAL_FALLBACK"] = "false"
os.environ["ETF_MIN_SCALE"] = "0"

V1 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(V1, "etf", "v1", "src"))
import _bootstrap  # noqa: E402,F401

import config  # noqa: E402
from remining_state import ReminingState  # noqa: E402
from trigger_logic import DualIndicatorTrigger  # noqa: E402

print(f"[probe] TRIGGER_LOGIC={json.dumps(config.TRIGGER_LOGIC, ensure_ascii=False)}")
print(f"[probe] AUTO_REMINING.enabled={config.AUTO_REMINING['enabled']} "
      f"remining_engines={config.AUTO_REMINING.get('remining_engines')}")
print(f"[probe] 状态文件（临时）: {config.REMINING_STATE_FILE}")

results = os.path.join(V1, "etf", "v1", "data", "results")
with open(os.path.join(results, "pbo_result.json"), encoding="utf-8") as f:
    pbo_today = json.load(f)
print(f"\n[落盘] 策略级 PBO: pbo={pbo_today.get('pbo')} "
      f"配置族={pbo_today.get('config_family')}×{pbo_today.get('n_configs')} "
      f"通过={pbo_today.get('passed')}")


def run(label, dsr, pbo, rounds=2):
    """同一份隔离状态连跑 N 轮，看连续计数怎么走。"""
    state = ReminingState(path=os.path.join(
        _TMP, f"state-{label.replace('/', '-')}.json"))
    trig = DualIndicatorTrigger(state=state)
    print(f"\n===== {label}: DSR={dsr} PBO={pbo} =====")
    for i in range(rounds):
        r = trig.check(dsr_now=dsr, pbo_now=pbo, current_bar=4062 + i)
        print(f"  第 {i + 1} 轮: 恶化={r.get('bad_this_round')} "
              f"连续 {r.get('consecutive')}/{config.TRIGGER_LOGIC['consecutive_rounds']} "
              f"触发重挖={r.get('triggered')}（{r.get('reason')}）")


# 1) 今天真实族：DSR 0.0214 / PBO 0.2286
dsr_today = 0.0214
run("真实族 real×11", dsr_today, float(pbo_today.get("pbo", 1)))
# 2) 伪变体族（退化路径 A/B 实测值）
run("伪变体族 pseudo×6", dsr_today, 0.6143)
# 3) 修复前的假数（DSR 恒 0 / 折级 PBO 恒 0.9444 那批口径下策略级仍是 0.2286）
run("修复前 DSR=0", 0.0, float(pbo_today.get("pbo", 1)))
