# -*- coding: utf-8 -*-
"""研究看板：streamlit run app.py"""

import os
import json
import _bootstrap  # noqa: F401  必须先于项目模块导入
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from config import RESULTS_DIR
from factor_naming import cn_name, METRIC_GLOSSARY

st.set_page_config(page_title="ETF 量化看板", layout="wide")
st.title("📊 ETF 量化系统看板")


@st.cache_data(ttl=30)
def load_csv(p):
    return pd.read_csv(p) if os.path.exists(p) else None


@st.cache_data(ttl=30)
def load_json(p):
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _holdings(signals):
    """长表信号里带持仓的行（code="" 是空仓哨兵，见 portfolio_engine）"""
    code = signals["code"].astype(str)
    return signals[code.notna() & ~code.isin(["", "nan", "None"])]


equity_df = load_csv(f"{RESULTS_DIR}/equity.csv")
trades_df = load_csv(f"{RESULTS_DIR}/trades.csv")
dsr_df = load_csv(f"{RESULTS_DIR}/dsr.csv")
signals_df = load_csv(f"{RESULTS_DIR}/signals.csv")

col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)
if equity_df is not None and not equity_df.empty:
    dt = equity_df.columns[0]
    eq = equity_df.set_index(dt)["equity"]
    total = eq.iloc[-1] / eq.iloc[0] - 1
    span_days = max((pd.to_datetime(eq.index[-1]) -
                     pd.to_datetime(eq.index[0])).days, 1)
    ann = (1 + total) ** (365 / span_days) - 1
    rets = eq.pct_change().dropna()
    sharpe = rets.mean() / (rets.std() + 1e-9) * np.sqrt(252)
    max_dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    col1.metric("总收益率", f"{total * 100:.2f}%")
    col2.metric("年化收益率", f"{ann * 100:.2f}%")
    col3.metric("最大回撤", f"{max_dd * 100:.2f}%")
    col4.metric("夏普比率", f"{sharpe:.2f}")
    col5.metric("最终资金", f"{eq.iloc[-1]:,.2f}")
if dsr_df is not None and not dsr_df.empty:
    dsr_val = float(dsr_df.iloc[0].get("dsr", 0))
    col6.metric("DSR", f"{dsr_val:.4f}",
                delta="显著" if dsr_val > 0.95 else "不显著")
if trades_df is not None:
    col7.metric("交易次数", len(trades_df))
if signals_df is not None and "code" in signals_df.columns:
    held = _holdings(signals_df)
    ts_col = signals_df.columns[0]
    # 信号是稀疏长表：一根调仓 bar 出多行（每只持仓一行），故按时间戳去重
    rebal_days = signals_df[ts_col].nunique()
    held_days = held[ts_col].nunique() if not held.empty else 0
    last_n = 0
    if not held.empty:
        last_n = int((held[ts_col] == held[ts_col].max()).sum())
    col8.metric("最近调仓只数", last_n,
                delta=f"在仓 {held_days}/{rebal_days} 次调仓")

st.divider()

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
    if signals_df is not None and "code" in signals_df.columns:
        held_df = _holdings(signals_df)
        ts_col = signals_df.columns[0]
        n_rebal = signals_df[ts_col].nunique()
        counts = held_df["code"].value_counts()
        if not counts.empty:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("调仓次数", n_rebal)
            c2.metric("平均持仓只数",
                      f"{len(held_df) / max(n_rebal, 1):.1f}")
            c3.metric("涉及标的数", counts.size)
            # 空仓 = 该次调仓只剩哨兵行（无任何标的过分数门槛）
            c4.metric("空仓调仓次数", int(n_rebal - held_df[ts_col].nunique()))
            fig = go.Figure(go.Bar(
                x=counts.index, y=counts.values,
                text=[f"{v / n_rebal * 100:.0f}%" for v in counts.values],
                textposition="outside", marker_color="steelblue"))
            fig.update_layout(
                title="标的入选次数（标注为占调仓次数比例）",
                xaxis_title="标的", yaxis_title="入选次数",
                height=400)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(held_df.tail(200), use_container_width=True)
        else:
            st.info("全程空仓")
    else:
        st.info("signals.csv 不是截面组合长表格式（缺 code 列），"
                "请重跑 main.py 生成")

with tabs[4]:
    coords = load_csv(f"{RESULTS_DIR}/factor_clusters.csv")
    if coords is not None and not coords.empty:
        fig = go.Figure()
        for cid in sorted(coords["cluster"].unique()):
            sub = coords[coords["cluster"] == cid]
            fig.add_trace(go.Scatter(
                x=sub["x"], y=sub["y"], mode="markers+text",
                marker=dict(size=10, color=f"hsl({(cid * 45) % 360}, 70%, 50%)"),
                text=sub["name"].map(cn_name), textposition="top center",
                name=f"簇{cid}"))
        fig.update_layout(title="因子空间", height=600)
        st.plotly_chart(fig, use_container_width=True)

