# -*- coding: utf-8 -*-
"""PBO + DSR 联动触发"""

import numpy as np
import pandas as pd
from config import TRIGGER_LOGIC


class DualIndicatorTrigger:
    def __init__(self, params=None):
        self.p = {**TRIGGER_LOGIC, **(params or {})}
        self.history = []
        self.consecutive_count = 0

    def check(self, dsr_now, pbo_now, dsr_prev=None, pbo_prev=None):
        if not self.p["enabled"]:
            return {"triggered": False, "reason": "关闭",
                    "mode": self.p["mode"]}

        dsr_bad = (dsr_now < self.p["dsr_threshold"] or
                   (dsr_prev is not None and
                    (dsr_now - dsr_prev) < self.p["dsr_delta_threshold"]))
        pbo_bad = (pbo_now > self.p["pbo_threshold"] or
                   (pbo_prev is not None and
                    (pbo_now - pbo_prev) > self.p["pbo_delta_threshold"]))

        mode = self.p["mode"]
        triggered, reason = False, ""
        if mode == "and":
            if dsr_bad and pbo_bad:
                triggered = True
                reason = "DSR + PBO 同时恶化"
        elif mode == "or":
            if dsr_bad or pbo_bad:
                triggered = True
                reason = "DSR 或 PBO 恶化"
        elif mode == "weighted":
            w = self.p["weights"]
            dsr_s = max(0, (self.p["dsr_threshold"] - dsr_now) /
                        max(self.p["dsr_threshold"], 1e-6))
            pbo_s = max(0, (pbo_now - self.p["pbo_threshold"]) /
                        max(1 - self.p["pbo_threshold"], 1e-6))
            score = w["dsr"] * dsr_s + w["pbo"] * pbo_s
            if score >= self.p["weighted_threshold"]:
                triggered = True
                reason = f"加权 score={score:.3f}"
            self.history.append({"dsr": dsr_now, "pbo": pbo_now,
                                 "triggered": triggered, "score": score})
            return {"triggered": triggered, "reason": reason,
                    "mode": mode, "score": float(score)}

        if triggered:
            self.consecutive_count += 1
        else:
            self.consecutive_count = 0
        final = self.consecutive_count >= self.p["consecutive_rounds"]
        self.history.append({"dsr": dsr_now, "pbo": pbo_now,
                             "triggered": final, "reason": reason})
        return {"triggered": final, "reason": reason if final
                else f"未达连续阈值 ({self.consecutive_count}/"
                     f"{self.p['consecutive_rounds']})",
                "mode": mode, "consecutive": self.consecutive_count}