# -*- coding: utf-8 -*-
"""多因子源并行"""

import concurrent.futures as cf
import numpy as np
import pandas as pd
from config import MULTI_SOURCE, RDAGENT_USE_OFFICIAL_FALLBACK
from factor_dsl import safe_eval, compute_ic, safe_spearman
from factor_library import get_library


def _run_official(pool):
    if not RDAGENT_USE_OFFICIAL_FALLBACK:
        # 显式关闭时直接跳过；开启时由 official_rdagent 前置体检
        # 决定是否运行（依赖缺失只记 ℹ️，不再以异常降级）
        print("    ℹ️ official 源已停用（RDAGENT_USE_OFFICIAL_FALLBACK=False）")
        return []
    try:
        from official_rdagent import try_official_rdagent
        factors = try_official_rdagent()
        if factors:
            return _attach_impl(factors, pool, "official")
    except Exception as e:
        print(f"    ⚠️ official 源失败: {e}")
    return []


def _run_llm(pool):
    try:
        from llm_factor_agent import LLMFactorAgent
        agent = LLMFactorAgent(pool)
        if not agent.llm.enabled:
            return []
        factors = agent.run()
        for f in factors:
            f["source"] = "llm"
        return factors
    except Exception as e:
        print(f"    ⚠️ llm 源失败: {e}")
    return []


def _run_simple(pool):
    try:
        from llm_factor_agent import LLMFactorAgent
        agent = LLMFactorAgent(pool)
        agent.llm.enabled = False
        factors = agent.run()
        for f in factors:
            f["source"] = "simple"
        return factors
    except Exception as e:
        print(f"    ⚠️ simple 源失败: {e}")
    return []


def _run_genetic(pool):
    try:
        from factor_genetic import genetic_mine
        from factor_library import get_library
        lib = get_library()
        active = lib.get_active()
        seeds = [lib.factors[n] for n in active
                 if n in lib.factors][:10]
        return genetic_mine(pool, seeds)
    except Exception as e:
        print(f"    ⚠️ genetic 源失败: {e}")
    return []


def _attach_impl(factors, pool, source):
    result = []
    for raw in factors:
        expr = raw.get("expr", "")
        if not expr:
            continue
        impl, ics = {}, []
        for code, df in pool.items():
            try:
                f = safe_eval(expr, df)
                fr = df["close"].pct_change().shift(-1)
                ic = compute_ic(f, fr)
                if not np.isnan(ic):
                    impl[code] = {"factor": f, "ic": ic}
                    ics.append(ic)
            except Exception:
                continue
        if impl:
            mean_ic = float(np.mean(ics))
            std_ic = float(np.std(ics)) if len(ics) > 1 else 1.0
            item = {"name": raw.get("name", f"{source}_{len(result)}"),
                    "expr": expr, "mean_ic": mean_ic,
                    "icir": mean_ic / (std_ic + 1e-9),
                    "impl": impl, "source": source}
            # 官方源的 LaTeX 原式随因子一起留存：报告里可核对
            # 「翻译后的 DSL 表达式」与「RD-Agent 给出的定义」是否一致
            formulation = raw.get("formulation")
            if formulation:
                item["formulation"] = formulation
            result.append(item)
    return result


def _factor_correlation(f1, f2, ref_code):
    if ref_code not in f1.get("impl", {}) or ref_code not in f2.get("impl", {}):
        return 0.0
    a = f1["impl"][ref_code]["factor"]
    b = f2["impl"][ref_code]["factor"]
    df = pd.concat([a, b], axis=1).dropna()
    if len(df) < 30:
        return 0.0
    return abs(safe_spearman(df.iloc[:, 0], df.iloc[:, 1]))


def dedup_factors(factors, pool, threshold=None):
    threshold = threshold or MULTI_SOURCE["corr_dedup_threshold"]
    ref_code = next(iter(pool))
    sorted_f = sorted(factors, key=lambda x: abs(x.get("mean_ic", 0)),
                      reverse=True)
    kept, seen = [], set()
    for f in sorted_f:
        if f["name"] in seen:
            continue
        is_dup = False
        for k in kept:
            if _factor_correlation(f, k, ref_code) > threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(f)
            seen.add(f["name"])
    return kept


def merge_factors(all_factors, weights=None):
    weights = weights or MULTI_SOURCE["weights"]
    grouped = {}
    for f in all_factors:
        grouped.setdefault(f["name"], []).append(f)

    merged = []
    for name, group in grouped.items():
        if len(group) == 1:
            f = group[0]
            w = weights.get(f.get("source", ""), 1.0)
            f["weighted_ic"] = f.get("mean_ic", 0) * w
            merged.append(f)
        else:
            total_w, weighted_ic = 0, 0
            for f in group:
                w = weights.get(f.get("source", ""), 1.0)
                weighted_ic += f.get("mean_ic", 0) * w
                total_w += w
            best = max(group, key=lambda x: abs(x.get("mean_ic", 0)))
            best["mean_ic"] = weighted_ic / total_w if total_w > 0 else 0
            best["sources"] = [f.get("source") for f in group]
            best["weighted_ic"] = best["mean_ic"]
            merged.append(best)
    return merged


def multi_source_mine(pool):
    print("\n========== 多因子源并行 ==========")
    sources = MULTI_SOURCE["sources"]
    timeout = MULTI_SOURCE["timeout_seconds"]

    runners = {"official": _run_official, "llm": _run_llm,
               "simple": _run_simple, "genetic": _run_genetic}

    all_factors, source_stats = [], {}
    with cf.ThreadPoolExecutor(max_workers=len(sources)) as executor:
        futures = {}
        for src in sources:
            if src in runners:
                futures[executor.submit(runners[src], pool)] = src
        for future in cf.as_completed(futures, timeout=timeout):
            src = futures[future]
            try:
                factors = future.result(timeout=timeout)
                source_stats[src] = len(factors)
                print(f"  ✅ {src:10s} 产出 {len(factors)} 个因子")
                all_factors.extend(factors)
            except Exception as e:
                print(f"  ❌ {src} 失败: {e}")
                source_stats[src] = 0

    if not all_factors:
        return []

    min_ic = MULTI_SOURCE["min_ic_per_source"]
    all_factors = [f for f in all_factors
                   if abs(f.get("mean_ic", 0)) >= min_ic]

    merged = merge_factors(all_factors)
    if MULTI_SOURCE["merge_mode"] == "union_dedup":
        merged = dedup_factors(merged, pool)
        try:
            from factor_clustering import cluster_factors, dedup_by_cluster
            clustering = cluster_factors(merged, pool)
            if clustering:
                merged = dedup_by_cluster(merged, clustering)
                from factor_clustering import save_clustering
                save_clustering(clustering)
        except Exception as e:
            print(f"  ⚠️ 聚类去重失败: {e}")

    lib = get_library()
    for src in sources:
        src_factors = [f for f in merged if f.get("source") == src]
        if src_factors:
            lib.batch_upsert(src_factors, source=src)
    lib.save_markdown()

    return merged