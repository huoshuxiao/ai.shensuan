# -*- coding: utf-8 -*-
"""一次性读数（10-04 统一 → 10-04 上午裁「乙＋铃铛」后改口径）：一把「窗口」旋钮喂到哪条腿。

`check_kwargs_env_plumbing_1003.py` 量的是「手写 JSON → 子进程 env」那一根线；10-04
把语义改了（用户裁「选项B＝统一项只写在 etf/v1/.env」）：
  `ETF_LLM_REASONING_EFFORT` / `ETF_LLM_NUM_CTX` 成了**源头**，official 那串 kwargs
  由 `_driver_env()` 派生。**主线那侧 10-04 裁「乙」已拔掉 num_ctx 注入**（`/v1` 四种
  写法实测一律只进 2050，塞了也不认 ⇒ 这支只做一件事：现在**拨到 16384 也不出现在请求体**），
  换上的是 `ETF_LLM_WARN_CHARS` 那枚**只叫不改**的超窗警铃。这一支同时接管这两件。

四臂各起一个子进程（环境变量必须在 import 之前定），判据互反：
  A1 源头都默认（effort 空 + num_ctx=0）⇒ 子进程 env 里**没有** RDAGENT_LLM_KWARGS
     （这是「股票线没设这两把 ⇒ 行为与 10-03 一字不差」那一句的牙）
  A2 只开窗口（num_ctx=16384，effort 空）⇒ 派生只带 num_ctx，不带 think
  A3 两把都开 ⇒ 派生逐字＝{"think": false, "num_ctx": 16384}
  A4 显式给了 ETF_RDAGENT_LLM_KWARGS ⇒ 照原样透传，派生**不许**盖掉它
另加三臂读侧（都不发网络）：M1 主线**拨着窗口也不注入**＋`describe_endpoint()` 念的是
「主线不注入／官方支 16384」；M2 铃铛的**互反两格**（未线不响、超线响）；M3 铃铛关着时
连包装都不发生（＝另一条线拿到的客户端与改动前逐字一致）。

⚠️ 这一支只证明**配置层接线**：A 臂那串 kwargs 到不到 litellm、主线请求体里有没有它；
`/v1` 认不认那个形状是另一回事，已由 `check_num_ctx_v1_1004.py` 四臂判过「不认」，
原生 `/api/chat` 才认（同一把尺子的另一份 log）。

用法：/usr/bin/python3.10 -u etf/v1/temp/check_kwargs_source_1004.py
"""
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")
FAILS = []

# 每一臂只跑一次子进程：env 必须在 import config 之前定好，所以只能在外面给
CHILD_DRIVER = r'''
import json, os, sys
_REPO = %r
for _p in (os.path.join(_REPO, "etf", "v1", "src", "config"),
           os.path.join(_REPO, "etf", "v1", "src", "core"),
           os.path.join(_REPO, "common", "src", "core"),
           os.path.join(_REPO, "common", "src")):
    sys.path.insert(0, _p)
import official_rdagent as off
from config import LLM_REASONING_EFFORT, LLM_NUM_CTX
env = off._driver_env()
print("CHILD " + json.dumps({
    "module": off.__file__,
    "effort": LLM_REASONING_EFFORT,
    "num_ctx": LLM_NUM_CTX,
    "has": "RDAGENT_LLM_KWARGS" in env,
    "val": env.get("RDAGENT_LLM_KWARGS"),
}, ensure_ascii=False))
''' % REPO

# 主线侧：把 create 换成一只钩子，量请求体长什么样 + 铃铛响不响（都不碰网络）
# 每臂连发两条：小的一条 2 字、大的一条 3000 字 ⇒ 同一臂里就能给出互反两格
# （未线不响／超线响），且两条都原样回显 ⇒ 「只叫不改」有牙。stdout 用 StringIO
# 接住再塞回 CHILD 那行 JSON，否则 `run()` 只会读到第一行、铃铛文本看不见。
CHILD_MAIN = r'''
import contextlib, io, json, os, sys
_REPO = %r
for _p in (os.path.join(_REPO, "etf", "v1", "src", "config"),
           os.path.join(_REPO, "etf", "v1", "src", "core"),
           os.path.join(_REPO, "common", "src", "core"),
           os.path.join(_REPO, "common", "src")):
    sys.path.insert(0, _p)
import llm_client

BIG = "字" * 3000


class _FakeCompletions(object):
    def __init__(self):
        self.calls = []

    def create(self, **req):
        self.calls.append(req)
        return "stub"


class _FakeOpenAI(object):
    def __init__(self, **kw):
        self.chat = type("c", (), {"completions": _FakeCompletions()})()


import openai
openai.OpenAI = _FakeOpenAI
c = llm_client.make_openai_client()
# 「连包装都不发生」的量法：没开关时 create 还挂在类上，实例字典里没有这个键
wrapped = "create" in vars(c.chat.completions)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    c.chat.completions.create(model="x", messages=[{"role": "user", "content": "hi"}])
    small = buf.getvalue()
    c.chat.completions.create(model="x", messages=[{"role": "user", "content": BIG}])
    both = buf.getvalue()
large = both[len(small):]
calls = c.chat.completions.calls
print("CHILD " + json.dumps({
    "wrapped": wrapped,
    "keys": [sorted(k) for k in calls],
    "untouched": [dict(kw) == {"model": "x", "messages": kw["messages"]}
                  for kw in calls],
    "body_has_num_ctx_key": ("num_ctx" in json.dumps(calls)) or ("extra_body" in calls[0]),
    "small": small,
    "large": large,
    "describe": llm_client.describe_endpoint(),
}, ensure_ascii=False))
''' % REPO


