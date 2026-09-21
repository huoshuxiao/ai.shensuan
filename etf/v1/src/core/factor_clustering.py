# -*- coding: utf-8 -*-
"""因子聚类

挖掘出的大量因子按取值相似性聚成簇，每簇只保留 |IC| 最高的代表，
在正交化之前先做一轮"家族去重"，减轻共线性并给 GP 提供种子。"""

import numpy as np
import pandas as pd
from config import FACTOR_CLUSTERING, RESULTS_DIR
from factor_dsl import spearman_corr_matrix


def compute_similarity_matrix(factors, ref_code):
    """因子两两 Spearman 相关矩阵（用参考标的的时间序列作代理，
    比截面拼接快且稳定）"""
    data = {}
    for f in factors:
        if ref_code in f.get("impl", {}):
            data[f["name"]] = f["impl"][ref_code]["factor"]
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data).dropna(how="all")
    return spearman_corr_matrix(df)


def _cluster_hierarchical(sim, threshold=0.6):
    """层次聚类（平均链接）：距离 d = 1 - |相关系数|，
    在距离轴切 t = 1 - threshold 高度 => |corr| > threshold 的归同簇。"""
    from scipy.cluster.hierarchy import linkage, fcluster
    from scipy.spatial.distance import squareform
    dist = 1 - sim.abs().values
    np.fill_diagonal(dist, 0)
    dist = (dist + dist.T) / 2
    condensed = squareform(dist, checks=False)
    Z = linkage(condensed, method="average")
    labels = fcluster(Z, t=1 - threshold, criterion="distance")
    return {sim.columns[i]: int(labels[i]) for i in range(len(labels))}


def _cluster_kmeans(sim, n_clusters=8):
    """KMeans 直接对相关矩阵的行向量聚类（以相关模式为特征）。"""
    from sklearn.cluster import KMeans
    n = min(n_clusters, len(sim.columns))
    model = KMeans(n_clusters=n, random_state=42, n_init=10)
    labels = model.fit_predict(sim.values)
    return {sim.columns[i]: int(labels[i]) for i in range(len(labels))}


def _pick_representatives(clusters, factors, mode="highest_ic"):
    """每簇选代表：|mean_ic| 最大者（mode 目前仅实现 highest_ic）。"""
    factor_ic = {f["name"]: abs(f.get("mean_ic", 0)) for f in factors}
    groups = {}
    for name, cid in clusters.items():
        groups.setdefault(cid, []).append(name)
    reps = {}
    for cid, members in groups.items():
        reps[cid] = max(members, key=lambda n: factor_ic.get(n, 0))
    return reps


def _reduce_dim(sim, method="tsne", perplexity=5):
    """相关矩阵 -> 2D 可视化坐标。TSNE 用相似度距离嵌入
    （样本极少时 perplexity 需收缩到 n-1 以内）。"""
    X = sim.values
    n = X.shape[0]
    if n < 3:
        return np.zeros((n, 2))
    if method == "pca":
        from sklearn.decomposition import PCA
        return PCA(n_components=2).fit_transform(X)
    from sklearn.manifold import TSNE
    p = min(perplexity, max(2, n - 1))
    return TSNE(n_components=2, perplexity=p, random_state=42,
                init="random").fit_transform(X)


def cluster_factors(factors, pool):
    """入口：相似矩阵 -> 聚类(method) -> 选代表 -> 降维坐标。
    返回 {clusters, representatives, similarity, coords(DataFrame),
    n_clusters}；因子 <3 个或未启用时返回 {}。"""
    cfg = FACTOR_CLUSTERING
    print("\n========== 因子聚类 ==========")
    if not cfg["enabled"] or len(factors) < 3:
        return {}
    ref_code = next(iter(pool))
    sim = compute_similarity_matrix(factors, ref_code)
    if sim.empty:
        return {}

    method = cfg["method"]
    if method == "hierarchical":
        clusters = _cluster_hierarchical(sim, cfg["corr_threshold"])
    elif method == "kmeans":
        clusters = _cluster_kmeans(sim, cfg["n_clusters"])
    else:
        clusters = {n: 0 for n in sim.columns}

    n_clusters = len(set(clusters.values()))
    print(f"  method={method}  簇数={n_clusters}")
    reps = _pick_representatives(clusters, factors,
                                 cfg["representative_mode"])
    coords = _reduce_dim(sim, cfg["visualize_method"],
                         cfg["tsne_perplexity"])
    factor_ic = {f["name"]: f.get("mean_ic", 0) for f in factors}
    coords_df = pd.DataFrame({
        "name": sim.columns, "x": coords[:, 0], "y": coords[:, 1],
        "cluster": [clusters[n] for n in sim.columns],
        "ic": [factor_ic.get(n, 0) for n in sim.columns],
        "is_rep": [n in reps.values() for n in sim.columns]})

    return {"clusters": clusters, "representatives": reps,
            "similarity": sim, "coords": coords_df,
            "n_clusters": n_clusters}


def dedup_by_cluster(factors, clustering):
    if not clustering:
        return factors
    reps = set(clustering["representatives"].values())
    kept = [f for f in factors if f["name"] in reps]
    print(f"  聚类去重: {len(factors)} → {len(kept)}")
    return kept


def save_clustering(clustering, out_dir=RESULTS_DIR):
    if not clustering:
        return
    clustering["coords"].to_csv(f"{out_dir}/factor_clusters.csv",
                                index=False, encoding="utf-8-sig")
    clustering["similarity"].to_csv(
        f"{out_dir}/factor_similarity.csv", encoding="utf-8-sig")
    rows = [{"factor": n, "cluster": c,
             "is_rep": n in clustering["representatives"].values()}
            for n, c in clustering["clusters"].items()]
    pd.DataFrame(rows).to_csv(
        f"{out_dir}/factor_cluster_members.csv",
        index=False, encoding="utf-8-sig")