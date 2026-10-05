# -*- coding: utf-8 -*-
"""#172 真跑的内层：只跑官方支那一腿，kwargs 由本线 .env 默认派生（10-04 起的形态）。

大目标：个人量化辅助系统要每天从 RD-Agent 官方支挖出新因子；10-03 已量到那条真提示词
有 5502 token，而 `/v1` 通路只让它进 2050（服务端悄悄截断）。10-04 用户裁「乙」把主线
那支空转的注入拔掉了，`LLM_NUM_CTX=16384` 现在**只剩官方支一个真消费者**
（`official_rdagent._driver_env` 派生成 litellm 认的 `{"think": false, "num_ctx": 16384}`）。
配置层由 `check_kwargs_source_1004.py`（22 格）证过；**这一支只回答剩下的那一格**：
kwargs 开着**真跑一场**，官方支到底出不出因子表达式。

为什么不走 `run_daily_backtest.py`（10-03 那场那个入口）：那个入口会把五张归档表、
optimized_params、png、因子库三文件（触发 [factor-lib] 自动 commit）和 trial_counter
（只增不减、不可回滚）一起覆写。本场要的那格只握在 `try_official_rdagent()` 这一支函数里
（它就是链路里 `multi_source_mining` 调的那一个，前置体检/超时收尸/回收全同一套），
所以直接调它：**覆写面只有 rdagent_output/ 一个目录**。

两道硬闸（缺一道就不起，免得白烧两小时）：
  G1 `RDAGENT_LLM_KWARGS` 必须真在发给驱动的环境里，且含 think/num_ctx 两键
     ⇒ 不在就说明这一场测的不是「kwargs 开着」那件事，直接 exit 2。
  G2 起场前后各取一次 `factors.json` 的 sha256 ⇒ 回收有没有写盘，字节层见分晓。
     ⚠️ 光看驱动自报的「回收 N 个」不够：10-03 那两个数（+2/+4）后来被 `cmp` 证成假绿
     ——`_recover_factors` 只查文件在不在，会把上一场遗留的旧档当本场的产出念。

退出码：0＝跑完且 factors.json 真被本场改写过；3＝跑完但 factors.json 逐字节没变
（＝本场 0 产出，这就是要拍下来的那种情况）；2＝G1 拒了；其它＝异常传播。
"""
import hashlib
import json
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, _SRC)
import _bootstrap  # noqa: E402,F401  扁平导入的 sys.path 引导，必须先于任何项目模块

import official_rdagent as o            # noqa: E402
from llm_client import describe_endpoint  # noqa: E402

OUT = o.RDAGENT_OUTPUT_DIR
FJ = os.path.join(OUT, "factors.json")


def sha(p):
    if not os.path.exists(p):
        return "<不存在>"
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main():
    print(f"[1004] 时刻 {__import__('time').strftime('%m-%d %H:%M:%S')}")
    print(f"[1004] 主线端点自述: {describe_endpoint()}")
    kv = o._ENV_DRIVER.get("RDAGENT_LLM_KWARGS")
    print(f"[1004] 发给驱动的 RDAGENT_LLM_KWARGS = {kv!r}")
    print(f"[1004] 官方支模型 = {o.official_chat_model(os.path.join(OUT, '.env'))}")
    print(f"[1004] 超时上限 = {o.RDAGENT_TIMEOUT_SEC}s｜conda 环境名 = {o.RDAGENT_CONDA_ENV}")
    parsed = json.loads(kv) if kv else {}
    if "think" not in parsed or "num_ctx" not in parsed:
        print("[1004] 🛑 G1：kwargs 缺 think/num_ctx ⇒ 这一场测不到「kwargs 开着出不出因子」，不起。")
        return 2
    before = sha(FJ)
    mtime_before = (os.path.getmtime(FJ) if os.path.exists(FJ) else 0)
    print(f"[1004] 起场前 factors.json sha256={before} mtime={mtime_before}")
    got = o.try_official_rdagent()
    after, mtime_after = sha(FJ), (os.path.getmtime(FJ) if os.path.exists(FJ) else 0)
    n = len(got or ())
    print(f"[1004] 回收返回 {n} 条")
    for f in (got or ())[:12]:
        print(f"        - {f}")
    print(f"[1004] 收场后 factors.json sha256={after} mtime={mtime_after}")
    print(f"[1004] 落盘净增：{(after != before) and '有（本场改写过）' or '无（逐字节相同 ⇒ 本场写入 0 条）'}")
    print(f"[1004] 对账：回收 {n} 条 vs 落盘 {'变' if after != before else '没变'}")
    if n and after == before:
        print("[1004] ⚠️ 自报有产出但文件没动 ⇒ 捞的是旧档，这个 n 不能算本场产出")
        return 3
    return 0 if n else 3


if __name__ == "__main__":
    sys.exit(main())
