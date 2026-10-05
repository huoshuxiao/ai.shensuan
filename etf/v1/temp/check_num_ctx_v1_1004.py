# -*- coding: utf-8 -*-
"""一次性读数（10-04）：主线那条 `/v1` 兼容口到底认不认 `options.num_ctx`。

大目标：个人量化辅助系统要在 16G 纯 CPU 的机器上每天跑因子挖掘；10-03 逐字节量过
官方支那条真提示词有 5502 token，而 ollama 服务端默认 4096 窗口只让它进 2050。
10-04 用户裁「选项B＝统一项只写在 etf/v1/.env」，`ETF_LLM_NUM_CTX=16384` 同时喂两条腿：
official 那侧走 litellm 顶层 kwargs（10-03 已实测撑得开），**主线这侧走 OpenAI SDK 的
extra_body 透传——这个形状本机从没量过**。配置层接上了没有由
`check_kwargs_source_1004.py` 管（12 格已全绿）；这一支只回答剩下那一格：
**发出去的请求到了 ollama 那里，窗口真变宽了没有**。

四臂（每臂都把那条 5502 token 的真提示词整条发过去，只许 prefill 不许解码）：
  V0 `/v1` 裸（没有任何 num_ctx）              ⇒ 基线，期望被截到 ≤4096
  V3 原生 `/api/chat` + options.num_ctx=16384  ⇒ **正对照**，10-03 实测它翻得动；
       它要是不翻，说明这把尺子本身坏了，四臂读数一律不作数
  V1 生产客户端（llm_client + ETF_LLM_NUM_CTX=16384）⇒ 这一格才是本场的问题
  V2 `/v1` + 顶层 num_ctx（不带 options 那层）  ⇒ 另一种可能的形状，如实拍下

每臂两个独立读数，互相对表：
  R1 响应里的 prompt_tokens / prompt_eval_count ＝**真有多少字进了模型**
  R2 `/api/ps` 的 context_length ＝服务端实际给这个模型分配了多长的窗口
判据（互反，不许恒真）：V0 与 V3 必须**不等**（相等＝这条提示词压根没超窗＝没有测试）；
V1 落点只有两种，都得念出来：≈V3 ⇒ 通道通；＝V0 ⇒ 主线那侧的 16384 是空转，
etf/v1/.env 那行注释里的「待验」要改口，且只能走原生口（那是另一个量级的改动）。

⚠️ 起这一支之前必须确认那条日更链已经收工：四臂都要让 qwen3.5:9b 驻留（6.1GB），
每臂一次 5502 token 的 prefill 实测要几分钟；链在跑时并发拉起＝两台一起变慢、
最坏内核会杀掉别人那一场。

用法：/usr/bin/python3.10 -u etf/v1/temp/check_num_ctx_v1_1004.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")
PROMPT = os.path.join(REPO, "etf/v1/temp/probe_real_prompt_hypothesis_gen_1003.txt")
V1_URL = "http://localhost:11434/v1/chat/completions"
NATIVE_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:9b"
FAILS = []


def say(m):
    print(m, flush=True)


def post(url, payload, timeout=1200):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def resident_ctx():
    """/api/ps 的 context_length；模型不在驻留时给 None（这本身就是读数，不许折成 0）"""
    out = subprocess.run(["curl", "-s", "--max-time", "8",
                          "http://localhost:11434/api/ps"],
                         capture_output=True, text=True).stdout
    for m in json.loads(out or "{}").get("models", []):
        if m["name"].startswith(MODEL):
            return m.get("context_length")
    return None


def arm(label, got, secs):
    say("  %-6s prompt_tokens=%-7s 驻留窗口=%-8s 墙钟=%ss"
        % (label, got, resident_ctx(), secs))
    return got


def v_http(label, extra):
    body = {"model": MODEL, "messages": [{"role": "user", "content": TEXT}],
            "max_tokens": 1, "temperature": 0}
    body.update(extra)
    t0 = time.time()
    try:
        d = post(V1_URL, body)
    except Exception as e:
        say("  %-6s 异常 %s: %s" % (label, type(e).__name__, str(e)[:200]))
        return None
    return arm(label, (d.get("usage") or {}).get("prompt_tokens"),
               round(time.time() - t0, 1))


def v_native(label):
    t0 = time.time()
    try:
        d = post(NATIVE_URL, {"model": MODEL,
                              "messages": [{"role": "user", "content": TEXT}],
                              "stream": False, "think": False,
                              "options": {"num_ctx": 16384, "num_predict": 1}})
    except Exception as e:
        say("  %-6s 异常 %s: %s" % (label, type(e).__name__, str(e)[:200]))
        return None
    return arm(label, d.get("prompt_eval_count"), round(time.time() - t0, 1))


def v_production(label, num_ctx_env):
    """走生产那一层：同一个 llm_client、同一份 .env，只把窗口旋钮换成臂的值"""
    child = r'''
import json, os, sys
_REPO = %r
for _p in (os.path.join(_REPO, "etf", "v1", "src", "config"),
           os.path.join(_REPO, "etf", "v1", "src", "core"),
           os.path.join(_REPO, "common", "src", "core"),
           os.path.join(_REPO, "common", "src")):
    sys.path.insert(0, _p)
import llm_client
from config import LLM_MODEL
text = open(%r, encoding="utf-8").read()
c = llm_client.make_openai_client()
r = c.chat.completions.create(model=LLM_MODEL,
                              messages=[{"role": "user", "content": text}],
                              max_tokens=1, temperature=0)
print("CHILD " + json.dumps({"pt": (r.usage or {{}}).prompt_tokens if r.usage else None,
                             "describe": llm_client.describe_endpoint()}))
''' % (REPO, PROMPT)
    p = subprocess.run([sys.executable, "-c", child], capture_output=True,
                       text=True, cwd=SRC, timeout=1500,
                       env={**os.environ, "ETF_LLM_NUM_CTX": str(num_ctx_env)})
    line = next((ln for ln in (p.stdout or "").splitlines()
                 if ln.startswith("CHILD ")), None)
    if line is None:
        say("  %-6s 没出读数 rc=%s｜%s" % (label, p.returncode, p.stderr[-300:]))
        return None
    d = json.loads(line[len("CHILD "):])
    say("  %-6s 生产那行自述：%s" % (label, d["describe"]))
    return arm(label, d["pt"], "-")


TEXT = ""


def main():
    global TEXT
    if not os.path.isfile(PROMPT):
        say("🛑 那条真提示词的逐字节原文不在（%s）⇒ 这一支没有输入，不起" % PROMPT)
        return 1
    TEXT = open(PROMPT, encoding="utf-8").read()
    say("提示词 %d 字符｜起点驻留窗口=%s" % (len(TEXT), resident_ctx()))

    say("[V0] /v1 裸发（不给任何窗口参数）＝基线")
    v0 = v_http("V0", {})
    say("[V3] 原生 /api/chat + options.num_ctx=16384＝正对照（它必须翻）")
    v3 = v_native("V3")

    # 尺子自洽闸：正对照不翻＝整支作废，不许拿它去判主线那一格
    if v0 is None or v3 is None:
        say("\n🛑 V0/V3 有臂没读数，拒判。")
        return 1
    if v3 <= v0:
        say("\n🛑 尺子坏了：正对照 %s 没比裸基线 %s 大 ⇒ 这条提示词没超窗，"
            "四臂读不作数" % (v3, v0))
        FAILS.append("正对照无差")

    say("[V1] 生产客户端 + ETF_LLM_NUM_CTX=16384（本场的问题）")
    v1 = v_production("V1", 16384)
    say("[V2] /v1 + 顶层 num_ctx（没有 options 那层）＝另一种形状")
    v2 = v_http("V2", {"num_ctx": 16384})
    say("[V1b] 生产客户端 + 窗口旋钮回 0（负对照：撤掉旋钮应当退回 V0 那一格）")
    v1b = v_production("V1b", 0)

    say("\n—— 判读 ——")
    say("裸 /v1 进了 %s token；原生口撑到 16384 进了 %s token；被丢掉 %s"
        % (v0, v3, (v3 - v0) if v3 and v0 else "?"))
    if v1 is None:
        say("V1 没读数 ⇒ 主线这一格仍无答案，别写进结论")
        FAILS.append("V1 无读数")
    elif v1 >= v3:
        say("V1=%s ≥ 正对照 ⇒ **extra_body 的 options 这条通道通**，"
            "主线的 16384 是真生效的" % v1)
    elif v1 == v0:
        say("V1=%s 与裸基线相等 ⇒ **`/v1` 不认 options.num_ctx**，"
            "ETF_LLM_NUM_CTX 对主线那侧是空转（official 那侧仍有效）" % v1)
    else:
        say("V1=%s 落在两格之间 ⇒ 通道部分生效，形状未定，如实记下别硬判" % v1)
    if v1b is not None and v1b != v0:
        say("⚠️ 负对照不等（V1b=%s vs V0=%s）⇒ 撤了旋钮还留着效果，这一支的读数要打折"
            % (v1b, v0))
        FAILS.append("负对照不退回")

    say("\n" + ("全部通过" if not FAILS else "失败项: " + ", ".join(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
