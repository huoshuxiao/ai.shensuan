# -*- coding: utf-8 -*-
"""模拟盘"""

import uuid
from datetime import datetime
from broker_interface import (
    BrokerInterface, Order, Position, Account,
)


class PaperBroker(BrokerInterface):
    def __init__(self, initial_capital=10_000, commission=0.0001,
                 min_commission=0.1, slippage=0.0005):
        self.initial = initial_capital
        self.cash = initial_capital
        self.commission = commission
        self.min_commission = min_commission
        self.slippage = slippage
        self.positions = {}
        self.orders = []
        self._connected = False
        self._price_cache = {}

    def connect(self):
        self._connected = True
        print("  [Paper] 模拟盘已连接")
        return True

    def disconnect(self):
        self._connected = False

    def is_connected(self):
        return self._connected

    def update_price(self, code, price):
        self._price_cache[code] = price

    def get_account(self):
        mv = sum(p.shares * self._price_cache.get(p.code, p.cost)
                 for p in self.positions.values())
        total = self.cash + mv
        return Account(total_asset=total, cash=self.cash,
                       market_value=mv, frozen=0.0,
                       pnl=total - self.initial,
                       pnl_pct=(total - self.initial) / self.initial)

    def get_positions(self):
        result = []
        for code, p in self.positions.items():
            cur = self._price_cache.get(code, p.cost)
            mv = p.shares * cur
            pnl = mv - p.shares * p.cost
            result.append(Position(code=code, shares=p.shares,
                                    available=p.available, cost=p.cost,
                                    market_value=mv, pnl=pnl,
                                    pnl_pct=pnl / (p.shares * p.cost + 1e-9),
                                    buy_date=p.buy_date))
        return result

    def get_orders(self, today_only=True):
        return self.orders[-100:]

    def _calc_cost(self, amount):
        return max(amount * self.commission, self.min_commission) + \
            amount * self.slippage

    def buy(self, code, price, shares, reason=""):
        amount = price * shares
        cost = self._calc_cost(amount)
        if self.cash < amount + cost:
            return Order(code=code, side="BUY", price=price,
                         shares=shares, amount=amount,
                         status="REJECTED", reason=reason,
                         error=f"现金不足: {self.cash:.2f}",
                         created_at=datetime.now().isoformat())
        self.cash -= (amount + cost)
        if code in self.positions:
            p = self.positions[code]
            total = p.shares + shares
            p.cost = (p.cost * p.shares + price * shares) / total
            p.shares = total
            p.available = p.shares
        else:
            self.positions[code] = Position(
                code=code, shares=shares, available=shares,
                cost=price, market_value=amount, pnl=0, pnl_pct=0,
                buy_date=datetime.now().strftime("%Y-%m-%d"))
        o = Order(code=code, side="BUY", price=price, shares=shares,
                  amount=amount, order_id=str(uuid.uuid4())[:8],
                  status="FILLED", filled_shares=shares,
                  filled_price=price, reason=reason,
                  created_at=datetime.now().isoformat())
        self.orders.append(o)
        return o

    def sell(self, code, price, shares, reason=""):
        if code not in self.positions:
            return Order(code=code, side="SELL", price=price,
                         shares=shares, amount=price * shares,
                         status="REJECTED", reason=reason,
                         error="无持仓",
                         created_at=datetime.now().isoformat())
        p = self.positions[code]
        if p.available < shares:
            return Order(code=code, side="SELL", price=price,
                         shares=shares, amount=price * shares,
                         status="REJECTED", reason=reason,
                         error=f"可用不足: {p.available}",
                         created_at=datetime.now().isoformat())
        amount = price * shares
        cost = self._calc_cost(amount)
        self.cash += (amount - cost)
        p.shares -= shares
        p.available -= shares
        if p.shares <= 0:
            del self.positions[code]
        o = Order(code=code, side="SELL", price=price, shares=shares,
                  amount=amount, order_id=str(uuid.uuid4())[:8],
                  status="FILLED", filled_shares=shares,
                  filled_price=price, reason=reason,
                  created_at=datetime.now().isoformat())
        self.orders.append(o)
        return o

    def cancel(self, order_id):
        return False