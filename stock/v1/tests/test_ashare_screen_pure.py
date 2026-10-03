# -*- coding: utf-8 -*-
"""筛选层里**不依赖行情数据**的三把纯判据：板块识别、贴涨停阈值、假台阶护栏。

为什么先给这三把上测试：它们是名单的「哪些票根本买不进」那一层，表达式短、
无需面板、错了必然当场可判；而 09-24 之前它们只被临时脚本量过一天。

口径（与 `ashare_screen.py` 的 docstring 同源）：
    阈值_i = 限幅(板块_i) × ASHARE_LIMIT_NEAR(0.95)
        主板 0.10 → 0.095 ｜ 科创/创业 0.20 → 0.19 ｜ 北交 0.30 → 0.285

    假台阶裁剪  fake = { |r_adj| > limit ∧ |r_raw| ≤ limit }
                r' = clip(r_adj, ±limit)  （fake 那批）  ｜  r_adj（其余）
    真实除权与新股首周暴涨都在盘面价上同幅越界 ⇒ 不属于 fake，一律原样保留。
"""

import pandas as pd
import pytest

from ashare_screen import board_of, guard_ret, limit_up_of

# M1 代码段 → 板块（判板块只用前缀，不看名称也不看价格）
BOARD_CASES = [
    ("SZ000001", "主板"),
    ("SH600000", "主板"),
    ("SH688981", "科创板"),
    ("SH689009", "科创板"),      # 689 = 科创板 CDR，历史上漏过一次
    ("SZ300750", "创业板"),
    ("SZ301158", "创业板"),
    ("BJ838227", "北交所"),
    ("sz300750", "创业板"),      # 大小写都吃得下
]

# M2 板块 → 贴涨停阈值（限幅 × 0.95）
LIMIT_CASES = [
    ("SZ000001", 0.095),
    ("SH688981", 0.19),
    ("SZ300750", 0.19),
    ("BJ838227", 0.285),
]


@pytest.mark.parametrize("code,board", BOARD_CASES)
def test_board_of_按代码前缀分段(code, board):
    assert board_of(code) == board


@pytest.mark.parametrize("code,threshold", LIMIT_CASES)
def test_limit_up_of_三段各用自己的限幅(code, threshold):
    assert limit_up_of(code) == pytest.approx(threshold, abs=1e-9)


def test_limit_up_of_未配限幅的板块回落旧单一阈值():
    """北交所那档如果被从表里摘掉，阈值必须退回 ASHARE_PORT_LIMIT_UP 而不是崩。

    牙口：把回落常数改成 0.095 之外的任何值，本条红。
    """
    import config
    from ashare_screen import ASHARE_BOARD_LIMIT_UP

    missing = "不存在的板块"
    assert missing not in ASHARE_BOARD_LIMIT_UP
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(ASHARE_BOARD_LIMIT_UP, "主板", None)
        assert limit_up_of("SZ000001") == pytest.approx(config.ASHARE_PORT_LIMIT_UP)


def test_guard_ret_只裁复权侧越界而盘面侧正常那批():
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-05") + pd.Timedelta(days=i), f"SZ00000{i}") for i in range(4)],
        names=["datetime", "instrument"])
    ret = pd.Series([0.05, 1.50, -0.35, 0.50], index=idx, name="ret")
    raw = pd.Series([0.05, -0.02, 0.31, -0.40], index=idx, name="raw")

    out = guard_ret(ret, raw)

    assert out.iloc[0] == pytest.approx(0.05)     # 正常日：原样
    assert out.iloc[1] == pytest.approx(0.30)     # 假台阶：裁到上限
    assert out.iloc[2] == pytest.approx(-0.35)    # 真实除权（盘面同向越界）：不裁
    assert out.iloc[3] == pytest.approx(0.50)     # 新股首周（两侧同时越界）：不裁
    # 裁的是值，不是行数：置 NaN 会改当天截面样本数、毁掉与旧基线的可比性
    assert len(out) == 4 and out.notna().all()


def test_guard_ret_对宽表同样成立():
    """组合层拿 Series、截面评估拿 DataFrame，同一份实现必须两边都吃得下。"""
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-05"), "SZ000001"),
         (pd.Timestamp("2026-01-06"), "SZ000001")], names=["datetime", "instrument"])
    wide = pd.DataFrame({"a": [1.50, 0.02], "b": [-0.90, 0.03]}, index=idx)
    raw = pd.DataFrame({"a": [-0.02, 0.01], "b": [0.50, 0.01]}, index=idx)

    out = guard_ret(wide, raw)

    assert out["a"].iloc[0] == pytest.approx(0.30)   # fake → 裁
    assert out["a"].iloc[1] == pytest.approx(0.02)   # 正常
    assert out["b"].iloc[0] == pytest.approx(-0.90)  # 两侧同越界 → 不裁
    assert out["b"].iloc[1] == pytest.approx(0.03)
