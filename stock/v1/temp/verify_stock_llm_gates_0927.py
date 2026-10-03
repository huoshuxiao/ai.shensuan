# -*- coding: utf-8 -*-
"""验证 `stock/v1/.env` 里那三道 LLM 闸门真的能吃到请求体（含负对照）。

要证的事只有一句：写进 `.env` 的 `STOCK_LLM_REASONING_EFFORT=none` 能不能穿过
`config_base.build()` → `config` → `llm_client._apply_request_defaults` → ollama `/v1`。
前两轮只量过**原生 `/api/chat`** 口（`probe_llm_qwen35_9b_0925.py`，`{"think": false}`），
那条口与本仓真实走的 `/v1` 兼容口不是一回事 ⇒ 这一轮换成正向链路的**同一个函数**
`make_openai_client()`，请求体照共享层 20 个调用点的共同形态（`temperature` +
`response_format=json_object`，**不传** max_tokens / reasoning_effort）。

三档：
  ⓪ 预热（走闸门客户端，最小 prompt）——只把 6.6GB 模型灌进内存，不参与判定，
     免得「A 档慢」被读成「不关思考就是慢」而真相只是冷加载。
  ① 负对照：裸 `openai.OpenAI`（= 加闸门之前本线拿到的客户端），90s 墙钟封顶。
  ② 正向：`make_openai_client()`（.env 的三键由共享层注入）。

判定（可反证，无恒真分支）：
  ② 必须「正文非空 + JSON 可解析 + 思考 0 字符」，且 `describe_endpoint()` 里
  看得见 `effort=none`；否则退出码 1。① 的读数只打印不断言 —— 它若意外也能
  出正文，说明"关思考"不是必需，那是**新信息**而不是这条配置的失败。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "stock", "v1", "src")))
import _bootstrap  # noqa: F402,E402
from config import LLM_BASE_URL, LLM_MODEL  # noqa: E402
from core.llm_client import describe_endpoint, make_openai_client  # noqa: F402,E402

# 与共享层自评入口同形的请求：要 JSON、字段固定（正文为空 = 拿不到分）
SYSTEM = ("你是量化因子评估器。只回 JSON，格式："
          '{"verdict": "pass|fail", "reason": "一句话"}')
USER = ("因子：SMA(量,20) 升序取最低 50 只，样本外年化超额 +13.04%，"
        "最大回撤 -53.6%。请判定是否放行，给一句理由。")
NO_GATE_TIMEOUT = 90.0   # ① 档封顶：纯粹为了不烧穿 16G 机器的时间预算


def ask(client, model, label):
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": USER}],
            temperature=0.3,
            response_format={"type": "json_object"})
    except Exception as e:
        print(f"{label}: " + json.dumps(
            {"秒": round(time.time() - t0, 1),
             "错误": f"{type(e).__name__}: {str(e)[:180]}"}, ensure_ascii=False),
            flush=True)
        return None
    m = resp.choices[0].message
    reasoning = getattr(m, "reasoning", None) or \
        getattr(m, "reasoning_content", None) or ""
    content = m.content or ""
    out = {"秒": round(time.time() - t0, 1),
           "completion_tokens": getattr(resp.usage, "completion_tokens", None),
           "finish_reason": resp.choices[0].finish_reason,
           "思考字符": len(reasoning), "正文字符": len(content),
           "正文": content[:140].replace("\n", " ")}
    try:
        out["可解析"] = isinstance(json.loads(content), dict)
    except Exception as e:
        out["可解析"] = f"否({type(e).__name__})"
    print(f"{label}: " + json.dumps(out, ensure_ascii=False), flush=True)
    return out


def main():
    from openai import OpenAI
    print(f"端点 {LLM_BASE_URL} | 模型 {LLM_MODEL}", flush=True)
    print(f"共享层视角 {describe_endpoint()}", flush=True)

    key = os.environ.get("OPENAI_API_KEY") or "local-llm"
    # ⓪ 预热：同一把客户端，只把模型拉进内存（不参与判定）
    t0 = time.time()
    ask(make_openai_client(api_key=key), LLM_MODEL, "⓪ 预热(不计入判定)")
    print(f"   预热含首次加载共 {round(time.time() - t0, 1)}s，其后 A/B 皆为热态",
          flush=True)

    # ① 负对照：与加闸门之前逐字一致（无 max_tokens / 无 reasoning_effort）
    raw = OpenAI(api_key=key, base_url=LLM_BASE_URL, timeout=NO_GATE_TIMEOUT)
    a = ask(raw, LLM_MODEL, "① 无闸门(负对照)")
    # ② 正向：三键由 stock/v1/.env 经共享层注入
    b = ask(make_openai_client(api_key=key), LLM_MODEL, "② 走 .env 闸门")

    bad = []
    ep = describe_endpoint()
    if "effort=none" not in ep:
        bad.append(f".env 没穿到共享层：{ep}")
    if b is None:
        bad.append("② 档调用直接抛错")
    else:
        if b["正文字符"] == 0:
            bad.append("② 正文为空 ⇒ 20 个 JSON 入口全会拿到空分")
        if b["可解析"] is not True:
            bad.append(f"② JSON 不可解析（{b['可解析']}，finish={b['finish_reason']}）")
        if b["思考字符"] != 0:
            bad.append(f"② 仍在思考：{b['思考字符']} 字符")
    if a is not None and a["正文字符"] > 0:
        print("\n⚠️ 读出来了一条新信息：① 无闸门也能出正文 ⇒ 关思考并非本线必需，"
              "这三道闸是保险不是救药。", flush=True)
    print("\n判定：" + ("✅ 三闸门生效，② 档正文非空且可解析" if not bad
                       else "❌ " + "；".join(bad)), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