def check(label, got, want):
    ok = got == want
    print("  %s %s: 实到 %r 期望 %r" % ("✅" if ok else "✗", label, got, want))
    if not ok:
        FAILS.append(label)


def run(child, extra):
    p = subprocess.run([sys.executable, "-c", child], capture_output=True,
                       text=True, cwd=SRC, env={**os.environ, **extra})
    line = next((ln for ln in (p.stdout or "").splitlines()
                 if ln.startswith("CHILD ")), None)
    if line is None:
        raise SystemExit(f"子进程没出读数 rc={p.returncode}\n{p.stderr[-800:]}")
    return json.loads(line[len("CHILD "):])


BASE = {"ETF_LLM_REASONING_EFFORT": "", "ETF_LLM_NUM_CTX": "0",
        "ETF_RDAGENT_LLM_KWARGS": "",
        # 主线那四把也一律钉回默认：本线 .env 从 10-04 起写着 WARN_CHARS=2200，
        # 漏钉一把，下面 M 臂就是在拿生产配置做断言
        "ETF_LLM_MAX_TOKENS": "0", "ETF_LLM_WARN_CHARS": "0"}

print("[A1] 两把源头都用默认值 ⇒ 一个键都不注入（股票线今天的样子）")
d = run(CHILD_DRIVER, BASE)
suf = os.path.join("common", "src", "core", "official_rdagent.py")
check("official_rdagent 解析到 common 那份",
      d["module"][-len(suf):], suf)
check("config 读到 effort 空", d["effort"], "")
check("config 读到 num_ctx=0", d["num_ctx"], 0)
check("env 里没有 RDAGENT_LLM_KWARGS", d["has"], False)

print("[A2] 只撑窗口、不关思考 ⇒ 派生只带 num_ctx")
d = run(CHILD_DRIVER, {**BASE, "ETF_LLM_NUM_CTX": "16384"})
check("env 有该键", d["has"], True)
check("派生内容", d["val"], '{"num_ctx": 16384}')

print("[A3] 两把都开（ETF 线今天的样子）⇒ 派生成 think+num_ctx")
d = run(CHILD_DRIVER, {**BASE, "ETF_LLM_NUM_CTX": "16384",
                       "ETF_LLM_REASONING_EFFORT": "none"})
check("派生逐字等于 10-03 手写的那串", d["val"],
      '{"think": false, "num_ctx": 16384}')

print("[A4] 显式给了手写串 ⇒ 原样透传，派生不插手")
RAW = '{"think": false, "num_ctx": 8192}'
d = run(CHILD_DRIVER, {**BASE, "ETF_LLM_NUM_CTX": "16384",
                       "ETF_LLM_REASONING_EFFORT": "none",
                       "ETF_RDAGENT_LLM_KWARGS": RAW})
check("env 里就是手写的这一串", d["val"], RAW)

print("[M1] 主线那侧「乙」的牙：窗口拨到 16384，请求体里一个 num_ctx 都不许出现")
d = run(CHILD_MAIN, {**BASE, "ETF_LLM_NUM_CTX": "16384",
                     "ETF_LLM_REASONING_EFFORT": "none",
                     "ETF_LLM_MAX_TOKENS": "1024"})
check("请求体形状（只有上限＋关思考这两把在注入）",
      d["keys"], [["max_tokens", "messages", "model", "reasoning_effort"],
                  ["max_tokens", "messages", "model", "reasoning_effort"]])
check("请求体里找不到 num_ctx / extra_body", d["body_has_num_ctx_key"], False)
check("describe 念的是「主线不注入」", "窗口=主线不注入" in d["describe"], True)
check("describe 把官方支那个数当「别人的」念一遍",
      "官方支=16384" in d["describe"], True)
print("           下一场生产日志靠这一行核对：" + d["describe"])

print("[M2] 铃铛的互反两格：报警线 200 字，2 字不响／3000 字响，且请求体一个字没改")
d = run(CHILD_MAIN, {**BASE, "ETF_LLM_WARN_CHARS": "200"})
check("未过线一格：stdout 一个字节都没有", d["small"], "")
check("超线一格响", "超窗铃铛" in d["large"], True)
check("响的那行报出实到字数", "3000 字" in d["large"], True)
check("响的那行报出报警线", "200 字" in d["large"], True)
check("只叫不改：两条请求体都与原样逐字相同", d["untouched"], [True, True])
check("铃铛单独开也生效（不靠另外三把）", d["wrapped"], True)
check("describe 念得出报警线", "铃铛=200" in d["describe"], True)

print("[M3] 四把全默认 ⇒ 连包装都不发生（＝另一条线拿到的客户端与改动前逐字一致）")
d = run(CHILD_MAIN, BASE)
check("实例上没有 create 覆盖", d["wrapped"], False)
check("3000 字也不响（铃铛关着）", d["large"], "")
check("describe 报「主线不注入／官方支默认／铃铛关」",
      ("窗口=主线不注入" in d["describe"] and "官方支=默认4096" in d["describe"]
       and "铃铛=关" in d["describe"]), True)

print("\n" + ("全部通过" if not FAILS else "失败项: " + ", ".join(FAILS)))
raise SystemExit(1 if FAILS else 0)
