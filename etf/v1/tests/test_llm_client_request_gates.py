# -*- coding: utf-8 -*-
"""共享层 LLM 请求体闸门（max_tokens / reasoning_effort / timeout）的行为契约。

为什么在本线测 `common/src`：换 `qwen3.5:9b` 的前提是"有人给 token 上限、有人把思考关掉"，
而这两样只能加在 `make_openai_client` 这一层（全仓 20 个 `completions.create` 调用点都不传）。
共享层两线共用 ⇒ 这里既验"设了开关就注入"，也**必须**验"没设开关时一行都不包装"
（股票线不设 `STOCK_LLM_*` 时行为要与改动前逐字一致）。
"""
import sys

import pytest

from core import llm_client as LC


class _Completions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return "resp"


class _Chat:
    def __init__(self):
        self.completions = _Completions()


class _Client:
    def __init__(self):
        self.chat = _Chat()


@pytest.fixture
def switches(monkeypatch):
    def _set(max_tokens=0, effort="", timeout=0.0):
        monkeypatch.setattr(LC, "LLM_MAX_TOKENS", max_tokens, raising=False)
        monkeypatch.setattr(LC, "LLM_REASONING_EFFORT", effort, raising=False)
        monkeypatch.setattr(LC, "LLM_TIMEOUT", timeout, raising=False)
    return _set


def test_all_defaults_do_not_wrap(switches):
    """三开关全默认 ⇒ 不在实例上覆盖 create：股票线拿到的就是原生客户端"""
    switches()
    c = _Client()
    out = LC._apply_request_defaults(c)
    assert out is c
    assert "create" not in vars(c.chat.completions), \
        "默认配置下也被包装 = 股票线行为会跟着变"


def test_injects_when_caller_omits(switches):
    switches(max_tokens=1024, effort="none")
    c = _Client()
    LC._apply_request_defaults(c)
    c.chat.completions.create(temperature=0.3)
    assert c.chat.completions.calls == [
        {"temperature": 0.3, "max_tokens": 1024, "reasoning_effort": "none"}]


def test_explicit_caller_values_win(switches):
    """注入只让位不覆盖：某入口自己传了 64/low 就必须按它自己的走"""
    switches(max_tokens=1024, effort="none")
    c = _Client()
    LC._apply_request_defaults(c)
    c.chat.completions.create(max_tokens=64, reasoning_effort="low")
    assert c.chat.completions.calls[0] == {
        "max_tokens": 64, "reasoning_effort": "low"}


def test_single_switch_still_wraps(switches):
    """只设 effort（不设上限）也得生效：关思考才是救挂死的那一道，上限只是兜底"""
    switches(effort="none")
    c = _Client()
    LC._apply_request_defaults(c)
    c.chat.completions.create()
    assert c.chat.completions.calls[0] == {"reasoning_effort": "none"}


def test_describe_endpoint_states_the_gates(switches, monkeypatch):
    """开关必须自报：这台机器上"配置写了什么"和"进程跑的什么"撞过太多次"""
    switches(max_tokens=1024, effort="none", timeout=1200.0)
    monkeypatch.setattr(LC, "LLM_MODEL", "qwen3.5:9b", raising=False)
    monkeypatch.setattr(LC, "LLM_BASE_URL", "http://localhost:11434/v1",
                        raising=False)
    text = LC.describe_endpoint()
    assert "qwen3.5:9b" in text
    assert "上限=1024" in text and "effort=none" in text and "超时=1200" in text
    switches()
    off = LC.describe_endpoint()
    assert "上限=无" in off and "effort=默认" in off


def test_make_client_passes_timeout_and_keeps_explicit(monkeypatch):
    """LLM_TIMEOUT 落在 client 构造上；调用方自带 timeout 时不覆盖"""
    pytest.importorskip("openai")
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_mod = type(sys)("openai")
    fake_mod.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_mod)
    monkeypatch.setattr(LC, "LLM_TIMEOUT", 1200.0, raising=False)
    monkeypatch.setattr(LC, "LLM_MAX_TOKENS", 0, raising=False)
    monkeypatch.setattr(LC, "LLM_REASONING_EFFORT", "", raising=False)
    monkeypatch.setattr(LC, "LLM_BASE_URL", "http://x/v1", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    LC.make_openai_client()
    assert captured["timeout"] == 1200.0 and captured["base_url"] == "http://x/v1"
    captured.clear()
    LC.make_openai_client(timeout=5.0)
    assert captured["timeout"] == 5.0
