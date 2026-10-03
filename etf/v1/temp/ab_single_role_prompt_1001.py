# -*- coding: utf-8 -*-
"""丁：单角色提示词打磨一轮的 A/B（10-01）——闸不动、判据不动，只换 prompt

四张探针（`probe_single_role_prompt_1001.py`）量到旧 prompt 的四个具体缺陷：
    F1 频率写错：正文第一行写「分钟线 ETF」，真实数据是**日线**（bar 间隔众数 1 天）
    F2 算子表落后：执行环境 22 个算子，提示词只点名 13 个 ⇒ `corr/slope/rsquare/resi/
       idx_max/idx_min/quantile/ts_max/ts_min` 这 9 个**模型压根不知道能用**
    F3 example 即最弱模板：示例逐字等于内置模板 `mom_5`（本池实测 |IC|=0.0004）
    F4 没有方向口径：只说"避免重复"，没要求标 channel/expect ⇒ 三条全落在短动量上

两臂都跑**真** LLM（qwen3.5:9b），各 1 个循环 × 3 条，同一批 5 只真实 ETF 日线，
同一把尺子（`_evaluate` 的逐标的时序 IC）。对照基线用探针那批现量数：
    内置模板 8 条中位 |IC|=0.0177｜在库 35 条中位 0.0165｜旧 prompt 3 条中位 0.0031

边界：不写因子库、不改生产 `SYSTEM_PROMPT`（本脚本只在**进程内**换字符串）、不动开关。
"""
import os
import sys
import time

import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

import llm_factor_agent as LA              # noqa: F402,E402
import hypothesis_roles as HR              # noqa: F402,E402
from config import LLM_MODEL, HYPOTHESIS_PER_LOOP, IC_THRESHOLD  # noqa: E402

CACHE = os.path.join(ROOT, "common/data/etf/cache")
CODES = ["510300", "510500", "159516", "512480", "518880"]
OUT = os.path.join(ROOT, "etf/v1/temp/tmp_roles_smoke_1001")

# 打磨稿：算子表直接取四角色里那份「与执行环境逐字一致」的清单（同源，不再各说各话）
NEW_PROMPT = """你是量化因子研究员。在**日线** ETF 上生成因子表达式（每根 bar = 1 个交易日）。

可用算子（与执行环境逐字一致，除此之外一律非法）：
- 数据列: close, open, high, low, volume, returns
- 滚动: ma(df, n), std(df, n), max(df, n), min(df, n), rank(s, n),
  ts_max(s, n), ts_min(s, n), quantile(s, n, q)
- 序列: delay(s, n), delta(s, n), ts_sum(s, n), ts_mean(s, n), ts_std(s, n),
  abs(s), log(s), sign(s)
- 关系: corr(a, b, n), slope(s, n), rsquare(s, n), resi(s, n), idx_max(s, n), idx_min(s, n)
- 窗口 n 是正整数，日线口径：5=一周、20=一月、60=一季、120=半年、240≈一年

输出 JSON:
{"factors":[{"name":"英文小写下划线短名","expr":"单行表达式","channel":"趋势|反转|波动|量能|期限结构","expect":"正|负|非对称","reason":"一句话经济逻辑"}]}

要求：
1. 生成 <<N>> 个因子，彼此的 channel 不许重复
2. 系统里已有一批模板覆盖了这些写法：delta(close,5)/ma、delta(close,20)/ma、
   -ts_std(returns,n)、volume/ts_mean(volume,n)、(close-min)/(max-min)、rsi 比值
   ——**照抄或只换窗口数字的，一律算重复**
3. 只用 t 日及更早的数据；不许 shift 负数、不许出现 df. 或 df[ 下标
4. 分母一律加 1e-9 防除零
"""


def check_teeth():
    """花 LLM 之前先确认两臂真的不一样（否则 A/B 是恒真判据）。"""
    old = LA.SYSTEM_PROMPT
    print("[牙] A/B 两臂的差异是否真实存在：")
    ok1 = "分钟线" in old and "日线" not in old
    ok2 = "日线" in NEW_PROMPT and "分钟线" not in NEW_PROMPT
    print(f"  {'✅' if ok1 else '❌'} F1 旧稿含「分钟线」且不含「日线」；"
          f"新稿含「日线」且不含「分钟线」")
    import re
    import factor_dsl as FD
    cols = {"close", "open", "high", "low", "volume", "returns"}
    real = set(FD.FACTOR_DSL) - cols
    miss_old = sorted(real - set(re.findall(r"\b([a-z_]+)\(", old)))
    miss_new = sorted(real - set(re.findall(r"\b([a-z_]+)\(", NEW_PROMPT)))
    print(f"  {'✅' if (len(miss_new) < len(miss_old) and len(miss_new) == 0) else '❌'} "
          f"F2 漏报算子：旧 {len(miss_old)} 个 {miss_old} → 新 {len(miss_new)} 个")
    tpl_exprs = {t["expr"] for t in LA.LLMFactorAgent._fallback(
        object.__new__(LA.LLMFactorAgent), 99)}
    ex_old = re.search(r'例如: "([^"]+)"', old).group(1)
    print(f"  {'✅' if ex_old in tpl_exprs and '例如' not in NEW_PROMPT else '❌'} "
          f"F3 旧稿 example 逐字等于模板、新稿不再给逐字 example")
    print(f"  {'✅' if 'channel' in NEW_PROMPT and 'channel' not in old else '❌'} "
          f"F4 新稿要求标 channel/expect 并显式列了模板禁区")
    return all([ok1, ok2, len(miss_new) == 0, ex_old in tpl_exprs])


