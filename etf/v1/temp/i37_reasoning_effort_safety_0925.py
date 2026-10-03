# -*- coding: utf-8 -*-
"""`reasoning_effort:"none"` 能不能安全地写进共享层：三个模型同形对比。

i36 的读数把方向改了：
  P1（派生 tag `qwen3.5:9b-etf`，只带 `PARAMETER num_predict 2048`）在真实形态下
     **900s 仍未返回** ⇒ Modelfile 的天花板要么在 /v1 这条路上不生效、要么 2048 token
     按本机 ~2.4 tok/s 要 870s —— 无论哪种，"封顶在模型侧"都不足以救入口。
  P2（原 tag + `reasoning_effort:"none"`）**17.9s / 42 token / 思考 0 字符**，
     返回 `18/20/19/20、total 77` 可解析 JSON ⇒ 兼容口认这个字段，思考能真关掉。

所以路径 3（改 `common/src/core/llm_client.py` 注入默认值）才是解法。但共享层两线共用，
注入前先确认它对**非思考模型**（本线现在的 `qwen2.5:7b`、股票线同族）不会报错、不会改语义：
这里三个模型打同一条请求，唯一差别就是带不带 `reasoning_effort`。
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

URL = LLM_BASE_URL.rstrip("/") + "/chat/completions"
SYSTEM = ("你是量化策略自评器。只回 JSON，格式："
          '{"scores": {"accuracy": 0, "completeness": 0, '
          '"actionability": 0, "logic": 0}, "total": 0}')
USER = ("日报：近 20 日组合夏普 0.42，最大回撤 -8.1%，换手 1.6，持仓 5 只 ETF。"
        "原始数据：{\"sharpe\": 0.42, \"mdd\": -0.081, \"turnover\": 1.6}。"
        "请按 0~25 分给四维打分，total 为四维之和。")
MODELS = [LLM_MODEL, "qwen3.5:9b", "qwen3.5:9b-etf"]


def call(model, effort, max_tokens=1024, timeout=300):
    body = {"model": model, "stream": False, "temperature": 0.3,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": USER}]}
    if effort:
        body["reasoning_effort"] = effort
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer local"})
    t0 = time.time()
    tag = f"{model} | effort={effort or '不带'} | max_tokens={max_tokens}"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode())
    except Exception as e:
        print(json.dumps({"请求": tag, "秒": round(time.time() - t0, 1),
                          "错误": f"{type(e).__name__}: {str(e)[:150]}"},
                         ensure_ascii=False), flush=True)
        return
    dt = round(time.time() - t0, 1)
    ch = res["choices"][0]["message"]
    content = ch.get("content") or ""
    reasoning = ch.get("reasoning") or ch.get("reasoning_content") or ""
    usage = res.get("usage") or {}
    out = {"请求": tag, "秒": dt,
           "completion_tokens": usage.get("completion_tokens"),
           "finish_reason": res["choices"][0].get("finish_reason"),
           "思考字符": len(reasoning),
           "正文": content[:100].replace("\n", " ")}
    try:
        out["可解析"] = isinstance(json.loads(content), dict)
    except Exception as e:
        out["可解析"] = f"否({type(e).__name__})"
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    print(f"端点 {URL}", flush=True)
    for m in MODELS:
        # 不带 effort = 现状语义（7b 必须与今天一致）；带 = 注入后的语义
        call(m, None)
        call(m, "none")
