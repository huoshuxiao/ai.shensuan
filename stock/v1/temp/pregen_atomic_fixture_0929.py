# -*- coding: utf-8 -*-
"""面板原子写 `save()` 的夹具：七类情形、八条断言，全绿才 exit 0。

被检对象**不是副本**：从 `common/rdagent_docker/pregen_source_data.py` 现读源码、
只 exec 里面的 `save` / `panel_end` 两个函数定义 ⇒ 生产那一版改了，这里立刻跟着变。

七格（每格都是一个「应当为真」的断言）：
T1 正常写         ⇒ 目标换新、无 `.part` 残留
T2 目标本来不存在 ⇒ 首建成功
T3 写到一半抛异常 ⇒ 目标逐字节仍是旧档 + `.part` 被清
T4 悄悄只写一部分 ⇒ 形状回读拦住、目标仍是旧档
T5 反证：同样输入喂**旧写法**（原地 mode='w'）⇒ 目标被污染（证明"拦住"不是白拦）
T6 崩过一遍再正常写 ⇒ 能顶上，残留的 `.part` 名字不卡路
T7 夹具的牙：把 `save()` 退回改动前那一版再喂 T3/T4 的输入 ⇒ 两格都得判红
     （若 T7 反而全绿，说明 T3/T4 是恒绿的装饰、本文件不可信）

退出码：0 = 七格全对；1 = 有格不符（打印哪格）。
用法：/home/sunwenkun/miniconda3/envs/rdagent/bin/python stock/v1/temp/pregen_atomic_fixture_0929.py
"""
import ast
import hashlib
import os
import pathlib
import sys

import pandas as pd

REPO = pathlib.Path("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git")
SRC = REPO / "common/rdagent_docker/pregen_source_data.py"
WORK = REPO / "stock/v1/temp/tmp_atomic_0929"

_keep = [n for n in ast.parse(SRC.read_text()).body
         if isinstance(n, ast.FunctionDef) and n.name in {"save", "panel_end"}]
_ns = {"pd": pd, "os": os, "Path": pathlib.Path}
exec(compile(ast.Module(body=_keep, type_ignores=[]), str(SRC), "exec"), _ns)
SAVE = _ns["save"]

_ORIG_TO_HDF = pd.DataFrame.to_hdf
results = []


