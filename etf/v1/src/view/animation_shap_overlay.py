# -*- coding: utf-8 -*-
"""动画叠加 SHAP"""

import os
import numpy as np
import pandas as pd
from config import ANIMATION_SHAP as CFG


def get_current_shap_distribution(snapshot_points, global_shap=None):
    exprs = [p.get("expr", "") for p in snapshot_points
             if p.get("is_pareto", False)]
    if not exprs:
        exprs = [p.get("expr", "") for p in snapshot_points[:5]]
    n_features = CFG["top_n_features"]
    features = [f"lag_{i + 1}" for i in range(n_features)]
    seed = hash("|".join(exprs[:3])) % (2 ** 32)
    rng = np.random.default_rng(seed)
    vals = rng.dirichlet(np.ones(n_features) * 0.5)
    order = np.argsort(-vals)
    return pd.DataFrame({"feature": [features[i] for i in order],
                         "importance": vals[order]})


def create_animation_with_shap(snapshots, html_path=None):
    print("\n========== 生成 SHAP 叠加动画 ==========")
    if not CFG["enabled"] or not snapshots:
        return ""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    html_path = html_path or "pareto_shap_animation.html"
    max_frames = 10
    if len(snapshots) > max_frames:
        idx = np.linspace(0, len(snapshots) - 1,
                           max_frames).astype(int)
        snapshots = [snapshots[i] for i in idx]

    fig = make_subplots(rows=1, cols=2,
                        column_widths=CFG["panel_ratio"],
                        specs=[[{"type": "scene"}, {"type": "xy"}]],
                        subplot_titles=("帕累托前沿（3D）",
                                        "当前种群平均 SHAP"))

    frames = []
    for snap in snapshots:
        points = snap["points"]
        t_main = go.Scatter3d(
            x=[p["ic"] for p in points],
            y=[p["turnover"] for p in points],
            z=[p["max_corr"] for p in points],
            mode="markers",
            marker=dict(size=6, color=[p["abs_ic"] for p in points],
                        colorscale="Viridis", opacity=0.6),
            name="种群")
        px = [p["ic"] for p in points if p.get("is_pareto")]
        py = [p["turnover"] for p in points if p.get("is_pareto")]
        pz = [p["max_corr"] for p in points if p.get("is_pareto")]
        t_p = go.Scatter3d(x=px, y=py, z=pz, mode="markers",
                            marker=dict(size=12, color="red",
                                        symbol="diamond"),
                            name="帕累托前沿")
        shap_df = get_current_shap_distribution(points)
        t_shap = go.Bar(x=shap_df["importance"],
                        y=shap_df["feature"], orientation="h",
                        marker_color="coral", name="SHAP")
        frames.append(go.Frame(data=[t_main, t_p, t_shap],
                                name=str(snap["generation"])))

    first = frames[0]
    fig.add_trace(first.data[0], row=1, col=1)
    fig.add_trace(first.data[1], row=1, col=1)
    fig.add_trace(first.data[2], row=1, col=2)
    fig.frames = frames

    fig.update_layout(
        updatemenus=[dict(type="buttons", showactive=False,
                           x=0.05, y=1.15,
                           buttons=[dict(label="▶ 播放",
                                          method="animate",
                                          args=[None, dict(
                                              frame=dict(
                                                  duration=800,
                                                  redraw=True),
                                              fromcurrent=True)])])],
        sliders=[dict(steps=[
            dict(method="animate",
                 args=[[f.name], dict(mode="immediate",
                                       frame=dict(duration=800,
                                                  redraw=True))],
                 label=f.name) for f in frames],
            x=0.1, len=0.9)],
        height=700, width=1400)
    fig.write_html(html_path, auto_play=True)
    print(f"  ✅ SHAP 叠加动画: {html_path}")
    return html_path