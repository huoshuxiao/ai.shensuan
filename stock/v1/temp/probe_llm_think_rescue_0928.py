# -*- coding: utf-8 -*-
"""开思考的最后一根杠杆：把上下文窗口从 4096 撑大，看正文能不能出来。

上一跑（`probe_llm_4b_think_0928.py`）量到的硬事实：4b 开思考时 **不设 max_tokens 也
被截停在 `finish=length`** —— 出词 2642、`reasoning` 6156 字符、**正文 0 字符**。
算术对得上：`api/ps` 显示运行时 `context_length=4096`，提示词吃 1454 token ⇒ 剩下
4096−1454=2642 全给思考，思考还没想完窗口就满了。所以「开思考」这件事真正的墙不是
`MAX_TOKENS=1024`，是**运行时上下文窗口**。模型文件本身支持 262144（`api/show` 里
`qwen35.context_length`），4096 是 ollama 侧的默认值 ⇒ 唯一没试的旋钮是 `num_ctx`。

但先别急着花钱：这颗旋钮在**两条路上认不认**是未知的。
`/v1` 兼容口只透传 OpenAI 标准字段，ollama 私有 options（`num_ctx` 这类）常被静默忽略；
若真被忽略，撑大窗口的请求会照旧在 4096 处截停，那这笔 30 分钟的开销就是纯浪费。
所以第一步是**便宜的否证**：故意把 `num_ctx` 压到 1000，喂一段 1454 token 的池子——
窗口若真生效，服务端必须**报错**（prompt 超长）；若返回 200，说明这颗旋钮在这条路上是装饰品。
两条路各测一次（K1=/v1、K2=/api/chat 原生口），只花几秒。

生效那条路才拿来跑正题：
  R7  4b + `reasoning_effort=low` + 窗口撑大 + 1024 封顶 + json  ← 低档思考能不能塞进现闸门
  R6  4b + 开思考 + 窗口撑大 + 不封顶 + json                     ← 想透之后到底给不给正文
R6 若仍在 1800s 内不收敛 ⇒ 本机纯 CPU 上「开思考出名单」判死，连大窗口都救不回来。

内存代价先算后跑：KV cache 随窗口线性长，4b 从 4096→16384 大约多占 1~2GB；
这一跑进程里**没有 3GB 面板**（只读归档 csv），且严格串行、不同时挂两个模型。

用法：
    /usr/bin/python3.10 stock/v1/temp/probe_llm_think_rescue_0928.py --knob-only
    /usr/bin/python3.10 stock/v1/temp/probe_llm_think_rescue_0928.py
产物：stock/v1/temp/tmp_llm_4b_think_0928/{rescue.csv, rescue_raw.jsonl, rescue.log}
"""
import json
import os
import sys
import time

import pandas as pd
import requests

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "stock", "v1", "src"))
# 同上一跑：故意关掉三道闸门（进程环境变量优先于 .env），请求体每行自己写。
os.environ["STOCK_LLM_MAX_TOKENS"] = "0"
os.environ["STOCK_LLM_REASONING_EFFORT"] = ""

import _bootstrap  # noqa: E402,F401  必须先挂路径，否则 `config` 解析到无名命名空间包
from config import LLM_BASE_URL  # noqa: E402
from core.llm_client import make_openai_client  # noqa: E402
from llm_evidence_common import SYSTEM_ORDER  # noqa: E402
sys.path.insert(0, os.path.join(_ROOT, "stock", "v1", "temp"))
from probe_llm_4b_think_0928 import load_pool, pool_text  # noqa: E402
from probe_llm_picks_0927 import check_compliance  # noqa: E402

OUT = os.path.join(_ROOT, "stock", "v1", "temp", "tmp_llm_4b_think_0928")
RAW_LOG = os.path.join(OUT, "rescue_raw.jsonl")
BIGNUM = 16384     # 撑大后的窗口：思考实测要 >2642 token，给 4 倍余量
SMALL = 1000       # 否证用的窗口：比池子的 1454 token 小 ⇒ 生效必报错
MODEL = "qwen3.5:4b"
API_CHAT = LLM_BASE_URL.rsplit("/v1", 1)[0] + "/api/chat"


