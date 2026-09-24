# -*- coding: utf-8 -*-
"""数据加载：日线 + 分钟线统一，多数据源按优先级降级（东方财富→新浪→腾讯）"""

import os
import time
import pandas as pd
import akshare as ak
from config import (
    BACKTEST_START, BACKTEST_END, FREQ, CACHE_DIR, FREQ_MAP,
    DATA_SOURCES, UNIVERSE_ALL_DIR,
)

OHLC = ["open", "high", "low", "close"]
# 全市场日线镜像（data/universe_all/）可接受的落后天数：镜像是整批更新的，
# 个别文件比批次末尾还旧超过这个天数，就当成缺数据回源重拉。
MIRROR_MAX_LAG_DAYS = 7


def _sina_symbol(code: str) -> str:
    """沪市基金以 5/6/9 开头，其余归深市"""
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def _last_date(path: str) -> str:
    """只读 CSV 末尾一块，取最后一行的时间列：判断镜像新鲜度不必整份载入"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(f.tell() - 4096, 0))
            tail = f.read().decode("utf-8-sig", "ignore").strip()
        rows = tail.splitlines()
        return rows[-1].split(",")[0].strip() if rows else ""
    except Exception:
        return ""


class DataLoader:
    """统一数据加载器（支持日线和分钟线）"""

    def __init__(self, freq: str = None):
        self.freq = freq or FREQ
        self.is_intraday = FREQ_MAP[self.freq]["is_intraday"]
        os.makedirs(CACHE_DIR, exist_ok=True)
        self._mirror_end = None
        self._stale_codes = []

    def _cache_path(self, code: str) -> str:
        return os.path.join(CACHE_DIR, f"{code}_{self.freq}.csv")

    def _mirror_batch_end(self) -> str:
        """镜像目录的批次末尾日期 = 目录内各文件最新日期的最大值。
        整进程只扫一次（上千份文件只读末块，实测零点几秒）。"""
        if self._mirror_end is None:
            end = ""
            try:
                for fn in os.listdir(UNIVERSE_ALL_DIR):
                    if fn.endswith(f"_{self.freq}.csv"):
                        end = max(end, _last_date(
                            os.path.join(UNIVERSE_ALL_DIR, fn)))
            except OSError:
                end = ""
            self._mirror_end = end
        return self._mirror_end

    def _load_mirror(self, code: str):
        """data/universe_all/ 的全市场日线镜像当作二级缓存用。

        主线池扩到上百只以后，逐只回源既慢又不稳：akshare 的请求不带
        socket 超时，单个连接挂起就能把整轮加载钉死，而这段历史本机已经有了
        （镜像由 data/fetch_etf_universe_all.py 走同一个 _load_daily 落盘，
        列名与复权口径逐字一致）。只在该代码存在、且它没有落后批次末尾
        MIRROR_MAX_LAG_DAYS 天时采用；否则返回 None 交给网络源。"""
        path = os.path.join(UNIVERSE_ALL_DIR, f"{code}_{self.freq}.csv")
        if not os.path.exists(path):
            return None
        end, last = self._mirror_batch_end(), _last_date(path)
        if not end or not last:
            return None
        lag = (pd.Timestamp(end) - pd.Timestamp(last)).days
        if lag > MIRROR_MAX_LAG_DAYS:
            print(f"    ⚠️ 镜像里的 {code} 落后批次末尾 {lag} 天，回源重拉")
            return None
        try:
            df = pd.read_csv(path)
        except Exception:
            return None
        df = self._standardize(df, self._time_col())
        if df.empty:
            return None
        df = df.loc[BACKTEST_START:BACKTEST_END]
        return df if not df.empty else None

    def _cache_is_stale(self, df) -> bool:
        """缓存的末日落后全市场镜像的批次末尾 ⇒ 本轮不能用它。

        判据是「落后于本机已知的最新一天」而不是「旧于今天」：`DataLoader` 原先
        见到缓存就原样返回，于是日更没跑的那几天里，回测安静地算着上周的净值
        （09-24 实测 20 只池缓存全止于 09-18，而同机的镜像止于 09-22）。
        分钟线没有镜像可比，恒判为不陈旧。"""
        if self.is_intraday or df is None or df.empty:
            return False
        end, last = self._mirror_batch_end(), str(df.index[-1])[:10]
        if not end or not last:
            return False
        return pd.Timestamp(last) < pd.Timestamp(end)

    def load(self, code: str, use_cache: bool = True) -> pd.DataFrame:
        """加载单只 ETF 数据：主线缓存 → 全市场镜像 → 网络源（按优先级降级）"""
        cache_file = self._cache_path(code)
        if use_cache and os.path.exists(cache_file):
            try:
                df = pd.read_csv(cache_file,
                                  parse_dates=[self._time_col()])
                df = df.set_index(self._time_col())
                if not self._cache_is_stale(df):
                    return df
                self._stale_codes.append(code)
            except Exception:
                pass

        from_cache = os.path.exists(cache_file)
        mirrored = None if self.is_intraday else self._load_mirror(code)
        if mirrored is not None:
            print(f"    📦 {code} 取全市场镜像 {self.freq} "
                  f"{len(mirrored)} bar（止于 {mirrored.index[-1].date()}）")
            if from_cache:      # 只自愈已有的缓存，不给镜像里没有的标的凭空建档
                mirrored.to_csv(cache_file, encoding="utf-8-sig")
            return mirrored

        sources = DATA_SOURCES["intraday" if self.is_intraday
                               else "daily"]
        df = pd.DataFrame()
        for src in sources:
            try:
                df = (self._load_intraday(code, src) if self.is_intraday
                      else self._load_daily(code, src))
            except Exception as e:
                print(f"    ⚠️ [{src}] {self.freq} {code} 失败: "
                      f"{type(e).__name__}: {e}")
                df = pd.DataFrame()
            if df is not None and not df.empty:
                if src != sources[0]:
                    print(f"    🔁 {code} 已降级到数据源 [{src}]")
                break
        if df.empty:
            print(f"    ❌ {code} 所有数据源均失败: {sources}")
            return pd.DataFrame()

        # 裁剪回测区间
        df = df.loc[BACKTEST_START:BACKTEST_END]
        if df.empty:
            return pd.DataFrame()

        df.to_csv(cache_file, encoding="utf-8-sig")
        time.sleep(0.3)
        return df

    def _time_col(self) -> str:
        return "date" if self.freq == "daily" else "datetime"

    @staticmethod
    def _standardize(raw: pd.DataFrame, time_col: str) -> pd.DataFrame:
        """任意列序统一为 open/high/low/close/volume/amount + 时间索引"""
        cols = [c for c in [time_col] + OHLC + ["volume", "amount"]
                if c in raw.columns]
        df = raw[cols].copy()
        df[time_col] = pd.to_datetime(df[time_col])
        for c in OHLC + ["volume", "amount"]:
            if c not in df.columns:
                df[c] = float("nan")
        df = df.set_index(time_col)
        df = df[OHLC + ["volume", "amount"]].astype(float)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df.dropna(subset=["close"])

    # ---------- 各数据源实现 ----------
    def _load_daily(self, code: str, src: str) -> pd.DataFrame:
        """日线数据（单源，失败抛异常由上层降级）"""
        if src == "em":
            raw = ak.fund_etf_hist_em(
                symbol=code, period="daily",
                start_date="20040101", end_date="20991231",
                adjust="qfq")
            if raw is None or raw.empty:
                return pd.DataFrame()
            raw = raw.rename(columns={
                "日期": "date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume",
                "成交额": "amount"})
            return self._standardize(raw, "date")
        if src == "sina":
            # 新浪 ETF 专用接口（不复权；ETF 分红除权少，与 qfq 基本一致）
            raw = ak.fund_etf_hist_sina(symbol=_sina_symbol(code))
            if raw is None or raw.empty:
                return pd.DataFrame()
            return self._standardize(raw, "date")
        if src == "tx":
            raw = ak.stock_zh_a_hist_tx(
                symbol=_sina_symbol(code),
                start_date="20040101", end_date="20991231",
                adjust="qfq")
            if raw is None or raw.empty:
                return pd.DataFrame()
            # 腾讯源 amount 实为成交量(手)，换算成近似成交量(股)
            if "volume" not in raw.columns and "amount" in raw.columns:
                raw = raw.rename(columns={"amount": "volume"})
                raw["volume"] = raw["volume"] * 100
            return self._standardize(raw, "date")
        raise ValueError(f"未知数据源: {src}")

    def _load_intraday(self, code: str, src: str) -> pd.DataFrame:
        """分钟线数据（单源，失败抛异常由上层降级）"""
        period = FREQ_MAP[self.freq]["akshare_period"]
        if src == "em":
            raw = ak.fund_etf_hist_min_em(
                symbol=code, period=period, adjust="qfq")
            if raw is None or raw.empty:
                return pd.DataFrame()
            raw = raw.rename(columns={
                "时间": "datetime", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume",
                "成交额": "amount"})
            df = self._standardize(raw, "datetime")
            return df.between_time("09:30", "15:00")
        if src == "tx":
            raw = ak.stock_zh_a_minute(
                symbol=_sina_symbol(code), period=period,
                adjust="qfq")
            if raw is None or raw.empty:
                return pd.DataFrame()
            raw = raw.reset_index().rename(columns={"day": "datetime"})
            df = self._standardize(raw, "datetime")
            return df.between_time("09:30", "15:00")
        raise ValueError(f"未知数据源: {src}")

    def load_pool(self, codes: list) -> dict:
        """批量加载"""
        pool = {}
        min_bars = 60 if self.freq == "daily" else 240
        self._stale_codes = []
        print(f"  加载 {len(codes)} 只 ETF [{self.freq}]...")
        for i, code in enumerate(codes):
            df = self.load(code)
            if df.empty or len(df) < min_bars:
                continue
            pool[code] = df
            if (i + 1) % 5 == 0:
                print(f"    已加载 {i + 1}/{len(codes)}")
        print(f"  实际加载成功: {len(pool)} 只 "
              f"(平均 {sum(len(d) for d in pool.values()) // max(len(pool), 1)} bar)")
        if self._stale_codes:
            # 一只一行地报会淹掉日志，聚合成一行：读了旧缓存的标的数量 + 补救入口
            print(f"  ⚠️ {len(self._stale_codes)} 只的本地缓存落后全市场镜像，"
                  f"本轮已改用镜像/网络源（例 "
                  f"{self._stale_codes[:5]}）；要根治就跑 "
                  f"`python data/update_etf_daily.py`")
            self._stale_codes = []
        return pool


class PointInTimeData:
    """时点数据访问器：任何取数只能看到 date 及之前的 bar。

    回测器与策略此前各自手写 `.loc[:ts]`，"有没有偷看未来"要靠逐个函数
    审查；收口到本类后只需审这一个类。"""

    def __init__(self, pool: dict):
        self.pool = pool

    def get_history(self, code, date, lookback=60):
        """code 在 date 时刻可见的最近 lookback 根 bar（含 date 当期）；
        池里没有该标的返回空 DataFrame。"""
        if code not in self.pool:
            return pd.DataFrame()
        return self.pool[code].loc[:date].tail(lookback)

    def get_price(self, code, date, field="close"):
        """date 当期某列价格；标的缺该 bar（停牌/未上市）或值为 NaN 时
        返回 None，由调用方决定沿用成本价还是推迟成交。"""
        if code not in self.pool:
            return None
        df = self.pool[code]
        if date not in df.index:
            return None
        v = df.loc[date, field]
        return float(v) if not pd.isna(v) else None

    def has_bar(self, code, date):
        """date 当日该标的是否有 bar（停牌/未上市/池里没有 → False）。
        get_price 对「无 bar」和「有 bar 但该列缺失」都返回 None，
        可交易性判定要区分这两件事，故单列此访问器。"""
        return code in self.pool and date in self.pool[code].index