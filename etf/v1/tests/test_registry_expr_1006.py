# -*- coding: utf-8 -*-
"""注册表因子的 DSL 表达式（10-06 用户裁「第 2 问＝丙：不改闸、改发射端」）。

要钉住的判决：`FACTOR_REGISTRY` 那 9 条 lambda 进库时**必须带一条与实算逐位相同的
`expr`**。在此之前这条腿交出的 dict 根本没有 `expr` 键，写库腿
`main.py:334` 的 `f.get("expr", "")` 就落空串——**每场 4 行**（10-06 实测，9 份日更
日志的「入池因子」都是 4）。空串不是无害的：库里存的是 lambda 算出来的 IC，而
`run_live.py:193` 打分时求值的是**库里那条 `expr`**；`expr` 为空则 `:156` 的非空过滤
直接把这几个名字挡在实盘门外，于是「成绩在账上、机器够不着」。

夹具五组：配对 1 格／过闸 1 格／等价 9 格（含**人造停牌缺口**那一档）／发射端真接上 1 格
／负对照 1 格（窗口差一档必须被等价尺判为不等——不然前三组就是恒真）。

⚠️ 等价对拍为什么要在**含缺口的池子**上再拍一遍（这一格是本批自我推翻换来的）：
第一版只在真池子上拍，9 条全判「A 形可用」；但真池子 `close/high/low/volume` 的
内部缺值是 **0 格**，而 DSL 的 `returns` 写 `pct_change(fill_method=None)`、
lambda 写 `pct_change()`（pandas 2.2 默认仍是 `'pad'`）⇒ 这两台机器**只在有停牌缺口
的数据上才分家**。无缺口给的只是"今天相等"，不是"按构造相等"，所以落地的形按
"含缺口也逐位相同"优先（前四条因此取方法链形）。全量对拍的尺子在
`etf/v1/temp/registry_expr_equivalence_1006.py`，本文件是它的常驻缩小版。
"""

import numpy as np
import pandas as pd
import pytest

from factor_dsl import safe_eval
from factor_static_check import check_expr
from factors import FACTOR_EXPR, FACTOR_REGISTRY

import main as M

# 合成池里挖的人造停牌缺口（起点、长度都写在下面）
HOLE_START = 500
HOLE_LEN = 3


@pytest.fixture(scope="module")
def holed_pool():
    """3 只标的 × 900 根 bar，其中一只在中间断 3 天（整行 OHLCV 皆空）。

    缺口必须**落在窗口里面**（`ma_ratio_10_30` 的 30 日窗、`price_position_20` 的
    20 日窗都跨过 500 这一带），否则 `pad` 与 `None` 两种口径不会分家＝白测。
    """
    pool = _clean_pool()
    victim = next(iter(pool))
    df = pool[victim]
    df.loc[df.index[HOLE_START:HOLE_START + HOLE_LEN],
           ["open", "high", "low", "close", "volume"]] = np.nan
    return pool


def _bitwise_identical(a: pd.Series, b: pd.Series) -> bool:
    """NaN 位置一致 **且** 共同非 NaN 格 max|Δ|==0（不放宽到 1e-12）"""
    if a.shape != b.shape or not a.index.equals(b.index):
        return False
    na, nb = a.isna().to_numpy(), b.isna().to_numpy()
    if (na != nb).any():
        return False
    both = ~na
    if not both.any():
        return True
    return float(np.max(np.abs(a.to_numpy()[both] - b.to_numpy()[both]))) == 0.0


# ---------- 配对：9 条 lambda 各有且只有一条表达式 ----------

def test_registry_and_expr_are_paired():
    assert set(FACTOR_EXPR) == set(FACTOR_REGISTRY)
    assert all((e or "").strip() for e in FACTOR_EXPR.values())


# ---------- 过闸：写库那一刻不能被静态闸挡 ----------

@pytest.mark.parametrize("name", sorted(FACTOR_EXPR))
def test_expr_passes_static_gate(name):
    assert check_expr(FACTOR_EXPR[name]) == "", check_expr(FACTOR_EXPR[name])


# ---------- 等价：表达式算出来的列必须与 lambda 逐位相同 ----------

@pytest.mark.parametrize("name", sorted(FACTOR_EXPR))
def test_expr_is_bitwise_identical_to_lambda(name, holed_pool):
    fn = FACTOR_REGISTRY[name]
    checked = 0
    for code, df in holed_pool.items():
        got = safe_eval(FACTOR_EXPR[name], df)
        assert _bitwise_identical(fn(df), got), (
            f"{name} 在 {code} 上表达式与 lambda 不同（NaN 位置或数值任一层差过）")
        checked += 1
    assert checked == len(holed_pool)


# ---------- 发射端：`mine_factors` 交出的每一条都带着 expr ----------

def test_emitter_attaches_expr_to_inbound_factors(capsys):
    pool = {c: df for c, df in _clean_pool().items()}
    got = M.mine_factors(pool)
    assert len(got) >= 1, "一条都没入池 ⇒ 这一格会恒真，先看 seed/门槛"
    assert all((f.get("expr") or "").strip() for f in got)
    assert all(f["expr"] == FACTOR_EXPR[f["name"]] for f in got)


# ---------- 负对照：等价尺必须抓得住"窗口写错一档" ----------

@pytest.mark.parametrize("name, wrong", [
    ("momentum_20", "close / delay(close, 19) - 1"),
    ("volatility_20", "0 - ts_std(returns, 19)"),
    ("price_position_20", "2 * (close - ts_min(low, 20)) / "
                          "(ts_max(high, 20) - ts_min(low, 20)) - 1"),
])
def test_equivalence_ruler_has_teeth(name, wrong, holed_pool):
    """抓不到＝上面那 9 格是恒真判据。三条分别踩在两层上：
    前两条差在 **NaN 层**（少一行头 NaN，共同格数值却相等），第三条差在**数值层**
    （少了 `+1e-9` 的防零除，量级只有 1e-7）——两层都得看得见才算这把尺有牙。"""
    fn = FACTOR_REGISTRY[name]
    hits = sum(1 for df in holed_pool.values()
               if not _bitwise_identical(fn(df), safe_eval(wrong, df)))
    assert hits == len(holed_pool)


def test_pad_vs_none_seam_is_real(holed_pool):
    """本文件为什么要在含缺口的池子上拍：这条直接量那个分岔口本身。

    `close / delay(close, 20) - 1`（DSL 惯用形）与 lambda 的 `close.pct_change(20)`
    在无缺口数据上逐位相同，一旦洞里去就分家 ⇒ 若只在真池子上测，第一版会误判"A 可落"。
    """
    victim = next(iter(holed_pool))
    df = holed_pool[victim]
    a = safe_eval("close / delay(close, 20) - 1", df)
    b = FACTOR_REGISTRY["momentum_20"](df)
    assert not _bitwise_identical(a, b), "缺口没落进窗口 ⇒ 这一格已经失去意义"
    clean = _clean_pool()[victim]
    assert _bitwise_identical(safe_eval("close / delay(close, 20) - 1", clean),
                              FACTOR_REGISTRY["momentum_20"](clean))


def _clean_pool():
    """与 `holed_pool` 同一份 seed，但没挖缺口——用来证"无缺口时两形相等" """
    from synth import make_daily_pool
    return make_daily_pool(n_codes=3, n_bars=900, seed=20261006)
