# -*- coding: utf-8 -*-
"""动画导出 GIF/MP4"""

import os
import shutil
import numpy as np
from config import ANIMATION_EXPORT as CFG


def export_frames(snapshots, frames_dir=None, max_frames=None):
    frames_dir = frames_dir or CFG["frames_dir"]
    max_frames = max_frames or CFG["max_frames"]
    if os.path.exists(frames_dir):
        shutil.rmtree(frames_dir)
    os.makedirs(frames_dir, exist_ok=True)
    if len(snapshots) > max_frames:
        idx = np.linspace(0, len(snapshots) - 1,
                           max_frames).astype(int)
        snapshots = [snapshots[i] for i in idx]
    try:
        import plotly.graph_objects as go
    except ImportError:
        return []

    png_paths = []
    for i, snap in enumerate(snapshots):
        points = snap["points"]
        fig = go.Figure()
        fig.add_trace(go.Scatter3d(
            x=[p["ic"] for p in points],
            y=[p["turnover"] for p in points],
            z=[p["max_corr"] for p in points],
            mode="markers",
            marker=dict(size=6, color=[p["abs_ic"] for p in points],
                        colorscale="Viridis")))
        fig.update_layout(
            title=f"帕累托 - 第 {snap['generation']} 代",
            width=CFG["width"], height=CFG["height"])
        png_path = os.path.join(frames_dir, f"frame_{i:03d}.png")
        try:
            fig.write_image(png_path, scale=1.0)
            png_paths.append(png_path)
        except Exception as e:
            print(f"    ❌ 帧导出失败: {e}")
    return png_paths


def make_gif(png_paths, output=None):
    output = output or CFG["gif_path"]
    if not png_paths:
        return ""
    try:
        from PIL import Image
    except ImportError:
        return ""
    frames = [Image.open(p).convert("RGBA") for p in png_paths]
    frames[0].save(output, save_all=True, append_images=frames[1:],
                   duration=CFG["gif_duration_ms"], loop=0)
    print(f"  ✅ GIF: {output}")
    return output


def make_mp4(png_paths, output=None):
    output = output or CFG["mp4_path"]
    if not png_paths:
        return ""
    try:
        import imageio.v2 as imageio
        writer = imageio.get_writer(output, fps=CFG["mp4_fps"],
                                     codec="libx264")
        for p in png_paths:
            img = imageio.imread(p)
            if img.shape[-1] == 4:
                img = img[..., :3]
            writer.append_data(img)
        writer.close()
        print(f"  ✅ MP4: {output}")
        return output
    except Exception as e:
        print(f"  ⚠️ MP4 失败: {e}")
        return ""


def export_animation(snapshots):
    print("\n========== 动画导出 ==========")
    if not CFG["enabled"]:
        return {}
    png_paths = export_frames(snapshots)
    if not png_paths:
        return {}
    result = {"frames": png_paths}
    if CFG["export_gif"]:
        result["gif"] = make_gif(png_paths)
    if CFG["export_mp4"]:
        result["mp4"] = make_mp4(png_paths)
    if not CFG["keep_frames"]:
        try:
            shutil.rmtree(os.path.dirname(png_paths[0]))
        except Exception:
            pass
    return result