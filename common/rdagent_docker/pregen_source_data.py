# -*- coding: utf-8 -*-
"""预生成 RD-Agent(Q) 因子实现所需的源数据（官方流程的本地替代）

官方 `generate_data_folder_from_qlib()` 要在 docker 容器里跑它的
generate.py，且其 debug 切片直接取 instruments 前 100 只——本数据集
含已退市无行情者（SH600001/003/005/087/102），pandas .loc 抛 KeyError。
本脚本按同一口径重算两份 h5 并落到 FACTOR_COSTEER_SETTINGS 指定的
data_folder / data_folder_debug；这两个目录一旦存在，官方流程就不再
触发容器内生成，绕开三方代码假设与本机的 docker 依赖。

数据来自哪份 qlib bin 由环境变量 QLIB_PROVIDER_URI 显式给出（分树后
两条线各有一份：stock/v1/data/qlib/... 与 etf/v1/data/qlib/...）。脚本
不再默认去 rdagent 包里 copy 那份 A 股 daily_pv_all.h5——ETF 线一旦沿用
就会拿个股行情去跑 ETF 因子。确实要复现成容器产物时用
QLIB_PREGEN_SOURCE_H5 指明路径。

列名口径对齐：h5 原始列带 $ 前缀（$close…），而 LLM 生成的 factor.py
按 df['close'] 取列，两口径不一致会在 execute("All") 里抛 KeyError，
使 running 阶段 process_factor_data 合不出因子数据。故为每个 $col 追加
一份去 $ 的别名列（$close 与 close 并存），两种取法都能命中。

过期判定：已有 h5 只在其最后一行日期 >= 该 qlib bin 日历末交易日时才复用，
否则重算。否则「拉了最新数据」这一步会静默被旧 h5 挡掉。

用法（在 rdagent conda 环境、工作区目录下）：
    QLIB_PROVIDER_URI=/abs/path/<line>/v1/data/qlib/qlib_data/cn_data \
        python .../pregen_source_data.py <workspace_out_dir>
"""

import os
import shutil
import sys
from pathlib import Path

import pandas as pd

PROVIDER = Path(os.environ.get("QLIB_PROVIDER_URI", "")).expanduser()
REUSE_ALL = os.environ.get("QLIB_PREGEN_SOURCE_H5", "").strip()
FIELDS = ["$open", "$close", "$high", "$low", "$volume", "$factor"]
# 全量面板的起点：官方 generate.py 同样从 2008-12-29 起（前一日留作 shift 余量）
START = "2008-12-29"
# debug 切片口径与官方一致：2018 全年 + 2019 全年、instruments 前 100 只
DEBUG_START, DEBUG_END = "2018-01-01", "2019-12-31"
DEBUG_N = 100

_flagged = [a for a in sys.argv[1:] if not a.startswith("-")]
OUT = Path(_flagged[0]).resolve() if _flagged else Path.cwd()
ALL_DIR = OUT / "git_ignore_folder/factor_implementation_source_data"
DEBUG_DIR = OUT / "git_ignore_folder/factor_implementation_source_data_debug"


def calendar_end() -> pd.Timestamp:
    """该 qlib bin 日历的最后一个交易日——数据打到了哪天。"""
    day_txt = PROVIDER / "calendars" / "day.txt"
    lines = [ln.strip() for ln in day_txt.read_text().splitlines() if ln.strip()]
    if not lines:
        raise SystemExit(f"empty calendar: {day_txt}")
    return pd.Timestamp(lines[-1])


def universe_size() -> int:
    """该 qlib bin 的标的数（instruments/all.txt 行数，即 D.instruments() 的池子）。

    缺文件或为 0 时返回 0，表示无从判定、不复用任何旧面板。
    """
    f = PROVIDER / "instruments" / "all.txt"
    if not f.is_file():
        return 0
    return sum(1 for ln in f.read_text().splitlines() if ln.strip())


def panel_instruments(df: pd.DataFrame) -> int:
    """面板里出现过的标的数。MultiIndex 里非 Timestamp 的那一层即 instrument。"""
    for i in (0, 1):
        vals = df.index.get_level_values(i)
        if not isinstance(vals.max(), pd.Timestamp):
            return vals.nunique()
    raise SystemExit("no instrument level in h5")


