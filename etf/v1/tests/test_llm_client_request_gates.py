# -*- coding: utf-8 -*-
"""共享层 LLM 请求体闸门（max_tokens / reasoning_effort / timeout）＋超窗铃铛的行为契约。

为什么在本线测 `common/src`：换 `qwen3.5:9b` 的前提是"有人给 token 上限、有人把思考关掉"，
而这两样只能加在 `make_openai_client` 这一层（全仓 21 个 `completions.create` 调用点都不传）。
共享层两线共用 ⇒ 这里既验"设了开关就注入"，也**必须**验"没设开关时一行都不包装"
（股票线不设 `STOCK_LLM_*` 时行为要与改动前逐字一致）。

⚠️ 10-04 用户裁「乙＋铃铛」之后，本文件的重点换了两处：
① **上下文窗口那支不再注入主线**（`LLM_NUM_CTX` 只喂官方支）⇒ 原来的三格「设了窗口就注入」
   的正例全部反过来，现在钉的是**拨到 16384 也不许往请求体里出现 num_ctx**（`test_num_ctx_
   never_reaches_request_body` 就是乙的牙：改前那格必红，因为当时真会多一个 `extra_body`）。
② 换上的是一枚**只叫不改**的警铃 ⇒ 它必须同时钉住"超线会响"（正对照）、"未线不响"（负对照）、
   "响了也照发、请求体一个键都不动"（否则铃铛就变成了第二道闸，那是另一件事、没裁）。

夹具仍要把**全部**开关一起钉回默认值（含 `LLM_WARN_CHARS`），且 `LLM_NUM_CTX` 默认**故意留在
16384**＝本线 `.env` 的真值：窗口那把既然是"别人的配置"，每格都该在它拨着的情况下测。
漏钉哪一把，那一格就是在拿生产配置做断言（10-04 上午因此红过 5 格，红得对——是夹具过期，不是代码坏）。
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
    def _set(max_tokens=0, effort="", timeout=0.0, warn_chars=0, num_ctx=16384):
        monkeypatch.setattr(LC, "LLM_MAX_TOKENS", max_tokens, raising=False)
        monkeypatch.setattr(LC, "LLM_REASONING_EFFORT", effort, raising=False)
        monkeypatch.setattr(LC, "LLM_TIMEOUT", timeout, raising=False)
        monkeypatch.setattr(LC, "LLM_WARN_CHARS", warn_chars, raising=False)
        monkeypatch.setattr(LC, "LLM_NUM_CTX", num_ctx, raising=False)
    return _set


def test_all_defaults_do_not_wrap(switches):
    """注入开关全默认 ⇒ 不在实例上覆盖 create：股票线拿到的就是原生客户端。
    注意 `num_ctx` 这里**是 16384**（拨着的）⇒ 乙之后它不再是触发包装的条件。"""
    switches()
    c = _Client()
    out = LC._apply_request_defaults(c)
    assert out is c
    assert "create" not in vars(c.chat.completions), \
        "默认配置下也被包装 = 股票线行为会跟着变"


def test_num_ctx_never_reaches_request_body(switches):
    """乙的牙：窗口旋钮拨到 16384，请求体里也不许出现 num_ctx／extra_body。
    改前这格必红——当时 `_apply_request_defaults` 会塞 `extra_body={"options":{"num_ctx":16384}}，
    而四臂实测证明 `/v1` 收了也不认（三种写法一律只进 2050），留着只让配置比进程显得更能干。"""
    switches(max_tokens=1024, effort="none", num_ctx=16384)
    c = _Client()
    LC._apply_request_defaults(c)
    c.chat.completions.create(temperature=0.3)
    assert c.chat.completions.calls == [
        {"temperature": 0.3, "max_tokens": 1024, "reasoning_effort": "none"}]
    assert "extra_body" not in c.chat.completions.calls[0]
    assert "num_ctx" not in str(c.chat.completions.calls[0])


def test_bell_alone_wraps(switches):
    """只设铃铛（其余全默认）也必须生效：铃铛是独立的一把，不搭另外两把的便车"""
    switches(warn_chars=10)
    c = _Client()
    LC._apply_request_defaults(c)
    assert "create" in vars(c.chat.completions), "铃铛单独开时没包装 = 永远不响"


def test_bell_rings_over_threshold(switches, capsys):
    """正对照：超线必须响，且**照发不误**——请求体一个键都不许多（只叫不改）"""
    switches(warn_chars=100)
    c = _Client()
    LC._apply_request_defaults(c)
    sent = {"messages": [{"role": "user", "content": "字" * 101}], "temperature": 0.7}
    resp = c.chat.completions.create(**sent)
    out = capsys.readouterr().out
    assert "超窗铃铛" in out, f"超线没响：{out!r}"
    assert "101 字" in out and "100 字" in out
    assert resp == "resp"
    assert c.chat.completions.calls == [sent], "铃铛动了请求体 = 从警铃变成了第二道闸"


def test_bell_silent_under_threshold(switches, capsys):
    """负对照：恰好等于线不许响（判据是**严格大于**，否则阈值本身每天误报）"""
    switches(warn_chars=100)
    c = _Client()
    LC._apply_request_defaults(c)
    c.chat.completions.create(messages=[{"role": "user", "content": "字" * 100}])
    assert capsys.readouterr().out == ""


def test_bell_counts_whole_request_not_last_message(switches, capsys):
    """数的是整条请求：system 60 + user 60 = 120 > 100 ⇒ 该响。
    只数最后一条会漏掉"历史堆起来"那一类，而 `history[-10:]` 正是今天的最大真件。"""
    switches(warn_chars=100)
    c = _Client()
    LC._apply_request_defaults(c)
    c.chat.completions.create(messages=[
        {"role": "system", "content": "系" * 60},
        {"role": "user", "content": "用" * 60}])
    assert "超窗铃铛" in capsys.readouterr().out


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
    """开关必须自报：这台机器上"配置写了什么"和"进程跑的什么"撞过太多次。
    窗口那半句现在要报的是**主线不注入**＋官方支那个数，不能再报成主线的窗口。"""
    switches(max_tokens=1024, effort="none", timeout=1200.0,
             warn_chars=2200, num_ctx=16384)
    monkeypatch.setattr(LC, "LLM_MODEL", "qwen3.5:9b", raising=False)
    monkeypatch.setattr(LC, "LLM_BASE_URL", "http://localhost:11434/v1",
                        raising=False)
    text = LC.describe_endpoint()
    assert "qwen3.5:9b" in text
    assert "上限=1024" in text and "effort=none" in text and "超时=1200" in text
    assert "窗口=主线不注入" in text, text
    assert "官方支=16384" in text, text
    assert "铃铛=2200" in text, text
    switches(warn_chars=0, num_ctx=0)
    off = LC.describe_endpoint()
    assert "上限=无" in off and "effort=默认" in off
    assert "铃铛=关" in off and "官方支=默认4096" in off


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
    monkeypatch.setattr(LC, "LLM_WARN_CHARS", 0, raising=False)
    monkeypatch.setattr(LC, "LLM_NUM_CTX", 0, raising=False)
    monkeypatch.setattr(LC, "LLM_BASE_URL", "http://x/v1", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    LC.make_openai_client()
    assert captured["timeout"] == 1200.0 and captured["base_url"] == "http://x/v1"
    captured.clear()
    LC.make_openai_client(timeout=5.0)
    assert captured["timeout"] == 5.0
