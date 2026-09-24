# -*- coding: utf-8 -*-
"""#17 采集器的离线自校验：只增不改的落盘约定 + 三个源的形状。

全程不触网：akshare 用假模块顶替（采集函数是函数内 `import akshare`，故注入
`sys.modules` 即可），落盘路径一律 monkeypatch 到 tmp_path。
"""

import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

import fetch_etf_risk_panel as RP


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """把三张长表指到临时目录，测试绝不写 data/risk/。"""
    d = tmp_path / "risk"
    d.mkdir()
    for name in ("SHARES_SSE_OUT", "SHARES_SZSE_OUT", "NAV_THS_OUT"):
        monkeypatch.setattr(RP, name, str(d / (name.lower().replace("_out", ".csv"))))
    return d


def _shares(date, code, shares, mkt="SH"):
    return pd.DataFrame({"date": pd.to_datetime([date] * len(code)),
                         "code": code, "mkt": [mkt] * len(code),
                         "shares": shares})


# ---------- 1. 区间切分：深市 3 个月硬上限 ----------

def test_month_windows_never_exceed_the_silent_truncation_limit():
    """每个窗口 ≤3 个月、首尾相接不重叠不留缝。

    写成断言而不是"我记得是 3 个月"：深市超窗的失败方式返回 0 行或截断到 65535 行
    —— 都不报错，是这三种读法里最坏的一种（静默）。切分函数是唯一的防线。
    """
    wins = RP._month_windows("2019-01-01", "2026-09-24")
    assert wins[0][0] == pd.Timestamp("2019-01-01")
    assert wins[-1][1] == pd.Timestamp("2026-09-24")
    for i, (s, e) in enumerate(wins):
        assert (e - s).days <= RP.SZSE_MAX_MONTHS * 31, (s, e)
        assert e > s
        if i:
            assert s == wins[i - 1][1] + pd.Timedelta(days=1), (i, s)


def test_month_ends_are_one_per_month_and_stop_at_the_range_end():
    ends = RP._month_ends("2020-02-05", "2020-05-20")
    assert [d.strftime("%Y-%m") for d in ends] == ["2020-02", "2020-03",
                                                   "2020-04", "2020-05"]
    assert ends[-1] == pd.Timestamp("2020-05-20")      # 末月被区间右端截住


# ---------- 2. 落盘：只增不改 ----------

def test_append_long_overwrites_same_key_but_never_loses_history(paths):
    """同 (date, code) 以新值为准（源方会回补更正），其余历史一条不少。"""
    p = RP.SHARES_SZSE_OUT
    RP.append_long(p, _shares("2026-01-05", ["159915", "159919"], [100.0, 200.0], "SZ"))
    RP.append_long(p, _shares("2026-01-06", ["159915"], [300.0], "SZ"))
    n_before = len(RP.read_long(p))
    # 回补 01-05 的 159915（值改了）并带来一只新代码
    RP.append_long(p, _shares("2026-01-05", ["159915", "159999"], [111.0, 9.0], "SZ"))
    out = RP.read_long(p)
    assert len(out) >= n_before
    assert float(out.query("date=='2026-01-05' and code=='159915'")["shares"]) == 111.0
    # 没被本次触及的旧行必须原样在
    assert float(out.query("date=='2026-01-05' and code=='159919'")["shares"]) == 200.0
    assert float(out.query("date=='2026-01-06'")["shares"]) == 300.0
    assert out["date"].is_monotonic_increasing and not out.duplicated(RP.SHARES_KEY).any()


def test_append_long_with_empty_new_is_a_no_op(paths):
    p = RP.SHARES_SSE_OUT
    RP.append_long(p, _shares("2026-01-05", ["510300"], [1e9]))
    mtime = os.path.getmtime(p)
    RP.append_long(p, _shares("2026-01-05", [], []))
    RP.append_long(p, None)
    assert len(RP.read_long(p)) == 1
    assert os.path.getmtime(p) == mtime            # 空抓不能重写文件（更不可能清空）


def test_codes_are_zero_padded_on_read(paths):
    """CSV 读回来 159003 会变成 int ⇒ 6 位补零必须发生在读取侧，否则跨表并不上。"""
    p = RP.SHARES_SZSE_OUT
    RP.append_long(p, _shares("2026-01-05", [159003, "159915"], [1.0, 2.0], "SZ"))
    assert sorted(RP.read_long(p)["code"]) == ["159003", "159915"]


def test_load_shares_matrix_merges_both_exchanges(paths):
    """沪/深两份长表拼成一张宽矩阵；同日同码以最后写入者为准。"""
    RP.append_long(RP.SHARES_SSE_OUT, _shares("2026-01-05", ["510300"], [1e9], "SH"))
    RP.append_long(RP.SHARES_SZSE_OUT, _shares("2026-01-05", ["159915"], [2e9], "SZ"))
    RP.append_long(RP.SHARES_SZSE_OUT, _shares("2026-01-06", ["159915"], [3e9], "SZ"))
    m = RP.load_shares_matrix()
    assert list(m.columns) == ["159915", "510300"]
    assert m.loc[pd.Timestamp("2026-01-05"), "159915"] == 2e9
    assert np.isnan(m.loc[pd.Timestamp("2026-01-06"), "510300"])   # 沪市没披露就是 NaN


