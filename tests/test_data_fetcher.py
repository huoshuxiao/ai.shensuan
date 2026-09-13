"""数据获取层测试：多数据源回退、腾讯格式解析、代码转换、缓存

覆盖盲区：此前全部用例用 MockFetcher，真实 DataFetcher 的
回退逻辑（东财不可用 -> 腾讯）与解析格式从未被验证。
本文件通过 monkeypatch requests.get 模拟腾讯接口响应，不依赖网络。
"""
import json

import pandas as pd
import pytest

import src.core.data_fetcher as fetcher_mod
from src.core.data_fetcher import DataFetcher


class FakeResp:
    """模拟 requests.Response"""

    def __init__(self, payload):
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def raise_for_status(self):
        pass

    def json(self):
        return json.loads(self.text)


def tencent_kline_payload(symbol: str, rows: list) -> dict:
    return {"code": 0, "msg": "", "data": {symbol: {"qfqday": rows}}}


def tencent_kline_rows(n: int = 5) -> list:
    # 腾讯行格式：[date, open, close, high, low, volume]
    return [
        [f"2026-09-0{i + 1}", "4.60", "4.63", "4.65", "4.58", f"{1000000 + i * 100}.000"]
        for i in range(n)
    ]


# ================= _to_tencent_symbol =================

@pytest.mark.parametrize("code,expected", [
    ("510300", "sh510300"),   # 上证ETF
    ("512480", "sh512480"),   # 上证ETF
    ("588000", "sh588000"),   # 科创板ETF
    ("159928", "sz159928"),   # 深证ETF
    ("399001", "sz399001"),   # 深证指数
    ("000300", "sh000300"),   # 沪深300指数
    ("sh000001", "sh000001"),  # 已带前缀原样返回
    ("sz159915", "sz159915"),  # 已带前缀原样返回
])
def test_to_tencent_symbol(code, expected):
    assert DataFetcher._to_tencent_symbol(code) == expected


# ================= 腾讯日线解析 =================

def test_fetch_daily_tencent_parses_qfqday(monkeypatch):
    rows = tencent_kline_rows(5)
    monkeypatch.setattr(fetcher_mod.requests, "get",
                        lambda *a, **k: FakeResp(tencent_kline_payload("sh510300", rows)))

    df = DataFetcher(use_cache=False)._fetch_daily_tencent("510300", 5)

    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(df) == 5
    # 腾讯行顺序 [open, close, high, low] 需正确映射到统一列
    first = df.iloc[0]
    assert float(first["open"]) == 4.60
    assert float(first["close"]) == 4.63
    assert float(first["high"]) == 4.65
    assert float(first["low"]) == 4.58
    assert float(first["volume"]) == 1000000.0


def test_fetch_daily_tencent_falls_back_to_day_key(monkeypatch):
    """不带 qfq 参数时腾讯返回 day 键，应同样可解析"""
    payload = {"code": 0, "msg": "", "data": {"sh510300": {"day": tencent_kline_rows(3)}}}
    monkeypatch.setattr(fetcher_mod.requests, "get", lambda *a, **k: FakeResp(payload))

    df = DataFetcher(use_cache=False)._fetch_daily_tencent("510300", 3)
    assert len(df) == 3


def test_fetch_daily_tencent_empty_raises(monkeypatch):
    monkeypatch.setattr(fetcher_mod.requests, "get",
                        lambda *a, **k: FakeResp({"code": 0, "msg": "", "data": {"sh510300": {}}}))

    with pytest.raises(RuntimeError, match="空数据"):
        DataFetcher(use_cache=False)._fetch_daily_tencent("510300", 5)


# ================= 东财失败 -> 腾讯回退 =================

def test_get_etf_daily_falls_back_to_tencent(monkeypatch, tmp_path):
    """东财源不可用时自动回退腾讯，且结果正确落缓存"""
    fetcher = DataFetcher(cache_dir=tmp_path, use_cache=True)
    monkeypatch.setattr(fetcher, "_fetch_daily_eastmoney", lambda code, days: None)

    rows = tencent_kline_rows(5)
    monkeypatch.setattr(fetcher_mod.requests, "get",
                        lambda *a, **k: FakeResp(tencent_kline_payload("sh510300", rows)))

    df = fetcher.get_etf_daily("510300", 5)
    assert len(df) == 5
    # 已写入缓存，可离线复用
    assert (tmp_path / "etf_daily_510300_5.csv").exists()


def test_get_etf_daily_prefers_eastmoney(monkeypatch, tmp_path):
    """东财源正常时直接使用，不触发腾讯"""
    fetcher = DataFetcher(cache_dir=tmp_path, use_cache=True)
    fake = pd.DataFrame({
        "date": pd.date_range("2026-08-01", periods=3),
        "open": [1.0, 1.1, 1.2], "high": [1.1, 1.2, 1.3],
        "low": [0.9, 1.0, 1.1], "close": [1.05, 1.15, 1.25],
        "volume": [1e6, 1e6, 1e6],
    })
    monkeypatch.setattr(fetcher, "_fetch_daily_eastmoney", lambda code, days: fake)

    def boom(*a, **k):
        raise AssertionError("东财可用时不应请求腾讯")

    monkeypatch.setattr(fetcher_mod.requests, "get", boom)

    df = fetcher.get_etf_daily("510300", 3)
    assert float(df["close"].iloc[-1]) == 1.25


