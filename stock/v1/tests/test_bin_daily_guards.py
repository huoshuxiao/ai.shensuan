# -*- coding: utf-8 -*-
"""bin 日更的两道写盘前置闸 + 四个阈值常量（`common/src/data/stock/update_qlib_bin_daily.py`）。

判据（与函数 docstring 同源）：
    ① `session` 晚于本机自然日 ⇒ 那场还没收盘 ⇒ **不抓快照、不落缓存、bin 一字节不动**
    ② 手敲 `--session` 且那天不在交易所日历 ⇒ 那天根本不交易（默认档不查这条）

缓存复用处另有一道对表（`align_against_bin`），两层各挡一半 ⇒ 这里只测语义、不联网。

为什么值得钉：09-25 那次休市日 dry-run 把 09-24 的行情钉成了 09-28 的缓存，写是挡住了、
但那次日更**静默失败**，除非有人知道要删文件 ⇒ 判据才挪到抓数之前。这条历史只能靠测试记住。
"""

import inspect

import pandas as pd
import pytest

import update_qlib_bin_daily as bin_daily

def _today():
    """今天（本机时区，与 guard_session 里同一把尺子）——日期一律现推，不许写死"""
    return pd.Timestamp.now().normalize()


# 假日历：够用就行，**不联网**（真 trade_days() 要发一次 akshare 请求）。
# ⚠️ 不许写死日期：09-29 那版钉在 09-22~09-30 六天 ⇒ 10-01（国庆）起「今天」不在表里，
# 那条**正对照**（本该放行）自己先红 ⇒ 表必须永远含「今天」，休市日由工作日尺子天然留空。
CAL = pd.DatetimeIndex(sorted(
    list(pd.date_range(_today() - pd.Timedelta(days=12),
                       _today() - pd.Timedelta(days=1), freq="B")) + [_today()]))


def _off_day() -> pd.Timestamp:
    """表外的那一天：从昨天往前找最近的周六（周六不是交易日，也不会被「今天」那一格带进来）。"""
    d = _today() - pd.Timedelta(days=1)
    while d.weekday() != 5 or d in CAL:
        d -= pd.Timedelta(days=1)
    return d


@pytest.fixture
def fake_calendar(monkeypatch):
    monkeypatch.setattr(bin_daily, "trade_days", lambda: CAL)
    return CAL


def test_未来场次拒在抓数之前():
    with pytest.raises(SystemExit) as e:
        bin_daily.guard_session("2099-01-01", explicit=False)
    assert "还没发生" in str(e.value)


def test_手敲的休市场次拒(fake_calendar):
    off_day = _off_day().strftime("%Y-%m-%d")     # 假日历里没有这一天（周六）
    assert pd.Timestamp(off_day) not in fake_calendar
    with pytest.raises(SystemExit) as e:
        bin_daily.guard_session(off_day, explicit=True)
    assert "不在交易所交易日历" in str(e.value)


def test_正对照_日历里且已到收盘日的场次必须放行(fake_calendar):
    """拒绝型判据一定要配一条放行对照，否则闸可以是恒真的（09-25 就是这么抓到 bug 的）。"""
    bin_daily.guard_session(_today().strftime("%Y-%m-%d"), explicit=True)


def test_默认档不去查日历(monkeypatch, fake_calendar):
    """explicit=False 时那条日历核验不该被触发：它的 session 本来就取自那张表。

    牙口：把 trade_days 换成「一调用就炸」，本条仍然过 ⇒ 证明确实没查；
    若哪天有人给默认档也加上核验，本条立刻红。
    """
    def _boom():
        raise AssertionError("默认档不该发日历请求")

    monkeypatch.setattr(bin_daily, "trade_days", _boom)
    bin_daily.guard_session(_today().strftime("%Y-%m-%d"), explicit=False)


def test_日历返回类型必须是_DatetimeIndex(monkeypatch):
    """Series 的 `d in s` 查的是**行号**不是值 ⇒ 每一个合法场次都会被误拒。

    这条钉的是那个坑真实存在：假日历给成 Series 时，今天的合法场次必须被拒。
    """
    monkeypatch.setattr(bin_daily, "trade_days", lambda: pd.Series(CAL))
    with pytest.raises(SystemExit):
        bin_daily.guard_session(_today().strftime("%Y-%m-%d"), explicit=True)


def test_四道阈值常量与说明书一致():
    """改这四个数 = 改判据：必须同时改 AGENT.md 的口径文字，本条就是那道提醒。"""
    assert bin_daily.ALIGN_EXACT_MIN == 0.90     # 逐字相同率下限
    assert bin_daily.ALIGN_MIN == 0.98           # 容差内对齐率下限
    assert bin_daily.ALIGN_SAMPLE == 400         # 缓存复用处抽样票数
    assert bin_daily.CLOSE_HM == 15 * 60         # 收盘闸（分钟）


def test_复牌首日_change_留_NaN_那条规则还在():
    """停牌空档后第一天没有可比前收盘 ⇒ `change` 必须是 NaN，不许填 0。

    这条是**源码钉桩**（规则写在 `write_day` 的循环里，喂不出单日行为）：
    删掉那一行本条即红，行为语义由重跑器的 B6 豁免夹具 `check_b6_waiver_0929.py` 管。
    """
    src = inspect.getsource(bin_daily.write_day)
    assert src.count('cells["change"] = np.nan') == 1