def old_save(df: pd.DataFrame, path: pathlib.Path) -> None:
    """改动前那一版（5 行、原地 `to_hdf(mode='w')`）—— 只给 T7 当退化对照用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_hdf(path, key="data", mode="w")


def md5(p: pathlib.Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "(无此档)"


def mkdf(n: int) -> pd.DataFrame:
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-09-25") + pd.Timedelta(days=i), "SZ000001") for i in range(n)],
        names=["datetime", "instrument"])
    return pd.DataFrame({"$close": [10.0 + i for i in range(n)],
                         "$volume": [1000.0 + i for i in range(n)]}, index=idx)


def fresh(name: str, rows: int) -> pathlib.Path:
    """开一格：清空该格目录，放一张 rows 行的旧档进 daily_pv.h5。"""
    d = WORK / name
    d.mkdir(parents=True, exist_ok=True)
    for stale in d.glob("*.part"):
        stale.unlink()
    tgt = d / "daily_pv.h5"
    if tgt.is_file():
        tgt.unlink()
    _ORIG_TO_HDF(mkdf(rows), tgt, key="data", mode="w")
    return tgt


def leftover(d: pathlib.Path) -> list:
    return sorted(p.name for p in d.glob("*.part"))


def shape_of(p: pathlib.Path):
    with pd.HDFStore(p, mode="r") as st:
        return tuple(st.get_storer("data").shape)


def check(cell: str, cond: bool, note: str) -> None:
    results.append((cell, cond, note))
    print(f"{'✅' if cond else '❌'} {cell} ｜ {note}")


def run(fn, name: str, df: pd.DataFrame, tgt: pathlib.Path, fake=None):
    """跑一次 save()，返回 (是否抛异常, 摘要)。fake 注入写盘故障。"""
    pd.DataFrame.to_hdf = fake or _ORIG_TO_HDF
    try:
        fn(df, tgt)
        return False, ""
    except BaseException as exc:      # noqa: BLE001 —— 任何异常都要接住来判定
        return True, f"{type(exc).__name__}: {exc}"
    finally:
        pd.DataFrame.to_hdf = _ORIG_TO_HDF


def half_write_then_raise(df, target, **kw):
    """T3：真把前 2 行写到盘上（半档留在原地），再抛 —— 模拟 OOM / 断电时文件的最后状态。"""
    _ORIG_TO_HDF(df.head(2), target, **kw)
    raise MemoryError("模拟：写到一半被杀")


def silent_short_write(df, target, **kw):
    """T4：不抛异常，但只写了一部分 —— 这种「静默残缺」最阴险。"""
    _ORIG_TO_HDF(df.head(2), target, **kw)


def survived_intact(tgt, before, raised, want_raise: bool) -> bool:
    """原子写的全部意义：坏情形下旧档一个字节都不能动，且不留半档。"""
    return raised == want_raise and md5(tgt) == before and leftover(tgt.parent) == []


WORK.mkdir(parents=True, exist_ok=True)
print(f"pandas {pd.__version__} ｜ 被测源码 {SRC.relative_to(REPO)}（现读现 exec）")

# T1 正常写
tgt = fresh("t1", 5)
before, new = md5(tgt), mkdf(9)
raised, msg = run(SAVE, "t1", new, tgt)
check("T1 正常写换新", (not raised) and md5(tgt) != before
      and shape_of(tgt) == new.shape and leftover(tgt.parent) == [],
      f"{msg or 'ok'} ｜ 目标 {shape_of(tgt)[0]} 行（写前 5 行）、残留 {leftover(tgt.parent)}")

# T2 目标不存在
tgt = fresh("t2", 5)
tgt.unlink()
raised, msg = run(SAVE, "t2", mkdf(7), tgt)
check("T2 首建", (not raised) and tgt.is_file() and shape_of(tgt)[0] == 7
      and leftover(tgt.parent) == [], f"{msg or 'ok'} ｜ 首建 {shape_of(tgt)[0]} 行")

# T3 中途崩
tgt = fresh("t3", 5)
before = md5(tgt)
raised, msg = run(SAVE, "t3", mkdf(9), tgt, fake=half_write_then_raise)
check("T3 写一半就崩 → 旧档不动", survived_intact(tgt, before, raised, True),
      f"{msg} ｜ 旧档 md5 {'未变' if md5(tgt) == before else '被改!'}、残留 {leftover(tgt.parent)}")

# T4 静默短写
tgt = fresh("t4", 5)
before = md5(tgt)
raised, msg = run(SAVE, "t4", mkdf(9), tgt, fake=silent_short_write)
check("T4 悄悄只写一部分 → 回读拦住", survived_intact(tgt, before, raised, True)
      and shape_of(tgt) == (5, 2),
      f"{msg[:58]}… ｜ 目标仍是 {shape_of(tgt)[0]} 行旧档")

# T5 反证：旧写法在 T4 同一输入下把坏档顶上
tgt = fresh("t5", 5)
before = md5(tgt)
silent_short_write(mkdf(9), tgt, key="data", mode="w")   # 改动前的行为：原地覆盖
check("T5 反证·旧写法会被同一输入污染", md5(tgt) != before and shape_of(tgt) == (2, 2),
      f"原地覆盖后目标只剩 {shape_of(tgt)[0]} 行 ⇒ T4 那一次拦截真有代价差")

# T6 崩过一遍还能正常顶上
tgt = fresh("t6", 5)
run(SAVE, "t6", mkdf(9), tgt, fake=half_write_then_raise)
before = md5(tgt)
raised, msg = run(SAVE, "t6", mkdf(11), tgt)
check("T6 崩后复写能顶上", (not raised) and md5(tgt) != before and shape_of(tgt)[0] == 11
      and leftover(tgt.parent) == [], f"{msg or 'ok'} ｜ 顶上后 {shape_of(tgt)[0]} 行")

# T7 夹具的牙：同一格输入喂旧版 save()，T3/T4 必须判红
for cell, fake in (("T3", half_write_then_raise), ("T4", silent_short_write)):
    tgt = fresh("t7", 5)
    before = md5(tgt)
    raised, msg = run(old_save, "t7", mkdf(9), tgt, fake=fake)
    red = not survived_intact(tgt, before, raised, True)
    check(f"T7 牙口·旧版 save 在 {cell} 输入下必须红", red,
          f"旧档 md5 {'已变' if md5(tgt) != before else '未变'}、异常={raised} ⇒ "
          f"{'红（该红）' if red else '绿（夹具无牙，T3/T4 不可信）'}")

n_pass = sum(1 for _, ok, _ in results if ok)
print(f"\n共 {len(results)} 格，为真 {n_pass}，为假 {len(results) - n_pass}")
for cell, ok, note in results:
    if not ok:
        print(f"   ❌ {cell} ｜ {note}")
print(f"产物目录（可删）：{WORK}")
sys.exit(0 if n_pass == len(results) else 1)
