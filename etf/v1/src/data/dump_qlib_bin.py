# -*- coding: utf-8 -*-
"""把 ETF 日线缓存 dump 成 qlib bin，供 ETF 线的 RD-Agent(Q) 沙箱读取

为什么需要这一步：RD-Agent(Q) 的因子回测跑在 qlib 上，只认 qlib 的日历 +
二进制特征文件；ETF 线此前没有任何 qlib 数据，官方循环一旦拉起就会去读股票线
那份 A 股 bin（个股面板），算出来的 IC 与本线池子无关。本脚本用本线
`data/cache/<code>_daily.csv`（akshare 前复权日线，与回测同源）造出一份只含
ETF 的 bin，落位 `data/qlib/qlib_data/cn_data`（= config.RDAGENT_QLIB_PROVIDER）。

bin 格式（与 chenditc/investment_data 一致，读回校验见 --verify）：
    features/<instrument小写>/<field>.day.bin = float32[]，
    第 0 个元素是该序列在 calendars/day.txt 里的起始下标，其后逐日对齐；
    instruments/<market>.txt 每行 `INSTR<TAB>起始日<TAB>结束日`。

默认池子是 `data/universe_all/`（全市场 ETF，由 `fetch_etf_universe_all.py` 拉），
不是主线那 20 只代表池 —— qlib 的 running 步做横截面回归，20 只池日均 7.7 只有
行情，IC 全是噪声。要复现旧的小池行为传 `--cache <CACHE_DIR>`。

market 名单是必需的伪装：rdagent 的 conf 模板写死 `market: csi300`
（factor_template/conf_*.yaml，且模板目录路径不可配、APP_TPL 也管不到），
所以 ETF bin 里 csi300/csi500/... 全部指向 ETF 池 —— 容器里的"CSI300 回测"
实际跑的就是这池 ETF。

$factor 恒为 1：缓存价已是 akshare 前复权（qfq），再乘因子会二次复权。

基准指数是另一回事：qlib 的持仓回测在 exchange.py 的 check_benchmark 里硬性要求
benchmark 标的能在 bin 里读到 $open，rdagent 的 conf 写死 `benchmark: SH000300`。
ETF 池本身不含指数，缺它会在 running 步末尾抛
`ValueError: The benchmark ['SH000300'] does not exist` —— IC 出得来、
excess_return 出不来（09-22 20:56 那轮实测）。故额外 dump 宽基指数的 features，
但**不写进任何 instruments/<market>.txt**：指数不是可选标的，混进池子会污染
横截面 IC，也让 Alpha158 把指数当成一只 ETF。
用法：
    cd etf/v1/src && /usr/bin/python3.10 data/dump_qlib_bin.py [--out DIR]
"""

import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

# 与股票线 bin 的字段集对齐：Alpha158 会用到 $vwap，缺失即 KeyError
PRICE_COLS = ["open", "high", "low", "close"]
VOLUME_COLS = ["volume", "amount"]

# qlib conf 写死的基准 -> akshare 新浪指数代码（可多条，成本可忽略）
BENCHMARK_INDEXES = {"SH000300": "sh000300", "SH000905": "sh000905",
                     "SH000852": "sh000852"}


