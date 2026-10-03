# -*- coding: utf-8 -*-
"""B4 探针：9b 在 RD-Agent 那条支线上，四条通道哪条能出「合法正文」。

背景（10-03 复查推翻旧结论）：官方支线换成 qwen3.5:9b 后，7200s 里连一轮都没走完
（只到 Loop 0 Step 0，6 次「Failed to continue the conversation」），而 qwen2.5:7b
同尺走完约两轮。根因不是"慢"是"关不掉思考"——RD-Agent 侧读的是 LITELLM_ 前缀那套
设置，其 reasoning_effort 字段类型是 Literal["low","medium","high"] | None
（实测 LITELLM_REASONING_EFFORT=none 直接 pydantic 报错），且 litellm 的 ollama 转换层
只在 reasoning_effort 非空时才发 think、发出去的还是 think=True。⇒ 环境变量这条路
写不出「关」字，只能另找通道。本探针就是来量哪条通道真能用、代价多少秒。

四条臂（同一条提示词，各打一次真请求，流式消费方式与 rdagent 一致）：
  A0  现状臂：ollama_chat/… 不加任何参数 ＝ 今天生产在发的那一句话（应当最难受）
  A1  think=False 走 litellm 顶层 kwargs：验「这个参数到底进不进得了 /api/chat」
  A2  num_ctx=16384：验原生口能不能把窗口撑大（09-28 实测撑大后能出正文，~660s）
  A3  换口臂：openai/… + api_base=/v1 + reasoning_effort="none"
      （研究线 09-25 实测唯一生效的写法，但 /v1 把窗口钉死 4096、超长只会静默截断）
  A4  对照臂：openai/… + api_base=/v1，不设 reasoning_effort
      （能失败的地方在这：若它和 A3 一样快，说明起作用的是"口"不是那个字段）

每臂读数：墙钟秒 / 正文字符 / 思考字符 / finish_reason / 正文能否 json.loads /
调用后 ollama /api/ps 的驻留窗口。任一臂抛异常就记异常类型与首 200 字符——
「参数被 litellm 拒了」本身就是这条通道不存在的证据。

用法（必须在 conda rdagent 环境里跑，本机只有那边装了 litellm）：
  env PYTHONNOUSERSITE=1 /home/sunwenkun/miniconda3/envs/rdagent/bin/python \
      etf/v1/temp/probe_rdagent_9b_channel_1003.py
"""
import json
import os
import subprocess
import time

import litellm

MODEL = "qwen3.5:9b"
OLLAMA_NATIVE = "ollama_chat/" + MODEL
OLLAMA_V1_BASE = "http://localhost:11434/v1"
V1_ARM = "openai/" + MODEL

# 一条贴近 RD-Agent 假设生成形状的提示词：要求只回合法 JSON、带两个字段。
# 不写「请思考」之类诱导词；长度 ~600 token，够把「窗口钉在 4096」这件事露出来。
SYSTEM = (
    "You are an expert quant researcher designing new alpha factors for ETFs. "
    "Respond with a single JSON object only. No markdown fences, no prose "
    "before or after the JSON. NO trailing comma."
)
USER = (
    "The dataframe `df` has columns open, high, low, close, volume indexed by "
    "(datetime, instrument). Available deterministic operators are: ma(df,n), "
    "std(df,n), max(df,n), min(df,n), delay(df,n), ts_sum(x,n), ts_mean(x,n), "
    "ts_rank(x,n), rank(x), corr(x,y,n).\n"
    "Propose exactly 2 new ETF volume-based factors that are not simple moving "
    "averages. For each factor give: hypothesis (one sentence explaining why it "
    "should predict future returns), expression (one of the operators above), "
    "and direction (either 1 or -1). "
    'Return JSON of the shape {"factors": [{"hypothesis": "...", '
    '"expression": "...", "direction": 1}]}'
)

MESSAGES = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER}]


