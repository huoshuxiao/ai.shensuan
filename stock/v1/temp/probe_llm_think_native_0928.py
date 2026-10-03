# -*- coding: utf-8 -*-
"""重测「开思考 + 大窗口」：上一版的**尺子读错了字段**，这一版先把原文落盘再拆。

上一跑（`think_native_test.log`）的读数：`done_reason=stop`、`eval_count=3351`、
`prompt_eval_count=376`，但"正文 0 字符 / 思考 0 字符"。**那 0 是尺子的锅不是模型的锅**：
ollama `/api/chat` 非流式把答复装在 `message` 对象里，顶层只有 `eval_count`/`done_reason`
这些统计位——我按顶层 `content`/`thinking` 去读，读到的自然是空串。生成明明发生了 3351 个词
（`done_reason=stop` 说明它自己想完了、没被截停），我却把它读成"一个字没出"。
这条坑记在这里：**先落原文，再谈解析**——这一版把整个 JSON 响应原样写盘，解析只从落盘的
原文上做，下次换字段名也不用重跑模型。

而要问的问题本身没变，且比上一版更值钱：
`/v1` 兼容口实测**不认 `num_ctx`**（运行时窗口钉在 4096 ⇒ 思考把窗口吃光、正文永远为空，
见 `probe_llm_4b_think_0928.py` 的 R1/R2/R3），而原生 `/api/chat` 认（`llama-server`
命令行里 `-c 16384` 看得见）。所以「开思考到底能不能用」只剩这一格能判：
窗口撑到 16384 后，模型自己收口时给的**正文里有没有那份 JSON 名单**。

边界（念给读数的人）：这一格走的是**原生口**，而生产 20 个调用点全走 `/v1`
（`common/src/core/llm_client.py`）。所以它答的是"这条路技术上有没有货"，不是"改一行
`.env` 就能拿到"——后者要先动服务启动参数或换调用路径，都在用户手里。
短池（10 只挑 2 只）也是刻意的：把输入压到 376 token，窗口 16384 减去它还有 ~16000 给思考，
若这样都收不了口，就没有"再大点窗口就好了"可说。

用法：
    /usr/bin/python3.10 stock/v1/temp/probe_llm_think_native_0928.py
产物：stock/v1/temp/tmp_llm_4b_think_0928/think_native/{raw_response.json, verdict.txt}
"""
import json
import os
import time

import requests

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "..", ".."))
OUT = os.path.join(_ROOT, "stock", "v1", "temp", "tmp_llm_4b_think_0928", "think_native")
B = "http://127.0.0.1:11434"
MODEL = "qwen3.5:4b"
N_PREDICT = 4096      # 上一跑实测它用 3351 词自己收口 ⇒ 4096 够，且给这一跑设个天花板
NUM_CTX = 16384
POOL = (
    "格式：名次|代码|简称|收盘价|板块|行业\n"
    "1|SZ002032|苏泊尔|39.23|主板|家电行业\n2|SZ002014|永新股份|10.96|主板|化工行业\n"
    "3|SH600987|航民股份|6.63|主板|纺织行业\n4|SZ300573|兴齐眼药|56.98|创业板|医药行业\n"
    "5|SZ300441|鲍斯股份|8.12|创业板|机械行业\n6|SZ000541|佛山发展|5.21|主板|园区发展\n"
    "7|SZ000028|一致药业|25.55|主板|医药行业\n8|BJ920670|艾芬达|25.36|北交所|家电零配\n"
    "9|SH603871|嘉友国际|12.34|主板|物流行业\n10|SZ002910|庄园牧场|10.65|主板|食品行业")
SYS = ("你是 A 股日线量化辅助系统的下单层。只回 JSON。\n"
       '输出格式：{"picks":[{"code":"SZ000001","reason":"不超过20字"}]}\n'
       "硬约束：同一板块最多 3 只；同一行业最多 1 只（行业写「未知」的不受此限）；"
       "只能从名单里选。")


def main():
    os.makedirs(OUT, exist_ok=True)
    print(f"请求：{MODEL}｜think=true｜num_ctx={NUM_CTX}｜num_predict={N_PREDICT}"
          f"｜短池 10 只挑 2 只", flush=True)
    t0 = time.time()
    r = requests.post(B + "/api/chat", timeout=1800, json={
        "model": MODEL, "stream": False, "think": True,
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": f"场次 20260924。观察名单：\n{POOL}\n\n"
                                                 "请挑 2 只。"}],
        "options": {"num_ctx": NUM_CTX, "num_predict": N_PREDICT, "temperature": 0}})
    dt = round(time.time() - t0, 1)
    r.raise_for_status()
    j = r.json()
    # ① 先原样落盘（上一版就是丢了原文，坏了尺子只能重烧 14 分钟）
    with open(os.path.join(OUT, "raw_response.json"), "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False, indent=1)
    # ② 再从落盘的原文上拆字段：顶层与 message 里同名的都看，别猜哪一个才对
    top = {k: (len(str(v)) if isinstance(v, str) else v) for k, v in j.items()}
    msg = j.get("message") or {}
    mfields = {k: (len(str(v)) if isinstance(v, str) else v) for k, v in msg.items()}
    print(f"墙钟 {dt}s｜顶层键 {top}", flush=True)
    print(f"message 键 {mfields}", flush=True)
    content = ""
    for src in (msg.get("content"), j.get("content")):
        if isinstance(src, str) and src.strip():
            content = src
            break
    thinking = ""
    for src in (msg.get("thinking"), j.get("thinking")):
        if isinstance(src, str) and src.strip():
            thinking = src
            break
    lines = [f"墙钟 {dt}s｜done_reason={j.get('done_reason')}"
             f"｜生成 {j.get('eval_count')} 词｜输入 {j.get('prompt_eval_count')} 词",
             f"正文 {len(content)} 字符｜思考 {len(thinking)} 字符",
             f"正文原文：{content[:600]}"]
    try:
        picks = json.loads(content).get("picks", [])
        codes = [str(p.get("code", "")).upper() for p in picks]
        pool = {ln.split("|")[1] for ln in POOL.splitlines() if "|" in ln}
        lines.append(f"可解析=True｜条数 {len(codes)}｜池外 "
                     f"{[c for c in codes if c not in pool] or '无'}｜名单 {codes}")
        lines.append(f"理由 {[str(p.get('reason', '')) for p in picks]}")
    except Exception as e:
        lines.append(f"可解析=False（{type(e).__name__}）")
    verdict = "\n".join(lines)
    print("\n" + verdict, flush=True)
    if thinking:
        print(f"\n思考头 300：{thinking[:300]}", flush=True)
        print(f"\n思考尾 300：{thinking[-300:]}", flush=True)
    with open(os.path.join(OUT, "verdict.txt"), "w", encoding="utf-8") as f:
        f.write(verdict + "\n\n思考全文：\n" + thinking + "\n")
    print(f"\n（产物：raw_response.json / verdict.txt @ {OUT}；原文已落盘，"
          "换字段名不用再重跑模型）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
