# -*- coding: utf-8 -*-
"""ETF 池管理：从 akshare 动态获取 + 上市日期"""

import os
import time
import pandas as pd
import akshare as ak
from config import ETF_FILTER, UNIVERSE_CACHE


class ETFUniverse:
    def __init__(self):
        self.universe = None

    def fetch_all(self) -> pd.DataFrame:
        print("  正在从 akshare 获取 ETF 列表...")
        df = ak.fund_etf_spot_em()
        df = df.rename(columns={
            "代码": "code", "名称": "name",
            "成交额": "amount"})[["code", "name", "amount"]]
        df["code"] = df["code"].astype(str).str.zfill(6)
        print(f"  获取到 {len(df)} 只 ETF")
        return df

    def fetch_list_date(self, code: str):
        try:
            df = ak.fund_etf_hist_em(
                symbol=code, period="daily",
                start_date="20040101", end_date="20991231",
                adjust="")
            if df is None or df.empty:
                return None
            return pd.to_datetime(df["日期"].iloc[0])
        except Exception:
            return None

    def build(self, use_cache: bool = True) -> pd.DataFrame:
        if use_cache and os.path.exists(UNIVERSE_CACHE):
            print(f"  从缓存加载: {UNIVERSE_CACHE}")
            df = pd.read_csv(UNIVERSE_CACHE, dtype={"code": str})
            df["code"] = df["code"].str.zfill(6)
            df["list_date"] = pd.to_datetime(df["list_date"])
            self.universe = df
            return df

        df = self.fetch_all()
        for kw in ETF_FILTER["exclude_keywords"]:
            df = df[~df["name"].str.contains(kw, na=False)]
        df = df[df["amount"] >= ETF_FILTER["min_avg_amount"]]
        df = (df.sort_values("amount", ascending=False)
                .head(ETF_FILTER["max_count"])
                .reset_index(drop=True))

        print(f"  查询 {len(df)} 只 ETF 上市日期...")
        list_dates = []
        for i, code in enumerate(df["code"]):
            ld = self.fetch_list_date(code)
            list_dates.append(ld)
            time.sleep(0.2)

        df["list_date"] = list_dates
        df = df.dropna(subset=["list_date"]).reset_index(drop=True)
        df["avg_amount"] = df["amount"]
        df.to_csv(UNIVERSE_CACHE, index=False, encoding="utf-8-sig")
        self.universe = df
        print(f"  ETF 池构建完成: {len(df)} 只")
        return df

    def get_tradable_at(self, date: pd.Timestamp) -> list:
        if self.universe is None:
            raise RuntimeError("请先调用 build()")
        min_days = ETF_FILTER["min_list_days"]
        cutoff = date - pd.Timedelta(days=min_days)
        return self.universe[
            self.universe["list_date"] <= cutoff]["code"].tolist()


_universe_instance = None

def get_universe() -> ETFUniverse:
    global _universe_instance
    if _universe_instance is None:
        _universe_instance = ETFUniverse()
        _universe_instance.build()
    return _universe_instance