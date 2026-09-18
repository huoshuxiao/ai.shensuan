# -*- coding: utf-8 -*-
"""反馈看板：streamlit run app_feedback.py"""

import os
import json
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

st.set_page_config(page_title="反馈闭环", layout="wide")
st.title("🔄 实盘反馈闭环看板")


@st.cache_data(ttl=10)
def load_csv(p):
    return pd.read_csv(p) if os.path.exists(p) else None


@st.cache_data(ttl=10)
def load_json(p):
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


report = load_json("live_data/feedback_report.json")
if not report:
    st.warning("未找到反馈报告，先运行: python run_feedback.py")
    st.stop()

sp = report.get("slippage", {})
lt = report.get("latency", {})
st_ = report.get("strategy", {})
fc = report.get("factor", {})

c1, c2, c3, c4 = st.columns(4)
c1.metric("实盘滑点", f"{sp.get('avg_slippage', 0)*100:.4f}%",
          delta=f"建议 {sp.get('suggested_slippage_conservative', 0)*100:.4f}%")
c2.metric("实盘延迟", f"{lt.get('avg_latency_sec', 0):.2f}s")
c3.metric("实盘夏普", f"{st_.get('live_sharpe', 0):.3f}",
          delta=f"{st_.get('sharpe_ratio', 0):.2f}x 回测"
          if "sharpe_ratio" in st_ else None)
c4.metric("因子淘汰", fc.get("n_retire", 0),
          delta=f"保留 {fc.get('n_keep', 0)}")

st.divider()

tabs = st.tabs(["📊 滑点", "⏱️ 延迟", "🔄 换手",
                "📈 策略", "🎯 因子", "📝 报告",
                "🤖 LLM 报告", "🔍 自评", "📅 月报",
                "🗳️ 投票评估", "🧪 A/B 测试",
                "🤖 多 LLM 生成"])

with tabs[0]:
    df = load_csv("live_data/slippage_detail.csv")
    if df is not None and not df.empty:
        fig = go.Figure(go.Histogram(x=df["slippage"], nbinsx=30,
                                      marker_color="coral"))
        fig.update_layout(title="滑点分布", height=350)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df.tail(100), use_container_width=True)

with tabs[1]:
    st.json(lt)

with tabs[2]:
    st.json(report.get("turnover", {}))

with tabs[3]:
    st.json(st_)

with tabs[4]:
    f = load_csv("live_data/factor_feedback.csv")
    if f is not None and not f.empty:
        st.dataframe(f, use_container_width=True)

with tabs[5]:
    if os.path.exists("live_data/feedback_report.md"):
        with open("live_data/feedback_report.md", "r",
                  encoding="utf-8") as fp:
            st.markdown(fp.read())

with tabs[6]:
    if os.path.exists("live_data/report_daily.md"):
        with open("live_data/report_daily.md", "r",
                  encoding="utf-8") as fp:
            st.markdown(fp.read())

with tabs[7]:
    summary = load_json("live_data/self_eval_summary.json")
    if summary:
        c1, c2, c3 = st.columns(3)
        c1.metric("平均分", summary.get("avg_score", 0))
        c2.metric("中位数", summary.get("median_score", 0))
        c3.metric("报告数", summary.get("n_reports", 0))
        for issue in summary.get("top_issues", []):
            st.markdown(f"- ({issue['count']}) {issue['text']}")

with tabs[8]:
    if os.path.exists("live_data/report_monthly.md"):
        with open("live_data/report_monthly.md", "r",
                  encoding="utf-8") as fp:
            st.markdown(fp.read())

with tabs[9]:
    s = load_json("live_data/voting_eval_summary.json")
    if s:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("平均分", s.get("avg_score", 0))
        c2.metric("平均一致性", f"{s.get('avg_consistency', 0):.3f}")
        c3.metric("高争议", s.get("n_controversial", 0))
        c4.metric("报告数", s.get("n_reports", 0))

with tabs[10]:
    if os.path.exists("live_data/ab_test_report.md"):
        with open("live_data/ab_test_report.md", "r",
                  encoding="utf-8") as fp:
            st.markdown(fp.read())

with tabs[11]:
    if os.path.exists("live_data/report_daily.md"):
        with open("live_data/report_daily.md", "r",
                  encoding="utf-8") as fp:
            st.markdown(fp.read())
    st.caption("多 LLM 生成后，此报告为胜出版本")