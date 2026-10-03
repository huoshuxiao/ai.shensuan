# -*- coding: utf-8 -*-
"""prompt 占位符夹具：4 处 LLM 调用点必须走 fill()，不许回到 str.format

背景（10-01 实测）：这几套 prompt 正文里带 JSON 字面大括号，`str.format` 会把
`{"seeds": [...]}` 当成字段名解析并抛 KeyError；调用点外面又套着宽 `except
Exception` ⇒ 异常被吞成「静默回退模板」。改前三处 100% 复现：
  · llm_genetic_hybrid.generate_seeds      → 每次都是 seed_mom5/seed_rev3/seed_vol 模板
  · llm_genetic_hybrid.generate_complementary → 直接 []，连一行打印都没有
  · llm_research_planner.generate_plan     → 每次都是硬编码的回退计划
也就是这三条 LLM 支路**从来没有真的调过模型**。

格子（放行 = 修好之后的期望；判红 = 钉住旧写法必坏）：
    T1 四套 prompt 的占位符都是 <<N>> 哨兵，正文里不再残留 {n}/{front}/{days}
    T2 旧写法 .format 对同一套 prompt 必然抛 KeyError（这格就是负对照的根：
       去掉它，夹具可以靠「把哨兵改回 {n}」而永不发红）
    T3 fill() 换掉数字与 <<FRONT>> 后 JSON 段逐字完好（不多杀一个大括号）
    T4 三处调用点接上假 _chat 后**真的发出了请求**，且发出去的 system 里
       数字已展开、JSON 段完好 ⇒ 证「走 LLM 分支」不是靠读代码猜的
    T5 反手一格「不许误杀」：正数滞后之外，FEEDBACK 那套的帕累托前沿正文
       必须原样出现在 system 里（front 里带引号与大括号都不许被吃掉）
    N5 负对照：把 fill 那一处换回 .format ⇒ 请求发不出去、模板回来了。
       没有它，T4 可能只是「桩本来就返回一条」的恒真读数。

零联网：_chat 一律换成桩，绝不碰本机 9b（16G 单机不允许第二个本地推理实例）。
"""
import json
import os
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

import hypothesis_roles as HR          # noqa: E402
import llm_genetic_hybrid as GH        # noqa: E402
import llm_research_planner as RP      # noqa: E402
import llm_factor_agent as LA          # noqa: E402

FAILS = []
CELLS = 0
KINDS = {}


def check(tag, kind, ok, detail):
    global CELLS
    CELLS += 1
    KINDS[kind] = KINDS.get(kind, 0) + 1
    print(f"  {'✅' if ok else '❌'} {tag}（{kind}）{detail}")
    if not ok:
        FAILS.append(tag)


SITES = [("llm_factor_agent", lambda: __import__("llm_factor_agent").SYSTEM_PROMPT,
          {"n": 3}, "factors"),
         ("genetic.SEED", lambda: GH.SEED_SYSTEM_PROMPT, {"n": 3}, "seeds"),
         ("genetic.FEEDBACK", lambda: GH.FEEDBACK_SYSTEM_PROMPT,
          {"front": "x", "n": 3}, "seeds"),
         ("planner.RESEARCH", lambda: RP.RESEARCH_PROMPT, {"days": 3}, "plan")]

# ========== T1 占位符必须是哨兵 ==========
print("\n[T1] 四套 prompt 的占位符都是 <<N>>，旧的花括号写法已清干净")
for label, get, kw, key in SITES:
    tpl = get()
    ok = ("<<N>>" in tpl) and not any(("{%s}" % k) in tpl for k in kw)
    check("T1-" + label, "放行", ok,
          f"含 <<N>>={'<<N>>' in tpl}、残留旧占位="
          f"{[('{%s}' % k) for k in kw if ('{%s}' % k) in tpl]}")

# ========== T2 旧写法必抛（这格红了=哨兵被换回去了） ==========
print("\n[T2] 同一套 prompt 走 str.format 必然当场 KeyError")
for label, get, kw, key in SITES:
    tpl = get()
    try:
        tpl.format(**kw)
        raised, err = False, "没抛"
    except KeyError as e:
        raised, err = True, f"KeyError: {e}"
    check("T2-" + label, "判红", raised,
          f"raised={raised} {err}——这就是改前每次静默回退的那一下")

# ========== T3 fill 不多吃大括号 ==========
print("\n[T3] fill() 展开数字后，JSON 示例段逐字完好")
for label, get, kw, key in SITES:
    tpl = get()
    filled = HR.fill(tpl, list(kw.values())[-1])
    json_ok = ('"%s"' % key) in filled
    leftover = "<<N>>" in filled
    check("T3-" + label, "放行", json_ok and not leftover,
          f'含「"{key}"」={json_ok}、残留 <<N>>={leftover}')

# ========== T4 真发出请求（桩 _chat，零联网） ==========
print("\n[T4] 三处调用点接上假 _chat ⇒ 真的发出了请求，且 system 已展开")
FAKE = {"factors": '{"factors": [{"name":"m","expr":"delta(close, 5)",'
                   '"reason":"动量"}]}',
        "seeds": '{"seeds": [{"name":"s","expr":"delta(close, 5)",'
                 '"logic":"动量"}]}',
        "plan": '{"plan": [{"title":"t"}], "summary":"整体策略"}'}


