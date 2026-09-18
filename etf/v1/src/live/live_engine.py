# -*- coding: utf-8 -*-
"""实盘引擎"""

import os
import time
import pandas as pd
from datetime import datetime
from .config_live import (
    ACCOUNT, MODE, LIVE_RISK, STORAGE, NOTIFY,
)
from .broker_interface import Order
from .paper_broker import PaperBroker
from .live_risk import LiveRiskController
from .market_data import MarketDataManager
from .live_attribution import LiveAttributionTracker


class Notifier:
    def __init__(self):
        self.cfg = NOTIFY

    def send(self, title, content, level="INFO"):
        if not self.cfg["enabled"]:
            return
        msg = f"[{level}] {title}\n{content}"
        if self.cfg["channel"] == "console":
            print(f"  🔔 {msg}")
        elif self.cfg["channel"] in ("wechat", "dingtalk") \
                and self.cfg["webhook"]:
            try:
                import requests
                requests.post(self.cfg["webhook"],
                              json={"msgtype": "text",
                                    "text": {"content": msg}},
                              timeout=3)
            except Exception:
                pass


class LiveEngine:
    def __init__(self, broker, signals_fn, factor_weights=None):
        self.broker = broker
        self.signals_fn = signals_fn
        self.factor_weights = factor_weights
        self.market = MarketDataManager()
        self.risk = LiveRiskController()
        self.attribution = LiveAttributionTracker()
        self.notifier = Notifier()
        self.positions_cache = []
        self.account_cache = None
        self.running = False
        self.loop_interval = 30
        os.makedirs(STORAGE["dir"], exist_ok=True)

    def start(self, codes):
        print("=" * 60)
        print(f"  🚀 实盘引擎启动 | "
              f"{'实盘' if MODE['live_trading'] else '模拟'}")
        print("=" * 60)
        if not self.broker.connect():
            return False
        self.market.subscribe(codes, self._on_tick)
        time.sleep(2)
        self.running = True
        self._main_loop()
        return True

    def stop(self):
        self.running = False
        self.market.unsubscribe()
        self.broker.disconnect()
        self.attribution.save()

    def _on_tick(self, code, tick):
        if isinstance(self.broker, PaperBroker):
            self.broker.update_price(code, tick["price"])

    @staticmethod
    def _is_trading_time(now):
        if now.weekday() >= 5:
            return False
        t = now.time()
        morning = (t.hour == 9 and t.minute >= 30) or \
                  (t.hour == 10) or (t.hour == 11 and t.minute <= 30)
        afternoon = (t.hour == 13) or (t.hour == 14) or \
                    (t.hour == 15 and t.minute == 0)
        return morning or afternoon

    def _main_loop(self):
        while self.running:
            try:
                now = datetime.now()
                if not self._is_trading_time(now):
                    time.sleep(60)
                    continue
                self.account_cache = self.broker.get_account()
                self.positions_cache = self.broker.get_positions()
                self.risk.on_new_day(self.account_cache.total_asset)

                target = self._generate_signal()
                if target:
                    self._execute(target)
                    self.attribution.track(
                        account=self.account_cache,
                        positions=self.positions_cache,
                        target=target,
                        prices={c: self.market.get_price(c)
                                for c in target.get("codes", [])})
                time.sleep(self.loop_interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                self.notifier.send("主循环异常", str(e), "ERROR")
                time.sleep(10)

    def _generate_signal(self):
        try:
            snapshot = {}
            for code in self.market.source.codes:
                d = self.market.get_tick(code)
                if d:
                    snapshot[code] = d
            return self.signals_fn(snapshot=snapshot,
                                   positions=self.positions_cache,
                                   account=self.account_cache,
                                   factor_weights=self.factor_weights)
        except Exception as e:
            self.notifier.send("信号生成失败", str(e), "ERROR")
            return None

    def _execute(self, target):
        target_code = target.get("target_code", "")
        prices = target.get("prices", {})
        cur_code = self.positions_cache[0].code \
            if self.positions_cache else ""
        if cur_code and cur_code != target_code:
            self._sell_position(cur_code, prices.get(cur_code, 0))
        if target_code and target_code != cur_code:
            self._buy_target(target_code, prices.get(target_code, 0))

    def _sell_position(self, code, price):
        if price <= 0:
            price = self.market.get_price(code)
        if price <= 0:
            return
        pos = next((p for p in self.positions_cache if p.code == code), None)
        if not pos or pos.available <= 0:
            return
        shares = pos.available
        approved, _ = self.risk.check_order(
            "SELL", code, price, shares, self.account_cache,
            self.positions_cache, price)
        if not approved:
            return
        if MODE["auto_order"] and not MODE["dry_run"]:
            order = self.broker.sell(code, price, shares, "换仓")
        else:
            order = Order(code=code, side="SELL", price=price,
                          shares=shares, amount=price * shares,
                          status="DRY_RUN", reason="换仓")
        self._record_order(order)
        self.risk.on_order_submitted(price * shares,
                                     self.account_cache.total_asset)
        self.notifier.send(f"卖出 {code}",
                           f"{price:.3f} × {shares}")

    def _buy_target(self, code, price):
        if price <= 0:
            price = self.market.get_price(code)
        if price <= 0:
            return
        cash = self.account_cache.cash
        invest = min(cash - LIVE_RISK["min_cash_reserve"],
                     cash * LIVE_RISK["max_position_ratio"],
                     LIVE_RISK["max_order_amount"])
        if invest < LIVE_RISK["min_order_amount"]:
            return
        shares = int(invest / price / 100) * 100
        if shares <= 0:
            return
        approved, _ = self.risk.check_order(
            "BUY", code, price, shares, self.account_cache,
            self.positions_cache, price)
        if not approved:
            return
        if MODE["auto_order"] and not MODE["dry_run"]:
            order = self.broker.buy(code, price, shares, "信号")
        else:
            order = Order(code=code, side="BUY", price=price,
                          shares=shares, amount=price * shares,
                          status="DRY_RUN", reason="信号")
        self._record_order(order)
        self.risk.on_order_submitted(price * shares,
                                     self.account_cache.total_asset)
        self.notifier.send(f"买入 {code}",
                           f"{price:.3f} × {shares}")

    def _record_order(self, order):
        path = f"{STORAGE['dir']}/{STORAGE['orders']}"
        row = {"time": datetime.now().isoformat(),
               "code": order.code, "side": order.side,
               "price": order.price, "shares": order.shares,
               "amount": order.amount, "status": order.status,
               "order_id": order.order_id, "reason": order.reason,
               "error": order.error}
        try:
            if os.path.exists(path):
                df = pd.read_csv(path)
                df = pd.concat([df, pd.DataFrame([row])],
                               ignore_index=True)
            else:
                df = pd.DataFrame([row])
            df.to_csv(path, index=False, encoding="utf-8-sig")
        except Exception:
            pass