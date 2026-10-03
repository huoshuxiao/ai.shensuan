# -*- coding: utf-8 -*-
"""甲暖 / 乙暖 成对对照：把上一场「甲 原样=300s 超时（没有读数）」变成真读数。

上一场（probe_9b_empty_body_0929.py）的缺陷：四条臂按顺序跑，甲排在第一个付了冷启动
载模型的代价，300s 到点只留下 Timeout，既没说 content 空、也没说 finish_reason。
乙/丙 6s 内回正文，甲却「无读数」⇒ 严格讲我只证明了「关思考的两条臂能回正文」，
没证明「原样这条臂回不了正文」。这一场就补这一格。

两臂同条件：模型此刻已驻留（/api/ps 实测 qwen3.5:9b 在内存里），同一句提问、
同一把尺子（content 字符数 / reasoning_content 字符数 / finish_reason），只是超时
放到 1200s——纯 CPU 下把 4096 窗口写满思考约 13~15 分钟，上一场就是死在这个时间里。

判据不恒真：两臂唯一差的是 reasoning_effort 这一个键；若甲也回正文，则上一场
「272/274 空正文」的根因不在思考开关，我这条补丁就不该打。
"""
import json
import time

import litellm

MODEL = "ollama_chat/qwen3.5:9b"
MSG = [{"role": "user", "content": "只回一个 JSON：{\"answer\": 1729}"}]
TIMEOUT = 1200

ARMS = [
    ("甲暖 原样", {}),
    ("乙暖 effort=none", {"reasoning_effort": "none"}),
]

print(f"litellm {getattr(litellm, '__version__', '?')}　模型已驻留与否见 /api/ps　超时={TIMEOUT}s", flush=True)
for label, kw in ARMS:
    t0 = time.time()
    try:
        r = litellm.completion(model=MODEL, messages=MSG, temperature=0.5, timeout=TIMEOUT, **kw)
        m = r.choices[0].message
        rc = getattr(m, "reasoning_content", None)
        print(f"{label:<18} 用 {time.time()-t0:7.1f}s  content={len(m.content or ''):5d} 字符　"
              f"reasoning={len(rc or ''):5d} 字符　finish={r.choices[0].finish_reason}　"
              f"正文开头={json.dumps((m.content or '')[:40], ensure_ascii=False)}", flush=True)
    except Exception as e:
        print(f"{label:<18} 用 {time.time()-t0:7.1f}s 调用失败 {type(e).__name__}: {str(e)[:160]}", flush=True)
