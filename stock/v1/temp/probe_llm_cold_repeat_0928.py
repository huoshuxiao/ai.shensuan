# -*- coding: utf-8 -*-
"""冷态可复现性：把模型**每次先卸载**再打同一份请求，N 次，看名单会不会换人。

为什么要单独问这一格：`probe_llm_model_grid_0928.py` 量到 `qwen2.5-coder:7b` 的
G1（冷加载那一次）与 G2/G3（模型已在内存里连打两次）给出**两份不同名单**，而 G2 与 G3
逐字相同。也就是"热态稳定、冷态只有一格样本"。这两个读数放一起有两种解释，必须分开：
  ① 冷/热差异（首次装载时的分块与批处理不同 ⇒ 同一份权重也能算出不同 logits）；
  ② 这个模型本身在 `temperature=0` 下就不逐位稳定。
判据很硬：**日更链每晚那一次调用永远是冷态**（模型晚上不常驻，实测 9b 冷 534.8s / 4b 冷
119.5s / coder:7b 冷 280.0s 都是装载时间），所以"冷态能不能复现"直接决定这份名单能不能
进归档链路——09-27 那张第④账单（`temperature=0` 连打两次逐位相同）用的就是这一条：
不复现 = 事后说不清是判据的功劳还是模型的运气，`qwen3.5:9b` 当时 6/6 次跨进程逐位相同才过关。

做法：每轮先 `POST /api/chat {"keep_alive":0}` 把模型从内存里卸掉，`/api/ps` 确认空了
（**只等不看字节就下结论是假绿**），再打那一版生产请求体（`effort=none` + 1024 + JSON 约束，
与 G1 逐字同体），记下墙钟与名单。N 轮之间请求体一字不动。

只读生产归档、不装面板、不改任何 `.env`/配置；卸载只针对本脚本自己装载的模型。

用法：
    /usr/bin/python3.10 stock/v1/temp/probe_llm_cold_repeat_0928.py qwen2.5-coder:7b 3
产物：stock/v1/temp/tmp_llm_model_grid_0928/<模型名>/cold_repeat.jsonl
"""
import json
import os
import sys
import time

import requests
import pandas as pd

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "stock", "v1", "src"))
os.environ["STOCK_LLM_MAX_TOKENS"] = "0"
os.environ["STOCK_LLM_REASONING_EFFORT"] = ""   # 与 G1 同体：闸门由请求体自己写

import _bootstrap  # noqa: F402,E402  必须先挂 sys.path，否则 `config` 解析到无名命名空间包
from config import LLM_BASE_URL  # noqa: E402
from core.llm_client import make_openai_client  # noqa: E402

sys.path.insert(0, os.path.join(_ROOT, "stock", "v1", "temp"))
from probe_llm_model_grid_0928 import ROWS  # noqa: E402  取 G1 那一格请求体
from probe_llm_4b_think_0928 import load_pool, pool_text  # noqa: E402
from probe_llm_picks_0927 import check_compliance  # noqa: E402
from llm_evidence_common import SYSTEM_ORDER  # noqa: E402

OUT_ROOT = os.path.join(_ROOT, "stock", "v1", "temp", "tmp_llm_model_grid_0928")
B = "http://127.0.0.1:11434"
G1 = ROWS[0][1]      # ("G1 带effort=none·1024·json", {...}) 的请求体参数


def resident():
    try:
        d = requests.get(B + "/api/ps", timeout=5).json()
    except Exception:
        return None
    return [(m["model"], m.get("context_length")) for m in d.get("models", [])]


def unload(model, wait=90.0):
    """`keep_alive=0` 请求即卸载指令；轮询 /api/ps 直到它真不在内存里。
    返回 (是否确认卸载, 等待秒)。**没确认就开打 = 这一轮其实是热态，读数作废。**"""
    try:
        requests.post(B + "/api/chat", timeout=30,
                      json={"model": model, "messages": [], "keep_alive": 0})
    except Exception as e:
        print(f"  卸载请求抛错（继续按 /api/ps 判）：{type(e).__name__}", flush=True)
    t0 = time.time()
    while time.time() - t0 < wait:
        r = resident()
        if r is not None and not any(m[0] == model for m in r):
            return True, round(time.time() - t0, 1)
        time.sleep(2)
    return False, round(time.time() - t0, 1)


