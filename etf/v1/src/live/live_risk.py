# -*- coding: utf-8 -*-
"""实盘风控前置"""

import time
import pandas as pd
from datetime import datetime, timedelta
from collections import deque
from .config_live import LIVE_RISK, STORAGE


class LiveRiskController:
    def __init__(self):
        self.p = LIVE_RISK
        self.peak_equity = 0.0
        self.daily_start_equity = None
        self.current_day = None
        self.orders_today = 0
        self.recent_orders = deque(maxlen=20)
        self.daily_turnover = 0.0
        self.cooldown_until = None
        self.risk_events = []
        self.halted = False

    def on_new_day(self, equity):
        day = datetime.now().date()
        if day != self.current_day:
            self.current_day = day
            self.orders_today = 0
            self.daily_turnover = 0.0
            self.daily_start_equity = equity
            self.halted = False

    def check_order(self, order_type, code, price, shares,
                    account, positions, current_price):
        amount = price * shares
        equity = account.total_asset
        if self.cooldown_until and datetime.now() < self.cooldown_until:
            return self._reject("熔断冷却", "冷却中")
        if self.daily_start_equity:
            r = equity / self.daily_start_equity - 1
            if r <= self.p["daily_stop_loss"]:
                self._halt("日止损", f"{r*100:.2f}%")
                return self._reject("日止损", f"{r*100:.2f}%")
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            dd = equity / self.peak_equity - 1
            if dd <= self.p["max_drawdown_stop"]:
                self.cooldown_until = datetime.now() + timedelta(
                    minutes=self.p["cooldown_minutes"])
                self._halt("回撤熔断", f"{dd*100:.2f}%")
                return self._reject("回撤熔断", f"{dd*100:.2f}%")
        if amount > self.p["max_order_amount"]:
            return self._reject("单笔超限", f"{amount:.2f}")
        if amount < self.p["min_order_amount"]:
            return self._reject("单笔过小", f"{amount:.2f}")
        if self.orders_today >= self.p["max_orders_per_day"]:
            return self._reject("日订单超限", "")
        now = time.time()
        recent = [o for o in self.recent_orders if now - o[0] < 60]
        if len(recent) >= self.p["max_orders_per_minute"]:
            return self._reject("分钟订单超限", "")
        new_turnover = self.daily_turnover + amount / (equity + 1e-9)
        if new_turnover > self.p["max_daily_turnover"]:
            return self._reject("日换手超限", f"{new_turnover:.3f}")
        if order_type == "BUY":
            if account.cash - amount < self.p["min_cash_reserve"]:
                return self._reject("现金不足", "")
            new_mv = amount
            for pos in positions:
                if pos.code == code:
                    new_mv += pos.market_value
                    break
            if new_mv / (equity + 1e-9) > self.p["max_position_ratio"]:
                return self._reject("仓位超限", "")
        if order_type == "SELL":
            pos = next((p for p in positions if p.code == code), None)
            if pos is None:
                return self._reject("无持仓", "")
            if pos.available < shares:
                return self._reject("可用不足", "")
        if current_price > 0:
            diff = abs(price - current_price) / current_price
            if diff > self.p["price_limit_ratio"]:
                return self._reject("价格偏离过大", f"{diff:.2%}")
        return True, "OK"

    def on_order_submitted(self, amount, equity):
        self.orders_today += 1
        self.recent_orders.append((time.time(), amount))
        self.daily_turnover += amount / (equity + 1e-9)

    def _halt(self, reason, detail):
        self.halted = True
        self._log_event("HALT", reason, detail)

    def _reject(self, reason, detail):
        self._log_event("REJECT", reason, detail)
        return False, f"{reason}: {detail}"

    def _log_event(self, level, reason, detail):
        self.risk_events.append({"time": datetime.now().isoformat(),
                                 "level": level, "reason": reason,
                                 "detail": detail})
        try:
            df = pd.DataFrame(self.risk_events[-100:])
            df.to_csv(f"{STORAGE['dir']}/{STORAGE['risk_events']}",
                      index=False, encoding="utf-8-sig")
        except Exception:
            pass