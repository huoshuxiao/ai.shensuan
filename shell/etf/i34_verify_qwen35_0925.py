# -*- coding: utf-8 -*-
"""验证 qwen3.5:9b 能不能接进 ETF 线（09-25）。

股票线那边的探针（`shell/stock/probe_llm_qwen35_9b_0925.log`，13:50）量的是 ollama
原生 `/api/chat`：9b 默认带思考 238.8s/64 token、`think:false` 5.6s。本线用的**不是**
那个口，而是 OpenAI 兼容口 `/v1/chat/completions`（`common/src/report/llm_client.py`
→ openai SDK），所以三件事必须在本线口径下重量一遍：

① 兼容口下 9b 的**单次代价**（这台机器的 ollama 被 systemd 锁在纯 CPU：
   `CUDA_VISIBLE_DEVICES=-1 OLLAMA_NUM_GPUS=0`，慢的不是效果是墙钟）；
② 兼容口**能不能关思考** —— openai SDK 只有把 `think:false` 塞进 extra_body 才可能关，
   而 `llm_client`/`self_evaluator` 都在 `common/src`，本线不改它们 ⇒ 如果关不掉，
   换模型的代价直接落到 `--self-eval`/日报/投票这些入口上，得先报账再决定换不换；
③ `response_format={"type":"json_object"}` 这条硬要求（`self_evaluator.py:45` 用它的）
   在 9b 上是否还能返回**可解析**的 JSON —— 老 7b 实测会把提示词里的示例 JSON
   原样吐回来（09-25 13:41 探针：四维 22/18/20/21、total 81，与 EVAL_PROMPT 示例逐字相同），
   所以"能回 JSON"不等于"在评"。

只打端点、不落任何生产文件；模型名与端点都从本线 config 现读，不写死。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "etf", "v1", "src")))
import _bootstrap  # noqa: F402,E402
from config import LLM_BASE_URL, LLM_MODEL  # noqa: E402

CANDIDATE = "qwen3.5:9b"
URL = LLM_BASE_URL.rstrip("/") + "/chat/completions"
PROMPT = "只回一个字：好"
JSON_PROMPT = ('按这个模式回答：{"scores": {"accuracy": 22}, "total": 22}。'
               '现在请把 accuracy 改成 17、total 改成 17，只回 JSON。')


def urllib_request(body):
    import urllib.request
    data = json.dumps(body).encode()
    return urllib.request.Request(
        URL, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer local"})


def run(label, model, messages, max_tokens=64, extra=None):
    body = {"model": model, "messages": messages, "stream": False,
            "max_tokens": max_tokens}
    body.update(extra or {})
    import urllib.request
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib_request(body),
                                    timeout=1200) as r:
            res = json.loads(r.read().decode())
        dt = time.time() - t0
        ch = res["choices"][0]["message"]
        content = (ch.get("content") or "")
        reasoning = (ch.get("reasoning") or ch.get("reasoning_content") or "")
        usage = res.get("usage") or {}
        out = {"档": label, "秒": round(dt, 1),
               "completion_tokens": usage.get("completion_tokens"),
               "tok/s": round((usage.get("completion_tokens") or 0)
                              / max(dt, 1e-9), 2),
               "thinking字符": len(reasoning),
               "正文": content[:120].replace("\n", " ")}
    except Exception as e:
        dt = time.time() - t0
        out = {"档": label, "秒": round(dt, 1), "错误":
               f"{type(e).__name__}: {str(e)[:180]}"}
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


if __name__ == "__main__":
    print(f"端点 {URL} | 本线现配置模型 = {LLM_MODEL} | 待验模型 = {CANDIDATE}",
          flush=True)
    # ① 兼容口默认档（思考开不开由服务端默认决定，这里先看真实代价）
    a = run("① 9b 兼容口默认", CANDIDATE, [{"role": "user",
                                            "content": PROMPT}], 64)
    # ② 兼容口下 extra_body.think=false 是否被接受（openai SDK 会把它并进请求体）
    b = run("② 9b think:false", CANDIDATE,
            [{"role": "user", "content": PROMPT}], 64, {"think": False})
    # ③ 自评器那条硬要求：response_format=json_object 能不能返回可解析 JSON
    c = run("③ 9b json_object", CANDIDATE,
            [{"role": "user", "content": JSON_PROMPT}], 128,
            {"response_format": {"type": "json_object"}})
    # ④ 对照：本线现在的模型同 prompt，换与不换比的是这一档
    d = run("④ 现模型对照", LLM_MODEL,
            [{"role": "user", "content": PROMPT}], 64)
    ok = sum(1 for r in (a, b, c, d) if "错误" not in r)
    print(f"\n四档里 {ok} 档成功返回", flush=True)
    if "错误" not in c:
        try:
            j = json.loads(c["正文"])
            print(f"  json_object 档解析成功: {j}", flush=True)
        except Exception as e:
            print(f"  ⚠️ json_object 档返回不可解析: {e}", flush=True)
