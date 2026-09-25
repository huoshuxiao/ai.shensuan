# -*- coding: utf-8 -*-
"""LLM 客户端统一出口

所有 OpenAI 兼容端点（官方 API / 本地 Ollama、vLLM、LM Studio）都经
make_openai_client() 构造：配置了 LLM_BASE_URL（环境变量
ETF_LLM_BASE_URL）即把请求指向该端点；本地服务不校验 key，缺 key 时
自动补占位符，保证零配置也能连通本地模型。"""

import os
from config import (LLM_MODEL, LLM_API_KEY_ENV, LLM_BASE_URL,
                    LLM_MAX_TOKENS, LLM_REASONING_EFFORT, LLM_TIMEOUT)


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


def _apply_request_defaults(client):
    """给 chat.completions.create 补上本线要求的请求体默认值（上限 / 关思考）。

    为什么要包在这一层：全仓 20 个 `completions.create` 调用点（自评、投票、
    日报、联合优化、因子 agent…）都不传 `max_tokens`，改不动也不必逐个改；而
    思考型模型在 OpenAI 兼容口下没有上限时**不收敛** —— 09-25 本机纯 CPU 实测
    `qwen3.5:9b` 单次 >900s 不返回，同一条请求带 `reasoning_effort="none"` 是
    19.8s。三个开关（`LLM_MAX_TOKENS`/`LLM_REASONING_EFFORT`/`LLM_TIMEOUT`）
    默认全空 ⇒ 没配 `ETF_LLM_*`/`STOCK_LLM_*` 的那条线拿到的客户端与改动前逐字一致；
    调用方显式传了同名参数时也只让位、不覆盖。
    """
    if not (LLM_MAX_TOKENS or LLM_REASONING_EFFORT):
        return client
    completions = client.chat.completions
    original = completions.create

    def create(**req):
        if LLM_MAX_TOKENS and not req.get("max_tokens"):
            req["max_tokens"] = LLM_MAX_TOKENS
        if LLM_REASONING_EFFORT and not req.get("reasoning_effort"):
            req["reasoning_effort"] = LLM_REASONING_EFFORT
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
            f"超时={LLM_TIMEOUT or 'SDK 默认'}s")
    return f"模型={LLM_MODEL} 端点={host} ({key_src}) {caps}"
