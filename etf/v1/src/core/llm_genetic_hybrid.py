# -*- coding: utf-8 -*-
"""LLM + 遗传混合"""

import os
import json
import random
import numpy as np
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential
from config import (
    LLM_GENETIC_HYBRID as CFG, LLM_MODEL, LLM_API_KEY_ENV,
    GENETIC_MULTI_OBJECTIVE,
)
from factor_genetic import ExprNode, random_expr, mutate, crossover
from genetic_multi_objective import (
    evaluate_multi_objective, fast_non_dominated_sort,
    crowding_distance,
)


SEED_SYSTEM_PROMPT = """你是量化因子研究员。生成一批因子表达式作为遗传编程的"种子"。

可用算子：
- 数据列: close, open, high, low, volume, returns
- 滚动: ma(df, n), std(df, n), max(df, n), min(df, n)
- 序列: delay(s, n), delta(s, n), ts_sum(s, n), ts_mean(s, n), ts_std(s, n)
- 数学: abs(s), log(s), sign(s)

要求：
1. 每个表达式一行 Python，用 df 作输入
2. 有清晰的金融逻辑
3. 生成 {n} 个种子

输出 JSON:
{"seeds": [{"name": "xxx", "expr": "表达式", "logic": "逻辑"}]}
"""


FEEDBACK_SYSTEM_PROMPT = """你是量化因子研究员。遗传编程已进化了若干代，以下是当前帕累托前沿：
{front}

请基于这些结果，生成 {n} 个**互补**的新种子。

输出 JSON:
{"seeds": [{"name": "xxx", "expr": "表达式", "logic": "逻辑"}]}
"""


class LLMSeedGenerator:
    def __init__(self):
        self.api_key = os.environ.get(LLM_API_KEY_ENV, "")
        self.enabled = bool(self.api_key)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def _chat(self, messages):
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages, temperature=0.8,
            response_format={"type": "json_object"})
        return resp.choices[0].message.content

    def _fallback(self, n):
        tpl = [
            {"name": "seed_mom5",
             "expr": "delta(close, 5) / (ma(df, 20) + 1e-9)"},
            {"name": "seed_rev3",
             "expr": "(-delta(close, 3)) / (ma(df, 10) + 1e-9)"},
            {"name": "seed_vol", "expr": "(-ts_std(returns, 20))"},
            {"name": "seed_volratio",
             "expr": "volume / (ts_mean(volume, 20) + 1e-9)"},
            {"name": "seed_ma",
             "expr": "ma(df, 5) / (ma(df, 20) + 1e-9) - 1"},
            {"name": "seed_pos",
             "expr": "(close - min(df, 20)) / (max(df, 20) - min(df, 20) + 1e-9)"},
            {"name": "seed_rsi",
             "expr": "ts_sum(returns.clip(lower=0), 14) / (ts_sum(returns.abs(), 14) + 1e-9)"},
            {"name": "seed_momvol",
             "expr": "delta(close, 5) / (ts_std(returns, 20) + 1e-9)"},
        ]
        return tpl[:n]

    def generate_seeds(self, n):
        if not self.enabled:
            return self._fallback(n)
        try:
            content = self._chat([
                {"role": "system",
                 "content": SEED_SYSTEM_PROMPT.format(n=n)},
                {"role": "user", "content": f"生成 {n} 个种子因子。"}])
            return json.loads(content).get("seeds", [])
        except Exception as e:
            print(f"    ⚠️ LLM 种子生成失败: {e}")
            return self._fallback(n)

    def generate_complementary(self, front, n):
        if not self.enabled:
            return []
        try:
            front_str = json.dumps(front[:8], ensure_ascii=False,
                                    indent=2)
            content = self._chat([
                {"role": "system",
                 "content": FEEDBACK_SYSTEM_PROMPT.format(
                     front=front_str, n=n)},
                {"role": "user", "content": "生成互补种子。"}])
            return json.loads(content).get("seeds", [])
        except Exception:
            return []


def expr_to_node(expr_str):
    return ExprNode("raw", value=expr_str)


