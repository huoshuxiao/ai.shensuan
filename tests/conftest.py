"""pytest fixtures：合成行情数据与 Mock 数据源"""
import numpy as np
import pandas as pd
import pytest

from src.storage.state import Position


def make_daily(n: int = 80, start_price: float = 1.0, trend: float = 0.0,
               noise: float = 0.01, seed: int = 42) -> pd.DataFrame:
    """生成合成日线数据（统一列名）"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    drift = trend
    returns = rng.normal(drift, noise, n)
    close = start_price * np.cumprod(1 + returns)
    open_ = close * (1 + rng.normal(0, noise / 2, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, noise, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, noise, n))
    volume = rng.uniform(1e6, 5e6, n)
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def make_downtrend_daily(n: int = 80, start_price: float = 2.0,
                         daily_drop: float = 0.03, seed: int = 7) -> pd.DataFrame:
    """连续下跌行情（用于超跌因子测试）"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    returns = rng.normal(-daily_drop, 0.002, n)
    close = start_price * np.cumprod(1 + returns)
    open_ = close * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    volume = np.linspace(5e6, 1e6, n)  # 缩量下跌
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def make_uptrend_daily(n: int = 80, start_price: float = 1.0,
                       daily_gain: float = 0.03, seed: int = 11) -> pd.DataFrame:
    """放量上涨行情（用于情绪因子贪婪测试）"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    returns = rng.normal(daily_gain, 0.002, n)
    close = start_price * np.cumprod(1 + returns)
    open_ = close * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    volume = np.linspace(1e6, 5e6, n)  # 逐步放量
    volume[-1] = volume[-1] * 2.5       # 最后一根显著放量（贪婪信号）
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


class MockFetcher:
    """Mock 数据源：返回预设数据，测试不依赖网络"""

    def __init__(self, daily_map: dict[str, pd.DataFrame] | None = None,
                 realtime_map: dict[str, dict] | None = None):
        self.daily_map = daily_map or {}
        self.realtime_map = realtime_map or {}
        self.call_count = 0

    def get_etf_daily(self, code: str, days: int = None) -> pd.DataFrame:
        self.call_count += 1
        df = self.daily_map.get(code)
        return df.copy() if df is not None else pd.DataFrame()

    def get_etf_realtime(self, code: str) -> dict | None:
        self.call_count += 1
        return self.realtime_map.get(code)

    def get_market_index_daily(self, symbol: str = "sh000001", days: int = None) -> pd.DataFrame:
        self.call_count += 1
        return self.daily_map.get(f"index_{symbol}", pd.DataFrame())


def make_position(code: str = "510300", name: str = "沪深300ETF",
                  buy_price: float = 1.0) -> Position:
    return Position(code=code, name=name, buy_price=buy_price,
                    quantity=1000, buy_date="2026-09-01", hold_days=3,
                    last_trade_date="2026-09-01")


@pytest.fixture
def normal_daily() -> pd.DataFrame:
    return make_daily(n=80, start_price=1.0, trend=0.0005)


@pytest.fixture
def downtrend_daily() -> pd.DataFrame:
    return make_downtrend_daily()


@pytest.fixture
def uptrend_daily() -> pd.DataFrame:
    return make_uptrend_daily()
