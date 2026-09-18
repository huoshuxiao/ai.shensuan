# -*- coding: utf-8 -*-
"""统一券商接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Order:
    code: str
    side: str
    price: float
    shares: int
    amount: float
    order_id: Optional[str] = None
    status: str = "PENDING"
    filled_shares: int = 0
    filled_price: float = 0.0
    created_at: str = ""
    reason: str = ""
    error: str = ""


@dataclass
class Position:
    code: str
    shares: int
    available: int
    cost: float
    market_value: float
    pnl: float
    pnl_pct: float
    buy_date: str = ""


@dataclass
class Account:
    total_asset: float
    cash: float
    market_value: float
    frozen: float
    pnl: float
    pnl_pct: float


class BrokerInterface(ABC):
    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def disconnect(self): ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_positions(self) -> list: ...

    @abstractmethod
    def get_orders(self, today_only: bool = True) -> list: ...

    @abstractmethod
    def buy(self, code, price, shares, reason="") -> Order: ...

    @abstractmethod
    def sell(self, code, price, shares, reason="") -> Order: ...

    @abstractmethod
    def cancel(self, order_id: str) -> bool: ...