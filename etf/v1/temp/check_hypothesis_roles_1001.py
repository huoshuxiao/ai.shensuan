#!/usr/bin/python3.10
# -*- coding: utf-8 -*-
"""多角色前置假设闸的牙：10 组 23 格夹具（放行 13 · 判红 10），全程零网络

闸门是新加的一层筛选，所以这里的「放行 / 判红」= 该不该让这条因子进 IC 闸。
每格都钉一条**能被真实 CSV 推翻**的行为，全部用脚本化的假 LLM 应答
（真调 9b 的账单在 smoke_hypothesis_roles_1001.py，不在这里）。

    甲 默认关（未设 ETF_HYPOTHESIS_ROLES）⇒ 原路一字未动：一次 LLM 调用都不发、
       产出=内置模板。甲-3/甲-4 再钉住 prompt 占位符必须走 <<N>> 而不是 {n}：
       本仓有 4 处调用点（llm_factor_agent.py:99 + llm_genetic_hybrid.py 的
       SEED/FEEDBACK + llm_research_planner.py 的 RESEARCH）就是被 str.format
       撞上 JSON 字面大括号、每次抛 KeyError 再被宽 except 静默吞成「回退模板」；
       4 处 10-01 已全部改走 fill()，逐处的账在 check_prompt_fill_sites_1001.py。
       **这一组是负对照的根**，去掉它「默认关」就只是句空话。
    乙 LLM 关掉之后才翻转（simple 源那种改法）⇒ enabled 必须是 False。
       这格是修好的那颗牙：早期把 enabled 快照成布尔，simple 源会偷偷多烧一次 LLM。
    丙 pass+revise 混合的一趟 ⇒ 定稿条数、四格计数逐项对上账单（放行）。
    丁 修正给出的写法用负窗口 delay(close,-5) ⇒ 静态闸必须硬拒（未来函数）；
       丁-3 是反手那格「不许误杀」：delay(close,5) 与 close.shift(1) 这类
       **正**滞后必须放行，否则闸门会把好因子一起挡掉。
    戊 修正给出的写法引用未知名字 foo(close,5) ⇒ 拒收并计入 dropped_static。
    己 批判者把 n 条全判 reject ⇒ 定稿为空，且「修正」那一趟**不发**（省一次调用）。
    庚 批判者返回的 JSON 不可解析 ⇒ 响亮作废（json_fail+1、有打印），不许静默放行。
    辛 批判者对某条不出具判定 ⇒ 按拒收计，闸门不能因为审稿人没签字就默认放行。
    壬 第 2 轮把第 1 轮的写法重写一遍 ⇒ dropped_dup=1（跨轮记忆真的生效）。
    癸 反思者那段话必须出现在下一轮的 user 消息里 ⇒ 否则第四角色只是摆设。

真数据：pool 取 common/data/etf/cache 的 5 只 ETF 日线 CSV，壬/癸 两格走完整的
LLMFactorAgent.run()（MAX_LOOPS 在本脚本里临时拨到 2，不动配置）。
"""
import json
import os
import sys

import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

import hypothesis_roles as HR  # noqa: E402
import llm_factor_agent as LA  # noqa: E402

CACHE = os.path.join(ROOT, "common/data/etf/cache")
CODES = ["510300", "510500", "159516", "512480", "518880"]


def load_pool():
    pool = {}
    for c in CODES:
        p = os.path.join(CACHE, f"{c}_daily.csv")
        df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
        pool[c] = df
    return pool


POOL = load_pool()

HYP = [{"id": 1, "idea": "近 20 日涨幅延续", "channel": "趋势", "expect": "正"},
       {"id": 2, "idea": "高波动下一段走弱", "channel": "波动", "expect": "负"}]
CODED = [{"name": "mom_20", "expr": "delta(close, 20) / (ma(df, 60) + 1e-9)",
          "idea": "近 20 日涨幅延续"},
         {"name": "wob_20", "expr": "-ts_std(returns, 20)",
          "idea": "高波动下一段走弱"}]


