# -*- coding: utf-8 -*-
"""面板落盘的原子性：`common/rdagent_docker/pregen_source_data.py` 的 `save()`。

被测对象**不是副本**：现读生产源码、只 exec 里面的 `save` / `panel_end` 两个函数定义
⇒ 生产那一版一改，这里立刻跟着变（判据单点，不复制逻辑）。

原子写的全部意义一句话：**坏情形下旧档一个字节都不能动，且不留半档。**
旧写法 `df.to_hdf(path, mode="w")` 是原地覆盖，第②步被 OOM 砍在中途就只剩半截面板，
而它是第③步与环 2/环 3 的共同输入。

T1..T7 与临时夹具 `stock/v1/temp/pregen_atomic_fixture_0929.py` 同一套格子；
T7 那两格是**夹具的牙**：把 `save()` 退回改动前那一版再喂 T3/T4 的输入，两格都必须判红
——若 T7 反而全绿，说明 T3/T4 是恒绿的装饰、本文件不可信。
"""

import ast
import hashlib
import os
import pathlib

import pandas as pd
import pytest

SRC = (pathlib.Path(__file__).resolve().parents[3]
       / "common" / "rdagent_docker" / "pregen_source_data.py")

_ORIG_TO_HDF = pd.DataFrame.to_hdf


def _load_save(path: pathlib.Path):
    """从生产源码里 exec 出 save()（含它依赖的 panel_end），不复制一份实现。"""
    keep = [n for n in ast.parse(path.read_text()).body
            if isinstance(n, ast.FunctionDef) and n.name in {"save", "panel_end"}]
    assert keep, f"{path} 里找不到 save()"
    ns = {"pd": pd, "os": os, "Path": pathlib.Path}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), ns)
    return ns["save"]


def old_save(df, path):
    """改动前那一版（原地 `to_hdf(mode='w')`）——只给 T7 当退化对照。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_hdf(path, key="data", mode="w")


def mkdf(n: int) -> pd.DataFrame:
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-09-25") + pd.Timedelta(days=i), "SZ000001") for i in range(n)],
        names=["datetime", "instrument"])
    return pd.DataFrame({"$close": [10.0 + i for i in range(n)],
                         "$volume": [1000.0 + i for i in range(n)]}, index=idx)


def md5(p: pathlib.Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "(无此档)"


def shape_of(p: pathlib.Path):
    with pd.HDFStore(p, mode="r") as st:
        return tuple(st.get_storer("data").shape)


def leftover(d: pathlib.Path):
    return sorted(x.name for x in d.glob("*.part"))


def seed(tmp_path: pathlib.Path, rows: int) -> pathlib.Path:
    """开一格：目录里放一张 rows 行的旧档当 daily_pv.h5，返回其路径。"""
    tgt = tmp_path / "daily_pv.h5"
    _ORIG_TO_HDF(mkdf(rows), tgt, key="data", mode="w")
    return tgt


def half_write_then_raise(df, target, **kw):
    """T3：真把前 2 行写到盘上（半档留在原地），再抛 —— 模拟写到一半被杀。"""
    _ORIG_TO_HDF(df.head(2), target, **kw)
    raise MemoryError("模拟：写到一半被杀")


def silent_short_write(df, target, **kw):
    """T4：不抛异常但只写了一部分 —— 这种「静默残缺」最阴险。"""
    _ORIG_TO_HDF(df.head(2), target, **kw)


def run(fn, df, tgt, fake=None):
    pd.DataFrame.to_hdf = fake or _ORIG_TO_HDF
    try:
        fn(df, tgt)
        return False, ""
    except BaseException as exc:  # noqa: BLE001 —— 任何异常都要接住来判定
        return True, f"{type(exc).__name__}: {exc}"
    finally:
        pd.DataFrame.to_hdf = _ORIG_TO_HDF


def survived_intact(tgt, before, raised, want_raise) -> bool:
    return raised == want_raise and md5(tgt) == before and leftover(tgt.parent) == []


@pytest.fixture
def save():
    return _load_save(SRC)


def test_T1_正常写换新档且不留半档(save, tmp_path):
    tgt = seed(tmp_path, 5)
    before, new = md5(tgt), mkdf(9)
    raised, msg = run(save, new, tgt)
    assert not raised, msg
    assert md5(tgt) != before
    assert shape_of(tgt) == (9, 2)
    assert leftover(tmp_path) == []


def test_T2_目标本来不存在也能首建(save, tmp_path):
    tgt = tmp_path / "daily_pv.h5"
    raised, msg = run(save, mkdf(7), tgt)
    assert not raised, msg
    assert tgt.is_file() and shape_of(tgt) == (7, 2)
    assert leftover(tmp_path) == []


def test_T3_写到一半崩_旧档逐字节不动(save, tmp_path):
    tgt = seed(tmp_path, 5)
    before = md5(tgt)
    raised, msg = run(save, mkdf(9), tgt, fake=half_write_then_raise)
    assert survived_intact(tgt, before, raised, want_raise=True), msg


def test_T4_静默短写被形状回读拦住(save, tmp_path):
    tgt = seed(tmp_path, 5)
    before = md5(tgt)
    raised, msg = run(save, mkdf(9), tgt, fake=silent_short_write)
    # 拦住的方式是抛 SystemExit（回读形状不符就不许覆盖），旧档保持原样
    assert survived_intact(tgt, before, raised, want_raise=True), msg
    assert shape_of(tgt) == (5, 2)


def test_T5_反证_旧写法会被同一输入污染(tmp_path):
    """证明 T4 那次拦截真有代价差：原地覆盖的写法在同一输入下把目标写成 2 行。"""
    tgt = seed(tmp_path, 5)
    before = md5(tgt)
    silent_short_write(mkdf(9), tgt, key="data", mode="w")
    assert md5(tgt) != before
    assert shape_of(tgt) == (2, 2)


def test_T6_崩过一次还能正常顶上(save, tmp_path):
    tgt = seed(tmp_path, 5)
    run(save, mkdf(9), tgt, fake=half_write_then_raise)
    before = md5(tgt)
    raised, msg = run(save, mkdf(11), tgt)
    assert not raised, msg
    assert md5(tgt) != before and shape_of(tgt) == (11, 2)
    assert leftover(tmp_path) == []


@pytest.mark.parametrize("fake,name", [
    (half_write_then_raise, "T3 崩在半途"),
    (silent_short_write, "T4 静默短写"),
])
def test_T7_牙口_退回旧版这两格必须判红(name, fake, tmp_path):
    """把 `save()` 换成改动前那一版，同样的输入必须污染旧档 ⇒ 夹具不是恒绿的装饰。"""
    tgt = seed(tmp_path, 5)
    before = md5(tgt)
    raised, _ = run(old_save, mkdf(9), tgt, fake=fake)
    assert not survived_intact(tgt, before, raised, want_raise=True), \
        f"{name}：旧版竟然「毫发无损」，说明这一格挡不住任何东西"
