# -*- coding: utf-8 -*-
"""因子聚类"""

import numpy as np
import pandas as pd
from config import FACTOR_CLUSTERING


def compute_similarity_matrix(factors, ref_code):
    data = {}
    for f in factors:
        if ref_code in f.get("impl", {}):
            data[f["name"]] = f["impl"][ref_code]["factor"]
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data).dropna(how="all")
    return df.corr(method="spearman")


def _cluster_hierarchical(sim, threshold=0.6):
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
    from sklearn.cluster import KMeans
    n = min(n_clusters, len(sim.columns))
    model = KMeans(n_clusters=n, random_state=42, n_init=10)
    labels = model.fit_predict(sim.values)
    return {sim.columns[i]: int(labels[i]) for i in range(len(labels))}


def _pick_representatives(clusters, factors, mode="highest_ic"):
    factor_ic = {f["name"]: abs(f.get("mean_ic", 0)) for f in factors}
    groups = {}
    for name, cid in clusters.items():
        groups.setdefault(cid, []).append(name)
    reps = {}
    for cid, members in groups.items():
        reps[cid] = max(members, key=lambda n: factor_ic.get(n, 0))
    return reps


def _reduce_dim(sim, method="tsne", perplexity=5):
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


def save_clustering(clustering, out_dir="."):
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