# -*- coding: utf-8 -*-
"""多角色前置假设闸：一次真调 qwen3.5:9b 的冒烟账单（10-01）

两臂同一把尺子，同一批真实 ETF 日线（5 只），各跑 1 个循环：
    甲臂 = 单角色 LLM（闸关，`_generate` 原路）
    乙臂 = 四角色闸（假设生成 → 代码实现 → 批判者 →（有 revise 才）修正 → 定稿）

甲臂之所以能当对照：10-01 丙那一改把 `llm_factor_agent.py:99` 的
`SYSTEM_PROMPT.format` 换成了 `fill()` ⇒ 原路第一次真的能把请求发出去
（改前三条 LLM 支路 100% 静默回退模板，见 check_prompt_fill_sites_1001.py）。
再早一点的「模板臂」已经不再是闸关时的真实行为。

报的是这些数：每段秒数、LLM 调用次数、发出去/收回来的**字符数**（不是 token，
代码路径只回 message.content，usage 从没被接出来 ⇒ 这里不假装有 token 数）、
批判者的判定分布、定稿条数与三条被拒原因、定稿过 `_evaluate` 之后的实测 IC。

边界（刻意）：
    · 只读缓存 CSV，产物只落本脚本自己的 tmp 目录 ⇒ 不碰因子库、不触发 [factor-lib] 提交
    · 不改任何默认开关：闸靠进程内改 HR.HYPOTHESIS_ROLES 打开，跑完即散
    · 每张表**边算边打**（崩在显示行之前，前面那臂的账还在）

`smoke_bill_1001.csv` 的列口径（10-01 定）：
    · to_ic_gate = 走到 `_evaluate` 之前的条数（两臂都有这一层）
    · finalized  = **只有闸开那一臂**才有的数：四角色定稿条数（gate.stats["final"]），
                   闸关那臂留空——单角色路径没有"定稿"这一层，填 0 会读成"被拒 0 条"
    · passed_ic  = 定稿之后 |IC| ≥ IC_THRESHOLD(0.02) 的条数
    · best_ic    = 这一臂最大的 |mean_ic|；in_library = 返回的 knowledge_base 长度
    ⚠️ **10-01 14:34 那一场落盘的 CSV 是这一列拆分之前写的**（进程 14:2x 起跑，源码 14:28:51
    才被改，Python 已经把旧字节码加载进内存）：那两行里 `finalized` 存的是「到 IC 闸的条数」、
    和 `n_evaluated` 同值。本场读数本身不受影响（乙臂定稿 0 ⇒ 到闸 0，两列都是 0；甲臂的 3 是
    到闸条数），但**别把那一列当"定稿数"引用**——定稿数在 `gate_bill` 那段文字里（"定稿 0"）。
    重跑才会得到拆分后的列。
"""
import json
import os
import subprocess
import sys
import time

import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

import hypothesis_roles as HR          # noqa: E402
import llm_factor_agent as LA          # noqa: E402
from config import (LLM_MODEL, HYPOTHESIS_PER_LOOP, IC_THRESHOLD)  # noqa: E402

CACHE = os.path.join(ROOT, "common/data/etf/cache")
CODES = ["510300", "510500", "159516", "512480", "518880"]
OUT_DIR = os.path.join(ROOT, "etf/v1/temp/tmp_roles_smoke_1001")

POOL = {}
for c in CODES:
    p = os.path.join(CACHE, f"{c}_daily.csv")
    df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
    if "returns" not in df:
        df["returns"] = df["close"].pct_change()
    POOL[c] = df


def preflight():
    """起之前把机器现状打出来：本机 16G、禁止并发第二个本地推理实例"""
    mem = subprocess.run(["free", "-c", "1"], capture_output=True, text=True).stdout
    avail = [l for l in mem.splitlines() if l.startswith("Mem:")][0].split()
    try:
        ps = json.loads(subprocess.run(
            ["curl", "-s", "-m", "5", "http://localhost:11434/api/ps"],
            capture_output=True, text=True).stdout).get("models", [])
    except Exception as e:
        ps = [f"读取失败 {type(e).__name__}"]
    print(f"  可用内存={avail[6]}  模型驻留={ps or '空（首调要付冷加载）'}")


