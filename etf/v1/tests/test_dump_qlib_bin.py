# -*- coding: utf-8 -*-
"""基准指数缓存的日更语义：`dump_qlib_bin.fetch_benchmarks` 必须增量贴新。

判据复用 `update_etf_daily.append_tail`（重叠尾段比收盘、只贴末日之后的新行），
这里锁的是四条分支：冷启动整段落盘 / 陈旧缓存只追加新行 / 源不可达沿用旧缓存 /
口径不合拒绝追加。09-24 的实事故子是"存在即复用"——ETF 日历推到 09-23、
SH000300 还停在 09-22，qlib 里末日 $open/$close 读成 NaN，超额收益算不出来。
"""

import os

import numpy as np
import pandas as pd
import pytest

import dump_qlib_bin as d


def _index_df(n=200, start="2025-01-01", scale=1.0):
    dates = pd.bdate_range(start, periods=n)
    close = (1000.0 + np.arange(n, dtype=float)) * scale
    return pd.DataFrame({
        "date": dates, "open": close * 0.99, "high": close * 1.01,
        "low": close * 0.98, "close": close,
        "volume": np.arange(n, dtype=float) * 1e6 + 1.0,
    })


@pytest.fixture
def one_benchmark(monkeypatch, tmp_path):
    """只留一只基准 + 空缓存目录：把网络分支换成受控的假源"""
    monkeypatch.setattr(d, "BENCHMARK_INDEXES", {"SH000300": "sh000300"})
    return tmp_path / "index_cache"


def _csv(path):
    return pd.read_csv(path, encoding="utf-8-sig", parse_dates=["date"])


def test_cold_start_writes_full_cache(one_benchmark, monkeypatch):
    src = _index_df()
    monkeypatch.setattr(d, "fetch_index", lambda code: src.copy())
    out = d.fetch_benchmarks(str(one_benchmark))
    assert list(out) == ["SH000300"]
    disk = _csv(one_benchmark / "SH000300.csv")
    assert len(disk) == len(src)
    assert out["SH000300"].index[-1] == src["date"].iloc[-1]


def test_stale_cache_appends_only_new_rows(one_benchmark, monkeypatch):
    """历史一格都不许动：旧行的收盘必须逐字不变，只往末尾贴新的一天"""
    old = _index_df(200)
    one_benchmark.mkdir(parents=True)
    old.to_csv(one_benchmark / "SH000300.csv", index=False, encoding="utf-8-sig")
    src = _index_df(202)                      # 同源、多两天
    monkeypatch.setattr(d, "fetch_index", lambda code: src.copy())

    out = d.fetch_benchmarks(str(one_benchmark))
    disk = _csv(one_benchmark / "SH000300.csv")
    assert len(disk) == 202, "应当只追加，不整段重写"
    pd.testing.assert_series_equal(disk["close"].head(200), old["close"],
                                   check_names=False)
    assert out["SH000300"].index[-1] == src["date"].iloc[-1]


def test_up_to_date_source_changes_nothing(one_benchmark, monkeypatch):
    src = _index_df(200)
    one_benchmark.mkdir(parents=True)
    path = one_benchmark / "SH000300.csv"
    src.to_csv(path, index=False, encoding="utf-8-sig")
    before = path.read_bytes()
    monkeypatch.setattr(d, "fetch_index", lambda code: _index_df(200))
    out = d.fetch_benchmarks(str(one_benchmark))
    assert path.read_bytes() == before, "没有新行就不该重写文件"
    assert len(out["SH000300"]) == 200


def test_source_down_falls_back_to_cache(one_benchmark, monkeypatch):
    """联网失败 ≠ 没有基准：沿用旧缓存，dump 继续（旧实现会整只丢掉）"""
    one_benchmark.mkdir(parents=True)
    path = one_benchmark / "SH000300.csv"
    _index_df(200).to_csv(path, index=False, encoding="utf-8-sig")
    before = path.read_bytes()
    monkeypatch.setattr(d, "fetch_index", lambda code: None)
    out = d.fetch_benchmarks(str(one_benchmark))
    assert list(out) == ["SH000300"]
    assert len(out["SH000300"]) == 200
    assert path.read_bytes() == before


def test_rescaled_source_is_refused(one_benchmark, monkeypatch):
    """源换了口径（整段缩放）时重叠段对不上 ⇒ 拒绝追加，末日不许被挪"""
    one_benchmark.mkdir(parents=True)
    path = one_benchmark / "SH000300.csv"
    old = _index_df(200)
    old.to_csv(path, index=False, encoding="utf-8-sig")
    before = path.read_bytes()
    monkeypatch.setattr(d, "fetch_index", lambda code: _index_df(202, scale=1.5))

    out = d.fetch_benchmarks(str(one_benchmark))
    assert path.read_bytes() == before, "口径不合却写了文件"
    assert out["SH000300"].index.max() == old["date"].iloc[-1], \
        "被拒绝的源不该把末日挪走"


def test_missing_everything_yields_no_benchmark(one_benchmark, monkeypatch,
                                                capsys):
    monkeypatch.setattr(d, "fetch_index", lambda code: None)
    out = d.fetch_benchmarks(str(one_benchmark))
    assert out == {}
    assert "does not exist" in capsys.readouterr().out