# ---------- 3. 三个源的形状（假 akshare，不触网） ----------

def _fake_ak(monkeypatch, **fns):
    mod = types.ModuleType("akshare")
    for k, v in fns.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, "akshare", mod)
    return mod


def test_sse_absence_is_reported_as_empty_not_as_an_outage(monkeypatch):
    """上交所对"该日无披露"的实现是抛 KeyError，长得和接口停机一模一样。

    必须翻译为空表，否则日更会因为"今天还没出数"而整批失败（实测 09-24 无披露、
    09-23 有 912 行）。而列名缺失（源真改了表结构）不能吞掉 —— 要炸出来。
    """
    def boom(date):
        raise KeyError("None of [Index(['序号', '基金代码'])] are in the columns")

    def ok(date):
        return pd.DataFrame({"序号": [1, 2], "基金代码": ["510300", "510310"],
                             "基金简称": ["a", "b"], "统计日期": [date] * 2,
                             "基金份额": ["1.5e10", "2.0e9"]})

    _fake_ak(monkeypatch, fund_etf_scale_sse=boom)
    assert len(RP.fetch_shares_sse("2026-09-24")) == 0
    monkeypatch.setattr(sys.modules["akshare"], "fund_etf_scale_sse", ok)
    df = RP.fetch_shares_sse("2026-09-23")
    assert list(df["shares"]) == [1.5e10, 2.0e9]        # 字符串份额要转数值
    assert df["mkt"].eq("SH").all()
    _fake_ak(monkeypatch, fund_etf_scale_sse=lambda date: pd.DataFrame(
        {"序号": [1], "别的列": ["x"]}))
    with pytest.raises(KeyError):
        RP.fetch_shares_sse("2026-09-23")               # 表结构变了不许静默


def test_sse_date_is_normalized_to_eight_digits(monkeypatch):
    """带横杠的日期会在 akshare 内部炸成"像停机"的 KeyError —— 入口就得压成 8 位。"""
    seen = {}

    def cap(date):
        seen["date"] = date
        return pd.DataFrame()

    _fake_ak(monkeypatch, fund_etf_scale_sse=cap)
    RP.fetch_shares_sse("2026-09-23")
    assert seen["date"] == "20260923"


def test_szse_refuses_windows_that_silently_truncate(monkeypatch):
    """>3 个月的窗口必须在本侧拒掉，不能把请求发出去等一个 0 行的假成功。"""
    called = []
    _fake_ak(monkeypatch, fund_scale_daily_szse=lambda **kw: called.append(kw)
             or pd.DataFrame())
    with pytest.raises(ValueError):
        RP.fetch_shares_szse("2019-01-01", "2026-09-24")
    assert not called                                   # 一个请求都不该发出去
    df = RP.fetch_shares_szse("2026-01-01", "2026-03-31")
    assert called and len(df) == 0


def test_szse_rows_are_keyed_by_their_own_date(monkeypatch):
    _fake_ak(monkeypatch, fund_scale_daily_szse=lambda **kw: pd.DataFrame({
        "日期": ["2026-01-05", "2026-01-06"], "基金代码": ["159915", 159915],
        "基金简称": ["a", "b"], "基金份额": [1e9, "0"]}))
    df = RP.fetch_shares_szse("2026-01-01", "2026-03-31")
    assert len(df) == 1                                  # 0 份额的行不算披露，剔掉
    assert df["code"].iloc[0] == "159915"
    assert df["date"].min() == pd.Timestamp("2026-01-05")


def test_nav_is_keyed_by_its_own_nav_date_not_today(monkeypatch):
    """快照里混着不同日的净值（非交易日的旧披露）。抓到哪天就存哪天，
    绝不能冒充"今天更新过" —— 否则折溢价曲线会凭空多出一段假平线。"""
    _fake_ak(monkeypatch, fund_etf_spot_ths=lambda: pd.DataFrame({
        "基金代码": ["510300", "159915", "510500"],
        "最新-单位净值": ["4.59", "6.10", "0"],
        "最新-交易日": ["2026-09-23", "2026-09-22", "2026-09-23"],
        "增长率": ["0.5%", "1.0%", "2.0%"]}))
    df = RP.fetch_nav_ths()
    assert len(df) == 2                                   # nav<=0 不算披露
    got = {r["code"]: str(r["date"].date()) for _, r in df.iterrows()}
    assert got == {"510300": "2026-09-23", "159915": "2026-09-22"}