class LLMGeneticHybrid:
    def __init__(self, pool, existing_factors=None):
        self.pool = pool
        self.cfg = CFG
        self.rng = random.Random(42)
        self.existing_factors = existing_factors or []
        self.ref_code = next(iter(pool))
        self.llm = LLMSeedGenerator()
        self.history = []
        self.llm_seeds_history = []

    def _init_population(self, llm_seeds):
        n_llm = int(self.cfg["population_size"] * self.cfg["llm_seed_ratio"])
        pop = []
        for s in llm_seeds[:n_llm]:
            pop.append(expr_to_node(s["expr"]))
        while len(pop) < self.cfg["population_size"]:
            pop.append(random_expr(
                self.rng, GENETIC_MULTI_OBJECTIVE["max_expr_depth"]))
        return pop

    def _evaluate(self, pop):
        results = []
        for node in pop:
            expr_str = node.value if node.op == "raw" \
                else node.to_string()
            m = evaluate_multi_objective(
                expr_str, self.pool, self.existing_factors,
                self.ref_code)
            m["expr"] = expr_str
            m["node"] = node
            results.append(m)
        return results

    def _evolve_one_generation(self, pop):
        metrics = self._evaluate(pop)
        fronts = fast_non_dominated_sort(metrics)
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
            if a.op == "raw":
                a = random_expr(self.rng,
                                GENETIC_MULTI_OBJECTIVE["max_expr_depth"])
            if b.op == "raw":
                b = random_expr(self.rng,
                                GENETIC_MULTI_OBJECTIVE["max_expr_depth"])
            if self.rng.random() < self.cfg["crossover_rate"]:
                c1, c2 = crossover(a, b, self.rng)
            else:
                c1, c2 = a.clone(), b.clone()
            if self.rng.random() < GENETIC_MULTI_OBJECTIVE["mutation_rate"]:
                c1 = mutate(c1, self.rng,
                            GENETIC_MULTI_OBJECTIVE["max_expr_depth"])
            next_pop.append(c1)
            if len(next_pop) < self.cfg["population_size"]:
                next_pop.append(c2)
        return next_pop[:self.cfg["population_size"]]

    def run(self):
        print("\n========== LLM + 遗传混合 ==========")
        if not self.cfg["enabled"]:
            return []
        print("  阶段 1: LLM 生成种子...")
        llm_seeds = self.llm.generate_seeds(self.cfg["llm_n_seeds"])
        self.llm_seeds_history.append(llm_seeds)
        print(f"    LLM 种子: {len(llm_seeds)}")
        pop = self._init_population(llm_seeds)
        for gen in range(self.cfg["n_generations"]):
            pop = self._evolve_one_generation(pop)
            metrics = self._evaluate(pop)
            best_ic = max(m["abs_ic"] for m in metrics)
            print(f"    代 {gen + 1}: 最佳|IC|={best_ic:.4f}")
            self.history.append({"generation": gen + 1,
                                 "best_ic": best_ic})

        if self.cfg["feedback_to_llm"]:
            for round_i in range(self.cfg["feedback_rounds"]):
                print(f"  阶段 4.{round_i + 1}: LLM 反馈...")
                metrics = self._evaluate(pop)
                fronts = fast_non_dominated_sort(metrics)
                front = []
                for i in (fronts[0] if fronts else [])[:8]:
                    front.append({"expr": metrics[i]["expr"],
                                  "ic": metrics[i]["ic"]})
                new_seeds = self.llm.generate_complementary(
                    front, self.cfg["llm_n_seeds"] // 2)
                if not new_seeds:
                    break
                self.llm_seeds_history.append(new_seeds)
                for s in new_seeds:
                    pop.append(expr_to_node(s["expr"]))
                pop = pop[-self.cfg["population_size"]:]
                for _ in range(2):
                    pop = self._evolve_one_generation(pop)

        final = self._evaluate(pop)
        fronts = fast_non_dominated_sort(final)
        pareto = fronts[0] if fronts else []
        pareto = sorted(pareto, key=lambda i: -final[i]["abs_ic"])
        pareto = pareto[:GENETIC_MULTI_OBJECTIVE["pareto_front_size"]]

        result, seen = [], set()
        for i in pareto:
            m = final[i]
            if m["expr"] in seen or m["abs_ic"] < 0.003:
                continue
            seen.add(m["expr"])
            result.append({"name": f"hybrid_{len(result)}",
                           "expr": m["expr"], "mean_ic": m["ic"],
                           "icir": 0.0, "turnover": m["turnover"],
                           "max_corr": m["max_corr"],
                           "impl": m["impl"], "source": "llm_genetic"})
        print(f"  混合产出: {len(result)} 个因子")
        return result

    def get_history_df(self):
        if not self.history:
            return pd.DataFrame()
        return pd.DataFrame(self.history)

    def get_seeds_df(self):
        rows = []
        for rnd, seeds in enumerate(self.llm_seeds_history):
            for s in seeds:
                rows.append({"round": rnd, "name": s.get("name", ""),
                             "expr": s.get("expr", ""),
                             "logic": s.get("logic", "")})
        return pd.DataFrame(rows)


def llm_genetic_mine(pool, existing_factors=None):
    hybrid = LLMGeneticHybrid(pool, existing_factors)
    factors = hybrid.run()
    hist = hybrid.get_history_df()
    if not hist.empty:
        hist.to_csv("hybrid_history.csv", index=False,
                    encoding="utf-8-sig")
    seeds = hybrid.get_seeds_df()
    if not seeds.empty:
        seeds.to_csv("hybrid_llm_seeds.csv", index=False,
                     encoding="utf-8-sig")
    return factors