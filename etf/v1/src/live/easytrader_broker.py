# -*- coding: utf-8 -*-
"""easytrader 券商实现"""

from datetime import datetime
from broker_interface import (
    BrokerInterface, Order, Position, Account,
)
from config_live import ACCOUNT


class EasytraderBroker(BrokerInterface):
    def __init__(self):
        self.cfg = ACCOUNT["easytrader"]
        self.user = None
        self._connected = False

    def connect(self):
        try:
            import easytrader
            self.user = easytrader.use(self.cfg["broker"])
            self.user.connect(self.cfg.get("exe_path"),
                              self.cfg.get("user"),
                              self.cfg.get("password"))
            self._connected = True
            print("  [easytrader] 已连接")
            return True
        except ImportError:
            print("  [easytrader] 未安装")
            return False
        except Exception as e:
            print(f"  [easytrader] 连接失败: {e}")
            return False

    def disconnect(self):
        if self.user:
            try:
                self.user.exit()
            except Exception:
                pass
        self._connected = False

    def is_connected(self):
        return self._connected

    def get_account(self):
        try:
            b = self.user.balance[0]
            total = float(b.get("资金余额", 0))
            mv = float(b.get("总资产", total))
            return Account(total_asset=mv, cash=total,
                           market_value=mv - total, frozen=0,
                           pnl=mv - ACCOUNT["initial_capital"],
                           pnl_pct=(mv - ACCOUNT["initial_capital"]) /
                                   ACCOUNT["initial_capital"])
        except Exception:
            return Account(0, 0, 0, 0, 0, 0)

    def get_positions(self):
        try:
            ps = self.user.position
            return [Position(code=p.get("证券代码", ""),
                             shares=int(p.get("股票余额", 0)),
                             available=int(p.get("可用余额", 0)),
                             cost=float(p.get("成本价", 0)),
                             market_value=int(p.get("股票余额", 0)) *
                                          float(p.get("市价", 0)),
                             pnl=0, pnl_pct=0)
                    for p in ps]
        except Exception:
            return []

    def get_orders(self, today_only=True):
        try:
            os_ = self.user.today_entrusts
            return [Order(code=o.get("证券代码", ""),
                          side="BUY" if "买" in o.get("买卖标志", "") else "SELL",
                          price=float(o.get("委托价格", 0)),
                          shares=int(o.get("委托数量", 0)),
                          amount=float(o.get("委托价格", 0)) *
                                 int(o.get("委托数量", 0)),
                          order_id=str(o.get("委托编号", "")),
                          status=o.get("状态说明", "PENDING"))
                    for o in os_]
        except Exception:
            return []

    def buy(self, code, price, shares, reason=""):
        try:
            r = self.user.buy(code, price=price, amount=shares)
            return Order(code=code, side="BUY", price=price,
                         shares=shares, amount=price * shares,
                         order_id=str(r.get("entrust_no", "")),
                         status="PENDING", reason=reason,
                         created_at=datetime.now().isoformat())
        except Exception as e:
            return Order(code=code, side="BUY", price=price,
                         shares=shares, amount=price * shares,
                         status="REJECTED", reason=reason, error=str(e),
                         created_at=datetime.now().isoformat())

    def sell(self, code, price, shares, reason=""):
        try:
            r = self.user.sell(code, price=price, amount=shares)
            return Order(code=code, side="SELL", price=price,
                         shares=shares, amount=price * shares,
                         order_id=str(r.get("entrust_no", "")),
                         status="PENDING", reason=reason,
                         created_at=datetime.now().isoformat())
        except Exception as e:
            return Order(code=code, side="SELL", price=price,
                         shares=shares, amount=price * shares,
                         status="REJECTED", reason=reason, error=str(e),
                         created_at=datetime.now().isoformat())

    def cancel(self, order_id):
        try:
            self.user.cancel_entrust(order_id)
            return True
        except Exception:
            return False