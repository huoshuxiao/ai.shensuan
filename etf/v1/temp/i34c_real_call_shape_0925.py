# -*- coding: utf-8 -*-
"""第三轮：照共享层的**真实调用形态**打一次，量"换模型后入口会不会静默拿到空正文"。

前两轮读数（i34 / i34b）：
  兼容口 `/v1` 下 `think:false`、`thinking:{"type":"disabled"}`、
  `chat_template_kwargs:{"think":false}` **三种写法全不被接受**（思考照出，213~239 字符）；
  但把 `max_tokens` 提到 1024 后正文能出、`response_format=json_object` 能返回可解析 JSON
  （D 档 82.5s / 198 token）。
问题在于：本仓 20 个 `chat.completions.create` 调用点**没有一个传 max_tokens**
（`self_evaluator.py:45`、`joint_optimizer.py:42`、`multi_llm_voter.py:45` 等），
所以"换 9b 后能不能用"只取决于 ollama 在缺省 num_predict 下吐多少 token ——
这一轮就用 `make_openai_client` + 与 `_chat` 逐字同形的请求体把它量出来。

仍然只打端点、不落生产文件；对照组是本线现在的 7b。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "etf", "v1", "src")))
import _bootstrap  # noqa: F402,E402
from config import LLM_BASE_URL, LLM_MODEL  # noqa: E402
from core.llm_client import make_openai_client  # noqa: E402

CANDIDATE = "qwen3.5:9b"
# 与 self_evaluator.EVAL_PROMPT 同形：要求只回 JSON、字段名一致
SYSTEM = ("你是量化策略自评器。只回 JSON，格式："
          '{"scores": {"accuracy": 0, "completeness": 0, '
          '"actionability": 0, "logic": 0}, "total": 0}')
USER = ("日报：近 20 日组合夏普 0.42，最大回撤 -8.1%，换手 1.6，"
        "持仓 5 只 ETF。原始数据：{\"sharpe\": 0.42, \"mdd\": -0.081, "
        "\"turnover\": 1.6}。请按 0~25 分给四维打分，total 为四维之和。")


def real_call(label, model):
    """照 self_evaluator._chat 的形态：不传 max_tokens，只传 temperature + response_format"""
    client = make_openai_client(api_key=os.environ.get("OPENAI_API_KEY") or "local-llm")
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": USER}],
            temperature=0.3,
            response_format={"type": "json_object"})
    except Exception as e:
        print(json.dumps({"档": label, "模型": model,
                          "秒": round(time.time() - t0, 1),
                          "错误": f"{type(e).__name__}: {str(e)[:200]}"},
                         ensure_ascii=False), flush=True)
        return None
    dt = time.time() - t0
    m = resp.choices[0].message
    reasoning = getattr(m, "reasoning", None) or \
        getattr(m, "reasoning_content", None) or ""
    content = m.content or ""
    usage = getattr(resp, "usage", None)
    out = {"档": label, "模型": model, "秒": round(dt, 1),
           "completion_tokens": getattr(usage, "completion_tokens", None),
           "finish_reason": resp.choices[0].finish_reason,
           "思考字符": len(reasoning), "正文字符": len(content),
           "正文": content[:160].replace("\n", " ")}
    try:
        out["可解析"] = isinstance(json.loads(content), dict)
    except Exception as e:
        out["可解析"] = f"否({type(e).__name__})"
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


if __name__ == "__main__":
    print(f"端点 {LLM_BASE_URL} | 本线现模型 {LLM_MODEL} | 待验 {CANDIDATE} "
          f"| 调用形态=self_evaluator._chat（无 max_tokens）", flush=True)
    a = real_call("① 9b 真实形态", CANDIDATE)
    b = real_call("② 现模型对照", LLM_MODEL)
    print("\n判定口径：正文为空或不可解析 ⇒ 本线 20 个 JSON 入口都拿不到分，"
          "换模型等于把入口打空。", flush=True)
    for r in (a, b):
        if r and not isinstance(r.get("可解析"), bool):
            print(f"  ⚠️ {r['档']}  finish_reason={r['finish_reason']} "
                  f"（被 token 上限截断 = 正文永远为空）", flush=True)
