# -*- coding: utf-8 -*-
"""QMT 券商实现"""

import time
from datetime import datetime
from .broker_interface import (
    BrokerInterface, Order, Position, Account,
)
from .config_live import ACCOUNT


class QMTBroker(BrokerInterface):
    def __init__(self):
        self.cfg = ACCOUNT["qmt"]
        self.xt_trader = None
        self.account = None
        self._connected = False

    def connect(self):
        try:
            from xtquant.xttrader import XtQuantTrader
            from xtquant.xttype import StockAccount
            session_id = int(time.time())
            self.xt_trader = XtQuantTrader(
                self.cfg["mini_qmt_path"], session_id)
            self.account = StockAccount(
                self.cfg["account"], self.cfg["account_type"])
            self.xt_trader.start()
            if self.xt_trader.connect() != 0:
                return False
            if self.xt_trader.subscribe(self.account) != 0:
                return False
            self._connected = True
            print("  [QMT] 已连接")
            return True
        except ImportError:
            print("  [QMT] 未安装 xtquant")
            return False
        except Exception as e:
            print(f"  [QMT] 连接异常: {e}")
            return False

    def disconnect(self):
        if self.xt_trader:
            try:
                self.xt_trader.stop()
            except Exception:
                pass
        self._connected = False

    def is_connected(self):
        return self._connected

    def get_account(self):
        if not self._connected:
            return Account(0, 0, 0, 0, 0, 0)
        try:
            a = self.xt_trader.query_stock_asset(self.account)
            return Account(total_asset=a.total_asset, cash=a.cash,
                           market_value=a.market_value,
                           frozen=a.frozen_cash,
                           pnl=a.total_asset - ACCOUNT["initial_capital"],
                           pnl_pct=(a.total_asset -
                                    ACCOUNT["initial_capital"]) /
                                   ACCOUNT["initial_capital"])
        except Exception:
            return Account(0, 0, 0, 0, 0, 0)

    def get_positions(self):
        if not self._connected:
            return []
        try:
            ps = self.xt_trader.query_stock_positions(self.account)
            return [Position(code=p.stock_code, shares=p.volume,
                             available=p.can_use_volume,
                             cost=p.open_price,
                             market_value=p.market_value,
                             pnl=p.market_value - p.volume * p.open_price,
                             pnl_pct=(p.market_value -
                                      p.volume * p.open_price) /
                                     (p.volume * p.open_price + 1e-9))
                    for p in ps]
        except Exception:
            return []

    def get_orders(self, today_only=True):
        if not self._connected:
            return []
        try:
            os_ = self.xt_trader.query_stock_orders(
                self.account, cancelable_only=False)
            status_map = {48: "PENDING", 49: "PENDING", 50: "PARTIAL",
                          51: "PARTIAL", 52: "PARTIAL", 53: "CANCELLED",
                          54: "CANCELLED", 55: "REJECTED", 56: "FILLED"}
            return [Order(code=o.stock_code,
                          side="BUY" if o.order_type == 23 else "SELL",
                          price=o.price, shares=o.order_volume,
                          amount=o.price * o.order_volume,
                          order_id=str(o.order_id),
                          status=status_map.get(o.order_status, "UNKNOWN"),
                          filled_shares=o.traded_volume,
                          filled_price=o.traded_price)
                    for o in os_]
        except Exception:
            return []

    def buy(self, code, price, shares, reason=""):
        try:
            from xtquant import xtconstant
            oid = self.xt_trader.order_stock(
                self.account, code, xtconstant.STOCK_BUY, shares,
                xtconstant.FIX_PRICE, price, "rd_agent", reason)
            return Order(code=code, side="BUY", price=price,
                         shares=shares, amount=price * shares,
                         order_id=str(oid), status="PENDING",
                         reason=reason,
                         created_at=datetime.now().isoformat())
        except Exception as e:
            return Order(code=code, side="BUY", price=price,
                         shares=shares, amount=price * shares,
                         status="REJECTED", reason=reason, error=str(e),
                         created_at=datetime.now().isoformat())

    def sell(self, code, price, shares, reason=""):
        try:
            from xtquant import xtconstant
            oid = self.xt_trader.order_stock(
                self.account, code, xtconstant.STOCK_SELL, shares,
                xtconstant.FIX_PRICE, price, "rd_agent", reason)
            return Order(code=code, side="SELL", price=price,
                         shares=shares, amount=price * shares,
                         order_id=str(oid), status="PENDING",
                         reason=reason,
                         created_at=datetime.now().isoformat())
        except Exception as e:
            return Order(code=code, side="SELL", price=price,
                         shares=shares, amount=price * shares,
                         status="REJECTED", reason=reason, error=str(e),
                         created_at=datetime.now().isoformat())

    def cancel(self, order_id):
        try:
            self.xt_trader.cancel_order_stock(
                self.account, int(order_id))
            return True
        except Exception:
            return False