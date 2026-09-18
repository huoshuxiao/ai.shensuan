# -*- coding: utf-8 -*-
"""自动重挖闭环"""

import time
import numpy as np
import pandas as pd
from config import AUTO_REMINING


class ReminingTrigger:
    def __init__(self, params=None):
        self.p = {**AUTO_REMINING, **(params or {})}
        self.last_remining_bar = -1_000_000
        self.remining_count = 0
        self.history = []

    def should_remining(self, current_bar, decay_alerts,
                        pbo_trend=None):
        if not self.p["enabled"]:
            return False, "重挖关闭", ""
        if self.remining_count >= self.p["max_remining_rounds"]:
            return False, "已达最大重挖轮数", ""
        if current_bar - self.last_remining_bar < self.p["cooldown_bars"]:
            return False, "冷却中", ""
        if "decay" in self.p["trigger_on"]:
            n = len([a for a in decay_alerts
                     if a.get("action") == "trigger_remining"])
            if n >= self.p["min_decay_alerts"]:
                return True, f"{n} 个因子衰减", "decay"
        if "pbo_rising" in self.p["trigger_on"] and pbo_trend:
            if pbo_trend.get("is_rising", False):
                return True, "PBO 上升", "pbo_rising"
        return False, "无触发条件", ""

    def mark_remining(self, bar, reason, ttype):
        self.last_remining_bar = bar
        self.remining_count += 1
        self.history.append({"bar": bar, "reason": reason,
                             "type": ttype,
                             "round": self.remining_count})


class FactorReplacer:
    def __init__(self, params=None):
        self.p = {**AUTO_REMINING, **(params or {})}
        self.replacement_log = []

    @staticmethod
    def _factor_ic(factor, pool):
        from factor_dsl import compute_ic
        ics = []
        for code, df in pool.items():
            if code not in factor.get("impl", {}):
                continue
            f = factor["impl"][code]["factor"]
            fr = df["close"].pct_change().shift(-1)
            ic = compute_ic(f, fr)
            if not np.isnan(ic):
                ics.append(ic)
        return float(np.mean(ics)) if ics else 0.0

    def decide_replacement(self, old_factors, new_factors, pool):
        old_ic = {f["name"]: self._factor_ic(f, pool)
                  for f in old_factors}
        new_ic = {f["name"]: self._factor_ic(f, pool)
                  for f in new_factors}
        worst_old = min(old_ic.items(), key=lambda x: abs(x[1])) \
            if old_ic else (None, 0)
        best_new = max(new_ic.items(), key=lambda x: abs(x[1])) \
            if new_ic else (None, 0)
        replace, keep, add = {}, [], []
        if worst_old[0] and best_new[0]:
            improve = abs(best_new[1]) - abs(worst_old[1])
            if (abs(worst_old[1]) < 0.005 and
                    improve >= self.p["new_factor_ic_improve"]):
                new_f = next(f for f in new_factors
                             if f["name"] == best_new[0])
                replace[worst_old[0]] = new_f
                self.replacement_log.append({
                    "old": worst_old[0], "new": best_new[0],
                    "improve": improve})
        for name in old_ic:
            if name not in replace:
                keep.append(name)
        used = set(r["name"] for r in replace.values())
        for f in new_factors:
            if f["name"] not in used and len(add) < 1:
                if abs(new_ic[f["name"]]) > 0.01:
                    add.append(f)
        return {"replace": replace, "keep": keep, "add": add,
                "old_ic": old_ic, "new_ic": new_ic}

    def apply_replacement(self, old_factors, decision):
        replace = decision["replace"]
        keep = set(decision["keep"])
        add = decision["add"]
        new_factors = []
        for f in old_factors:
            if f["name"] in replace:
                new_factors.append(replace[f["name"]])
            elif f["name"] in keep:
                new_factors.append(f)
        for f in add:
            new_factors.append(f)
        return new_factors


class AutoReminingLoop:
    def __init__(self, mine_fn, evaluate_fn, params=None):
        self.p = {**AUTO_REMINING, **(params or {})}
        self.mine_fn = mine_fn
        self.evaluate_fn = evaluate_fn
        self.trigger = ReminingTrigger(params)
        self.replacer = FactorReplacer(params)
        self.log = []

    def run_once(self, current_bar, current_factors, pool,
                 decay_alerts, pbo_trend=None):
        should, reason, ttype = self.trigger.should_remining(
            current_bar, decay_alerts, pbo_trend)
        if not should:
            return {"triggered": False, "reason": reason}

        print(f"\n  🔁 触发自动重挖 | bar={current_bar} | {reason}")
        metrics_before = self.evaluate_fn(current_factors)
        try:
            new_factors = self.mine_fn(pool)
        except Exception as e:
            return {"triggered": False, "reason": f"重挖失败: {e}"}
        if not new_factors:
            return {"triggered": False, "reason": "无新因子"}

        decision = self.replacer.decide_replacement(
            current_factors, new_factors, pool)
        candidate = self.replacer.apply_replacement(
            current_factors, decision)
        metrics_after = self.evaluate_fn(candidate)

        dsr_b = metrics_before.get("dsr", 0)
        dsr_a = metrics_after.get("dsr", 0)
        if self.p["keep_old_factor_if_new_worse"] and dsr_a < dsr_b:
            final, adopted = current_factors, False
        else:
            final, adopted = candidate, True
            self.trigger.mark_remining(current_bar, reason, ttype)

        self.log.append({"bar": current_bar, "reason": reason,
                         "trigger_type": ttype, "adopted": adopted,
                         "dsr_before": dsr_b, "dsr_after": dsr_a})
        return {"triggered": True, "new_factors": final,
                "decision": decision, "adopted": adopted,
                "metrics_before": metrics_before,
                "metrics_after": metrics_after}

    def get_log_df(self):
        if not self.log:
            return pd.DataFrame()
        return pd.DataFrame(self.log)