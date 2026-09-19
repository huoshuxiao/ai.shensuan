# -*- coding: utf-8 -*-
"""帕累托演化动画"""

import os
import numpy as np
import pandas as pd
from config import PARETO_ANIMATION as CFG


class ParetoRecorder:
    def __init__(self):
        self.snapshots = []

    def record(self, generation, metrics):
        points = []
        sample_size = min(CFG["sample_per_gen"], len(metrics))
        idx = np.linspace(0, len(metrics) - 1,
                           sample_size).astype(int)
        for i in idx:
            m = metrics[i]
            points.append({
                "ic": float(m.get("ic", 0)),
                "abs_ic": float(m.get("abs_ic", 0)),
                "turnover": float(m.get("turnover", 0)),
                "max_corr": float(m.get("max_corr", 0)),
                "expr": m.get("expr", "")[:80],
                "is_pareto": bool(m.get("is_pareto", False))})
        self.snapshots.append({"generation": generation,
                                "points": points})

    def to_dataframe(self):
        rows = []
        for snap in self.snapshots:
            for p in snap["points"]:
                row = dict(p)
                row["generation"] = snap["generation"]
                rows.append(row)
        return pd.DataFrame(rows)

    def save(self, path=None):
        path = path or CFG["history_path"]
        df = self.to_dataframe()
        if not df.empty:
            df.to_csv(path, index=False, encoding="utf-8-sig")


def create_animation(snapshots, html_path=None, dims=None):
    print("\n========== 生成帕累托演化动画 ==========")
    if not snapshots:
        return ""
    import plotly.graph_objects as go
    html_path = html_path or CFG["html_path"]
    dims = dims or CFG["dimensions"]

    if len(snapshots) > CFG["max_generations"]:
        idx = np.linspace(0, len(snapshots) - 1,
                           CFG["max_generations"]).astype(int)
        snapshots = [snapshots[i] for i in idx]

    all_ic = [p["ic"] for s in snapshots for p in s["points"]]
    all_tv = [p["turnover"] for s in snapshots for p in s["points"]]
    all_corr = [p["max_corr"] for s in snapshots for p in s["points"]]

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
            text=[p["expr"] for p in points], name="种群")
        px = [p["ic"] for p in points if p["is_pareto"]]
        py = [p["turnover"] for p in points if p["is_pareto"]]
        pz = [p["max_corr"] for p in points if p["is_pareto"]]
        t_p = go.Scatter3d(x=px, y=py, z=pz, mode="markers",
                            marker=dict(size=12, color="red",
                                        symbol="diamond"),
                            name="帕累托前沿")
        frames.append(go.Frame(data=[t_main, t_p],
                                name=str(snap["generation"])))

    fig = go.Figure(data=frames[0].data, frames=frames,
                    layout=go.Layout(
                        title="帕累托前沿演化",
                        scene=dict(
                            xaxis=dict(title="IC"),
                            yaxis=dict(title="换手率"),
                            zaxis=dict(title="相关性")),
                        updatemenus=[dict(
                            type="buttons", showactive=False,
                            buttons=[dict(label="▶ 播放",
                                          method="animate",
                                          args=[None, dict(
                                              frame=dict(
                                                  duration=800,
                                                  redraw=True),
                                              fromcurrent=True)])])],
                        height=700))
    fig.write_html(html_path, auto_play=True)
    print(f"  ✅ 动画: {html_path}")
    return html_path


def compute_pareto_metrics(snapshots):
    rows = []
    for snap in snapshots:
        pareto = [p for p in snap["points"] if p["is_pareto"]]
        if not pareto:
            continue
        rows.append({"generation": snap["generation"],
                     "front_size": len(pareto),
                     "best_ic": max(p["abs_ic"] for p in pareto)})
    return pd.DataFrame(rows)