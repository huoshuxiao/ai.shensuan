#!/usr/bin/env python3.10
# -*- coding: utf-8 -*-
"""换模型前的代价探针（09-25）：qwen3.5:9b 在本机（纯 CPU）上单次调用要多久。

为什么先量再改配置：这台机器的 ollama 被 systemd 用 `CUDA_VISIBLE_DEVICES=-1
OLLAMA_NUM_GPUS=0` 锁在 CPU 上（965M 只有 2GB 显存），而股票线一轮 RD-Agent 循环
≈33 分钟起、每次循环要打好几次 LLM 调用（hypothesis + CoSTEER 演化 ×CoSTEER_MAX_LOOP
+ 裁判）。09-22 实测同尺寸 8B 在 CPU 上「单次平凡调用 7.5min」，所以**换模型的不是
效果而是墙钟**——必须先知道单次调用多少钱，才谈得上要不要把循环开着跑。

三项读数各自的含义：
  ① 默认（模型自带思考）——thinking 是 qwen3.5 的 advertised capability，若默认开启，
     思考 token 也计入生成，慢的就是这一档；
  ② `think:false`——同一模型关掉思考，拿它和 ① 的差 = 「思考」这道税；
  ③ 现有生产模型 qwen2.5:7b 同 prompt 作对照，才是「换不换」的比较基准。
只发一句话、限 num_predict，不落任何生产文件。
"""

import json
import subprocess
import time
import urllib.request

URL = "http://localhost:11434/api/chat"
PROMPT = "只回一个字：好"


def call(model, extra=None, num_predict=64):
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}],
            "stream": False, "options": {"num_predict": num_predict}}
    body.update(extra or {})
    t0 = time.time()
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as resp:
        out = json.load(resp)
    dt = time.time() - t0
    msg = out.get("message") or {}
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "")
    return {"model": model, "extra": extra or {}, "秒": round(dt, 1),
            "eval_count": out.get("eval_count"),
            "生成速度 tok/s": round((out.get("eval_count") or 0) / dt, 2) if dt else None,
            "thinking 字符": len(thinking), "正文": content[:60]}


def main():
    for label, model, extra in (
            ("① 9b 默认（带思考）", "qwen3.5:9b", None),
            ("② 9b think=false", "qwen3.5:9b", {"think": False}),
            ("③ 现生产 7b 对照", "qwen2.5:7b", None)):
        try:
            r = call(model, extra)
            print(f"{label}: {json.dumps(r, ensure_ascii=False)}", flush=True)
        except Exception as e:
            print(f"{label}: 失败 {type(e).__name__} {e}", flush=True)


if __name__ == "__main__":
    main()
