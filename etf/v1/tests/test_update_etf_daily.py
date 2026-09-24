# -*- coding: utf-8 -*-
"""日线日更的追加护栏。

量纲/口径要点：追加的判据是「重叠段收盘逐格相对差 ≤ 1e-6」，它挡的是复权锚漂移
（前复权整段缩放、腾讯与新浪口径不同），不是单日行情波动 —— 所以测试用**整段乘
一个系数**来触发 mismatch，而不是改一格收盘价。
"""


import pandas as pd
import pytest

from update_etf_daily import APPEND_TOL, append_tail, market_of


def _write(path, rows):
    pd.DataFrame(rows, columns=["date", "open", "high", "low", "close",
                                 "volume", "amount"]).to_csv(
        path, index=False, encoding="utf-8-sig")


def _mirror(n=5, scale=1.0):
    idx = pd.bdate_range("2026-09-01", periods=n)
    close = [1.0 * (1.01 ** i) * scale for i in range(n)]
    return pd.DataFrame({"date": idx, "open": close, "high": close,
                         "low": close, "close": close,
                         "volume": [1000.0] * n, "amount": [1e6] * n})


def _as_frame(df):
    return df.set_index("date")


def test_appends_only_new_rows(tmp_path):
    path = str(tmp_path / "510300_daily.csv")
    old = _mirror(5)
    _write(path, old.values.tolist())
    new = _mirror(7)                      # 同口径、多出两天
    st, added, rel = append_tail(path, _as_frame(new))
    assert (st, added) == ("ok", 2)
    assert rel <= APPEND_TOL
    out = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    assert len(out) == 7
    # 历史一格不动：前 5 行与原文件逐值相等
    pd.testing.assert_frame_equal(out.iloc[:5].astype(float),
                                  old.set_index("date").astype(float))


def test_rescaled_source_is_rejected_and_file_untouched(tmp_path):
    """整段缩放 = 复权锚不同（实测腾讯源对 510300 就是这个形状）"""
    path = str(tmp_path / "510300_daily.csv")
    old = _mirror(5)
    _write(path, old.values.tolist())
    before = open(path, encoding="utf-8-sig").read()
    st, added, rel = append_tail(path, _as_frame(_mirror(7, scale=0.5)))
    assert st == "mismatch" and added == 0
    assert rel > 0.4
    assert open(path, encoding="utf-8-sig").read() == before


def test_source_without_new_rows_writes_nothing(tmp_path):
    path = str(tmp_path / "510300_daily.csv")
    _write(path, _mirror(5).values.tolist())
    before = open(path, encoding="utf-8-sig").read()
    st, added, _ = append_tail(path, _as_frame(_mirror(5)))
    assert (st, added) == ("uptodate", 0)
    assert open(path, encoding="utf-8-sig").read() == before


def test_duplicate_and_unsorted_new_rows_are_deduped(tmp_path):
    path = str(tmp_path / "510300_daily.csv")
    _write(path, _mirror(5).values.tolist())
    dup = pd.concat([_mirror(6), _mirror(6).tail(1)])
    st, added, _ = append_tail(path, _as_frame(dup))
    assert (st, added) == ("ok", 1)
    out = pd.read_csv(path, parse_dates=["date"])
    assert len(out) == 6 and out["date"].is_monotonic_increasing


def test_missing_close_column_in_source_still_appends_shared_cols(tmp_path):
    path = str(tmp_path / "510300_daily.csv")
    _write(path, _mirror(5).values.tolist())
    thin = _mirror(7).drop(columns=["amount"])
    st, added, _ = append_tail(path, _as_frame(thin))
    assert (st, added) == ("ok", 2)
    out = pd.read_csv(path)
    assert list(out.columns) == ["date", "open", "high", "low", "close",
                                 "volume", "amount"]
    assert pd.isna(out["amount"].iloc[-1])


def test_market_split_follows_sina_rule():
    assert market_of("510300") == "沪" and market_of("600000") == "沪"
    assert market_of("159915") == "深" and market_of("588000") == "沪"


# ---------- 池缓存的陈旧判定（DataLoader 侧的同一件事） ----------

def _loader(freq="daily"):
    import data_loader as DL
    return DL.DataLoader(freq=freq)


def _frame_until(day):
    idx = pd.bdate_range(end=day, periods=8)
    return pd.DataFrame({"close": [1.0] * len(idx)}, index=idx)


def test_cache_older_than_mirror_batch_is_stale(monkeypatch):
    dl = _loader()
    monkeypatch.setattr(dl, "_mirror_batch_end", lambda: "2026-09-23")
    df = _frame_until("2026-09-23")
    assert dl._cache_is_stale(df) is False
    assert dl._cache_is_stale(df.iloc[:-3]) is True


def test_intraday_has_no_mirror_to_compare(monkeypatch):
    dl = _loader("1min")
    monkeypatch.setattr(dl, "_mirror_batch_end", lambda: "2026-09-23")
    assert dl._cache_is_stale(_frame_until("2026-09-10")) is False


def test_stale_cache_is_healed_from_mirror(tmp_path, monkeypatch):
    """陈旧缓存要自愈：改读镜像并把缓存回写，否则下一次还是旧的一天"""
    import data_loader as DL
    code = "510300"
    fresh = _mirror(7)
    fresh.to_csv(tmp_path / f"{code}_daily.csv", index=False,
                 encoding="utf-8-sig")
    monkeypatch.setattr(DL, "UNIVERSE_ALL_DIR", str(tmp_path))
    dl = _loader()
    monkeypatch.setattr(dl, "_mirror_batch_end",
                        lambda: str(fresh["date"].max())[:10])
    cache = tmp_path / "cache_daily.csv"
    _write(str(cache), _mirror(5).values.tolist())
    monkeypatch.setattr(dl, "_cache_path", lambda c: str(cache))

    out = dl.load(code)
    assert out.index[-1] == fresh["date"].max()          # 读到镜像的最新一天
    healed = pd.read_csv(cache, parse_dates=["date"])
    assert len(healed) == 7 and str(healed["date"].max())[:10] == \
        str(fresh["date"].max())[:10]


def test_fresh_cache_short_circuits_without_touching_mirror(tmp_path, monkeypatch):
    import data_loader as DL
    cache = tmp_path / "c.csv"
    _write(str(cache), _mirror(7).values.tolist())
    dl = _loader()
    monkeypatch.setattr(dl, "_cache_path", lambda c: str(cache))
    monkeypatch.setattr(dl, "_mirror_batch_end", lambda: "2026-09-07")
    monkeypatch.setattr(DL, "UNIVERSE_ALL_DIR", str(tmp_path / "nonexistent"))
    out = dl.load("510300")
    assert len(out) == 7