def make_gate(script, enabled=True):
    """假 LLM：按 system prompt 认角色，逐次从 script 里取应答"""
    g = HR.HypothesisGate(POOL, type("L", (), {"enabled": True})())
    g._llm.enabled = enabled
    calls = []

    def role_of(system, user):
        # 认角色的顺序就是这里的牙（10-01 第一遍实跑红 4 格全落在这一行）：
        # 「修正」那一趟**复用代码实现的 prompt**，只按 system prompt 匹配就会把
        # 返工应答喂成第一轮的老写法 ⇒ 丁/戊 两格看着"闸门没拦"，其实压根没送到闸前。
        # 返工只有 user 消息里的「返工」二字认得出。
        if "返工" in user:
            return "修正"
        if "翻译成因子表达式" in system:
            return "代码实现"
        if "只提**经济学假设**" in system:
            return "假设生成"
        if "苛刻的审稿人" in system:
            return "批判者"
        if "你是复盘者" in system:
            return "反思者"
        raise AssertionError(f"未注册的 system prompt: {system[:24]}")

    def fake_chat(system, user):
        role = role_of(system, user)
        # 真 _chat 里那句计数被整体替换掉了，账单要在这里自己补上，
        # 否则 calls 恒 0、丙/己/癸 三格就变成没有牙的摆设
        g.stats["calls"] += 1
        calls.append((role, system, user))
        if role not in script:
            raise AssertionError(f"夹具没准备 {role} 的应答")
        resp = script[role]
        return resp(calls, user) if callable(resp) else resp

    g._chat = fake_chat
    g._calls_log = calls
    return g


def j(obj):
    return json.dumps(obj, ensure_ascii=False)


def verdicts(*items):
    return j({"verdicts": [{"name": n, "verdict": v, "problem": p,
                             "fix_expr": f} for n, v, p, f in items]})


FAILS = []
CELLS = []


def check(tag, kind, cond, detail):
    """边算边打：判据一执行就把读数打在行里，崩了也不丢表"""
    ok = bool(cond)
    if not ok:
        FAILS.append(f"{tag} {detail}")
    CELLS.append((tag, kind, ok, detail))
    print(f"  {'✅' if ok else '❌'} {tag}（{kind}）{detail}")


# ========== 甲 默认关 ⇒ 一字未动 ==========
print("\n[甲] 默认关：一次 LLM 都不发")
os.environ.pop("ETF_HYPOTHESIS_ROLES", None)
import config as CFG  # noqa: E402
HR.HYPOTHESIS_ROLES = CFG.HYPOTHESIS_ROLES          # 底座实读到的默认值
g = make_gate({"假设生成": j({"hypotheses": HYP})})
check("甲-1", "放行", HR.HYPOTHESIS_ROLES is False and g.enabled is False,
      f"底座默认 HYPOTHESIS_ROLES={HR.HYPOTHESIS_ROLES!r}、gate.enabled={g.enabled}")
agent = LA.LLMFactorAgent(POOL)
agent.llm.enabled = False                            # 无 LLM 端点
agent.history = [{"name": "x", "ic": 0.01, "expr": "close"}]
out = agent._generate("上轮平均 IC=+0.0100")
check("甲-2", "放行", all(o["name"] == t["name"] for o, t in
                          zip(out, agent._fallback(LA.HYPOTHESIS_PER_LOOP)))
      and len(out) == LA.HYPOTHESIS_PER_LOOP,
      f"默认关时 _generate 产出=内置模板 {len(out)} 条"
      f"（{[o['name'] for o in out]}）")
# 甲-3：prompt 里带 JSON 字面大括号，占位符必须是 <<N>> 而不是 {n}。
# 本仓已有 4 处（llm_factor_agent.py:99 等）因此每次调用都抛 KeyError，
# 再被宽 except 静默吞成「回退模板」——这格就是不让闸重犯同一个错。
_filled = HR.fill(HR.PROMPT_HYPOTHESIS, 7)
_has_count = "恰好 7 条" in _filled
_has_json = '"hypotheses"' in _filled
check("甲-3", "放行", "<<N>>" not in _filled and _has_count and _has_json,
      f"fill() 换掉占位符且不动 JSON 大括号：含「恰好 7 条」={_has_count}、"
      f"JSON 段完整={_has_json}")
try:
    HR.PROMPT_HYPOTHESIS.format(n=3)
    _fmt_raised = False
except KeyError:
    _fmt_raised = True
check("甲-4", "判红", _fmt_raised,
      f"同一套 prompt 走 str.format 必然当场 KeyError（实得 raised={_fmt_raised}）"
      f"——本仓 4 处旧调用点就是这个形状，异常被宽 except 吞成「回退模板」")

# ========== 乙 enabled 必须是活的，不是快照 ==========
print("\n[乙] LLM 事后关掉 ⇒ enabled 跟着关（simple 源那颗牙）")
HR.HYPOTHESIS_ROLES = True
g = make_gate({})
g._llm.enabled = False
check("乙-1", "判红", g.enabled is False,
      "构造后把 llm.enabled 改 False，gate.enabled 必须同步变 False（否则 simple 源偷烧 LLM）")
g._llm.enabled = True
check("乙-2", "放行", g.enabled is True, "恢复 llm.enabled=True 后 gate.enabled 回到 True")

