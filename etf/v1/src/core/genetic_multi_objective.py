# -*- coding: utf-8 -*-
"""多目标遗传编程（NSGA-II 简化版）

单目标 GP 只追 |IC|，容易挖出高换手、与已有因子雷同的"刷分"表达式。
本模块同时优化三维目标：|IC| 越大越好，turnover（信号翻转率）与
max_corr（与库内因子最大相关）越小越好，用 Pareto 前沿保留
互不支配的折中解集。"""

import random
import numpy as np
import pandas as pd
from config import (
    GENETIC_MULTI_OBJECTIVE as CFG,
    LLM_MUTATION, LLM_CROSSOVER, RL_WEIGHT, RESULTS_DIR,
)
from factor_genetic import (
    ExprNode, random_expr, mutate, crossover,
)
from genetic_extended_objectives import evaluate_extended
from adaptive_mutation import AdaptiveMutationController
from dynamic_objective_weights import DynamicWeightScheduler
from pareto_animation import ParetoRecorder


def dominates(a, b):
    """Pareto 支配：a 三目标全部不劣于 b，且至少一个严格更优。
    （不存在单一起步权重可把 a 排到 b 之后 => b 应被淘汰）"""
    better_or_equal = (a["abs_ic"] >= b["abs_ic"] and
                       a["turnover"] <= b["turnover"] and
                       a["max_corr"] <= b["max_corr"])
    strictly = (a["abs_ic"] > b["abs_ic"] or
                a["turnover"] < b["turnover"] or
                a["max_corr"] < b["max_corr"])
    return better_or_equal and strictly


def fast_non_dominated_sort(metrics):
    """NSGA-II 快速非支配分层：
    第 0 层 = 不被任何人支配的解；剔除后再找下一层……
    返回 [[前沿1下标...], [前沿2...], ...]，复杂度 O(M·n²)。"""
    n = len(metrics)
    S = [[] for _ in range(n)]           # i 支配的解集
    n_dominated = [0] * n                # i 被多少个解支配
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
    while fronts[k]:                     # 剥洋葱：支配数清零则进入下一层
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
    """拥挤度：同层内保持多样性的排名的密度代理。
    对每个目标轴排序后，个体得分 = 左右邻居间距/该轴全域跨度 之和；
    前沿两端无穷大（边界解必留）。选择时优先淘汰拥挤度小（密集区）
    的解，使 Pareto 前沿均匀铺开。"""
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
        if RL_WEIGHT.get("enabled"):
            from rl_weight_scheduler import RLWeightScheduler
            self.weight_scheduler = RLWeightScheduler(
                self.cfg["n_generations"],
                alpha=RL_WEIGHT.get("alpha", 0.5),
                load_path=RL_WEIGHT.get("load_path"))
        else:
            self.weight_scheduler = DynamicWeightScheduler(
                self.cfg["n_generations"])
        self.recorder = ParetoRecorder()
        self.mutation_rate = self.cfg["mutation_rate"]
        self.crossover_rate = self.cfg["crossover_rate"]
        self.mut_op = None
        self.cross_op = None
        if LLM_MUTATION.get("enabled"):
            from llm_mutation_operator import LLMMutationOperator
            self.mut_op = LLMMutationOperator()
        if LLM_CROSSOVER.get("enabled"):
            from llm_crossover_operator import LLMCrossoverOperator
            self.cross_op = LLMCrossoverOperator()

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
        stagnation = self.cfg.get("stagnation_generations", 0)
        stall, best_so_far = 0, 0.0
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
            if self.mut_op is not None:
                self.mut_op.reset_generation()
            if self.cross_op is not None:
                self.cross_op.reset_generation()
            best_ic = max(m["abs_ic"] for m in metrics)
            print(f"  第 {gen + 1} 代: 最佳|IC|={best_ic:.4f}  "
                  f"前沿={len(fronts[0]) if fronts else 0}")
            if best_ic > best_so_far + 1e-6:
                best_so_far, stall = best_ic, 0
            else:
                stall += 1
                if stagnation and stall >= stagnation \
                        and gen + 1 < self.cfg["n_generations"]:
                    print(f"  ℹ️ 最佳|IC| 已连续 {stall} 代无提升，"
                          f"早停于第 {gen + 1} 代"
                          f"（计划 {self.cfg['n_generations']} 代）")
                    break

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
                ma = metrics[self.rng.randint(0, len(metrics) - 1)]
                mb = metrics[self.rng.randint(0, len(metrics) - 1)]
                a, b = ma["node"], mb["node"]
                if self.rng.random() < self.crossover_rate:
                    if self.cross_op is not None:
                        from llm_crossover_operator import smart_crossover
                        c1, c2 = smart_crossover(a, b, ma, mb,
                                                 self.rng, self.cross_op)
                    else:
                        c1, c2 = crossover(a, b, self.rng)
                else:
                    c1, c2 = a.clone(), b.clone()
                if self.rng.random() < self.mutation_rate:
                    if self.mut_op is not None:
                        from llm_mutation_operator import smart_mutate
                        c1 = smart_mutate(c1, ma, self.rng,
                                          self.cfg["max_expr_depth"],
                                          self.mut_op)
                    else:
                        c1 = mutate(c1, self.rng,
                                    self.cfg["max_expr_depth"])
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
            adapt_hist.to_csv(
                f"{RESULTS_DIR}/adaptive_mutation_history.csv",
                index=False, encoding="utf-8-sig")
        weights_hist = self.weight_scheduler.get_history_df()
        if not weights_hist.empty:
            weights_hist.to_csv(
                f"{RESULTS_DIR}/dynamic_weights_history.csv",
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
        hist.to_csv(f"{RESULTS_DIR}/mogp_history.csv",
                    index=False, encoding="utf-8-sig")
    if factors:
        from factor_naming import cn_name
        pd.DataFrame([{**{k: f.get(k) for k in
                          ["name", "expr", "mean_ic", "turnover",
                           "max_corr", "stability", "simplicity"]},
                       "cn_name": cn_name(f.get("name", ""),
                                          f.get("expr", ""))}
                      for f in factors]).to_csv(
            f"{RESULTS_DIR}/mogp_pareto_front.csv",
            index=False, encoding="utf-8-sig")
    try:
        from animation_exporter import export_animation
        if gp.recorder.snapshots:
            export_animation(gp.recorder.snapshots)
    except Exception as e:
        print(f"  ⚠️ 动画导出失败: {e}")
    try:
        from pareto_animation import create_animation
        if gp.recorder.snapshots:
            create_animation(gp.recorder.snapshots)
    except Exception as e:
        print(f"  ⚠️ HTML 动画生成失败: {e}")
    try:
        from animation_shap_overlay import create_animation_with_shap
        if gp.recorder.snapshots:
            create_animation_with_shap(gp.recorder.snapshots)
    except Exception as e:
        print(f"  ⚠️ SHAP 叠加动画失败: {e}")
    try:
        for op, fn in ((gp.mut_op, "llm_mutation_history.csv"),
                       (gp.cross_op, "llm_crossover_history.csv")):
            if op is not None and op.history:
                op.get_history_df().to_csv(
                    f"{RESULTS_DIR}/{fn}",
                    index=False, encoding="utf-8-sig")
    except Exception:
        pass
    return factors


# genetic_multi_objective.py 补充
def evaluate_multi_objective(expr_str, pool, existing_factors=None,
                              ref_code=None):
    """兼容接口：复用 evaluate_extended 的核心字段"""
    from genetic_extended_objectives import evaluate_extended
    m = evaluate_extended(expr_str, pool, existing_factors, ref_code)
    return m