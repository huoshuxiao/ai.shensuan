# -*- coding: utf-8 -*-
"""因子遗传编程（单目标）"""

import random
from copy import deepcopy
import numpy as np
import pandas as pd
from config import GENETIC, GENETIC_OPERATORS
from factor_dsl import safe_eval, compute_ic


class ExprNode:
    def __init__(self, op, children=None, value=None):
        self.op = op
        self.children = children or []
        self.value = value

    def to_string(self):
        if self.op == "terminal":
            return self.value
        if self.op == "window":
            return str(self.value)
        if self.op == "raw":
            return self.value
        if self.op in GENETIC_OPERATORS["unary"]:
            arg = self.children[0].to_string()
            if self.op == "neg":
                return f"(-{arg})"
            return f"{self.op}({arg})"
        if self.op in GENETIC_OPERATORS["binary"]:
            a = self.children[0].to_string()
            b = self.children[1].to_string()
            return f"({a} {self.op} {b})"
        if self.op in GENETIC_OPERATORS["rolling"]:
            a = self.children[0].to_string()
            w = self.children[1].to_string()
            return f"{self.op}({a}, {w})"
        return "0"

    def depth(self):
        if not self.children:
            return 1
        return 1 + max(c.depth() for c in self.children)

    def clone(self):
        return deepcopy(self)


def random_terminal(rng):
    return ExprNode("terminal", value=rng.choice(GENETIC_OPERATORS["terminal"]))


def random_window(rng):
    return ExprNode("window", value=str(rng.choice(GENETIC_OPERATORS["window"])))


def random_expr(rng, max_depth=4, depth=0):
    if depth >= max_depth:
        return random_terminal(rng)
    choice = rng.random()
    if choice < 0.3:
        return random_terminal(rng)
    elif choice < 0.5:
        op = rng.choice(GENETIC_OPERATORS["unary"])
        return ExprNode(op, [random_expr(rng, max_depth, depth + 1)])
    elif choice < 0.7:
        op = rng.choice(GENETIC_OPERATORS["rolling"])
        child = random_expr(rng, max_depth, depth + 1)
        win = random_window(rng)
        return ExprNode(op, [child, win])
    else:
        op = rng.choice(GENETIC_OPERATORS["binary"])
        left = random_expr(rng, max_depth, depth + 1)
        right = random_expr(rng, max_depth, depth + 1)
        return ExprNode(op, [left, right])


def mutate(node, rng, max_depth=4):
    node = node.clone()
    nodes = []
    def _collect(n):
        nodes.append(n)
        for c in n.children:
            _collect(c)
    _collect(node)
    target = rng.choice(nodes)
    new_subtree = random_expr(rng, max_depth)

    def _replace(n, target):
        for i, c in enumerate(n.children):
            if c is target:
                n.children[i] = new_subtree
                return True
            if _replace(c, target):
                return True
        return False

    if target is node:
        return new_subtree
    _replace(node, target)
    return node


def crossover(a, b, rng):
    a, b = a.clone(), b.clone()
    def _collect(n):
        nodes = [n]
        for c in n.children:
            nodes.extend(_collect(c))
        return nodes
    a_nodes, b_nodes = _collect(a), _collect(b)
    if len(a_nodes) < 2 or len(b_nodes) < 2:
        return a, b
    a_target = rng.choice(a_nodes[1:])
    b_target = rng.choice(b_nodes[1:])
    def _replace(n, target, new):
        for i, c in enumerate(n.children):
            if c is target:
                n.children[i] = new
                return True
            if _replace(c, target, new):
                return True
        return False
    _replace(a, a_target, b_target.clone())
    return a, b