def arm(label, gate_on):
    """跑一臂，返回账单 dict。全程只在这一臂内部改模块常量，跑完立刻还原"""
    HR.HYPOTHESIS_ROLES = gate_on
    agent = LA.LLMFactorAgent(POOL)
    agent.llm.enabled = True          # 冒烟的前提：端点必须真的开
    assert agent.gate.enabled == gate_on, "闸开关没按预期翻转，账单作废"

    probe = {"calls": 0, "prompt_chars": 0, "reply_chars": 0, "sec": 0.0,
             "evaluated": []}

    real_eval = agent._evaluate

    def spy_eval(fd):
        r = real_eval(fd)
        probe["evaluated"].append({"name": r["name"], "expr": fd.get("expr", ""),
                                   "mean_ic": r["mean_ic"], "pass": r["pass"]})
        return r
    agent._evaluate = spy_eval

    def wrap_chat():
        real = agent.llm.chat

        def shim(messages):
            probe["calls"] += 1
            probe["prompt_chars"] += sum(len(m["content"]) for m in messages)
            t0 = time.time()
            try:
                out = real(messages)
            finally:
                probe["sec"] += time.time() - t0
            probe["reply_chars"] += len(out or "")
            return out
        agent.llm.chat = shim

    def wrap_gate_chat():
        real = agent.gate._chat

        def shim(system, user):
            probe["calls"] += 1
            probe["prompt_chars"] += len(system) + len(user)
            t0 = time.time()
            try:
                out = real(system, user)
            finally:
                probe["sec"] += time.time() - t0
            probe["reply_chars"] += len(out or "")
            return out
        agent.gate._chat = shim

    (wrap_gate_chat if gate_on else wrap_chat)()

    old_loops = LA.MAX_LOOPS
    LA.MAX_LOOPS = 1
    t0 = time.time()
    print(f"\n{'=' * 62}\n【{label}】闸={'开' if gate_on else '关'}"
          f"｜模型={LLM_MODEL}｜每轮条数={HYPOTHESIS_PER_LOOP}｜1 个循环\n{'=' * 62}")
    kb = []
    try:
        kb = agent.run()
    except Exception as e:
        print(f"  ❌ 这一臂中途炸了: {type(e).__name__}: {e}")
    finally:
        LA.MAX_LOOPS = old_loops
        HR.HYPOTHESIS_ROLES = False
    wall = time.time() - t0

    ev = probe["evaluated"]
    got = (agent.gate.stats["calls"] if gate_on else probe["calls"])
    row = {
        "arm": label, "gate": "on" if gate_on else "off",
        "llm_calls": got, "wall_sec": round(wall, 1),
        "llm_sec": round(probe["sec"], 1),
        "prompt_chars": probe["prompt_chars"],
        "reply_chars": probe["reply_chars"],
        "to_ic_gate": len(ev),
        "finalized": agent.gate.stats["final"] if gate_on else "",
        "passed_ic": int(sum(1 for r in ev if r["pass"])),
        "best_ic": round(max((abs(r["mean_ic"]) for r in ev),
                             default=0.0), 4),
        "in_library": len(kb),
        "exprs": [f"{r['name']} IC={r['mean_ic']:+.4f}"
                  f"{'✅' if r['pass'] else '❌'} = {r['expr']}" for r in ev],
        "gate_bill": agent.gate.bill() if gate_on else "",
    }
    print(f"  ▎小结：{row['llm_calls']} 次调用、LLM 计时 {row['llm_sec']}s / "
          f"墙钟 {row['wall_sec']}s｜发出 {row['prompt_chars']} 字符 → 收回 "
          f"{row['reply_chars']} 字符｜到 IC 闸前 {row['to_ic_gate']} 条、"
          f"|IC|≥{IC_THRESHOLD} 有 {row['passed_ic']} 条、最大 |IC|={row['best_ic']}"
          + (f"｜四角色定稿 {row['finalized']} 条" if gate_on else ""))
    for e in row["exprs"]:
        print(f"     · {e}")
    return row


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"冒烟产物目录（只写这里）: {OUT_DIR}")
    print("[体检]")
    preflight()
    rows = []
    for lbl, on in (("甲臂 单角色 LLM（闸关）", False),
                    ("乙臂 四角色假设闸（闸开）", True)):
        try:
            rows.append(arm(lbl, on))
        except Exception as ex:
            print(f"❌ {lbl} 整体失败: {type(ex).__name__}: {ex}")
    flat = []
    for r in rows:
        rr = dict(r)
        rr["exprs"] = " ; ".join(rr["exprs"])
        flat.append(rr)
    if flat:
        p = os.path.join(OUT_DIR, "smoke_bill_1001.csv")
        pd.DataFrame(flat).to_csv(p, index=False)
        print(f"\n两臂账单已落盘: {p}")
    HR.HYPOTHESIS_ROLES = False
    print("\n开关已还原为关（默认档没动过）。")
