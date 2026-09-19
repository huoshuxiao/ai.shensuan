# -*- coding: utf-8 -*-
"""实时行情"""

import time
import threading
import requests
from collections import deque
from config_live import MARKET_DATA


class SinaDataSource:
    URL = "https://hq.sinajs.cn/list={}"

    def __init__(self):
        self.codes = []
        self.callback = None
        self.running = False
        self.thread = None
        self.cache = {}
        self.last_update = {}

    def _format_code(self, code):
        if code.startswith("5"):
            return f"sh{code}"
        return f"sz{code}"

    def _fetch(self):
        codes_str = ",".join(self._format_code(c) for c in self.codes)
        try:
            resp = requests.get(self.URL.format(codes_str), timeout=3,
                                headers={"Referer":
                                         "https://finance.sina.com.cn"})
            resp.encoding = "gbk"
            result = {}
            for line in resp.text.strip().split("\n"):
                if "=" not in line:
                    continue
                code_part, data_part = line.split("=", 1)
                code = code_part.split("_")[-1][2:]
                data = data_part.strip('";').split(",")
                if len(data) < 32:
                    continue
                result[code] = {"code": code, "name": data[0],
                                "open": float(data[1]),
                                "prev_close": float(data[2]),
                                "price": float(data[3]),
                                "high": float(data[4]),
                                "low": float(data[5]),
                                "volume": float(data[8]),
                                "amount": float(data[9])}
            return result
        except Exception:
            return {}

    def _loop(self):
        while self.running:
            data = self._fetch()
            now = time.time()
            for code, d in data.items():
                self.cache[code] = d
                self.last_update[code] = now
                if self.callback:
                    try:
                        self.callback(code, d)
                    except Exception:
                        pass
            time.sleep(2)

    def subscribe(self, codes, callback):
        self.codes = list(codes)
        self.callback = callback
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def unsubscribe(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)

    def get_latest(self, code):
        return self.cache.get(code, {})


class MarketDataManager:
    def __init__(self):
        self.source = SinaDataSource()
        self.callbacks = []
        self.recent_data = {}
        self.max_ticks = 240

    def subscribe(self, codes, callback=None):
        def _cb(code, tick):
            if code not in self.recent_data:
                self.recent_data[code] = deque(maxlen=self.max_ticks)
            self.recent_data[code].append({**tick,
                                            "recv_time": time.time()})
            for cb in self.callbacks:
                try:
                    cb(code, tick)
                except Exception:
                    pass
            if callback:
                try:
                    callback(code, tick)
                except Exception:
                    pass
        if callback:
            self.callbacks.append(callback)
        self.source.subscribe(codes, _cb)

    def unsubscribe(self):
        self.source.unsubscribe()

    def get_price(self, code):
        return self.source.get_latest(code).get("price", 0.0)

    def get_tick(self, code):
        return self.source.get_latest(code)