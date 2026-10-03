#!/usr/bin/python3.10
# -*- coding: utf-8 -*-
"""甲二那道口子的牙：`spot_labels()` 的备用名称表必须「该顶时才顶、该让位时让位」

改的是判据入口的取数逻辑（run_ashare_daily_signal.py:136），按规矩同批交夹具。
八格：放行 4 · 判红 4 —— 这里的「放行/判红」= 该返回名称表 / 该返回两份空。

格子设计（每格都钉一条会被真实数据推翻的行为）
    甲 无本场 spot + 无 env ⇒ 两份空、来源 none。**这一格是负对照的根**：它保证
       「默认关」不是空话，日更链不带 env 时行为与加这道口子之前逐字节相同。
    乙 无 spot + env 指向不存在的文件 ⇒ 两份空、不崩。
    丙 无 spot + env 指向缺「名称」列的表 ⇒ 两份空、不崩（造出来的表列名打错必须抓）。
    丁 无 spot + env 指向**真**名称表 ⇒ 必须有 SH603429 的名称与 ST 标记、来源带 fallback。
    戊 **有当日真快照 + env 也设了** ⇒ 一律用真快照。这一格是「备用表不许顶掉行情」的牙，
       去掉它，备用表就会在正常日更里悄悄盖掉当日快照。
    己 备用表代码写成小写 sh600000 ⇒ 归一化后必须匹配（与 spot 那条路径同一口径）。
    庚 名称里带小写 st（"Star"）⇒ 不许判成 ST（大小写敏感那一坑，docstring 早就钉着）。
    辛 "*ST集友" 这种带星号的 ⇒ 必须在 ST 集里（真表里的真行，不是手写行形）。

真数据：丁/辛 直接读生产名称表 cache/name_table_sina_20260930.csv；戊/己/庚 的「spot」
由真快照 spot_20260928.csv 取若干真行改日期写成，不手写 CSV 形状。
"""
import datetime as dt
import os
import sys

import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "stock/v1/src"))
import _bootstrap  # noqa: F402,E402
import run_ashare_daily_signal as R  # noqa: E402

SNAP = os.path.join(ROOT, "common/data/stock/daily_snapshot")
CACHE = os.path.join(ROOT, "common/data/stock/cache")
FIXDIR = os.path.join(ROOT, "stock/v1/temp/tmp_spot_labels_fixture_0930")
NAME_TABLE = os.path.join(CACHE, "name_table_sina_20260930.csv")
REAL_SPOT = os.path.join(SNAP, "spot_20260928.csv")
ST_CODE = "SH603429"          # 09-29 名单里那枚 *ST集友（第 21 名）
import datetime as dt
SESSION = dt.date(2026, 9, 29)


