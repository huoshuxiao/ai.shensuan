# -*- coding: utf-8 -*-
"""RL 权重调度（LinUCB 上下文赌博机）"""

import os
import pickle
import numpy as np
from config import RL_WEIGHT


ACTION_SPACE = {
    "explore_heavy": {"ic": 0.25, "low_turnover": 0.10,
                      "low_corr": 0.20, "stability": 0.10,
                      "simplicity": 0.35},
    "balanced": {"ic": 0.30, "low_turnover": 0.15, "low_corr": 0.15,
                 "stability": 0.20, "simplicity": 0.20},
    "ic_focus": {"ic": 0.50, "low_turnover": 0.10, "low_corr": 0.10,
                 "stability": 0.20, "simplicity": 0.10},
    "stability_focus": {"ic": 0.25, "low_turnover": 0.15,
                        "low_corr": 0.15, "stability": 0.35,
                        "simplicity": 0.10},
    "cost_focus": {"ic": 0.30, "low_turnover": 0.30, "low_corr": 0.15,
                   "stability": 0.15, "simplicity": 0.10},
    "diversity_focus": {"ic": 0.25, "low_turnover": 0.10,
                        "low_corr": 0.35, "stability": 0.15,
                        "simplicity": 0.15},
    "simplicity_focus": {"ic": 0.25, "low_turnover": 0.10,
                         "low_corr": 0.15, "stability": 0.15,
                         "simplicity": 0.35},
    "converge": {"ic": 0.35, "low_turnover": 0.15, "low_corr": 0.10,
                 "stability": 0.30, "simplicity": 0.10},
}


class LinUCBBandit:
    def __init__(self, n_actions, n_features, alpha=0.5):
        self.n_actions = n_actions
        self.n_features = n_features
        self.alpha = alpha
        self.A = [np.identity(n_features) for _ in range(n_actions)]
        self.b = [np.zeros(n_features) for _ in range(n_actions)]
        self.total_pulls = np.zeros(n_actions)

    def _context(self, gen, diversity, best_score_hist):
        x = np.zeros(self.n_features)
        x[0] = 1.0
        x[1] = gen / 100.0
        x[2] = diversity
        if best_score_hist:
            x[3] = np.mean(best_score_hist[-5:])
            if len(best_score_hist) >= 5:
                x[4] = best_score_hist[-1] - best_score_hist[-5]
        return x

    def select(self, context):
        ucb_values = []
        for a in range(self.n_actions):
            A_inv = np.linalg.inv(self.A[a])
            theta = A_inv @ self.b[a]
            mean = theta @ context
            ucb = mean + self.alpha * np.sqrt(context @ A_inv @ context)
            ucb_values.append(ucb)
        return int(np.argmax(ucb_values))

    def update(self, action, context, reward):
        self.A[action] += np.outer(context, context)
        self.b[action] += reward * context
        self.total_pulls[action] += 1

    def save(self, path):
        with open(path, "wb") as f:
            pickle.dump({"A": self.A, "b": self.b,
                         "total_pulls": self.total_pulls}, f)

    def load(self, path):
        if os.path.exists(path):
            with open(path, "rb") as f:
                d = pickle.load(f)
                self.A = d["A"]
                self.b = d["b"]
                self.total_pulls = d["total_pulls"]


class RLWeightScheduler:
    def __init__(self, n_generations, n_features=5, alpha=0.5,
                 load_path=None):
        self.n = n_generations
        self.actions = list(ACTION_SPACE.keys())
        self.bandit = LinUCBBandit(len(self.actions), n_features, alpha)
        self.load_path = load_path or RL_WEIGHT.get(
            "load_path", "rl_weight_bandit.pkl")
        self.bandit.load(self.load_path)
        self.history = []
        self.best_score_hist = []
        self.prev_context = None
        self.prev_action = None

    def get_weights(self, generation, diversity=None):
        div = diversity if diversity is not None else 0.5
        context = self.bandit._context(generation, div,
                                        self.best_score_hist)
        action_idx = self.bandit.select(context)
        self.prev_context = context
        self.prev_action = action_idx
        action_name = self.actions[action_idx]
        weights = dict(ACTION_SPACE[action_name])
        self.history.append({"generation": generation,
                             "action": action_name,
                             "diversity": div, "weights": weights})
        return weights

    def update_reward(self, reward):
        if self.prev_context is not None and self.prev_action is not None:
            self.bandit.update(self.prev_action, self.prev_context, reward)
            self.best_score_hist.append(reward)
            self.bandit.save(self.load_path)

    def compute_score(self, metrics, weights):
        score = 0.0
        score += weights.get("ic", 0) * min(
            1.0, metrics.get("abs_ic", 0) / 0.05)
        score += weights.get("low_turnover", 0) * max(
            0, 1 - metrics.get("turnover", 0) / 0.5)
        score += weights.get("low_corr", 0) * max(
            0, 1 - metrics.get("max_corr", 0))
        score += weights.get("stability", 0) * metrics.get("stability", 0)
        score += weights.get("simplicity", 0) * metrics.get(
            "simplicity", 0)
        return score

    def get_history_df(self):
        import pandas as pd
        if not self.history:
            return pd.DataFrame()
        rows = []
        for h in self.history:
            row = {"generation": h["generation"], "action": h["action"],
                   "diversity": h["diversity"]}
            for k, v in h["weights"].items():
                row[f"w_{k}"] = v
            rows.append(row)
        return pd.DataFrame(rows)