def test_get_etf_daily_uses_cache_without_network(monkeypatch, tmp_path):
    """缓存命中时零网络请求"""
    fetcher = DataFetcher(cache_dir=tmp_path, use_cache=True)
    rows = tencent_kline_rows(5)
    monkeypatch.setattr(fetcher, "_fetch_daily_eastmoney", lambda code, days: None)
    monkeypatch.setattr(fetcher_mod.requests, "get",
                        lambda *a, **k: FakeResp(tencent_kline_payload("sh510300", rows)))
    fetcher.get_etf_daily("510300", 5)

    # 第二次读取：拦截一切网络调用
    def boom(*a, **k):
        raise AssertionError("缓存命中不应发起网络请求")

    monkeypatch.setattr(fetcher, "_fetch_daily_eastmoney", boom)
    monkeypatch.setattr(fetcher_mod.requests, "get", boom)
    df = fetcher.get_etf_daily("510300", 5)
    assert len(df) == 5


# ================= 数据源配置（settings.data_sources） =================

def test_data_sources_only_tencent_skips_akshare(monkeypatch, tmp_path):
    """配置只含 tencent 时，不请求 akshare 直接用腾讯"""
    monkeypatch.setattr(fetcher_mod.settings, "data_sources", ["tencent"])
    fetcher = DataFetcher(cache_dir=tmp_path, use_cache=True)

    def boom(code, days):
        raise AssertionError("配置未启用 akshare，不应请求东财源")

    monkeypatch.setattr(fetcher, "_fetch_daily_eastmoney", boom)
    rows = tencent_kline_rows(5)
    monkeypatch.setattr(fetcher_mod.requests, "get",
                        lambda *a, **k: FakeResp(tencent_kline_payload("sh510300", rows)))

    df = fetcher.get_etf_daily("510300", 5)
    assert len(df) == 5


def test_data_sources_only_akshare_failure_raises(monkeypatch, tmp_path):
    """配置只含 akshare 且其失败时，不降级腾讯而是报错"""
    monkeypatch.setattr(fetcher_mod.settings, "data_sources", ["akshare"])
    fetcher = DataFetcher(cache_dir=tmp_path, use_cache=True)
    monkeypatch.setattr(fetcher, "_fetch_daily_eastmoney", lambda code, days: None)

    def boom(*a, **k):
        raise AssertionError("配置未启用 tencent，不应请求腾讯")

    monkeypatch.setattr(fetcher_mod.requests, "get", boom)
    with pytest.raises(RuntimeError, match="所有数据源均无法获取日线"):
        fetcher.get_etf_daily("510300", 5)


def test_data_sources_unknown_skipped(monkeypatch, tmp_path):
    """配置中的未知数据源被跳过，正常源仍可用"""
    monkeypatch.setattr(fetcher_mod.settings, "data_sources", ["unknown", "tencent"])
    fetcher = DataFetcher(cache_dir=tmp_path, use_cache=True)
    monkeypatch.setattr(fetcher, "_fetch_daily_eastmoney", lambda code, days: None)
    rows = tencent_kline_rows(5)
    monkeypatch.setattr(fetcher_mod.requests, "get",
                        lambda *a, **k: FakeResp(tencent_kline_payload("sh510300", rows)))

    df = fetcher.get_etf_daily("510300", 5)
    assert len(df) == 5


# ================= 腾讯实时解析 =================

def tencent_quote_text(symbol: str = "sh510300") -> str:
    parts = [""] * 45
    parts[1] = "沪深300ETF华泰柏瑞"
    parts[2] = "510300"
    parts[3] = "4.579"       # 最新价
    parts[32] = "-0.82"      # 涨跌幅%
    parts[33] = "4.592"      # 最高
    parts[34] = "4.532"      # 最低
    parts[36] = "9666652"    # 成交量(手)
    parts[37] = "440941"     # 成交额(万元)
    return f'v_{symbol}="' + "~".join(parts) + '";\n'


def test_fetch_realtime_tencent_parses(monkeypatch):
    monkeypatch.setattr(fetcher_mod.requests, "get",
                        lambda *a, **k: FakeResp(tencent_quote_text()))

    result = DataFetcher(use_cache=False)._fetch_realtime_tencent("510300")

    assert result["code"] == "510300"
    assert result["name"] == "沪深300ETF华泰柏瑞"
    assert result["price"] == 4.579
    assert result["pct_change"] == -0.82
    assert result["high"] == 4.592
    assert result["low"] == 4.532
    assert result["volume"] == 9666652.0
    assert result["amount"] == pytest.approx(440941 * 10000)  # 万元转元


def test_fetch_realtime_tencent_bad_payload_returns_none(monkeypatch):
    monkeypatch.setattr(fetcher_mod.requests, "get",
                        lambda *a, **k: FakeResp('v_pv_none="";'))
    assert DataFetcher(use_cache=False)._fetch_realtime_tencent("510300") is None


# ================= 列名映射 =================

def test_normalize_daily_maps_chinese_columns():
    raw = pd.DataFrame({
        "日期": ["2026-09-01"], "开盘": [1.0], "收盘": [1.1],
        "最高": [1.2], "最低": [0.9], "成交量": [1000000], "成交额": [1100000],
        "涨跌幅": [0.1],
    })
    df = DataFetcher._normalize_daily(raw)
    assert set(df.columns) == {"date", "open", "high", "low", "close", "volume"}
    assert float(df["close"].iloc[0]) == 1.1
