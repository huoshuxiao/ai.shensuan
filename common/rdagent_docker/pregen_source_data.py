# -*- coding: utf-8 -*-
"""预生成 RD-Agent(Q) 因子实现所需的源数据（官方流程的本地替代）

官方 `generate_data_folder_from_qlib()` 要在 docker 容器里跑它的
generate.py，且其 debug 切片直接取 instruments 前 100 只——本数据集
含已退市无行情者（SH600001/003/005/087/102），pandas .loc 抛 KeyError。
本脚本按同一口径重算两份 h5 并落到 FACTOR_COSTEER_SETTINGS 指定的
data_folder / data_folder_debug；这两个目录一旦存在，官方流程就不再
触发容器内生成，绕开三方代码假设与本机的 docker 依赖。

daily_pv_all.h5 若已由容器产出则直接复用，避免重复全量读盘。

列名口径对齐：h5 原始列带 $ 前缀（$close…），而 LLM 生成的 factor.py
按 df['close'] 取列，两口径不一致会在 execute("All") 里抛 KeyError，
使 running 阶段 process_factor_data 合不出因子数据。故为每个 $col 追加
一份去 $ 的别名列（$close 与 close 并存），两种取法都能命中。追加幂等：
已存在的 h5 若缺别名列则就地补齐，齐全则跳过。
"""

import os
import shutil
import sys
from pathlib import Path

import pandas as pd
import qlib
from qlib.data import D

qlib.init(provider_uri="~/.qlib/qlib_data/cn_data")


def with_alias(df: pd.DataFrame) -> pd.DataFrame:
    """为每个 `$col` 追加去 `$` 的别名列（已存在同名裸列则不覆盖）。"""
    for col in df.columns:
        if isinstance(col, str) and col.startswith("$"):
            bare = col[1:]
            if bare not in df.columns:
                df[bare] = df[col]
    return df


def migrate(path: Path) -> None:
    """就地补齐已有 h5 的别名列；齐全则跳过，避免无谓重写。"""
    df = pd.read_hdf(path, key="data")
    missing = [c[1:] for c in df.columns
               if isinstance(c, str) and c.startswith("$") and c[1:] not in df.columns]
    if not missing:
        print(f"alias ok: {path}")
        return
    df = with_alias(df)
    df.to_hdf(path, key="data", mode="w")   # mode='w': 整文件覆盖，避免重复 key 报错
    print(f"aliased -> {path} added={missing}")

OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
ALL_DIR = OUT / "git_ignore_folder/factor_implementation_source_data"
DEBUG_DIR = OUT / "git_ignore_folder/factor_implementation_source_data_debug"
ALL_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

FIELDS = ["$open", "$close", "$high", "$low", "$volume", "$factor"]

all_path = ALL_DIR / "daily_pv.h5"
if all_path.exists():
    print(f"all: {all_path} already exists")
    migrate(all_path)
else:
    src = Path("~/miniconda3/envs/rdagent/lib/python3.10/site-packages/rdagent/"
                "scenarios/qlib/experiment/factor_data_template/daily_pv_all.h5"
                ).expanduser()
    if src.exists():
        shutil.copy(src, all_path)
        print(f"reused container output -> {all_path} ({all_path.stat().st_size} B)")
        migrate(all_path)
    else:
        df = (D.features(D.instruments(), FIELDS, freq="day")
              .swaplevel().sort_index().loc["2008-12-29":].sort_index())
        df = with_alias(df)
        df.to_hdf(all_path, key="data")
        print(f"generated all -> {all_path} rows={len(df)}")

dbg_path = DEBUG_DIR / "daily_pv.h5"
if dbg_path.exists():
    print(f"debug: {dbg_path} already exists")
    migrate(dbg_path)
else:
    # 官方口径：2018-01-01~2019-12-31、前 100 只标的。h5 索引为
    # (datetime, instrument)，故 level 1 才是标的；从全量切片避免二次读盘
    full = pd.read_hdf(all_path, key="data")
    full = (full.loc["2018-01-01":"2019-12-31"]
            .swaplevel().sort_index())          # -> (instrument, datetime)
    picked = [i for i in full.index.get_level_values(0).unique()[:100]]
    sub = full.loc[picked].swaplevel().sort_index()
    sub = with_alias(sub)
    sub.to_hdf(dbg_path, key="data")
    print(f"generated debug -> {dbg_path} instruments={len(picked)} rows={len(sub)}")

shutil.copy(
    Path("~/miniconda3/envs/rdagent/lib/python3.10/site-packages/rdagent/"
         "scenarios/qlib/experiment/factor_data_template/README.md").expanduser(),
    DEBUG_DIR / "README.md")
print("DONE")