def ollama_ps():
    """读 /api/ps：[(名字, 驻留 GB, context_length)]——窗口读数只认这个，不认 HTTP 200"""
    try:
        out = subprocess.run(["curl", "-s", "--max-time", "8",
                              "http://localhost:11434/api/ps"],
                             capture_output=True, text=True).stdout
        models = json.loads(out or "{}").get("models", [])
        return [(m["name"], round(m["size"] / 2 ** 30, 2),
                 m.get("context_length")) for m in models]
    except Exception as e:
        return [("读失败", type(e).__name__, None)]


def mem():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return round(int(line.split()[1]) / 2 ** 20, 2)
    return None


def consume(resp):
    """按 rdagent backend 的读法消费流：只认 delta.content，思考另记一列"""
    content, reasoning, finish = "", "", None
    for chunk in resp:
        try:
            choice = chunk["choices"][0]
        except (KeyError, IndexError, TypeError):
            continue
        delta = choice.get("delta") or {}
        if choice.get("finish_reason"):
            finish = choice["finish_reason"]
        content += delta.get("content") or ""
        reasoning += (delta.get("reasoning_content")
                      or delta.get("thinking") or "")
    return content, reasoning, finish


def arm(tag, model, cap, **extra):
    """打一臂；任何异常都如实记成读数，不重试（重试次数正是生产的代价之一）"""
    print(f"\n=== {tag}  model={model}  cap={cap}s  extra={extra}")
    print(f"    [起场] MemAvailable={mem()}GB  驻留={ollama_ps()}")
    t0 = time.time()
    try:
        resp = litellm.completion(model=model, messages=MESSAGES,
                                  stream=True, request_timeout=cap, **extra)
        content, reasoning, finish = consume(resp)
    except Exception as e:
        sec = time.time() - t0
        print(f"    ✗ {tag} {type(e).__name__} @ {sec:.1f}s :: "
              f"{str(e)[:200]}")
        print(f"    [终场] 驻留={ollama_ps()}")
        return {"arm": tag, "sec": round(sec, 1), "error": type(e).__name__}
    sec = time.time() - t0
    ok = None
    try:
        json.loads(content)
        ok = True
    except Exception:
        ok = False
    print(f"    {tag}: 墙钟={sec:.1f}s 正文={len(content)}字符 "
          f"思考={len(reasoning)}字符 finish={finish} JSON可解析={ok}")
    print(f"    正文首 160 字: {content[:160]!r}")
    print(f"    [终场] MemAvailable={mem()}GB  驻留={ollama_ps()}")
    return {"arm": tag, "sec": round(sec, 1), "content": len(content),
            "reasoning": len(reasoning), "finish": finish, "json_ok": ok}


def main():
    # litellm 对不支持的参数默认报错而不是丢弃；这里保持默认，让「通道不存在」暴露出来
    litellm.drop_params = False
    print("== B4 探针：9b 四通道实测（一次/臂，只发请求不写盘）==")
    print(f"    ollama 版本: "
          f"{subprocess.run(['ollama', '--version'], capture_output=True, text=True).stdout.strip()}")
    results = [
        arm("A0 现状(ollama_chat 裸调)", OLLAMA_NATIVE, 300),
        arm("A1 think=False", OLLAMA_NATIVE, 300, think=False),
        arm("A3 openai+/v1+effort=none", V1_ARM, 300,
            api_base=OLLAMA_V1_BASE, api_key="ollama",
            reasoning_effort="none"),
        arm("A4 openai+/v1 裸调(对照)", V1_ARM, 300,
            api_base=OLLAMA_V1_BASE, api_key="ollama"),
        # A2 单独放最后：09-28 那一次实测 660s，给它 720s 上限，别把前三臂挤掉
        arm("A2 num_ctx=16384", OLLAMA_NATIVE, 720, num_ctx=16384),
    ]
    print("\n== 汇总 ==")
    for r in results:
        print("   ", r)
    print("\n判读口径：正文>0 且 JSON 可解析 且 墙钟 << 7200/轮数 ⇒ 那条通道能接进驱动；"
          "正文=0 或超时 ⇒ 通道无效，官方支只能退回 7b（B3）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