def load_pool():
    pool = {}
    for c in CODES:
        df = pd.read_csv(os.path.join(CACHE, f"{c}_daily.csv"),
                         parse_dates=["date"]).set_index("date").sort_index()
        df.columns = [str(x).lower() for x in df.columns]
        pool[c] = df
    return pool


def arm(label, prompt):
    pool = load_pool()
    agent = LA.LLMFactorAgent(pool)
    old_attr, old_loops = LA.SYSTEM_PROMPT, LA.MAX_LOOPS
    LA.SYSTEM_PROMPT = prompt
    LA.MAX_LOOPS = 1
    HR.HYPOTHESIS_ROLES = False
    probe = {"calls": 0, "sec": 0.0, "p": 0, "r": 0, "sys": ""}
    real_chat = agent.llm.chat

    def spy(messages):
        probe["calls"] += 1
        probe["p"] += sum(len(m["content"]) for m in messages)
        probe["sys"] = messages[0]["content"]
        t = time.time()
        out = real_chat(messages)
        probe["sec"] += time.time() - t
        probe["r"] += len(out or "")
        return out
    agent.llm.chat = spy
    ev = []
    real_eval = agent._evaluate

    def spy_eval(fd):
        r = real_eval(fd)
        ev.append(r)
        return r
    agent._evaluate = spy_eval
    t0 = time.time()
    try:
        kb = agent.run()
    finally:
        LA.SYSTEM_PROMPT = old_attr
        LA.MAX_LOOPS = old_loops
        HR.HYPOTHESIS_ROLES = False
    row = {"arm": label, "calls": probe["calls"],
           "wall_sec": round(time.time() - t0, 1),
           "llm_sec": round(probe["sec"], 1),
           "prompt_chars": probe["p"], "reply_chars": probe["r"],
           "to_ic_gate": len(ev),
           "passed_ic": int(sum(1 for r in ev if r["pass"])),
           "median_abs_ic": (round(float(pd.Series([abs(r["mean_ic"])
                                                   for r in ev]).median()), 4)
                             if ev else None),
           "best_abs_ic": (round(max(abs(r["mean_ic"]) for r in ev), 4)
                           if ev else None),
           "in_library": len(kb),
           "exprs": [f"{r['name']} IC={r['mean_ic']:+.4f}"
                     f"{'✅' if r['pass'] else '❌'} = {r.get('expr','')}"
                     for r in ev]}
    print(f"\n  ▎{label}：{row['calls']} 次调用、LLM {row['llm_sec']}s / "
          f"墙钟 {row['wall_sec']}s｜发 {row['prompt_chars']} 字 → 收 "
          f"{row['reply_chars']} 字｜到 IC 闸 {row['to_ic_gate']} 条、"
          f"过 0.02 有 {row['passed_ic']} 条、中位 |IC|={row['median_abs_ic']}、"
          f"最大 {row['best_abs_ic']}")
    for e in row["exprs"]:
        print(f"     · {e}")
    return row, probe["sys"]


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    if not check_teeth():
        print("❌ 两臂差异没有被确认 ⇒ 拒绝花 LLM 时间（避免恒真 A/B）")
        sys.exit(1)
    if not LA.LLMClient().enabled:
        print("❌ LLM 端点不可达 ⇒ 两臂都会退成模板，A/B 无意义")
        sys.exit(1)
    print(f"模型={LLM_MODEL}｜每臂 1 循环 × {HYPOTHESIS_PER_LOOP} 条｜"
          f"IC 门槛 {IC_THRESHOLD}｜池 {len(CODES)} 只真实日线")
    rows, syss = [], {}
    for lbl, p in (("A 旧 prompt", LA.SYSTEM_PROMPT), ("B 打磨后", NEW_PROMPT)):
        r, used = arm(lbl, p)
        rows.append(r)
        syss[lbl] = used
    assert syss["A 旧 prompt"] != syss["B 打磨后"], "两臂发出的 system 一样 ⇒ A/B 无效"
    print(f"\n✅ 两臂实际发出的 system prompt 不同（{len(syss['A 旧 prompt'])} 字 vs "
          f"{len(syss['B 打磨后'])} 字）⇒ 差异确实到了模型那一侧")
    flat = [dict(r, exprs=" ; ".join(r["exprs"])) for r in rows]
    p = os.path.join(OUT, "ab_prompt_1001.csv")
    pd.DataFrame(flat).to_csv(p, index=False)
    print(f"A/B 账单已落盘: {p}")
    print("生产 SYSTEM_PROMPT 未改动（脚本只在进程内换字符串）。")
