# -*- coding: utf-8 -*-
"""多目标遗传编程（NSGA-II 简化版）"""

import random
import numpy as np
import pandas as pd
from config import GENETIC_MULTI_OBJECTIVE as CFG
from factor_genetic import (
    ExprNode, random_expr, mutate, crossover,
)
from genetic_extended_objectives import evaluate_extended
from adaptive_mutation import AdaptiveMutationController
from dynamic_objective_weights import DynamicWeightScheduler
from pareto_animation import ParetoRecorder


def dominates(a, b):
    better_or_equal = (a["abs_ic"] >= b["abs_ic"] and
                       a["turnover"] <= b["turnover"] and
                       a["max_corr"] <= b["max_corr"])
    strictly = (a["abs_ic"] > b["abs_ic"] or
                a["turnover"] < b["turnover"] or
                a["max_corr"] < b["max_corr"])
    return better_or_equal and strictly


def fast_non_dominated_sort(metrics):
    n = len(metrics)
    S = [[] for _ in range(n)]
    n_dominated = [0] * n
    fronts = [[]]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if dominates(metrics[i], metrics[j]):
                S[i].append(j)
            elif dominates(metrics[j], metrics[i]):
                n_dominated[i] += 1
        if n_dominated[i] == 0:
            fronts[0].append(i)
    k = 0
    while fronts[k]:
        nxt = []
        for i in fronts[k]:
            for j in S[i]:
                n_dominated[j] -= 1
                if n_dominated[j] == 0:
                    nxt.append(j)
        k += 1
        fronts.append(nxt)
    return [f for f in fronts if f]


def crowding_distance(front, metrics):
    if len(front) <= 2:
        return {i: float("inf") for i in front}
    distances = {i: 0.0 for i in front}
    for obj in ["abs_ic", "turnover", "max_corr"]:
        vals = [(i, metrics[i][obj]) for i in front]
        vals.sort(key=lambda x: x[1])
        distances[vals[0][0]] = float("inf")
        distances[vals[-1][0]] = float("inf")
        rng = vals[-1][1] - vals[0][1]
        if rng < 1e-9:
            continue
        for k in range(1, len(vals) - 1):
            distances[vals[k][0]] += (
                (vals[k + 1][1] - vals[k - 1][1]) / rng)
    return distances


