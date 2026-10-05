# -*- coding: utf-8 -*-
"""LLM 客户端统一出口

所有 OpenAI 兼容端点（官方 API / 本地 Ollama、vLLM、LM Studio）都经
make_openai_client() 构造：配置了 LLM_BASE_URL（环境变量
ETF_LLM_BASE_URL）即把请求指向该端点；本地服务不校验 key，缺 key 时
自动补占位符，保证零配置也能连通本地模型。"""

import os
from config import (LLM_MODEL, LLM_API_KEY_ENV, LLM_BASE_URL,
                    LLM_MAX_TOKENS, LLM_REASONING_EFFORT, LLM_NUM_CTX,
                    LLM_WARN_CHARS, LLM_TIMEOUT)


def llm_available() -> bool:
    """有 API key 或配置了自定义端点，二者居一即视为可用"""
    return bool(os.environ.get(LLM_API_KEY_ENV) or LLM_BASE_URL)


def endpoint_enabled(api_key: str) -> bool:
    """各 LLM 客户端的启用判据：显式 key 或全局本地端点"""
    return bool(api_key) or bool(LLM_BASE_URL)


def make_openai_client(**kwargs):
    """构造 openai.OpenAI：注入环境 key 与 LLM_BASE_URL（若配置）。
    调用方可显式覆盖 api_key/base_url（多模型路由场景），默认值只在
    未提供或为空时生效。"""
    from openai import OpenAI
    if not kwargs.get("api_key"):
        kwargs["api_key"] = os.environ.get(LLM_API_KEY_ENV) or "local-llm"
    if not kwargs.get("base_url") and LLM_BASE_URL:
        kwargs["base_url"] = LLM_BASE_URL
    if not kwargs.get("timeout") and LLM_TIMEOUT:
        kwargs["timeout"] = LLM_TIMEOUT
    return _apply_request_defaults(OpenAI(**kwargs))


def _prompt_chars(messages) -> int:
    """一条请求要发出去的**字符**总数（各条 message 的 content 相加）。

    按字符不按 token＝主线没有 tokenizer，而字符→token 实测跨句不可迁移（10-04 三条真件
    0.743／0.597／0.925）。非字符串的 content 走 `str()` 数其形状长度＝宁可多算不可漏报。
    """
    return sum(len(str(m.get("content") or "")) for m in messages or ())


def _apply_request_defaults(client):
    """给 chat.completions.create 补上本线要求的请求体默认值（上限 / 关思考）＋一枚超窗警铃。

    为什么要包在这一层：全仓 21 个 `completions.create` 调用点（自评、投票、
    日报、联合优化、因子 agent…）都不传 `max_tokens`，改不动也不必逐个改；而
    思考型模型在 OpenAI 兼容口下没有上限时**不收敛** —— 09-25 本机纯 CPU 实测
    `qwen3.5:9b` 单次 >900s 不返回，同一条请求带 `reasoning_effort="none"` 是
    19.8s。这几把开关（`LLM_MAX_TOKENS`/`LLM_REASONING_EFFORT`/`LLM_WARN_CHARS`/
    `LLM_TIMEOUT`）默认全空 ⇒ 没配 `ETF_LLM_*`/`STOCK_LLM_*` 的那条线拿到的客户端
    与改动前逐字一致；调用方显式传了同名参数时也只让位、不覆盖。

    ⚠️ 这里**没有**上下文窗口那一支了（10-04 用户裁「乙」）。原先它往 `extra_body` 塞
    `options.num_ctx`，而四臂实测（`etf/v1/temp/check_num_ctx_v1_1004.log`，同一条 5502 token
    真提示词）判的是空转：`/v1` 裸发／options 内给／顶层给三种写法**一律只进 2050**、
    `/api/ps` 驻留窗口恒 4096 ⇒ 不报错、也不变宽，唯一的产物是让配置看起来比进程真做的事多。
    拔掉之后 `LLM_NUM_CTX` 只喂官方支（`official_rdagent._driver_env` 派生成 litellm 的
    `num_ctx`，那腿走原生 `/api/chat`，16384 真进）；主线这一侧只在 `describe_endpoint()` 里
    **把那个数当"别人的"念一遍**，不再写进请求体。主线要真撑宽窗口只剩换通路（丙），今天没选。

    换上的这枚铃铛**只叫不改**：`LLM_WARN_CHARS`（默认 0＝不响）是个字符数上限，发出去之前
    数一遍、超线就打一行 ⚠️，请求照常发出、不拦不报错。阈值把服务端现量输入线 ≈2050 token
    按最密那句（0.925 token/字）换算成 ≈2200 字＝**保守界**（稀疏的句子可能提前响，已坐实的
    截断不会漏响）。它不解决问题，只是把「一半提示词被静默丢掉」变成日志里看得见的一行。
    """
    if not (LLM_MAX_TOKENS or LLM_REASONING_EFFORT or LLM_WARN_CHARS):
        return client
    completions = client.chat.completions
    original = completions.create

    def create(**req):
        if LLM_MAX_TOKENS and not req.get("max_tokens"):
            req["max_tokens"] = LLM_MAX_TOKENS
        if LLM_REASONING_EFFORT and not req.get("reasoning_effort"):
            req["reasoning_effort"] = LLM_REASONING_EFFORT
        if LLM_WARN_CHARS:
            n = _prompt_chars(req.get("messages"))
            if n > LLM_WARN_CHARS:
                print(f"  ⚠️[超窗铃铛] 本条提示词 {n} 字 > 报警线 {LLM_WARN_CHARS} 字 ⇒ "
                      f"服务端很可能已静默截断（/v1 现量输入线 ≈2050 token；换算尺子 "
                      f"etf/v1/temp/check_mainline_prompt_fit_1004.log）")
        return original(**req)

    completions.create = create
    return client


def describe_endpoint() -> str:
    """一行文字说明当前 LLM 走向，供日志核对配置是否生效"""
    key_src = ("env:" + LLM_API_KEY_ENV
               if os.environ.get(LLM_API_KEY_ENV) else "无 key")
    host = LLM_BASE_URL or "OpenAI 官方端点"
    caps = (f"上限={LLM_MAX_TOKENS or '无'} "
            f"effort={LLM_REASONING_EFFORT or '默认'} "
            f"窗口=主线不注入(/v1 不认) 官方支={LLM_NUM_CTX or '默认4096'} "
            f"铃铛={(str(LLM_WARN_CHARS) + '字') if LLM_WARN_CHARS else '关'} "
            f"超时={LLM_TIMEOUT or 'SDK 默认'}s")
    return f"模型={LLM_MODEL} 端点={host} ({key_src}) {caps}"