def main():
    args = [a for a in sys.argv[1:]]
    model = args[0] if args else "qwen2.5-coder:7b"
    n = int(args[1]) if len(args) > 1 else 3
    tag, df, det = load_pool()
    rows = df.set_index("code")
    user_prompt = (f"场次 {tag}（收盘价截面，T+1 开盘买）。观察名单：\n{pool_text(df)}\n\n"
                   "请挑 5 只，按你想要的优先级从高到低排列。")
    cl = make_openai_client(max_retries=0, timeout=900, api_key="ollama")
    print(f"模型 {model}｜端点 {LLM_BASE_URL}｜场次 {tag}｜池子 {len(df)} 只｜"
          f"生产 5 只={det}｜轮数 {n}", flush=True)
    print("请求体固定为 G1 那一格：effort=none + max_tokens=1024 + JSON 约束 + temperature=0\n",
          flush=True)
    out = []
    for i in range(1, n + 1):
        ok, wt = unload(model)
        print(f"[第 {i} 轮] 卸载确认={ok}（等 {wt}s）｜当前驻留 {resident()}", flush=True)
        if not ok:
            print("  ❌ 没能确认卸载 ⇒ 这一轮读数会混进热态，**不记入可复现性**，续跑下一轮",
                  flush=True)
            out.append({"轮": i, "卸载确认": False})
            continue
        kw = dict(model=model,
                  messages=[{"role": "system", "content": SYSTEM_ORDER},
                            {"role": "user", "content": user_prompt}],
                  temperature=0.0, max_tokens=G1["max_tokens"])
        if G1["effort"]:
            kw["reasoning_effort"] = G1["effort"]
        if G1["json_mode"]:
            kw["response_format"] = {"type": "json_object"}
        t0 = time.time()
        try:
            resp = cl.chat.completions.create(**kw)
        except Exception as e:
            print(f"  抛错 {type(e).__name__}: {str(e)[:160]} ({round(time.time()-t0,1)}s)",
                  flush=True)
            out.append({"轮": i, "卸载确认": True, "秒": round(time.time() - t0, 1),
                        "错误": f"{type(e).__name__}: {str(e)[:160]}"})
            continue
        dt = round(time.time() - t0, 1)
        content = resp.choices[0].message.content or ""
        rec = {"轮": i, "卸载确认": True, "秒": dt, "驻留窗口": (resident() or [["", None]])[0][1],
               "出词": getattr(resp.usage, "completion_tokens", None),
               "finish": resp.choices[0].finish_reason, "原文": content}
        try:
            picks = json.loads(content).get("picks", [])
            codes = [str(p.get("code", "")).upper() for p in picks]
        except Exception as e:
            rec.update({"可解析": f"否({type(e).__name__})", "picks": None})
            print("  " + json.dumps({k: v for k, v in rec.items() if k != "原文"},
                                    ensure_ascii=False), flush=True)
            out.append(rec)
            continue
        outside, bo, idup = check_compliance(codes, rows)
        rec.update({"可解析": True, "picks": codes, "池外": outside,
                    "板块超限": bo, "行业重复": idup,
                    "与生产重合": len(set(codes) & set(det))})
        print("  " + json.dumps({k: v for k, v in rec.items() if k != "原文"},
                                ensure_ascii=False), flush=True)
        out.append(rec)
    unload(model)   # 收尾把内存还给下一件事（不托着 4.7GB 走人）
    good = [r for r in out if r.get("picks")]
    seqs = {tuple(r["picks"]) for r in good}
    out_dir = os.path.join(OUT_ROOT, model.replace(":", "_"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "cold_repeat.jsonl"), "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n===== 冷态可复现性（{model}）=====", flush=True)
    print(f"有效轮次 {len(good)}/{n}（卸载未确认或不可解析的不计入）｜"
          f"不同名单 {len(seqs)} 种", flush=True)
    for s, k in sorted([(s, sum(1 for r in good if tuple(r['picks']) == s)) for s in seqs]):
        print(f"  {list(s)} ← {k} 轮", flush=True)
    print(f"判据：{n} 轮全部同一条请求体 ⇒ 只有 1 种名单才算复现过关"
          f"（过关={len(seqs) == 1 and len(good) >= 2}）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