def _append(rec):
    os.makedirs(OUT, exist_ok=True)
    with open(RAW_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_live_context():
    """从 /api/ps 读**当前**运行时窗口 —— 只在调用进行中有效，所以要在请求期间抓一次"""
    try:
        d = requests.get("http://127.0.0.1:11434/api/ps", timeout=5).json()
        for m in d.get("models", []):
            if m.get("model") == MODEL:
                return m.get("context_length")
    except Exception:
        pass
    return None


def knob_v1(user_prompt):
    """K1：`/v1` 兼容口 + `extra_body={"num_ctx": 1000}`，池子有 1454 token ⇒ 生效必须报错"""
    cl = make_openai_client(max_retries=0, timeout=120, api_key="ollama")
    t0 = time.time()
    try:
        cl.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM_ORDER},
                      {"role": "user", "content": user_prompt}],
            temperature=0.0, max_tokens=1,
            extra_body={"num_ctx": SMALL})
        return {"路": "K1 /v1 extra_body", "秒": round(time.time() - t0, 1),
                "生效": False, "读数": "HTTP 200（超长没被拒 ⇒ num_ctx 被静默忽略）"}
    except Exception as e:
        return {"路": "K1 /v1 extra_body", "秒": round(time.time() - t0, 1),
                "生效": True, "读数": f"{type(e).__name__}: {str(e)[:180]}"}


def knob_native(user_prompt):
    """K2：ollama 原生 `/api/chat` + `options.num_ctx=1000` —— 兜底路，兼容口不认时用它"""
    t0 = time.time()
    try:
        r = requests.post(API_CHAT, timeout=120, json={
            "model": MODEL, "stream": False, "think": True,
            "messages": [{"role": "system", "content": SYSTEM_ORDER},
                         {"role": "user", "content": user_prompt}],
            "options": {"num_ctx": SMALL, "num_predict": 1, "temperature": 0}})
        r.raise_for_status()
        return {"路": "K2 /api/chat options", "秒": round(time.time() - t0, 1),
                "生效": False, "读数": "HTTP 200（同上）"}
    except Exception as e:
        return {"路": "K2 /api/chat options", "秒": round(time.time() - t0, 1),
                "生效": True, "读数": f"{type(e).__name__}: {str(e)[:180]}"}


def call_v1(cl, user_prompt, rows, det, tag, effort, max_tokens, ctx):
    kw = dict(model=MODEL,
              messages=[{"role": "system", "content": SYSTEM_ORDER},
                        {"role": "user", "content": user_prompt}],
              temperature=0.0, response_format={"type": "json_object"},
              extra_body={"num_ctx": ctx})
    if effort:
        kw["reasoning_effort"] = effort
    if max_tokens:
        kw["max_tokens"] = max_tokens
    rec = {"档": tag, "effort": effort or "开(默认)", "封顶": max_tokens or "无",
           "num_ctx": ctx}
    t0 = time.time()
    try:
        resp = cl.chat.completions.create(**kw)
    except Exception as e:
        rec.update({"秒": round(time.time() - t0, 1),
                    "错误": f"{type(e).__name__}: {str(e)[:200]}"})
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        _append(rec)
        return rec
    live = read_live_context()          # 请求还在飞行中时才抓得到运行时窗口
    msg = resp.choices[0].message
    dump = msg.model_dump()
    extras = {k: len(str(v)) for k, v in dump.items()
              if k not in ("role", "content") and v}
    content = msg.content or ""
    rec.update({"秒": round(time.time() - t0, 1), "运行时窗口": live,
                "finish": resp.choices[0].finish_reason,
                "出词": getattr(resp.usage, "completion_tokens", None),
                "正文字符": len(content), "附加字段": extras, "原文": content})
    try:
        picks = json.loads(content).get("picks", [])
        codes = [str(p.get("code", "")).upper() for p in picks]
    except Exception as e:
        rec["可解析"] = f"否({type(e).__name__})"
        print(json.dumps({k: v for k, v in rec.items() if k != "原文"},
                         ensure_ascii=False), flush=True)
        _append(rec)
        return rec
    outside, board_over, ind_dup = check_compliance(codes, rows)
    rec.update({"可解析": True, "条数": len(codes), "池外": len(outside),
                "板块超限": len(board_over), "行业重复": len(ind_dup),
                "与生产重合": len(set(codes) & set(det)), "picks": codes})
    print(json.dumps({k: v for k, v in rec.items() if k != "原文"},
                     ensure_ascii=False), flush=True)
    _append(rec)
    return rec


