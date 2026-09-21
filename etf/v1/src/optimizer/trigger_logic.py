# -*- coding: utf-8 -*-
"""PBO + DSR 联动触发

单一判据：本轮 DSR/PBO 是否恶化（水平越线或与上一轮相比 Δ 越线），
连续 consecutive_rounds 轮恶化才真正触发重挖。
"上一轮"取自持久状态 ReminingState，否则进程内的 Δ 分支永远拿不到
基准、连续计数也永远凑不满。
"""

from config import TRIGGER_LOGIC
from remining_state import ReminingState


class DualIndicatorTrigger:
    def __init__(self, params=None, state=None):
        self.p = {**TRIGGER_LOGIC, **(params or {})}
        self.state = state if state is not None else ReminingState()

    @property
    def consecutive_count(self) -> int:
        return self.state.consecutive_bad_rounds

    @property
    def history(self) -> list:
        return self.state.data.get("indicator_history", [])

    def _bad(self, dsr_now, pbo_now, dsr_prev, pbo_prev):
        """单轮恶化判定：水平越线 or 相对上一轮 Δ 越线"""
        dsr_bad = (dsr_now < self.p["dsr_threshold"] or
                   (dsr_prev is not None and
                    (dsr_now - dsr_prev) < self.p["dsr_delta_threshold"]))
        pbo_bad = (pbo_now > self.p["pbo_threshold"] or
                   (pbo_prev is not None and
                    (pbo_now - pbo_prev) > self.p["pbo_delta_threshold"]))
        return dsr_bad, pbo_bad

    def _score(self, dsr_now, pbo_now):
        """weighted 模式的恶化严重度：
        score = w_dsr·(门槛-DSR)/门槛 + w_pbo·(PBO-门槛)/(1-门槛)，两项均为
        越线幅度的相对归一化，score >= weighted_threshold 记为恶化。"""
        w = self.p["weights"]
        dsr_s = max(0, (self.p["dsr_threshold"] - dsr_now) /
                    max(self.p["dsr_threshold"], 1e-6))
        pbo_s = max(0, (pbo_now - self.p["pbo_threshold"]) /
                    max(1 - self.p["pbo_threshold"], 1e-6))
        return w["dsr"] * dsr_s + w["pbo"] * pbo_s

    def check(self, dsr_now, pbo_now, dsr_prev=None, pbo_prev=None,
              current_bar=None):
        """返回 {triggered, bad_this_round, reason, mode, consecutive}"""
        if not self.p["enabled"]:
            return {"triggered": False, "bad_this_round": False,
                    "reason": "关闭", "mode": self.p["mode"],
                    "consecutive": self.consecutive_count}

        mode = self.p["mode"]
        if dsr_prev is None:
            dsr_prev = self.state.last_dsr
        if pbo_prev is None:
            pbo_prev = self.state.last_pbo
        if dsr_now is None or pbo_now is None:
            # 上游阶段被关掉时指标为 None，不能按"恶化"计入连续轮数
            return {"triggered": False, "bad_this_round": False,
                    "reason": f"指标缺失（DSR={dsr_now}, PBO={pbo_now}）",
                    "mode": mode, "consecutive": self.consecutive_count}

        score = None
        if mode == "weighted":
            score = float(self._score(dsr_now, pbo_now))
            bad = score >= self.p["weighted_threshold"]
            reason = f"加权 score={score:.3f}"
        else:
            dsr_bad, pbo_bad = self._bad(dsr_now, pbo_now,
                                         dsr_prev, pbo_prev)
            if mode == "and":
                bad = dsr_bad and pbo_bad
                reason = "DSR + PBO 同时恶化"
            else:  # or
                bad = dsr_bad or pbo_bad
                reason = "DSR 或 PBO 恶化"
            if not bad:
                reason = ""

        if self.state.data.get("current_bar") != current_bar:
            self.state.data["current_bar"] = current_bar
        # 本轮判定计入后再看是否达到连续阈值（bad 轮 +1，好转归零）
        consecutive = (self.consecutive_count + 1 if bad else 0)
        final = consecutive >= self.p["consecutive_rounds"]
        if not final:
            reason = (f"本轮恶化，连续 {consecutive}/"
                      f"{self.p['consecutive_rounds']} 轮" if bad
                      else "本轮未恶化")
        self.state.record_indicator(bad, dsr=dsr_now, pbo=pbo_now,
                                    triggered=final, reason=reason)
        self.state.save()
        out = {"triggered": final, "bad_this_round": bad,
               "reason": reason, "mode": mode, "consecutive": consecutive}
        if score is not None:
            out["score"] = score
        return out