with tabs[5]:
    attr = load_csv(f"{RESULTS_DIR}/factor_attribution.csv")
    if attr is not None and not attr.empty:
        col = "shapley" if "shapley" in attr.columns else "contribution"
        if col in attr.columns:
            top = attr.head(15)
            fig = go.Figure(go.Bar(
                x=top["factor"].map(cn_name), y=top[col],
                customdata=top["factor"],
                hovertemplate="%{x}<br>原名: %{customdata}<br>%{y:+.4f}<extra></extra>",
                marker_color=["green" if v > 0 else "red"
                              for v in top[col]]))
            fig.update_layout(title="因子贡献", height=400)
            st.plotly_chart(fig, use_container_width=True)
        if "factor" in attr.columns:
            attr.insert(1, "中文名", attr["factor"].map(cn_name))
        st.dataframe(attr, use_container_width=True)
        with st.expander("📖 指标口径说明"):
            st.markdown(
                "| 指标 | 中文名 | 含义与参考口径 |\n|---|---|---|\n"
                + "\n".join(f"| `{k}` | {l} | {d.replace('|', chr(92) + '|')} |"
                            for k, l, d in METRIC_GLOSSARY))

with tabs[6]:
    decay = load_csv(f"{RESULTS_DIR}/factor_decay.csv")
    pred = load_csv(f"{RESULTS_DIR}/factor_decay_predict.csv")
    for df in (decay, pred):
        if df is not None and "factor" in df.columns \
                and "中文名" not in df.columns:
            df.insert(1, "中文名", df["factor"].map(cn_name))
    if decay is not None and not decay.empty:
        st.dataframe(decay, use_container_width=True)
    if pred is not None and not pred.empty:
        st.subheader("衰减预测")
        st.dataframe(pred, use_container_width=True)

with tabs[7]:
    mogp = load_csv(f"{RESULTS_DIR}/mogp_pareto_front.csv")
    hist = load_csv(f"{RESULTS_DIR}/mogp_history.csv")
    if hist is not None and not hist.empty:
        fig = go.Figure(go.Scatter(
            x=hist["generation"], y=hist["best_ic"],
            mode="lines+markers"))
        fig.update_layout(title="GP 进化", height=400)
        st.plotly_chart(fig, use_container_width=True)
    if mogp is not None and not mogp.empty:
        st.dataframe(mogp, use_container_width=True)

with tabs[8]:
    hist = load_csv(f"{RESULTS_DIR}/factor_git_history.csv")
    if hist is not None and not hist.empty:
        st.dataframe(hist, use_container_width=True)
    else:
        st.info("未找到 git 历史")

with tabs[9]:
    st.subheader("📊 日线 vs 分钟线对比")

    daily_eq = load_csv(f"{RESULTS_DIR}/equity_daily.csv")
    minute_eq = load_csv(f"{RESULTS_DIR}/equity_1min.csv")

    if daily_eq is not None or minute_eq is not None:
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

        comp = load_csv(f"{RESULTS_DIR}/freq_comparison.csv")
        if comp is not None:
            st.subheader("指标对比")
            st.dataframe(comp, use_container_width=True)

        st.subheader("🎯 持仓相似度（每日收盘仓位：日线 vs 分钟线）")
        sim = load_json(f"{RESULTS_DIR}/holdings_similarity.json")
        if sim:
            def _pct(v):
                return "N/A" if v is None else f"{v * 100:.2f}%"
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("对齐天数", sim.get("对齐天数", 0))
            c2.metric("总相似度", _pct(sim.get("总相似度")),
                      help="全部对齐日中两频率收盘持仓相同的比例")
            c3.metric("持仓日相似度", _pct(sim.get("持仓日相似度")),
                      help="仅统计任一频率持仓的日子")
            c4.metric("双边持仓日相似度",
                      _pct(sim.get("双边持仓日相似度")),
                      help="仅统计两个频率同时持仓的日子")
            c5, c6 = st.columns(2)
            c5.metric("日线持仓时间占比", _pct(sim.get("持仓占比_a")))
            c6.metric("分钟线持仓时间占比", _pct(sim.get("持仓占比_b")))
            ex = sim.get("不一致示例") or []
            if ex:
                st.caption("不一致示例（最多 20 条）")
                st.dataframe(pd.DataFrame(ex), use_container_width=True)
        else:
            st.info("未找到 holdings_similarity.json，"
                    "请在两种频率回测完成后运行: python compare_freq.py")
    else:
        st.info("请先运行 run_daily_backtest.py 和 run_minute_backtest.py")        