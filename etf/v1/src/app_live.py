# -*- coding: utf-8 -*-
"""影子盘监控看板（原名「实盘监控」，09-27 改名）。

单跑：`streamlit run app_live.py`；被 `app.py` 当一节调时 import 本模块并调
`render()`（此时不碰 `st.set_page_config`，那一次由宿主页面用掉）。

为什么改名：这一节读的是 `run_live.py` **默认模拟盘**（paper 账户）落在 `data/live/`
的三张 csv，本页不发任何委托。旧名"实盘监控"会让人读成系统在替人下单 —— 对外表述
统一为"辅助决策，不自动实盘"，标题就得跟着改。`src/live/` 里的真实委托代码
（qmt / easytrader）没有接进这一页，也不打算接。
"""

import os
import pandas as pd
import streamlit as st
import _bootstrap  # noqa: F401  必须先于项目模块导入
import plotly.graph_objects as go
from config import LIVE_DATA_DIR


@st.cache_data(ttl=10)
def load_csv(path):
    if os.path.exists(path):
        return pd.read_csv(path)
    return None


def render():
    st.title("📡 影子盘监控看板")
    st.caption("只读 **影子盘**（`run_live.py` 默认的 paper 账户）落在 `data/live/` 的 csv："
               "这一页不接下单、不发委托，`src/live/` 里的真实委托代码没接进来。"
               "订单流水里的成交是模拟账户自己记账的结果，真实成交由人在券商端手工下单、"
               "手工录回。")

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
            adf = attribution.copy()
            adf["time"] = pd.to_datetime(adf["time"])
            adf = adf.set_index("time")
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


if __name__ == "__main__":
    st.set_page_config(page_title="影子盘监控", layout="wide")
    render()