def main():
    tag, df, det = load_pool()
    rows = df.set_index("code")
    user_prompt = (f"场次 {tag}（收盘价截面，T+1 开盘买）。观察名单：\n{pool_text(df)}\n\n"
                   "请挑 5 只，按你想要的优先级从高到低排列。")
    print(f"场次 {tag}｜池子 {len(df)} 只｜生产 5 只={det}｜原生口 {API_CHAT}", flush=True)
    print("\n===== 第一步：num_ctx 这颗旋钮在两条路上认不认（只花几秒）=====", flush=True)
    k1, k2 = knob_v1(user_prompt), knob_native(user_prompt)
    for k in (k1, k2):
        print(f"{k['路']}: 生效={k['生效']}｜{k['读数']}（{k['秒']}s）", flush=True)
    if "--knob-only" in sys.argv:
        print("（--knob-only：只验旋钮，没跑正题）", flush=True)
        return 0
    path = "/v1" if k1["生效"] else ("/api/chat" if k2["生效"] else None)
    if path is None:
        print("❌ 两条路都不认 num_ctx ⇒ 窗口撑不开，「开思考」到此判死（不用花 30 分钟）",
              flush=True)
        return 1
    print(f"\n===== 第二步：用 {path} 这条生效的路跑正题 =====", flush=True)

    out = []
    if path == "/v1":
        cl = make_openai_client(max_retries=0, timeout=1800, api_key="ollama")
        out.append(call_v1(cl, user_prompt, rows, det,
                           "R7 4b·effort=low·窗口16384·1024·json", "low", 1024, BIGNUM))
        out.append(call_v1(cl, user_prompt, rows, det,
                           "R6 4b·开思考·窗口16384·无上限·json", None, None, BIGNUM))
    else:
        # 兼容口不认时走原生口：思考在 `thinking` 字段、正文在 `content`，两者都能单独量
        def native(tag, effort, n_predict):
            body = {"model": MODEL, "stream": False, "think": True,
                    "messages": [{"role": "system", "content": SYSTEM_ORDER},
                                 {"role": "user", "content": user_prompt}],
                    "options": {"num_ctx": BIGNUM, "temperature": 0}}
            if effort:
                body["think"] = {"level": effort}
            if n_predict:
                body["options"]["num_predict"] = n_predict
            rec = {"档": tag, "effort": effort or "开(默认)", "封顶": n_predict or "无",
                   "num_ctx": BIGNUM}
            t0 = time.time()
            try:
                r = requests.post(API_CHAT, timeout=1800, json=body)
                r.raise_for_status()
                j = r.json()
            except Exception as e:
                rec.update({"秒": round(time.time() - t0, 1),
                            "错误": f"{type(e).__name__}: {str(e)[:200]}"})
                print(json.dumps(rec, ensure_ascii=False), flush=True)
                _append(rec)
                return rec
            content = j.get("content") or ""
            rec.update({"秒": round(time.time() - t0, 1), "运行时窗口": read_live_context(),
                        "done_reason": j.get("done_reason"),
                        "出词": (j.get("eval_count") or 0),
                        "正文字符": len(content),
                        "思考字符": len(j.get("thinking") or ""), "原文": content})
            try:
                codes = [str(p.get("code", "")).upper()
                         for p in json.loads(content).get("picks", [])]
            except Exception as e:
                rec["可解析"] = f"否({type(e).__name__})"
                print(json.dumps({k: v for k, v in rec.items() if k != "原文"},
                                 ensure_ascii=False), flush=True)
                _append(rec)
                return rec
            outside, bo, idup = check_compliance(codes, rows)
            rec.update({"可解析": True, "条数": len(codes), "池外": len(outside),
                        "板块超限": len(bo), "行业重复": len(idup),
                        "与生产重合": len(set(codes) & set(det)), "picks": codes})
            print(json.dumps({k: v for k, v in rec.items() if k != "原文"},
                             ensure_ascii=False), flush=True)
            _append(rec)
            return rec
        out.append(native("R7 4b·低档思考·窗口16384·1024·原生口", "low", 1024))
        out.append(native("R6 4b·开思考·窗口16384·无上限·原生口", None, None))
    pd.DataFrame(out).to_csv(os.path.join(OUT, "rescue.csv"),
                             index=False, encoding="utf-8-sig")

    print("\n===== 大窗口能不能救回「开思考」=====", flush=True)
    for r in out:
        if "错误" in r:
            print(f"{r['档']}: ❌ {r['秒']}s {r['错误'][:130]}", flush=True)
            continue
        line = (f"{r['档']}: {r['秒']}s｜运行时窗口={r.get('运行时窗口')}"
                f"｜截停={r.get('finish') or r.get('done_reason')}｜出词 {r['出词']}"
                f"｜正文 {r['正文字符']} 字符｜可解析={r.get('可解析')}")
        print(line, flush=True)
        if "条数" in r:
            print(f"    条数 {r['条数']}/5｜池外 {r['池外']}｜破板块 {r['板块超限']}"
                  f"｜破行业 {r['行业重复']}｜与生产重合 {r['与生产重合']}/5", flush=True)
    print("\n判读（跑之前定死）：", flush=True)
    print("  ① 两行都「正文 0 字符 / 截停」⇒ 开思考在本机判死，讨论回到「只换模型、闸门不动」；",
          flush=True)
    print("  ② R7 成而 R6 废 ⇒ 能用的是「低档思考」，代价是 1024 封顶仍可能不够（要拨 MAX_TOKENS）；",
          flush=True)
    print("  ③ R6 成 ⇒ 开思考技术上可行，但这一格秒数就是每晚的额外墙钟，"
          "且窗口撑大要多占 1~2GB 内存 —— 值不值由用户拍。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
