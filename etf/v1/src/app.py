# -*- coding: utf-8 -*-
"""研究看板：streamlit run app.py"""

import os
import json
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="ETF 量化看板", layout="wide")
st.title("📊 ETF 分钟线量化系统看板")


@st.cache_data(ttl=30)
def load_csv(p):
    return pd.read_csv(p) if os.path.exists(p) else None


@st.cache_data(ttl=30)
def load_json(p):
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


equity_df = load_csv("equity.csv")
trades_df = load_csv("trades.csv")
dsr_df = load_csv("dsr.csv")
signals_df = load_csv("signals.csv")

col1, col2, col3, col4, col5 = st.columns(5)
if equity_df is not None and not equity_df.empty:
    dt = equity_df.columns[0]
    eq = equity_df.set_index(dt)["equity"]
    total = eq.iloc[-1] / eq.iloc[0] - 1
    col1.metric("总收益率", f"{total * 100:.2f}%")
    col2.metric("最终资金", f"{eq.iloc[-1]:.2f}")
if dsr_df is not None and not dsr_df.empty:
    dsr_val = float(dsr_df.iloc[0].get("dsr", 0))
    col3.metric("DSR", f"{dsr_val:.4f}",
                delta="显著" if dsr_val > 0.95 else "不显著")
if trades_df is not None:
    col4.metric("交易次数", len(trades_df))
if signals_df is not None and "target_code" in signals_df.columns:
    hold = (signals_df["target_code"].notna() &
            (signals_df["target_code"] != "")).sum()
    col5.metric("持仓 bar", int(hold))

st.divider()

# 在 app.py 的 tabs 里加
tabs = st.tabs(["📈 净值", "📋 交易", "🎯 DSR", "⚙️ 信号",
                "🧩 聚类", "🎯 归因", "📉 衰减", "🧬 GP",
                "📜 Git", "📊 频率对比"])

with tabs[0]:
    if equity_df is not None and not equity_df.empty:
        dt = equity_df.columns[0]
        edf = equity_df.set_index(dt)
        eq = edf["equity"]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.7, 0.3],
                            subplot_titles=("净值", "回撤"))
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values,
                                  name="净值"), row=1, col=1)
        dd = (eq - eq.cummax()) / eq.cummax() * 100
        fig.add_trace(go.Scatter(x=dd.index, y=dd.values,
                                  name="回撤", fill="tozeroy"),
                      row=2, col=1)
        fig.update_layout(height=600, hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

with tabs[1]:
    if trades_df is not None and not trades_df.empty:
        st.dataframe(trades_df.tail(200), use_container_width=True)

with tabs[2]:
    if dsr_df is not None and not dsr_df.empty:
        row = dsr_df.iloc[0].to_dict()
        for k, v in row.items():
            if isinstance(v, float):
                st.metric(k, f"{v:.4f}")
            else:
                st.metric(k, v)

with tabs[3]:
    if signals_df is not None and "target_code" in signals_df.columns:
        counts = signals_df[signals_df["target_code"] != ""][
            "target_code"].value_counts()
        if not counts.empty:
            fig = go.Figure(go.Bar(x=counts.index, y=counts.values))
            st.plotly_chart(fig, use_container_width=True)

with tabs[4]:
    coords = load_csv("factor_clusters.csv")
    if coords is not None and not coords.empty:
        fig = go.Figure()
        for cid in sorted(coords["cluster"].unique()):
            sub = coords[coords["cluster"] == cid]
            fig.add_trace(go.Scatter(
                x=sub["x"], y=sub["y"], mode="markers+text",
                marker=dict(size=10, color=f"hsl({(cid * 45) % 360}, 70%, 50%)"),
                text=sub["name"], textposition="top center",
                name=f"簇{cid}"))
        fig.update_layout(title="因子空间", height=600)
        st.plotly_chart(fig, use_container_width=True)

with tabs[5]:
    attr = load_csv("factor_attribution.csv")
    if attr is not None and not attr.empty:
        col = "shapley" if "shapley" in attr.columns else "contribution"
        if col in attr.columns:
            top = attr.head(15)
            fig = go.Figure(go.Bar(
                x=top["factor"], y=top[col],
                marker_color=["green" if v > 0 else "red"
                              for v in top[col]]))
            fig.update_layout(title="因子贡献", height=400)
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(attr, use_container_width=True)

with tabs[6]:
    decay = load_csv("factor_decay.csv")
    pred = load_csv("factor_decay_predict.csv")
    if decay is not None and not decay.empty:
        st.dataframe(decay, use_container_width=True)
    if pred is not None and not pred.empty:
        st.subheader("衰减预测")
        st.dataframe(pred, use_container_width=True)

with tabs[7]:
    mogp = load_csv("mogp_pareto_front.csv")
    hist = load_csv("mogp_history.csv")
    if hist is not None and not hist.empty:
        fig = go.Figure(go.Scatter(
            x=hist["generation"], y=hist["best_ic"],
            mode="lines+markers"))
        fig.update_layout(title="GP 进化", height=400)
        st.plotly_chart(fig, use_container_width=True)
    if mogp is not None and not mogp.empty:
        st.dataframe(mogp, use_container_width=True)

with tabs[8]:
    hist = load_csv("factor_git_history.csv")
    if hist is not None and not hist.empty:
        st.dataframe(hist, use_container_width=True)
    else:
        st.info("未找到 git 历史")

with tabs[9]:
    st.subheader("📊 日线 vs 分钟线对比")

    daily_eq = load_csv("equity_daily.csv")
    minute_eq = load_csv("equity_1min.csv")

    if daily_eq is not None or minute_eq is not None:
        import plotly.graph_objects as go
        fig = go.Figure()

        if daily_eq is not None:
            d = daily_eq.set_index(daily_eq.columns[0])
            d_eq = d["equity"] / d["equity"].iloc[0]
            fig.add_trace(go.Scatter(x=d.index, y=d_eq,
                                      name="日线",
                                      line=dict(color="steelblue",
                                                width=2)))
        if minute_eq is not None:
            m = minute_eq.set_index(minute_eq.columns[0])
            m_eq = m["equity"] / m["equity"].iloc[0]
            fig.add_trace(go.Scatter(x=m.index, y=m_eq,
                                      name="分钟线",
                                      line=dict(color="orange",
                                                width=2)))

        fig.update_layout(title="归一化净值对比", height=500,
                          hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

        comp = load_csv("freq_comparison.csv")
        if comp is not None:
            st.subheader("指标对比")
            st.dataframe(comp, use_container_width=True)

        # 一致性检查
        st.subheader("信号一致性")
        st.caption("同一策略在两种频率下应产生同方向信号")
    else:
        st.info("请先跑 python main.py（FREQ=daily）"
                "和 python main.py（FREQ=1min）")        