def write_rows(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def run_cell(tag, spot_present, fallback, expect_named, expect_st, expect_src_prefix):
    """把 ASHARE_SNAPSHOT_DIR / ASHARE_SPOT_NAME_FALLBACK 两个模块级量换进夹具目录再调"""
    R.ASHARE_SNAPSHOT_DIR = FIXDIR if spot_present else os.path.join(FIXDIR, "empty_dir")
    R.ASHARE_SPOT_NAME_FALLBACK = fallback
    os.makedirs(R.ASHARE_SNAPSHOT_DIR, exist_ok=True)
    names, st, src = R.spot_labels(pd.Timestamp(SESSION))
    bad = []
    got_named = ST_CODE in names
    if got_named != expect_named:
        bad.append(f"名称表里该有 {ST_CODE} 吗：期望 {expect_named}、实得 {got_named}"
                   f"（值 {names.get(ST_CODE, '<无>')!r}）")
    got_st = ST_CODE in st
    if got_st != expect_st:
        bad.append(f"ST 集里该有 {ST_CODE} 吗：期望 {expect_st}、实得 {got_st}"
                   f"（ST 集共 {len(st)} 只）")
    if not src.startswith(expect_src_prefix):
        bad.append(f"来源前缀：期望 {expect_src_prefix!r}、实得 {src!r}")
    print(("  OK  " if not bad else "  红  ") + tag + ("" if not bad else "　" + "；".join(bad)))
    return bad


def main():
    if not (os.path.exists(NAME_TABLE) and os.path.exists(REAL_SPOT)):
        raise SystemExit(f"夹具缺真数据：{NAME_TABLE} / {REAL_SPOT}")
    real = pd.read_csv(REAL_SPOT, encoding="utf-8-sig")
    # 真快照里取含 SH603429 的若干真行，原样写成 09-29 的 spot（戊/己/庚 用同一份底）
    keep = pd.concat([real.head(400), real[real["代码"].str.lower() == ST_CODE.lower()]])
    spot_real = write_rows(keep, os.path.join(FIXDIR, f"spot_{SESSION:%Y%m%d}.csv"))
    assert ST_CODE.lower() in set(keep["代码"].str.lower()), "夹具的底没含那枚 ST"

    t = pd.read_csv(NAME_TABLE, encoding="utf-8-sig")
    bad = []

    print("[甲] 默认关：无 spot + 无 env")
    bad += run_cell("甲 无 spot 无 env ⇒ 两份空、来源 none（默认关必须真是关）",
                    False, "", False, False, "none")

    print("[乙] env 指向不存在的表")
    bad += run_cell("乙 env 文件不在 ⇒ 两份空、不崩",
                    False, os.path.join(CACHE, "name_table_不存在_000000.csv"),
                    False, False, "none")

    print("[丙] 表缺「名称」列")
    broken = write_rows(t[["代码", "取数日"]], os.path.join(FIXDIR, "table_no_name.csv"))
    bad += run_cell("丙 表缺名称列 ⇒ 两份空、不崩", False, broken, False, False, "none")

    print("[丁] 真名称表顶上")
    bad += run_cell("丁 无 spot + 真名称表 ⇒ 有名称、ST 集含 SH603429、来源 fallback",
                    False, NAME_TABLE, True, True, "fallback:")

    print("[戊] 真快照在场 ⇒ 备用表必须让位（关键牙）")
    # 造一张「同名不同人」的表：若备用表顶掉真快照，名称会变成假的那个
    swap = t.copy()
    swap.loc[swap["代码"] == ST_CODE, "名称"] = "这名字是伪造的"
    swap_path = write_rows(swap, os.path.join(FIXDIR, "table_swap.csv"))
    R.ASHARE_SNAPSHOT_DIR = FIXDIR
    R.ASHARE_SPOT_NAME_FALLBACK = swap_path
    names, st, src = R.spot_labels(pd.Timestamp(SESSION))
    cell = []
    if src != f"spot_{SESSION:%Y%m%d}.csv":
        cell.append(f"有真快照却把来源记成了 {src!r}")
    if names.get(ST_CODE) == "这名字是伪造的":
        cell.append("备用表顶掉了当日真快照 ⇒ 名称来自假表")
    if not names:
        cell.append("有真快照却返回空名称表")
    print(("  OK  " if not cell else "  红  ") + "戊 真快照在场 ⇒ 用 spot、备用表一个字都不许盖" +
          ("" if not cell else "　" + "；".join(cell)))
    bad += cell

    print("[己] 表里代码写成小写")
    lower = t.copy()
    lower["代码"] = lower["代码"].str[:2].str.lower() + lower["代码"].str[2:]
    bad += run_cell("己 小写代码 sh603429 ⇒ 归一化后仍匹配", False,
                    write_rows(lower, os.path.join(FIXDIR, "table_lower.csv")),
                    True, True, "fallback:")

    print("[庚] 名称里带小写 st")
    case = t.copy()
    case.loc[case["代码"] == ST_CODE, "名称"] = "Star集友"
    bad += run_cell("庚 名称 'Star集友'（小写 st）⇒ 不许判成 ST", False,
                    write_rows(case, os.path.join(FIXDIR, "table_case.csv")),
                    True, False, "fallback:")

    print("[辛] 带星号的真行")
    row = t[t["代码"] == ST_CODE]["名称"].iloc[0]
    star = "ST" in str(row) and str(row).startswith("*")
    print(("  OK  " if star else "  红  ") + f"辛 真表里 {ST_CODE} 的简称 = {row!r}，必须带 * 且含 ST" +
          ("" if star else "　这一格没有牙：真数据变了要先确认表还对不对"))
    if not star:
        bad.append("辛：真名称表里 SH603429 已不是带星号的 ST ⇒ 夹具的底失效，先重取名称表")

    print(f"\n合计：8 格（放行 4 · 判红 4）｜失败 {len(bad)} 条")
    import shutil
    shutil.rmtree(FIXDIR, ignore_errors=True)
    leftovers = [p for p in os.listdir(os.path.join(ROOT, "stock/v1/temp"))
                 if p == "tmp_spot_labels_fixture_0930"]
    print(f"清理夹具目录：残留 {len(leftovers)} 枚")
    if leftovers:
        bad.append(f"夹具目录没清干净：{leftovers}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