def _instrument(code: str) -> str:
    """6 位代码 → qlib 标的名。沪市基金以 5/6/9 开头，其余归深市"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def read_pool(cache_dir: str) -> dict:
    """{ETF 代码: 日线 DataFrame}，只收本线缓存里的日线（与回测同一份数据）"""
    pool = {}
    for path in sorted(glob.glob(os.path.join(cache_dir, "*_daily.csv"))):
        code = os.path.basename(path)[:-len("_daily.csv")]
        try:
            df = pd.read_csv(path, encoding="utf-8-sig",
                             parse_dates=["date"])
        except Exception as e:
            print(f"  ⚠️ 跳过 {code}: {type(e).__name__}: {e}")
            continue
        keep = [c for c in ["date"] + PRICE_COLS + VOLUME_COLS
                if c in df.columns]
        df = (df[keep].drop_duplicates(subset="date", keep="last")
              .set_index("date").sort_index())
        df = df.dropna(subset=["close"])
        if len(df) < 60:
            print(f"  ⚠️ 跳过 {code}: 仅 {len(df)} 根 K 线")
            continue
        pool[code] = df
    return pool


def derive_fields(df: pd.DataFrame) -> pd.DataFrame:
    """补齐 qlib 侧需要而缓存没有的列：$factor/$vwap/$adjclose/$change

    vwap = 成交额 / 成交量（元/股），与交易所口径同为当日均价；
    change = 前收盘环比涨跌幅，pct_change 必须显式 fill_method=None，
    否则停牌日会被前向填充成 0 以外的假收益。
    """
    out = df.copy()
    out["factor"] = 1.0
    out["adjclose"] = out["close"]
    vol = out["volume"].replace(0, np.nan)
    if "amount" in out.columns:
        out["vwap"] = (out["amount"] / vol).fillna(out["close"])
    else:
        out["vwap"] = out["close"]
    out["change"] = out["close"].pct_change(fill_method=None)
    return out


def fetch_index(sina_code: str):
    """新浪指数日线（date/open/high/low/close/volume）。

    em 源（index_zh_a_hist）近期常 RemoteDisconnected，而基准只要 $open/$close，
    新浪这份没有成交额，故 vwap 在 derive_fields 里退化用 close。
    """
    import akshare as ak
    try:
        df = ak.stock_zh_index_daily(symbol=sina_code)
    except Exception as e:
        print(f"  ⚠️ 指数 {sina_code} 拉取失败: {type(e).__name__}: {e}")
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df[[c for c in ["date"] + PRICE_COLS + ["volume"]
               if c in df.columns]].dropna(subset=["close"])


def fetch_benchmarks(cache_dir: str) -> dict:
    """基准指数日线 {标的名: DataFrame}，CSV 落缓存以免每次 dump 都联网"""
    os.makedirs(cache_dir, exist_ok=True)
    out = {}
    for inst, sina_code in BENCHMARK_INDEXES.items():
        path = os.path.join(cache_dir, f"{inst}.csv")
        df = None
        if os.path.exists(path) and os.path.getsize(path) > 1024:
            df = pd.read_csv(path, encoding="utf-8-sig", parse_dates=["date"])
        if df is None or df.empty:
            df = fetch_index(sina_code)
            if df is None or df.empty:
                print(f"  ⚠️ 缺基准 {inst}：running 步的持仓回测会继续报 "
                      f"The benchmark ['{inst}'] does not exist")
                continue
            df.to_csv(path, index=False, encoding="utf-8-sig")
        out[inst] = df.set_index("date").sort_index()
    return out


def _write_field(fdir: str, col: str, start: int, calendar, series) -> None:
    """写一个字段的 .day.bin：[0] 位是起始下标，其后从 start 一路对齐到日历末尾

    标的在日历里缺的那些天留 NaN（不前向填充）：ETF 停牌日与指数缺日都走这条。
    """
    tail = calendar[start:]
    arr = np.full(len(tail) + 1, np.nan, dtype="<f4")
    arr[0] = start
    arr[1:] = series.reindex(tail).to_numpy(dtype="<f4")
    arr.tofile(os.path.join(fdir, f"{col}.day.bin"))


def write_bin(target: str, pool: dict, benchmarks: dict = None) -> dict:
    """写日历 + 标的清单 + 特征 bin，返回 {标的名: (起始日, 结束日)}

    benchmarks 只写 features，不进 instruments 清单——它是回测基准，不是可选标的。
    """
    qlib_dir = os.path.join(target, "qlib_data", "cn_data")
    os.makedirs(os.path.join(qlib_dir, "calendars"), exist_ok=True)
    os.makedirs(os.path.join(qlib_dir, "instruments"), exist_ok=True)

    all_days = sorted(set().union(*[set(df.index) for df in pool.values()]))
    calendar = pd.DatetimeIndex(all_days)
    day_str = [d.strftime("%Y-%m-%d") for d in calendar]
    pos = {d: i for i, d in enumerate(day_str)}
    with open(os.path.join(qlib_dir, "calendars", "day.txt"), "w") as fh:
        fh.write("\n".join(day_str) + "\n")

    spans = {}
    for code, raw in pool.items():
        inst = _instrument(code)
        df = derive_fields(raw)
        fdir = os.path.join(qlib_dir, "features", inst.lower())
        os.makedirs(fdir, exist_ok=True)
        start = pos[df.index[0].strftime("%Y-%m-%d")]
        for col in df.columns:
            _write_field(fdir, col, start, calendar, df[col])
        spans[inst] = (day_str[start], df.index[-1].strftime("%Y-%m-%d"))

    for inst, raw in (benchmarks or {}).items():
        df = derive_fields(raw)
        in_cal = df.index[df.index.isin(calendar)]
        if len(in_cal) < 60:
            print(f"  ⚠️ 基准 {inst} 落在 ETF 日历内只有 {len(in_cal)} 天，跳过")
            continue
        fdir = os.path.join(qlib_dir, "features", inst.lower())
        os.makedirs(fdir, exist_ok=True)
        start = int(calendar.get_loc(in_cal[0]))
        for col in df.columns:
            _write_field(fdir, col, start, calendar, df[col])
        print(f"  基准 {inst}: {len(in_cal)} 天 "
              f"{in_cal[0].strftime('%Y-%m-%d')}~{in_cal[-1].strftime('%Y-%m-%d')}"
              f"（不进 instruments）")

    lines = [f"{i}\t{s}\t{e}" for i, (s, e) in sorted(spans.items())]
    # rdagent 的 conf 模板写死 market: csi300，故各 market 一律给同一份 ETF 池
    for market in ("all", "csi300", "csi500", "csi800", "csi1000", "csiall"):
        with open(os.path.join(qlib_dir, "instruments", f"{market}.txt"),
                  "w") as fh:
            fh.write("\n".join(lines) + "\n")
    return spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None,
                    help="qlib 挂载根（内含 qlib_data/cn_data），默认 config 的"
                         " RDAGENT_QLIB_PROVIDER 往上两级")
    ap.add_argument("--cache", default=None,
                    help="日线缓存目录，默认 UNIVERSE_ALL_DIR（全市场 ETF）；"
                         "传 CACHE_DIR 可退回主线那 20 只代表池")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import _bootstrap  # noqa: F401  裸模块名导入（from config import ...）的前提
    from config import UNIVERSE_ALL_DIR, RDAGENT_QLIB_PROVIDER
    cache_dir = args.cache or UNIVERSE_ALL_DIR
    target = args.out or os.path.dirname(os.path.dirname(RDAGENT_QLIB_PROVIDER))
    index_dir = os.path.join(os.path.dirname(UNIVERSE_ALL_DIR), "index_cache")

    pool = read_pool(cache_dir)
    if not pool:
        raise SystemExit(f"缓存目录没有可用的日线 CSV: {cache_dir}")
    spans = write_bin(target, pool, fetch_benchmarks(index_dir))
    days = sum(len(df) for df in pool.values())
    print(f"dump 完成: {len(spans)} 只 ETF / {days} 行 -> {target}"
          f"/qlib_data/cn_data")
    print(f"  日历 {min(s for s, _ in spans.values())} ~ "
          f"{max(e for _, e in spans.values())}")
    print(f"  校验：QLIB_PROVIDER_URI={RDAGENT_QLIB_PROVIDER} "
          f"/home/sunwenkun/miniconda3/envs/rdagent/bin/python -c \"import qlib;"
          f"from qlib.data import D; qlib.init(provider_uri='{RDAGENT_QLIB_PROVIDER}');"
          f"print(D.features(D.instruments('csi300'), ['$close','$vwap']))\"")


if __name__ == "__main__":
    main()
