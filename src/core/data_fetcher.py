"""数据获取层：多数据源（akshare/东财、腾讯行情），含限频、重试、缓存与来源合规标注

合规说明：
- 本模块仅用于个人研究学习，不用于商业用途
- 通过 rate_limit_interval 限频 + 随机抖动，避免冲击数据源
- 指数退避重试，失败不无限重试
- 本地磁盘缓存，减少重复抓取
- 多数据源回退：东财(akshare) 不可用时自动切换腾讯行情，保证链路可用性
"""
import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from src.config.settings import CACHE_DIR, settings

logger = logging.getLogger(__name__)

# 数据来源声明（合规输出用）
DATA_SOURCE = "akshare/腾讯行情（仅供个人研究学习使用）"

# 通用浏览器请求头，降低被数据源拒绝的概率
_TENCENT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://gu.qq.com/",
}

_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="

# akshare 列名映射 -> 统一列名
DAILY_COLUMN_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "涨跌幅": "pct_change",
}

INDEX_COLUMN_MAP = {
    "date": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}

UNIFIED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


class DataFetcher:
    """统一数据获取入口（可注入 mock 以便测试）"""

    def __init__(self, cache_dir: Path = CACHE_DIR, use_cache: bool = True):
        self.cache_dir = Path(cache_dir)
        self.use_cache = use_cache
        self._last_request_ts = 0.0
        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 合规限频 ----------
    def _rate_limit(self):
        """请求间隔 + 随机抖动，尊重数据源"""
        elapsed = time.time() - self._last_request_ts
        wait = settings.rate_limit_interval + random.uniform(0, 0.5)
        if elapsed < wait:
            time.sleep(wait - elapsed)
        self._last_request_ts = time.time()

    def _call_with_retry(self, func, *args, max_attempts: int = None, **kwargs):
        """指数退避重试；max_attempts 可覆盖全局重试次数（回退场景快速失败）"""
        max_attempts = max_attempts or settings.max_retries
        last_err = None
        for attempt in range(max_attempts):
            try:
                self._rate_limit()
                return func(*args, **kwargs)
            except Exception as err:  # noqa: BLE001 - 数据源异常统一重试
                last_err = err
                if attempt + 1 < max_attempts:
                    wait = 2 ** attempt
                    logger.warning("数据请求失败(第%d次): %s，%ds后重试", attempt + 1, err, wait)
                    time.sleep(wait)
        raise RuntimeError(f"数据请求最终失败: {last_err}")

    # ---------- 缓存 ----------
    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.csv"

    def _load_cache(self, key: str, max_age_hours: int = 12) -> pd.DataFrame | None:
        """缓存有效期：盘中实时数据 10 分钟，日线 12 小时"""
        path = self._cache_path(key)
        if not (self.use_cache and path.exists()):
            return None
        age = time.time() - path.stat().st_mtime
        if age > max_age_hours * 3600:
            return None
        try:
            return pd.read_csv(path)
        except Exception:  # noqa: BLE001
            return None

    def _save_cache(self, key: str, df: pd.DataFrame):
        if self.use_cache and df is not None and not df.empty:
            df.to_csv(self._cache_path(key), index=False)

    # ---------- 公共接口 ----------
    @staticmethod
    def _to_tencent_symbol(code: str) -> str:
        """纯数字代码转腾讯带交易所前缀代码（sh/sz）"""
        code = str(code).strip().lower()
        if code.startswith(("sh", "sz")):
            return code
        if code.startswith(("51", "56", "58")) or code.startswith("0"):
            return "sh" + code
        if code.startswith(("15", "39")):
            return "sz" + code
        return "sh" + code

    def _fetch_daily_eastmoney(self, code: str, days: int) -> pd.DataFrame | None:
        """东财(akshare)日线源，失败返回 None 以便回退"""
        try:
            import akshare as ak

            end = datetime.now()
            start = end - timedelta(days=int(days * 1.8))  # 多取一些覆盖停牌
            df = self._call_with_retry(
                ak.fund_etf_hist_em,
                symbol=code,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
                max_attempts=1,  # 快速失败，交回退源处理
            )
            df = self._normalize_daily(df)
            return df.tail(days).reset_index(drop=True)
        except Exception as err:  # noqa: BLE001
            logger.warning("东财日线源不可用(%s): %s", code, err)
            return None

    def _fetch_daily_tencent(self, symbol: str, days: int) -> pd.DataFrame:
        """腾讯行情日线源（ETF/指数通用），返回统一列名 DataFrame

        腾讯返回行格式：[date, open, close, high, low, volume]
        """
        tencent_symbol = self._to_tencent_symbol(symbol)
        resp = requests.get(
            _TENCENT_KLINE_URL,
            params={"param": f"{tencent_symbol},day,,,{days},qfq"},
            headers=_TENCENT_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        node = (payload.get("data") or {}).get(tencent_symbol) or {}
        rows = node.get("qfqday") or node.get("day") or []
        if not rows:
            raise RuntimeError(f"腾讯K线返回空数据: {tencent_symbol}")
        df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[UNIFIED_COLUMNS].dropna(subset=["close"])
        return df.tail(days).reset_index(drop=True)

    def get_etf_daily(self, code: str, days: int = None) -> pd.DataFrame:
        """获取ETF日K线（前复权），按 settings.data_sources 顺序尝试各数据源，返回统一列名 DataFrame"""
        days = days or settings.history_days
        cache_key = f"etf_daily_{code}_{days}"
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached

        df = None
        for source in settings.data_sources:
            if source == "akshare":
                df = self._fetch_daily_eastmoney(code, days)
            elif source == "tencent":
                df = self._fetch_daily_tencent(code, days)
            else:
                logger.warning("未知数据源配置: %s，已跳过", source)
                continue
            if df is not None and not df.empty:
                logger.info("数据源 %s 获取日线成功: %s", source, code)
                break
        if df is None or df.empty:
            raise RuntimeError(f"所有数据源均无法获取日线: {code}")
        self._save_cache(cache_key, df)
        return df

    def _fetch_realtime_eastmoney(self, code: str) -> dict | None:
        """东财(akshare)实时行情源，失败返回 None 以便回退"""
        try:
            import akshare as ak

            spot = self._call_with_retry(ak.fund_etf_spot_em, max_attempts=1)
            row = spot[spot["代码"].astype(str).str.zfill(6) == code]
            if row.empty:
                return None
            r = row.iloc[0]
            return {
                "code": code,
                "name": r.get("名称"),
                "price": float(r.get("最新价")),
                "pct_change": float(r.get("涨跌幅")),
                "volume": float(r.get("成交量")),
                "amount": float(r.get("成交额")),
                "high": float(r.get("最高价")),
                "low": float(r.get("最低价")),
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as err:  # noqa: BLE001
            logger.warning("东财实时行情源不可用(%s): %s", code, err)
            return None

    def _fetch_realtime_tencent(self, code: str) -> dict | None:
        """腾讯实时行情源，失败返回 None 以便降级日线"""
        try:
            symbol = self._to_tencent_symbol(code)
            resp = requests.get(_TENCENT_QUOTE_URL + symbol, headers=_TENCENT_HEADERS, timeout=10)
            resp.raise_for_status()
            if '="' not in resp.text:
                return None
            payload = resp.text.split('="', 1)[1].rsplit('"', 1)[0]
            parts = payload.split("~")
            if len(parts) < 40:
                return None
            return {
                "code": code,
                "name": parts[1],
                "price": float(parts[3]),
                "pct_change": float(parts[32]),
                "volume": float(parts[36]),  # 手
                "amount": float(parts[37]) * 10000,  # 万元 -> 元
                "high": float(parts[33]),
                "low": float(parts[34]),
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as err:  # noqa: BLE001
            logger.warning("腾讯实时行情源不可用(%s): %s", code, err)
            return None

    def _realtime_from_daily(self, code: str) -> dict | None:
        """实时行情降级：用最新日线收盘价"""
        daily = self.get_etf_daily(code)
        if daily.empty:
            return None
        last = daily.iloc[-1]
        return {
            "code": code,
            "name": None,
            "price": float(last["close"]),
            "pct_change": float(last["pct_change"]) if "pct_change" in last else None,
            "volume": float(last["volume"]),
            "amount": None,
            "high": float(last["high"]),
            "low": float(last["low"]),
            "ts": str(last["date"]) + " (收盘价, 实时接口不可用)",
        }

    def get_etf_realtime(self, code: str) -> dict | None:
        """获取ETF实时行情（盘中）：按 settings.data_sources 顺序尝试，全部失败降级为最新日线"""
        cache_key = f"etf_rt_{code}"
        cached = self._load_cache(cache_key, max_age_hours=10 / 60)  # 10分钟
        if cached is not None:
            return cached.iloc[0].to_dict()

        result = None
        for source in settings.data_sources:
            if source == "akshare":
                result = self._fetch_realtime_eastmoney(code)
            elif source == "tencent":
                result = self._fetch_realtime_tencent(code)
            else:
                continue
            if result is not None:
                break
        if result is None:
            logger.warning("实时行情获取失败，降级为最新日线: %s", code)
            result = self._realtime_from_daily(code)
        if result:
            pd.DataFrame([result]).to_csv(self._cache_path(cache_key), index=False)
        return result

    def _fetch_index_akshare(self, symbol: str, days: int) -> pd.DataFrame | None:
        """akshare(新浪)指数日线源，失败返回 None 以便回退"""
        try:
            import akshare as ak

            raw = self._call_with_retry(ak.stock_zh_index_daily, symbol=symbol, max_attempts=1)
            raw = raw.rename(columns=INDEX_COLUMN_MAP)
            raw = raw[[c for c in UNIFIED_COLUMNS if c in raw.columns]]
            raw["date"] = pd.to_datetime(raw["date"])
            return raw.sort_values("date").tail(days).reset_index(drop=True)
        except Exception as err:  # noqa: BLE001
            logger.warning("akshare指数日线源不可用: %s", err)
            return None

    def get_market_index_daily(self, symbol: str = "sh000001", days: int = None) -> pd.DataFrame:
        """获取大盘指数日线（默认上证指数），按 settings.data_sources 顺序尝试，用于市场情绪因子"""
        days = days or settings.history_days
        cache_key = f"index_{symbol}_{days}"
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached

        df = None
        for source in settings.data_sources:
            if source == "akshare":
                df = self._fetch_index_akshare(symbol, days)
            elif source == "tencent":
                df = self._fetch_daily_tencent(symbol, days)
            else:
                logger.warning("未知数据源配置: %s，已跳过", source)
                continue
            if df is not None and not df.empty:
                logger.info("数据源 %s 获取指数日线成功: %s", source, symbol)
                break
        if df is None or df.empty:
            raise RuntimeError(f"所有数据源均无法获取指数日线: {symbol}")
        self._save_cache(cache_key, df)
        return df

    # ---------- 工具 ----------
    @staticmethod
    def _normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
        """统一日线列名与类型"""
        df = df.rename(columns=DAILY_COLUMN_MAP)
        cols = [c for c in UNIFIED_COLUMNS if c in df.columns]
        df = df[cols]
        df["date"] = pd.to_datetime(df["date"])
        for col in cols:
            if col != "date":
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
