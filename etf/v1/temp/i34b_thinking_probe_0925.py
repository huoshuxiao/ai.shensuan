# -*- coding: utf-8 -*-
"""qwen3.5:9b 能不能接进本线：第二轮，专测"关思考"的三种写法与预算。

第一轮（`i34_verify_qwen35_0925.log`）的读数：
  ① 默认 96.3s / 64 token 全花在思考上，**正文为空**；
  ② `{"think": false}` 在 `/v1` 兼容口**不生效**（仍 239 字符思考、正文空）；
  ③ `response_format={"type":"json_object"}` 同样只得到空正文
     —— 而 `self_evaluator.py:45` 与日报/投票都要 `json.loads(content)`。
所以本轮只问两件事：兼容口有没有别的关思考写法（ollama 新版认
`thinking:{"type":"disabled"}` 与 chat_template_kwargs），以及**不关**的话
一条真实提示词要花多少 token 才吐得出正文。仍然只打端点、不落生产文件。
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "etf", "v1", "src")))
import _bootstrap  # noqa: F402,E402
from config import LLM_BASE_URL, LLM_MODEL  # noqa: E402

MODEL = "qwen3.5:9b"
URL = LLM_BASE_URL.rstrip("/") + "/chat/completions"
PROMPT = "只回一个字：好"
# 自评器真实的调用形态：要 JSON、正文里不能有前后缀
JSON_PROMPT = ('按 {"scores": {"accuracy": <int>}, "total": <int>} 回答，'
               '把 accuracy 与 total 都填 17。只回 JSON。')


def run(label, body, timeout=1500):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        URL, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer local"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode())
    except Exception as e:
        print(json.dumps({"档": label, "秒": round(time.time() - t0, 1),
                          "错误": f"{type(e).__name__}: {str(e)[:160]}"},
                         ensure_ascii=False), flush=True)
        return None
    dt = time.time() - t0
    ch = res["choices"][0]["message"]
    usage = res.get("usage") or {}
    out = {"档": label, "秒": round(dt, 1),
           "completion_tokens": usage.get("completion_tokens"),
           "思考字符": len(ch.get("reasoning")
                          or ch.get("reasoning_content") or ""),
           "正文": (ch.get("content") or "")[:80].replace("\n", " ")}
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


def base(messages, max_tokens, **extra):
    b = {"model": MODEL, "messages": messages, "stream": False,
         "max_tokens": max_tokens}
    b.update(extra)
    return b


if __name__ == "__main__":
    print(f"端点 {URL} | 目标 {MODEL} | 对照 {LLM_MODEL}", flush=True)
    msg = [{"role": "user", "content": PROMPT}]
    run("A thinking:{type:disabled}", base(msg, 64,
                                          thinking={"type": "disabled"}))
    run("B chat_template_kwargs.think", base(
        msg, 64, chat_template_kwargs={"think": False}))
    run("C 预算提到 1024（不关思考）", base(msg, 1024))
    j = run("D json_object + disabled thinking", base(
        [{"role": "user", "content": JSON_PROMPT}], 1024,
        response_format={"type": "json_object"},
        thinking={"type": "disabled"}))
    if j and j.get("正文"):
        try:
            print(f"   json_object 档解析结果 = {json.loads(j['正文'])}",
                  flush=True)
        except Exception as e:
            print(f"   ⚠️ 仍不可解析: {e}", flush=True)
