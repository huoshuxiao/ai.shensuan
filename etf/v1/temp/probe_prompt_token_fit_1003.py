# -*- coding: utf-8 -*-
"""量一件事：生产那条真提示词在 ollama 自己的分词器下到底占多少 token。

为什么要量（10-03 夜第二轮探针逼出来的）：我先前用 tiktoken cl100k 数出 5088 token，
就写下「默认 4096 窗口装不下 ⇒ 生产发出去的指令本身被截断」这条根因。
第二轮里 B0（默认 4096 窗口、思考开着）那 8539 字思考里**逐字复述了提示词最尾部的约束**
（"Limit in two or three sentences."、"Only reply with one set of hypothesis and reason."），
又同时引用了中段的具体数字（+27.99% at 6bp）⇒ 尾部没被砍，那我那条根因就是**拿 tiktoken 的
偏高计数冒分了 Qwen 的分词**，必须用模型自己上报的 prompt_eval_count 重新判。

三格读数（都用原生 /api/chat 非流式、max_tokens=1，只让模型prefill不许解码）：
  T_full_16384  窗口撑到 16384 ⇒ prompt_eval_count = 这条提示词的**真实** token 长度
  T_real_4096   窗口就是生产默认的 4096 ⇒ 若它比上一格**小**，说明确实截了；相等＝没截
  T_control     负对照：只发一句「hi」，证明 prompt_eval_count 报的是真条数不是常数

判据（不满足就拒绝下结论）：T_control 必须是各位数；T_real_4096 必须 ≤ 4096；
  若 T_real_4096 < T_full_16384 ⇒ 生产真的在发半截提示词（旧根因成立）
  若两格相等 ⇒ 整条提示词本来就装得进 4096（旧根因作废，病只有一個＝思考关不掉）

用法：python3.10 etf/v1/temp/probe_prompt_token_fit_1003.py
"""
import json
import os
import subprocess
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PROMPT = os.path.join(REPO, "etf/v1/temp/probe_real_prompt_hypothesis_gen_1003.txt")
URL = "http://localhost:11434/api/chat"


def post(payload, timeout):
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def one(label, text, num_ctx):
    body = {
        "model": "qwen3.5:9b",
        "messages": [{"role": "user", "content": text}],
        "stream": False,
        "think": False,          # 只要 prefill 读数，别让思考把这一格拖成长跑
        "options": {"num_ctx": num_ctx, "num_predict": 1},
    }
    t0 = __import__("time").time()
    try:
        d = post(body, 900)
    except Exception as e:
        print("%-14s num_ctx=%-6s 异常 %s: %s" % (label, num_ctx, type(e).__name__, str(e)[:160]))
        return None
    sec = round(__import__("time").time() - t0, 1)
    pe = d.get("prompt_eval_count")
    print("%-14s num_ctx=%-6s prompt_eval_count=%-6s 墙钟=%ss 正文=%r"
          % (label, num_ctx, pe, sec, (d.get("message") or {}).get("content", ""))[:200])
    return pe


def ps():
    out = subprocess.run(["curl", "-s", "--max-time", "8", "http://localhost:11434/api/ps"],
                         capture_output=True, text=True).stdout
    ms = json.loads(out or "{}").get("models", [])
    return [(m["name"], round(m["size"] / 2 ** 30, 2), m.get("context_length")) for m in ms]


def main():
    text = open(PROMPT, encoding="utf-8").read()
    print("提示词 %d 字符｜驻留起点 %s" % (len(text), ps()))
    ctl = one("T_control", "hi", 4096)
    big = one("T_full_16384", text, 16384)
    small = one("T_real_4096", text, 4096)

    print("\n[自检] 负对照 %s（须个位数）｜4096 格 %s（须 ≤4096）" % (ctl, small))
    if ctl is None or not (0 < ctl < 30) or small is None or small > 4096:
        print("尺子不自洽，拒绝判读。")
        return 1
    if big is None:
        print("16384 那格没读数，只能给半边结论：生产窗口下实占 %d token。" % small)
        return 1
    print("\n读数：整条真提示词实际 %d token；生产 4096 窗口里进了 %d token；被丢掉 %d token"
          % (big, small, max(0, big - small)))
    if big - small <= 0:
        print("判读：整条装得进 ⇒「指令被截断」这条根因作废，病只剩「思考关不掉」。")
    else:
        print("判读：确实被截 ⇒ 丢掉的是提示词的 %d%%（分词器从哪头截未测）。"
              % round(100.0 * (big - small) / big))
    print("驻留终点 %s" % (ps(),))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
