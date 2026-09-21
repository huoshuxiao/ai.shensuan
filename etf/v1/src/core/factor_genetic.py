# -*- coding: utf-8 -*-
"""因子遗传编程（单目标：|IC|）

把因子表达式表示成语法树（终端=OHLCV/returns，非终端=一元/二元/
滚动算子），用"评估 IC -> 锦标赛选择 -> 交叉/变异"的进化循环自动
搜索表达式空间。可选接入 LLM 指导的变异/交叉算子。"""

import random
from copy import deepcopy
import numpy as np
import pandas as pd
from config import (
    GENETIC, GENETIC_OPERATORS, LLM_MUTATION, LLM_CROSSOVER,
    RESULTS_DIR,
)
from factor_dsl import safe_eval, compute_ic


class ExprNode:
    """表达式语法树节点。op 类型：
    terminal(裸列名 close/returns...) / window(窗口常数字符串) /
    raw(整段现成表达式种子，直接透传求值) / 具体算子名。"""

    def __init__(self, op, children=None, value=None):
        self.op = op
        self.children = children or []
        self.value = value

    def to_string(self):
        """展开为 DSL 表达式字符串，如
        'ts_mean(returns, 20) / ts_std(returns, 20)'、二元 -> '(a / b)'、
        neg -> '(-a)'。输出必须能被 factor_dsl.safe_eval 回读。"""
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
    """递归随机生成子树（grow 方法）：
    30% 终端 / 20% 一元算子 / 20% 滚动算子(带窗口常数) /
    30% 二元算子（左右各递归一次）；到 max_depth 强制收敛到终端。"""
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
    """子树替换变异：随机挑一个节点，用全新随机子树整枝换掉。"""
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
    """子树交叉：从两棵树各随机取一个非根节点，把 B 的子树
    嫁接到 A 的随机点上（A 变异为新个体，B 原样返回作第二亲本）。
    跳过根节点可保证后代至少保留部分自身结构。"""
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
        self.mut_op = None
        self.cross_op = None
        if LLM_MUTATION.get("enabled"):
            from llm_mutation_operator import LLMMutationOperator
            self.mut_op = LLMMutationOperator()
        if LLM_CROSSOVER.get("enabled"):
            from llm_crossover_operator import LLMCrossoverOperator
            self.cross_op = LLMCrossoverOperator()

    def _init_population(self):
        """初始种群：若开启种子模式，取前 1/3 种群规模用聚类种子
        表达式（raw 节点透传），其余随机生成。"""
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
        """适应度评估：表达式在每只 ETF 上独立求 IC（Spearman，
        与下一期收益），跨标的取均值 mean_ic 为适应度、
        mean_ic/std(IC) 为 ICIR。返回 (节点, mean_ic, icir, impl)。
        全池求值都失败时打印一次首异常样例（否则静默 IC=0 难排查）。"""
        results = []
        first_err = None
        for node in pop:
            expr_str = node.value if node.op == "raw" else node.to_string()
            ics, impl = [], {}
            for code, df in self.pool.items():
                try:
                    f = safe_eval(expr_str, df)
                    fr = df["close"].pct_change().shift(-1)  # 下一期收益
                    ic = compute_ic(f, fr)
                    if not np.isnan(ic):
                        ics.append(ic)
                        impl[code] = {"factor": f, "ic": ic}
                except Exception as e:
                    if first_err is None:
                        first_err = (expr_str, e)
                    continue
            if not ics:
                results.append((node, 0.0, 0.0, {}))
                continue
            mean_ic = float(np.mean(ics))
            std_ic = float(np.std(ics)) if len(ics) > 1 else 1.0
            icir = mean_ic / (std_ic + 1e-9)
            results.append((node, mean_ic, icir, impl))
        if all(r[1] == 0.0 for r in results) and first_err:
            if not getattr(self, "_err_reported", False):
                self._err_reported = True
                print(f"  ⚠️ 表达式求值全部失败，样例 {first_err[0]!r}: "
                      f"{type(first_err[1]).__name__}: {first_err[1]}")
        return results

    def _tournament_select(self, evaluated):
        """锦标赛选择：随机抽 k 个候选，返回 |IC| 最大者的节点"""
        k = self.cfg["tournament_size"]
        candidates = self.rng.sample(evaluated, min(k, len(evaluated)))
        return max(candidates, key=lambda x: abs(x[1]))[0]

    def _tournament_select_tuple(self, evaluated):
        """同 _tournament_select，但返回完整 (节点, ic, icir, impl) 元组，
        供 LLM 算子读取亲本指标"""
        k = self.cfg["tournament_size"]
        candidates = self.rng.sample(evaluated, min(k, len(evaluated)))
        return max(candidates, key=lambda x: abs(x[1]))

    def _maybe_llm_mutate(self, node, parent_metrics):
        if self.mut_op is not None:
            from llm_mutation_operator import smart_mutate
            return smart_mutate(node, parent_metrics, self.rng,
                                self.cfg["max_expr_depth"], self.mut_op)
        return mutate(node, self.rng, self.cfg["max_expr_depth"])

    def run(self):
        """进化主循环（世代数 = n_generations）：
        评估 -> 按 |IC| 排序 -> 保留 elite_ratio 精英 ->
        锦标赛选亲本 -> 以 crossover_rate 交叉、mutation_rate 变异补齐
        种群。终局再评估一次，按 |IC| >= min_ic_to_survive 与表达式
        去重筛选，最多产出 keep_top_n 个因子（命名 gp_i）。"""
        print("\n========== 因子遗传编程 ==========")
        if not self.cfg["enabled"]:
            return []
        pop = self._init_population()
        best_overall = None
        best_ic = 0
        stagnation = self.cfg.get("stagnation_generations", 0)
        stall = 0  # 最佳 |IC| 连续无提升的代数
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
                stall = 0
            else:
                stall += 1
                if stagnation and stall >= stagnation \
                        and gen + 1 < self.cfg["n_generations"]:
                    print(f"  ℹ️ 最佳 |IC| 已连续 {stall} 代无提升，"
                          f"早停于第 {gen + 1} 代"
                          f"（计划 {self.cfg['n_generations']} 代）")
                    break

            n_elite = max(1, int(self.cfg["population_size"]
                                 * self.cfg["elite_ratio"]))
            elites = [e[0] for e in evaluated[:n_elite]]
            next_pop = list(elites)
            if self.mut_op is not None:
                self.mut_op.reset_generation()
            if self.cross_op is not None:
                self.cross_op.reset_generation()
            while len(next_pop) < self.cfg["population_size"]:
                ta = self._tournament_select_tuple(evaluated)
                tb = self._tournament_select_tuple(evaluated)
                parent_a, parent_b = ta[0], tb[0]
                m_a = {"mean_ic": ta[1], "icir": ta[2]}
                m_b = {"mean_ic": tb[1], "icir": tb[2]}
                if self.rng.random() < self.cfg["crossover_rate"]:
                    if self.cross_op is not None:
                        from llm_crossover_operator import smart_crossover
                        child_a, child_b = smart_crossover(
                            parent_a, parent_b, m_a, m_b,
                            self.rng, self.cross_op)
                    else:
                        child_a, child_b = crossover(parent_a, parent_b,
                                                     self.rng)
                else:
                    child_a, child_b = parent_a.clone(), parent_b.clone()
                if self.rng.random() < self.cfg["mutation_rate"]:
                    child_a = self._maybe_llm_mutate(child_a, m_a)
                if self.rng.random() < self.cfg["mutation_rate"]:
                    child_b = self._maybe_llm_mutate(child_b, m_b)
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


def genetic_mine(pool, seed_factors=None, history_path=None):
    """history_path：折内/重挖多次调用时传各自路径，
    否则都会覆写同一份 genetic_history.csv。"""
    miner = GeneticFactorMiner(pool, seed_factors)
    factors = miner.run()
    hist = miner.get_history_df()
    if not hist.empty:
        hist.to_csv(history_path or f"{RESULTS_DIR}/genetic_history.csv",
                    index=False, encoding="utf-8-sig")
    return factors