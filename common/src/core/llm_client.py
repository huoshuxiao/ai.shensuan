# -*- coding: utf-8 -*-
"""LLM 客户端统一出口

所有 OpenAI 兼容端点（官方 API / 本地 Ollama、vLLM、LM Studio）都经
make_openai_client() 构造：配置了 LLM_BASE_URL（环境变量
ETF_LLM_BASE_URL）即把请求指向该端点；本地服务不校验 key，缺 key 时
自动补占位符，保证零配置也能连通本地模型。"""

import os
from config import LLM_MODEL, LLM_API_KEY_ENV, LLM_BASE_URL


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
    return OpenAI(**kwargs)


def describe_endpoint() -> str:
    """一行文字说明当前 LLM 走向，供日志核对配置是否生效"""
    key_src = ("env:" + LLM_API_KEY_ENV
               if os.environ.get(LLM_API_KEY_ENV) else "无 key")
    host = LLM_BASE_URL or "OpenAI 官方端点"
    return f"模型={LLM_MODEL} 端点={host} ({key_src})"
