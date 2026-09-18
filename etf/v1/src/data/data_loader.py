# -*- coding: utf-8 -*-
"""数据加载：日线 + 分钟线统一"""

import os
import time
import pandas as pd
import akshare as ak
from config import (
    BACKTEST_START, BACKTEST_END, FREQ, CACHE_DIR, FREQ_MAP,
)


class DataLoader:
    """统一数据加载器（支持日线和分钟线）"""

    def __init__(self, freq: str = None):
        self.freq = freq or FREQ
        self.is_intraday = FREQ_MAP[self.freq]["is_intraday"]
        os.makedirs(CACHE_DIR, exist_ok=True)

    def _cache_path(self, code: str) -> str:
        return os.path.join(CACHE_DIR, f"{code}_{self.freq}.csv")

    def load(self, code: str, use_cache: bool = True) -> pd.DataFrame:
        """加载单只 ETF 数据"""
        cache_file = self._cache_path(code)
        if use_cache and os.path.exists(cache_file):
            try:
                df = pd.read_csv(cache_file,
                                  parse_dates=[self._time_col()])
                return df.set_index(self._time_col())
            except Exception:
                pass

        if self.freq == "daily":
            df = self._load_daily(code)
        else:
            df = self._load_intraday(code)

        if df is None or df.empty:
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

    def _load_daily(self, code: str) -> pd.DataFrame:
        """日线数据"""
        try:
            raw = ak.fund_etf_hist_em(
                symbol=code, period="daily",
                start_date="20040101", end_date="20991231",
                adjust="qfq")
            if raw is None or raw.empty:
                return pd.DataFrame()

            rename_map = {"日期": "date", "开盘": "open",
                          "最高": "high", "最低": "low",
                          "收盘": "close", "成交量": "volume",
                          "成交额": "amount"}
            for old, new in rename_map.items():
                if old in raw.columns and new not in raw.columns:
                    raw = raw.rename(columns={old: new})

            cols = [c for c in ["date", "open", "high", "low",
                                "close", "volume", "amount"]
                    if c in raw.columns]
            df = raw[cols].copy()
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").astype(float)
            return df
        except Exception as e:
            print(f"    ⚠️ 日线加载 {code} 失败: {e}")
            return pd.DataFrame()

    def _load_intraday(self, code: str) -> pd.DataFrame:
        """分钟线数据"""
        try:
            period = FREQ_MAP[self.freq]["akshare_period"]
            raw = ak.fund_etf_hist_min_em(
                symbol=code, period=period, adjust="qfq")
            if raw is None or raw.empty:
                return pd.DataFrame()

            rename_map = {"时间": "datetime", "开盘": "open",
                          "最高": "high", "最低": "low",
                          "收盘": "close", "成交量": "volume",
                          "成交额": "amount"}
            for old, new in rename_map.items():
                if old in raw.columns and new not in raw.columns:
                    raw = raw.rename(columns={old: new})

            cols = [c for c in ["datetime", "open", "high", "low",
                                "close", "volume", "amount"]
                    if c in raw.columns]
            df = raw[cols].copy()
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.set_index("datetime").astype(float)
            df = df.between_time("09:30", "15:00")
            return df
        except Exception as e:
            print(f"    ⚠️ 分钟线加载 {code} 失败: {e}")
            return pd.DataFrame()

    def load_pool(self, codes: list) -> dict:
        """批量加载"""
        pool = {}
        min_bars = 60 if self.freq == "daily" else 240
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
        return pool


class PointInTimeData:
    """时点数据访问器"""

    def __init__(self, pool: dict):
        self.pool = pool

    def get_history(self, code, date, lookback=60):
        if code not in self.pool:
            return pd.DataFrame()
        return self.pool[code].loc[:date].tail(lookback)

    def get_price(self, code, date, field="close"):
        if code not in self.pool:
            return None
        df = self.pool[code]
        if date not in df.index:
            return None
        v = df.loc[date, field]
        return float(v) if not pd.isna(v) else None