def stub(obj, payload_key):
    got = []

    def fake_chat(messages, *a, **k):
        got.append(messages)
        return FAKE[payload_key]
    obj._chat = fake_chat
    return got


g = GH.LLMSeedGenerator()
g.enabled = True
got = stub(g, "seeds")
out = g.generate_seeds(3)
sys_txt = got[0][0]["content"] if got else ""
check("T4-generate_seeds", "放行",
      len(got) == 1 and "生成 3 个种子" in sys_txt and '"seeds"' in sys_txt
      and [s.get("name") for s in out] == ["s"],
      "发请求 {} 次｜含「生成 3 个种子」={}｜JSON 段完好={}｜返回={}（不是模板 seed_mom5 那批）".format(
          len(got), "生成 3 个种子" in sys_txt, '"seeds"' in sys_txt,
          [s.get("name") for s in out]))

got = stub(g, "seeds")
front = [{"name": "a", "expr": 'delta(close, 5)', "logic": '带"引号"和 {大括号}'}]
out = g.generate_complementary(front, 2)
sys_txt = got[0][0]["content"] if got else ""
check("T4-generate_complementary", "放行",
      len(got) == 1 and "生成 2 个**互补**" in sys_txt
      and [s.get("name") for s in out] == ["s"],
      f"发请求 {len(got)} 次｜返回 {len(out)} 条（改前恒为 []）")

p = RP.LLMResearchPlanner()
p.enabled = True
got = stub(p, "plan")
plan = p.generate_plan(days=4)
sys_txt = got[0][0]["content"] if got else ""
check("T4-generate_plan", "放行",
      len(got) == 1 and "制定未来 4 天研究计划" in sys_txt
      and plan.get("plan") == [{"title": "t"}],
      f"发请求 {len(got)} 次｜含「未来 4 天」={'制定未来 4 天研究计划' in sys_txt}"
      f"｜返回的是模型给的 plan 不是回退文案")

# ========== T5 帕累托前沿正文原样送达 ==========
print("\n[T5] FEEDBACK 那套的 front 正文必须原样出现在 system 里")
got = stub(g, "seeds")
g.generate_complementary(front, 2)
sys_txt = got[0][0]["content"] if got else ""
raw = json.dumps(front[:8], ensure_ascii=False, indent=2)
check("T5-front 原样", "放行",
      raw in sys_txt and "<<FRONT>>" not in sys_txt and "<<N>>" not in sys_txt,
      f"front 逐字在 system 里={raw in sys_txt}（含引号与大括号）、"
      f"残留哨兵={'<<FRONT>>' in sys_txt or '<<N>>' in sys_txt}")

# ========== T6 四处调用点的源码都不许再出现 .format( ==========
# T4 只跑到了三处（llm_factor_agent 那处的 _chat 要真发请求），第四处只能读源码。
print("\n[T6] 四套 prompt 名字后面都不许再挂 .format(")
_src = ""
for path in (GH.__file__, RP.__file__, LA.__file__):
    with open(path, encoding="utf-8") as f:
        _src += f.read()
_bad = [nm for nm in ("SEED_SYSTEM_PROMPT", "FEEDBACK_SYSTEM_PROMPT",
                      "RESEARCH_PROMPT", "SYSTEM_PROMPT")
        if nm + ".format(" in _src]
_filled = _src.count("fill(")
check("T6-源码", "判红", not _bad,
      f"仍挂 .format( 的 prompt={_bad}｜三文件里 fill( 共 {_filled} 处")

# ========== N5 负对照：把牙拔回 .format，这格必须看见 bug 回来 ==========
# 没有这一格，T4 可能只是「桩本来就返回 1 条」的恒真读数。
print("\n[N5] 反事实：把 fill 换回 .format ⇒ 请求发不出去、模板回来了")
import types  # noqa: E402

with open(GH.__file__, encoding="utf-8") as f:
    _src = f.read()
_old = '"content": fill(SEED_SYSTEM_PROMPT, n)}'
assert _src.count(_old) == 1, f"反事实锚点命中 {_src.count(_old)} 处，不唯一"
_mut = types.ModuleType("gh_mutant")
_mut.__dict__["__file__"] = "llm_genetic_hybrid_mutant"
exec(compile(_src.replace(_old, '"content": SEED_SYSTEM_PROMPT.format(n=n)}'),
             "gh_mutant", "exec"), _mut.__dict__)
_gm = _mut.LLMSeedGenerator()
_gm.enabled = True
_got = []
_gm._chat = lambda messages: _got.append(messages)
_out = _gm.generate_seeds(3)
_reverted = (not _got) and [s.get("name") for s in _out][:1] == ["seed_mom5"]
check("N5-拔牙", "负对照", _reverted,
      f"换回 .format 后发请求 {len(_got)} 次、返回={[s.get('name') for s in _out]}"
      f"（= T4-generate_seeds 那格确实由 fill 这一处咬住，不是桩白给）")

print(f"\n合计：{CELLS} 格（放行 {KINDS.get('放行', 0)} · 判红 {KINDS.get('判红', 0)} · "
      f"负对照 {KINDS.get('负对照', 0)}）｜失败 {len(FAILS)} 条 {FAILS}")
sys.exit(1 if FAILS else 0)
