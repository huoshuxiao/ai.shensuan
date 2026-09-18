# -*- coding: utf-8 -*-
"""自适应变异率"""

import random
import numpy as np
from difflib import SequenceMatcher
from config import ADAPTIVE_MUTATION


def _expr_distance_diversity(exprs):
    if len(exprs) < 2:
        return 0.0
    sample_size = min(20, len(exprs))
    sample = random.sample(exprs, sample_size)
    sims = []
    for i in range(len(sample)):
        for j in range(i + 1, len(sample)):
            sims.append(SequenceMatcher(
                None, sample[i], sample[j]).ratio())
    return 1.0 - float(np.mean(sims)) if sims else 0.0


def compute_diversity(pop, metrics, metric="expr_distance"):
    exprs = []
    for node in pop:
        if hasattr(node, "op") and node.op == "raw":
            exprs.append(node.value)
        else:
            exprs.append(node.to_string())
    if metric == "expr_distance":
        return _expr_distance_diversity(exprs)
    return _expr_distance_diversity(exprs)


class AdaptiveMutationController:
    def __init__(self, params=None):
        self.p = {**ADAPTIVE_MUTATION, **(params or {})}
        self.mutation_rate = 0.3
        self.crossover_rate = 0.5
        self.diversity_history = []
        self.rate_history = []

    def update(self, pop, metrics):
        if not self.p["enabled"]:
            return {"mutation_rate": self.mutation_rate,
                    "crossover_rate": self.crossover_rate,
                    "diversity": 0.0}
        div = compute_diversity(pop, metrics, self.p["diversity_metric"])
        self.diversity_history.append(div)
        window = self.p["history_window"]
        smooth = float(np.mean(self.diversity_history[-window:]))
        gap = self.p["target_diversity"] - smooth
        delta = gap * self.p["adaptation_speed"]
        new_mut = max(self.p["min_mutation_rate"],
                      min(self.p["max_mutation_rate"],
                          self.mutation_rate + delta))
        new_cross = self.crossover_rate
        if self.p["crossover_adaptive"]:
            new_cross = max(self.p["min_crossover_rate"],
                            min(self.p["max_crossover_rate"],
                                self.crossover_rate - delta * 0.5))
        self.mutation_rate = new_mut
        self.crossover_rate = new_cross
        self.rate_history.append({
            "mutation_rate": new_mut,
            "crossover_rate": new_cross,
            "diversity": smooth})
        return {"mutation_rate": new_mut,
                "crossover_rate": new_cross,
                "diversity": smooth, "gap": gap}

    def get_history_df(self):
        import pandas as pd
        if not self.rate_history:
            return pd.DataFrame()
        df = pd.DataFrame(self.rate_history)
        df["generation"] = df.index + 1
        return df