# ========== 丙 pass + revise 混合 ==========
print("\n[丙] pass/revise 混合一趟")
g = make_gate({
    "假设生成": j({"hypotheses": HYP}),
    "代码实现": j({"factors": CODED}),
    "批判者": verdicts(("mom_20", "pass", "", ""),
                       ("wob_20", "revise", "符号与 expect 反了", "")),
    "修正": j({"factors": [{"name": "wob_20", "expr": "ts_std(returns, 20)",
                            "idea": "高波动下一段走弱"}]}),
})
res = g.generate()
s = g.stats
check("丙-1", "放行", sorted(x["name"] for x in res) == ["mom_20", "wob_20"],
      f"定稿 {sorted(x['name'] for x in res)}（返工那条换了写法后仍在）")
check("丙-2", "放行", (s["hypotheses"], s["coded"], s["pass"], s["revise"],
                       s["reject"], s["final"]) == (2, 2, 1, 1, 0, 2),
      f"计数 假设{s['hypotheses']}/代码{s['coded']}/pass{s['pass']}/"
      f"revise{s['revise']}/reject{s['reject']}/定稿{s['final']}")
check("丙-3", "放行", s["calls"] == 4 and
      [c[0] for c in g._calls_log] == ["假设生成", "代码实现", "批判者", "修正"],
      f"发了 {s['calls']} 次调用：{[c[0] for c in g._calls_log]}")

# ========== 丁 未来函数必须硬拒 ==========
print("\n[丁] 修正给出负窗口 ⇒ 未来函数硬拒")
g = make_gate({
    "假设生成": j({"hypotheses": HYP}),
    "代码实现": j({"factors": CODED[:1]}),
    "批判者": verdicts(("mom_20", "revise", "该看更长窗口", "")),
    "修正": j({"factors": [{"name": "mom_20",
                            "expr": "delay(close, -5) / (ma(df, 60) + 1e-9)",
                            "idea": "偷看未来"}]}),
})
res = g.generate()
check("丁-1", "判红", res == [] and g.stats["dropped_static"] == 1,
      f"delay(close,-5) 被静态闸拒掉：定稿 {len(res)} 条、dropped_static="
      f"{g.stats['dropped_static']}（拒因 {HR.check_expr('delay(close, -5)')}）")
g2 = make_gate({})
check("丁-2", "判红", "shift" in HR.check_expr(
    "close.pct_change(fill_method=None).shift(-1)"),
    f"shift(-1) 写法同样被拒：{HR.check_expr('close.pct_change(fill_method=None).shift(-1)')}")
# 反手一格「不许误杀」：正的滞后就是 delay，闸门不能把好因子一起挡掉
check("丁-3", "放行", HR.check_expr("delay(close, 5) / (ma(df, 60) + 1e-9)") == ""
      and HR.check_expr("close.shift(1) / (delay(volume, 1) + 1e-9)") == "",
      f"正窗口/正滞后必须放行（实得 "
      f"{HR.check_expr('delay(close, 5) / (ma(df, 60) + 1e-9)')!r} 与 "
      f"{HR.check_expr('close.shift(1) / (delay(volume, 1) + 1e-9)')!r}）")

# ========== 戊 未知名字 ⇒ 拒 ==========
print("\n[戊] 未知名字 foo() ⇒ 拒")
g = make_gate({
    "假设生成": j({"hypotheses": HYP}),
    "代码实现": j({"factors": CODED[:1]}),
    "批判者": verdicts(("mom_20", "revise", "写法要改", "")),
    "修正": j({"factors": [{"name": "mom_20", "expr": "foo(close, 5)",
                            "idea": "编了个算子"}]}),
})
res = g.generate()
check("戊-1", "判红", res == [] and g.stats["dropped_static"] == 1 and "未知名字 foo" in
      HR.check_expr("foo(close, 5)"),
      f"foo 不在算子白名单：定稿 {len(res)} 条、"
      f"dropped_static={g.stats['dropped_static']}")

# ========== 己 全 reject ⇒ 不发修正那一趟 ==========
print("\n[己] 全拒收 ⇒ 修正那一趟不发")
g = make_gate({
    "假设生成": j({"hypotheses": HYP}),
    "代码实现": j({"factors": CODED}),
    "批判者": verdicts(("mom_20", "reject", "经济含义讲不通", ""),
                       ("wob_20", "reject", "恒为常数", "")),
})
res = g.generate()
check("己-1", "判红", res == [] and g.stats["reject"] == 2,
      f"全拒收后定稿 {len(res)} 条、reject={g.stats['reject']}")
check("己-2", "放行", g.stats["calls"] == 3 and
      "修正" not in [c[0] for c in g._calls_log],
      f"没有 revise 就不发修正那一趟：calls={g.stats['calls']}")

