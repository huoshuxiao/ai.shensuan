# -*- coding: utf-8 -*-
"""动态目标权重"""

import numpy as np
from config import DYNAMIC_WEIGHTS as CFG


def get_phase(generation, n_generations):
    progress = generation / max(n_generations, 1)
    if progress < CFG["explore_ratio"]:
        return "explore"
    elif progress < CFG["explore_ratio"] + CFG["converge_ratio"]:
        return "converge"
    return "refine"


def _three_phase_weights(generation, n_generations):
    phase = get_phase(generation, n_generations)
    weights = dict(CFG["phases"][phase])
    if CFG["smooth_transition"]:
        weights = _smooth(generation, n_generations, weights, phase)
    return weights


def _smooth(generation, n_generations, current, phase):
    transition = CFG["transition_bars"]
    progress = generation / max(n_generations, 1)
    phases_order = ["explore", "converge", "refine"]
    boundaries = [0, CFG["explore_ratio"],
                  CFG["explore_ratio"] + CFG["converge_ratio"], 1.0]
    idx = phases_order.index(phase)
    next_boundary = boundaries[idx + 1]
    transition_start = max(0, next_boundary - transition / n_generations)
    if progress >= transition_start and idx < len(phases_order) - 1:
        t = (progress - transition_start) / max(
            next_boundary - transition_start, 1e-6)
        t = max(0.0, min(1.0, t))
        next_weights = CFG["phases"][phases_order[idx + 1]]
        merged = {k: (1 - t) * current[k] + t * next_weights[k]
                  for k in current}
        total = sum(merged.values())
        return {k: v / total for k, v in merged.items()}
    return current


class DynamicWeightScheduler:
    def __init__(self, n_generations, params=None):
        self.n = n_generations
        self.p = {**CFG, **(params or {})}
        self.history = []

    def get_weights(self, generation, diversity=None):
        if not self.p["enabled"]:
            return {k: 1.0 / 5 for k in CFG["phases"]["explore"]}
        w = _three_phase_weights(generation, self.n)
        self.history.append({"generation": generation,
                             "phase": get_phase(generation, self.n),
                             "weights": dict(w)})
        return w

    def compute_score(self, metrics, weights):
        score = 0.0
        score += weights.get("ic", 0) * min(
            1.0, metrics.get("abs_ic", 0) / 0.05)
        score += weights.get("low_turnover", 0) * max(
            0, 1 - metrics.get("turnover", 0) / 0.5)
        score += weights.get("low_corr", 0) * max(
            0, 1 - metrics.get("max_corr", 0))
        score += weights.get("stability", 0) * metrics.get("stability", 0)
        score += weights.get("simplicity", 0) * metrics.get("simplicity", 0)
        return score

    def get_history_df(self):
        import pandas as pd
        if not self.history:
            return pd.DataFrame()
        rows = []
        for h in self.history:
            row = {"generation": h["generation"], "phase": h["phase"]}
            for k, v in h["weights"].items():
                row[f"w_{k}"] = v
            rows.append(row)
        return pd.DataFrame(rows)