def panel_end(df: pd.DataFrame) -> pd.Timestamp:
    """面板最后一行的日期。MultiIndex 里唯一的 Timestamp 层即 datetime。"""
    for i in (0, 1):
        v = df.index.get_level_values(i).max()
        if isinstance(v, pd.Timestamp):
            return v
    raise SystemExit("no datetime level in h5")


def init_qlib() -> None:
    """只在全量重算时才连 qlib；缺 provider 直接报错，绝不回落到包内 A 股数据"""
    if not PROVIDER.is_dir():
        raise SystemExit(
            f"QLIB_PROVIDER_URI 未设置或目录不存在: {PROVIDER or '(空)'}\n"
            "指向本线的 data/qlib/qlib_data/cn_data（见 RDAGENT_QLIB_PROVIDER）")
    import qlib
    qlib.init(provider_uri=str(PROVIDER))
    print(f"qlib.init provider_uri={PROVIDER}")


def with_alias(df: pd.DataFrame) -> pd.DataFrame:
    """为每个 `$col` 追加去 `$` 的别名列（已存在同名裸列则不覆盖）。"""
    for col in df.columns:
        if isinstance(col, str) and col.startswith("$"):
            bare = col[1:]
            if bare not in df.columns:
                df[bare] = df[col]
    return df


def missing_alias(df: pd.DataFrame) -> list:
    return [c[1:] for c in df.columns
            if isinstance(c, str) and c.startswith("$") and c[1:] not in df.columns]


def save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_hdf(path, key="data", mode="w")   # mode='w': 整文件覆盖，避免重复 key 报错
    print(f"wrote {path} rows={len(df)} cols={len(df.columns)} "
          f"end={panel_end(df).date()}")


# ---------- 全量面板 ----------
all_path = ALL_DIR / "daily_pv.h5"
need_full = True
if all_path.exists():
    df = pd.read_hdf(all_path, key="data")
    end, want = panel_end(df), calendar_end()
    n_have, n_want = panel_instruments(df), universe_size()
    miss = missing_alias(df)
    if end >= want and not miss and (not n_want or n_have >= n_want):
        need_full = False
        print(f"all: reuse {all_path} end={end.date()} "
              f"instruments={n_have}/{n_want} alias ok")
    else:
        print(f"all: stale/incomplete end={end.date()} want>={want.date()} "
              f"instruments={n_have}/{n_want} missing_alias={miss} -> 重新生成")
        del df
if need_full:
    src = Path(REUSE_ALL).expanduser() if REUSE_ALL else None
    if src and src.is_file():
        ALL_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, all_path)
        print(f"reused {src} -> {all_path} ({all_path.stat().st_size} B)")
        df = pd.read_hdf(all_path, key="data")
        save(with_alias(df), all_path)
    else:
        init_qlib()
        from qlib.data import D
        df = (D.features(D.instruments(), FIELDS, freq="day")
              .swaplevel().sort_index().loc[START:].sort_index())
        save(with_alias(df), all_path)

# ---------- debug 切片（官方口径：短区间 + 前 100 只） ----------
# 每次都从全量面板重算：切片只有 100 只 × 2 年，秒级；而它的区间固定在
# 2019 年底，用「末日期」根本判不出上游是否重述过复权因子。
dbg_path = DEBUG_DIR / "daily_pv.h5"
full = pd.read_hdf(all_path, key="data")
# h5 索引为 (datetime, instrument)，按标的取前 100 只要先换成 (instrument, datetime)
panel = full.loc[DEBUG_START:DEBUG_END].swaplevel().sort_index()
picked = list(panel.index.get_level_values(0).unique()[:DEBUG_N])
save(with_alias(panel.loc[picked].swaplevel().sort_index()), dbg_path)
README_SRC = (Path(__file__).with_name("README.md")
              if Path(__file__).with_name("README.md").is_file() else
              Path("~/miniconda3/envs/rdagent/lib/python3.10/site-packages/rdagent/"
                   "scenarios/qlib/experiment/factor_data_template/README.md"
                   ).expanduser())
if README_SRC.is_file():
    shutil.copy(README_SRC, DEBUG_DIR / "README.md")
print("DONE")
