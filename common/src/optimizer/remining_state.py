# -*- coding: utf-8 -*-
"""自动重挖的跨运行状态

DSR/PBO 连续恶化计数、重挖轮数、冷却基准这些量只有在"上一次运行"
的基础上才有意义，而触发器对象每次进程都重新构造，纯内存实现等于
每轮从零开始：consecutive_rounds 永远凑不满，cooldown_bars 与
max_remining_rounds 跨运行全部失效。此处把状态落盘并按频率分桶
（日线与分钟线的 bar 量纲不同，混用会让冷却判断失真）。
"""

import os
import json
from datetime import datetime

from config import REMINING_STATE_FILE, FREQ

_MAX_HISTORY = 50
_UNSET_BAR = -1_000_000


class ReminingState:
    """按频率分桶的持久状态；读写同一份 JSON，写失败不抛断流水线。"""

    def __init__(self, path=REMINING_STATE_FILE, freq=None):
        self.path = path
        self.freq = freq or FREQ
        self._all = self._read()
        self.data = self._all.setdefault(self.freq, {})

    def _read(self):
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return raw if isinstance(raw, dict) else {}
        except Exception as e:
            print(f"  ⚠️ 重挖状态损坏，忽略重建: {type(e).__name__}: {e}")
            return {}

    def save(self):
        self.data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._all[self.freq] = self.data
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._all, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- DSR/PBO 联动指标 ----
    @property
    def consecutive_bad_rounds(self) -> int:
        return int(self.data.get("consecutive_bad_rounds", 0))

    def record_indicator(self, bad: bool, dsr=None, pbo=None,
                         triggered=None, reason=None):
        """更新连续恶化轮数，并把本轮指标存为下一轮的 Δ 基准"""
        self.data["consecutive_bad_rounds"] = (
            self.consecutive_bad_rounds + 1 if bad else 0)
        if dsr is not None:
            self.data["last_dsr"] = round(float(dsr), 6)
        if pbo is not None:
            self.data["last_pbo"] = round(float(pbo), 6)
        hist = self.data.setdefault("indicator_history", [])
        hist.append({"bar": self.data.get("current_bar"),
                     "dsr": dsr, "pbo": pbo, "bad": bad,
                     "triggered": triggered, "reason": reason})
        del hist[:-_MAX_HISTORY]

    @property
    def last_dsr(self):
        return self.data.get("last_dsr")

    @property
    def last_pbo(self):
        return self.data.get("last_pbo")

    # ---- 重挖节流 ----
    @property
    def remining_count(self) -> int:
        return int(self.data.get("remining_count", 0))

    @property
    def last_remining_bar(self) -> int:
        return int(self.data.get("last_remining_bar", _UNSET_BAR))

    def mark_remining(self, bar, reason="", ttype=""):
        self.data["last_remining_bar"] = int(bar)
        self.data["remining_count"] = self.remining_count + 1
        hist = self.data.setdefault("remining_history", [])
        hist.append({"bar": int(bar), "reason": reason, "type": ttype,
                     "round": self.data["remining_count"]})
        del hist[:-_MAX_HISTORY]

    def __repr__(self):
        return (f"ReminingState(freq={self.freq}, "
                f"consecutive_bad={self.consecutive_bad_rounds}, "
                f"remining_count={self.remining_count}, "
                f"last_remining_bar={self.last_remining_bar})")
