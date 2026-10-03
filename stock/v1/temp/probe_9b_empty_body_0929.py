# -*- coding: utf-8 -*-
"""四臂探针：9b 走 RD-Agent 那条 LiteLLM 路「正文恒空」到底是哪一环。

现场事实（09-29 上午，loop_qwen3.5_1loop_9bbase_0929.log 逐条数出来的）：
274 次回复里 272 次正文为空，唯一一条有字的是 689 字符的假设 JSON。
RD-Agent 读的 .env 里既没有 max_tokens 也没有 reasoning 键，
所以怀疑是「qwen3.5 默认思考把 /v1 的 4096 窗口吃光 ⇒ content 空」。

四臂各打一次真调用，判据是**字节级**的：
  甲 = 现场原样（不传任何参数）         → 若 content 空而 reasoning_content 有字，假设成立
  乙 = reasoning_effort="none"          → litellm 是否认这个值、ollama 是否转发
  丙 = extra_body={"think": False}      → ollama 原生关思考开关，能否从 openai 兼容层递进去
  丁 = extra_body={"options":{"num_ctx":8192}} → 窗口加倍，够不够把正文挤出来
只读探针：不写生产任何路径，不动 site-packages。
"""
import json
import sys
import time

import litellm

MODEL = "ollama_chat/qwen3.5:9b"
MSG = [{"role": "user", "content": "只回一个 JSON：{\"answer\": 1729}"}]

ARMS = {
    "甲 原样": {},
    "乙 effort=none": {"reasoning_effort": "none"},
    "丙 think=False": {"extra_body": {"think": False}},
    "丁 num_ctx=8192": {"extra_body": {"options": {"num_ctx": 8192}}},
}


def main():
    print("litellm", getattr(litellm, "__version__", "?"), flush=True)
    for tag, kw in ARMS.items():
        t0 = time.time()
        try:
            r = litellm.completion(model=MODEL, messages=MSG, temperature=0.5,
                                   timeout=300, **kw)
            m = r.choices[0].message
            content = (m.content or "")
            rc = getattr(m, "reasoning_content", None) or ""
            # finish_reason 是关键读数：length = 被窗口截断
            fr = r.choices[0].finish_reason
            print(f"{tag:<18} 用 {time.time() - t0:>6.1f}s  content={len(content):>5} 字符　"
                  f"reasoning={len(rc):>5} 字符　finish={fr}　"
                  f"正文开头={content[:40]!r}", flush=True)
        except Exception as e:
            print(f"{tag:<18} 用 {time.time() - t0:>6.1f}s 调用失败 "
                  f"{type(e).__name__}: {str(e)[:140]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