class GeneticFactorMiner:
    def __init__(self, pool, seed_factors=None):
        self.pool = pool
        self.cfg = GENETIC
        self.rng = random.Random(self.cfg["random_state"])
        self.seed_factors = seed_factors or []
        self.history = []

    def _init_population(self):
        pop = []
        if self.cfg["use_cluster_seeds"] and self.seed_factors:
            for f in self.seed_factors[:self.cfg["population_size"] // 3]:
                expr_str = f.get("expr", "")
                if expr_str:
                    pop.append(ExprNode("raw", value=expr_str))
        while len(pop) < self.cfg["population_size"]:
            pop.append(random_expr(self.rng, self.cfg["max_expr_depth"]))
        return pop

    def _evaluate_population(self, pop):
        results = []
        for node in pop:
            expr_str = node.value if node.op == "raw" else node.to_string()
            ics, impl = [], {}
            for code, df in self.pool.items():
                try:
                    f = safe_eval(expr_str, df)
                    fr = df["close"].pct_change().shift(-1)
                    ic = compute_ic(f, fr)
                    if not np.isnan(ic):
                        ics.append(ic)
                        impl[code] = {"factor": f, "ic": ic}
                except Exception:
                    continue
            if not ics:
                results.append((node, 0.0, 0.0, {}))
                continue
            mean_ic = float(np.mean(ics))
            std_ic = float(np.std(ics)) if len(ics) > 1 else 1.0
            icir = mean_ic / (std_ic + 1e-9)
            results.append((node, mean_ic, icir, impl))
        return results

    def _tournament_select(self, evaluated):
        k = self.cfg["tournament_size"]
        candidates = self.rng.sample(evaluated, min(k, len(evaluated)))
        return max(candidates, key=lambda x: abs(x[1]))[0]

    def run(self):
        print("\n========== 因子遗传编程 ==========")
        if not self.cfg["enabled"]:
            return []
        pop = self._init_population()
        best_overall = None
        best_ic = 0
        for gen in range(self.cfg["n_generations"]):
            evaluated = self._evaluate_population(pop)
            evaluated.sort(key=lambda x: abs(x[1]), reverse=True)
            gen_best = evaluated[0]
            self.history.append({"generation": gen + 1,
                                 "best_ic": gen_best[1]})
            print(f"  第 {gen + 1} 代: 最佳 |IC|={abs(gen_best[1]):.4f}")
            if abs(gen_best[1]) > abs(best_ic):
                best_ic = gen_best[1]
                best_overall = gen_best

            n_elite = max(1, int(self.cfg["population_size"]
                                 * self.cfg["elite_ratio"]))
            elites = [e[0] for e in evaluated[:n_elite]]
            next_pop = list(elites)
            while len(next_pop) < self.cfg["population_size"]:
                parent_a = self._tournament_select(evaluated)
                parent_b = self._tournament_select(evaluated)
                if self.rng.random() < self.cfg["crossover_rate"]:
                    child_a, child_b = crossover(parent_a, parent_b, self.rng)
                else:
                    child_a, child_b = parent_a.clone(), parent_b.clone()
                if self.rng.random() < self.cfg["mutation_rate"]:
                    child_a = mutate(child_a, self.rng,
                                     self.cfg["max_expr_depth"])
                if self.rng.random() < self.cfg["mutation_rate"]:
                    child_b = mutate(child_b, self.rng,
                                     self.cfg["max_expr_depth"])
                next_pop.append(child_a)
                if len(next_pop) < self.cfg["population_size"]:
                    next_pop.append(child_b)
            pop = next_pop[:self.cfg["population_size"]]

        final_eval = self._evaluate_population(pop)
        final_eval.sort(key=lambda x: abs(x[1]), reverse=True)
        result, seen_exprs = [], set()
        for node, ic, icir, impl in final_eval:
            if abs(ic) < self.cfg["min_ic_to_survive"]:
                continue
            expr_str = node.value if node.op == "raw" else node.to_string()
            if expr_str in seen_exprs:
                continue
            seen_exprs.add(expr_str)
            result.append({"name": f"gp_{len(result)}", "expr": expr_str,
                           "mean_ic": ic, "icir": icir,
                           "impl": impl, "source": "genetic"})
            if len(result) >= self.cfg["keep_top_n"]:
                break
        print(f"  遗传编程产出: {len(result)} 个新因子")
        return result

    def get_history_df(self):
        if not self.history:
            return pd.DataFrame()
        return pd.DataFrame(self.history)


def genetic_mine(pool, seed_factors=None):
    miner = GeneticFactorMiner(pool, seed_factors)
    factors = miner.run()
    hist = miner.get_history_df()
    if not hist.empty:
        hist.to_csv("genetic_history.csv", index=False,
                    encoding="utf-8-sig")
    return factors