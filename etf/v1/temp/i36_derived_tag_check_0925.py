# -*- coding: utf-8 -*-
"""派生 tag `qwen3.5:9b-etf` 验收：Modelfile 的 token 天花板在 /v1 兼容口到底生不生效。

用户批复「2 换 / 3 改」，所以本轮要量的不是"9b 好不好"，而是两条**能不能落地**：

P1 —— 共享层那个"不传 max_tokens"的真实形态（照 `common/src/report/self_evaluator.py:45`）
      打到派生 tag 上。ollama 0.34.2 实测**不认** `PARAMETER think/thinking`
      （`unknown parameter 'think'` / `'thinking'`），所以派生 tag 只带了
      `PARAMETER num_predict 2048` 这一道天花板 —— 若它生效，调用最多数千 token 必被截停，
      不再出现 31 分钟不返回；若不生效，说明 Modelfile 的 num_predict 在 /v1 这条路上被
      客户端缺省值盖掉，那"换 9b"就只剩路径 3（改共享层）。
P2 —— `/v1` 下 `reasoning_effort:"none"` 能不能真把思考关掉（这是比"截停"更好的解法：
      省掉整段思考 token）。只探端点行为，不碰任何代码。

只打端点，不写生产文件。
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "etf", "v1", "src")))
import _bootstrap  # noqa: F402,E402
from config import LLM_BASE_URL  # noqa: E402

DERIVED = "qwen3.5:9b-etf"
BASE = "qwen3.5:9b"
URL = LLM_BASE_URL.rstrip("/") + "/chat/completions"
SYSTEM = ("你是量化策略自评器。只回 JSON，格式："
          '{"scores": {"accuracy": 0, "completeness": 0, '
          '"actionability": 0, "logic": 0}, "total": 0}')
USER = ("日报：近 20 日组合夏普 0.42，最大回撤 -8.1%，换手 1.6，持仓 5 只 ETF。"
        "原始数据：{\"sharpe\": 0.42, \"mdd\": -0.081, \"turnover\": 1.6}。"
        "请按 0~25 分给四维打分，total 为四维之和。")


def post(body, timeout=900):
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer local"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode())
    except Exception as e:
        return {"秒": round(time.time() - t0, 1),
                "错误": f"{type(e).__name__}: {str(e)[:160]}"}
    dt = round(time.time() - t0, 1)
    ch = res["choices"][0]["message"]
    content = ch.get("content") or ""
    reasoning = ch.get("reasoning") or ch.get("reasoning_content") or ""
    usage = res.get("usage") or {}
    out = {"秒": dt,
           "completion_tokens": usage.get("completion_tokens"),
           "finish_reason": res["choices"][0].get("finish_reason"),
           "思考字符": len(reasoning), "正文字符": len(content),
           "正文": content[:120].replace("\n", " ")}
    try:
        out["可解析"] = isinstance(json.loads(content), dict)
    except Exception as e:
        out["可解析"] = f"否({type(e).__name__})"
    return out


if __name__ == "__main__":
    print(f"端点 {URL} | 派生 {DERIVED}(Modelfile num_predict 2048) | 原 {BASE}",
          flush=True)
    # P1：与共享层逐字同形（无 max_tokens），打派生 tag
    body = {"model": DERIVED, "stream": False, "temperature": 0.3,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": USER}]}
    r1 = post(body)
    print("P1 真实形态→派生tag: " + json.dumps(r1, ensure_ascii=False), flush=True)
    # P2：/v1 下 reasoning_effort=none 能否关思考（给足预算，只看思考长度是否归零）
    body2 = dict(body)
    body2["model"] = BASE
    body2["max_tokens"] = 1024
    body2["reasoning_effort"] = "none"
    r2 = post(body2)
    print("P2 reasoning_effort=none→原tag: " + json.dumps(r2, ensure_ascii=False),
          flush=True)
    print("\n口径：P1 的 completion_tokens ≤2048 且正文非空 ⇒ Modelfile 天花板生效；"
          "P2 思考字符=0 ⇒ 兼容口能关思考。", flush=True)