class MultiObjectiveGP:
    def __init__(self, pool, existing_factors=None):
        self.pool = pool
        self.cfg = CFG
        self.rng = random.Random(self.cfg["random_state"])
        self.existing_factors = existing_factors or []
        self.ref_code = next(iter(pool))
        self.history = []
        self.adaptive = AdaptiveMutationController()
        self.weight_scheduler = DynamicWeightScheduler(
            self.cfg["n_generations"])
        self.recorder = ParetoRecorder()
        self.mutation_rate = self.cfg["mutation_rate"]
        self.crossover_rate = self.cfg["crossover_rate"]

    def _init_population(self):
        return [random_expr(self.rng, self.cfg["max_expr_depth"])
                for _ in range(self.cfg["population_size"])]

    def _evaluate_population(self, pop):
        results = []
        for node in pop:
            expr_str = node.to_string()
            m = evaluate_extended(expr_str, self.pool,
                                  self.existing_factors,
                                  self.ref_code)
            m["node"] = node
            results.append(m)
        return results

    def run(self):
        print("\n========== 多目标遗传编程 ==========")
        if not self.cfg["enabled"]:
            return []
        pop = self._init_population()
        for gen in range(self.cfg["n_generations"]):
            metrics = self._evaluate_population(pop)
            fronts = fast_non_dominated_sort(metrics)
            pareto_idx = set(fronts[0]) if fronts else set()

            snapshot = [{**m, "is_pareto": i in pareto_idx}
                        for i, m in enumerate(metrics)]
            self.recorder.record(gen + 1, snapshot)

            adapt = self.adaptive.update(pop, metrics)
            self.mutation_rate = adapt["mutation_rate"]
            self.crossover_rate = adapt["crossover_rate"]
            weights = self.weight_scheduler.get_weights(gen)
            best_ic = max(m["abs_ic"] for m in metrics)
            print(f"  第 {gen + 1} 代: 最佳|IC|={best_ic:.4f}  "
                  f"前沿={len(fronts[0]) if fronts else 0}")

            next_pop = []
            for front in fronts:
                if len(next_pop) >= self.cfg["population_size"]:
                    break
                cd = crowding_distance(front, metrics)
                for i in sorted(front, key=lambda x: -cd.get(x, 0)):
                    if len(next_pop) >= self.cfg["population_size"]:
                        break
                    next_pop.append(metrics[i]["node"])
            while len(next_pop) < self.cfg["population_size"]:
                a = metrics[self.rng.randint(0, len(metrics) - 1)]["node"]
                b = metrics[self.rng.randint(0, len(metrics) - 1)]["node"]
                if self.rng.random() < self.crossover_rate:
                    c1, c2 = crossover(a, b, self.rng)
                else:
                    c1, c2 = a.clone(), b.clone()
                if self.rng.random() < self.mutation_rate:
                    c1 = mutate(c1, self.rng, self.cfg["max_expr_depth"])
                next_pop.append(c1)
                if len(next_pop) < self.cfg["population_size"]:
                    next_pop.append(c2)
            pop = next_pop[:self.cfg["population_size"]]

        final = self._evaluate_population(pop)
        fronts = fast_non_dominated_sort(final)
        pareto = fronts[0] if fronts else []
        pareto = sorted(pareto, key=lambda i: -final[i]["abs_ic"])
        pareto = pareto[:self.cfg["pareto_front_size"]]

        result, seen = [], set()
        for i in pareto:
            m = final[i]
            if m["expr"] in seen or m["abs_ic"] < 0.003:
                continue
            seen.add(m["expr"])
            result.append({"name": f"mogp_{len(result)}",
                           "expr": m["expr"], "mean_ic": m["ic"],
                           "icir": 0.0, "turnover": m["turnover"],
                           "max_corr": m["max_corr"],
                           "stability": m.get("stability", 0.0),
                           "simplicity": m.get("simplicity", 0.0),
                           "impl": m["impl"], "source": "multi_gp"})

        self.recorder.save()
        adapt_hist = self.adaptive.get_history_df()
        if not adapt_hist.empty:
            adapt_hist.to_csv("adaptive_mutation_history.csv",
                              index=False, encoding="utf-8-sig")
        weights_hist = self.weight_scheduler.get_history_df()
        if not weights_hist.empty:
            weights_hist.to_csv("dynamic_weights_history.csv",
                                index=False, encoding="utf-8-sig")
        return result

    def get_history_df(self):
        if not self.history:
            return pd.DataFrame()
        return pd.DataFrame(self.history)


def multi_objective_mine(pool, existing_factors=None):
    gp = MultiObjectiveGP(pool, existing_factors)
    factors = gp.run()
    hist = gp.get_history_df()
    if not hist.empty:
        hist.to_csv("mogp_history.csv", index=False,
                    encoding="utf-8-sig")
    if factors:
        pd.DataFrame([{k: f.get(k) for k in
                       ["name", "expr", "mean_ic", "turnover",
                        "max_corr", "stability", "simplicity"]}
                      for f in factors]).to_csv(
            "mogp_pareto_front.csv", index=False,
            encoding="utf-8-sig")
    try:
        from animation_exporter import export_animation
        if gp.recorder.snapshots:
            export_animation(gp.recorder.snapshots)
    except Exception as e:
        print(f"  ⚠️ 动画导出失败: {e}")
    return factors


# genetic_multi_objective.py 补充
def evaluate_multi_objective(expr_str, pool, existing_factors=None,
                              ref_code=None):
    """兼容接口：复用 evaluate_extended 的核心字段"""
    from genetic_extended_objectives import evaluate_extended
    m = evaluate_extended(expr_str, pool, existing_factors, ref_code)
    return m