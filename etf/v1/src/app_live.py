# -*- coding: utf-8 -*-
"""实盘看板"""

import os
import pandas as pd
import streamlit as st
import _bootstrap  # noqa: F401  必须先于项目模块导入
from config import LIVE_DATA_DIR

st.set_page_config(page_title="实盘监控", layout="wide")
st.title("📡 实盘监控看板")


@st.cache_data(ttl=10)
def load_csv(path):
    if os.path.exists(path):
        return pd.read_csv(path)
    return None


orders = load_csv(f"{LIVE_DATA_DIR}/live_orders.csv")
attribution = load_csv(f"{LIVE_DATA_DIR}/live_attribution.csv")
risk_events = load_csv(f"{LIVE_DATA_DIR}/live_risk_events.csv")

col1, col2, col3, col4 = st.columns(4)
if attribution is not None and not attribution.empty:
    latest = attribution.iloc[-1]
    col1.metric("总资产", f"{latest['total_asset']:.2f}")
    col2.metric("现金", f"{latest['cash']:.2f}")
    col3.metric("持仓市值", f"{latest['market_value']:.2f}")
    col4.metric("持仓数", int(latest["n_positions"]))

st.divider()
tabs = st.tabs(["📊 账户", "📋 订单", "⚠️ 风控"])

with tabs[0]:
    if attribution is not None and not attribution.empty:
        attribution["time"] = pd.to_datetime(attribution["time"])
        adf = attribution.set_index("time")
        import plotly.graph_objects as go
        fig = go.Figure(go.Scatter(x=adf.index,
                                    y=adf["total_asset"],
                                    mode="lines"))
        fig.update_layout(title="总资产", height=400)
        st.plotly_chart(fig, use_container_width=True)

with tabs[1]:
    if orders is not None and not orders.empty:
        st.dataframe(orders.tail(100), use_container_width=True)

with tabs[2]:
    if risk_events is not None and not risk_events.empty:
        st.dataframe(risk_events.tail(50), use_container_width=True)
    else:
        st.success("✅ 无风控事件")