# ========== 庚 坏 JSON ⇒ 响亮作废，不静默放行 ==========
print("\n[庚] 坏 JSON ⇒ 本趟作废且有计数")
g = make_gate({"假设生成": "这不是 JSON", "代码实现": j({"factors": CODED}),
               "批判者": verdicts(("mom_20", "pass", "", ""),
                                  ("wob_20", "pass", "", ""))})
res = g.generate()
check("庚-1", "判红", res == [] and g.stats["json_fail"] == 1,
      f"假设生成返回坏 JSON：定稿 {len(res)} 条、json_fail={g.stats['json_fail']}"
      f"（上面应有一行 ⚠️ 打印，不是静默跳过）")
check("庚-2", "判红", g.stats["calls"] == 1,
      f"坏 JSON 后直接收尾，不再往下烧调用：calls={g.stats['calls']}")

# ========== 辛 批判者漏判 ⇒ 按拒收计 ==========
print("\n[辛] 批判者漏签 ⇒ 不许默认放行")
g = make_gate({
    "假设生成": j({"hypotheses": HYP}),
    "代码实现": j({"factors": CODED}),
    "批判者": verdicts(("mom_20", "pass", "", "")),   # wob_20 一个字没提
})
res = g.generate()
check("辛-1", "判红", sorted(x["name"] for x in res) == ["mom_20"]
      and g.stats["reject"] == 1,
      f"漏判的 wob_20 按拒收计：定稿 {[x['name'] for x in res]}、"
      f"reject={g.stats['reject']}")

# ========== 壬 跨轮去重 ==========
print("\n[壬] 第 2 轮重复第 1 轮的写法 ⇒ 判重")
g = make_gate({
    "假设生成": lambda calls, user: j({"hypotheses": HYP}),
    "代码实现": j({"factors": CODED}),
    "批判者": verdicts(("mom_20", "pass", "", ""), ("wob_20", "pass", "", "")),
})
r1 = g.generate()
r2 = g.generate("", tried=["delta(close, 20) / (ma(df, 60) + 1e-9)"])
check("壬-1", "放行", len(r1) == 2,
      f"第 1 轮定稿 {len(r1)} 条")
check("壬-2", "判红", r2 == [] and g.stats["dropped_dup"] == 2,
      f"第 2 轮同一批写法全部判重：定稿 {len(r2)} 条、dropped_dup="
      f"{g.stats['dropped_dup']}（历史里那条也拦住了）")

# ========== 癸 反思者的话真的进了下一轮 ==========
print("\n[癸] 反思者输出必须出现在下一轮 user 消息里")
MARK = "趋势类拿不到 IC，量能写法被反复拒收"
agent = LA.LLMFactorAgent(POOL)
agent.llm.enabled = True
HR.HYPOTHESIS_ROLES = True
agent.gate = make_gate({
    "假设生成": j({"hypotheses": HYP}),
    "代码实现": j({"factors": CODED}),
    "批判者": verdicts(("mom_20", "pass", "", ""), ("wob_20", "pass", "", "")),
    "反思者": j({"feedback": MARK}),
})
old_loops, LA.MAX_LOOPS = LA.MAX_LOOPS, 2
try:
    kb = agent.run()
finally:
    LA.MAX_LOOPS = old_loops
hyp_users = [c[2] for c in agent.gate._calls_log if c[0] == "假设生成"]
check("癸-1", "放行", len(hyp_users) == 2,
      f"两轮各发一次假设生成：{len(hyp_users)} 次")
check("癸-2", "放行", MARK in hyp_users[1] and MARK not in hyp_users[0],
      f"反思者的话进了第 2 轮的提示词（第 1 轮没有）："
      f"第2轮含标记={MARK in hyp_users[1]}")
check("癸-3", "放行", agent.gate.stats["calls"] == 7,
      f"两轮调用数={agent.gate.stats['calls']}"
      f"（每轮 假设+代码+批判=3，第 2 轮多一次反思=7，无 revise）")
print(f"  ℹ️ 完整 run() 实出 IC：{[(r['name'], round(r['mean_ic'], 4)) for r in kb]}"
      f"｜账单 {agent.gate.bill()}")

# ========== 汇总 ==========
n_pass = sum(1 for c in CELLS if c[1] == "放行")
n_red = sum(1 for c in CELLS if c[1] == "判红")
print(f"\n合计：{len(CELLS)} 格（放行 {n_pass} · 判红 {n_red}）｜"
      f"失败 {len(FAILS)} 条")
for f in FAILS:
    print(f"  ❌ {f}")
sys.exit(1 if FAILS else 0)
