# -*- coding: utf-8 -*-
"""反馈看板：streamlit run app_feedback.py"""

import os
import json
import _bootstrap  # noqa: F401  必须先于项目模块导入
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from config import (
    LIVE_DATA_DIR as _LIVE, RESULTS_DIR as _RESULTS,
    REPORT_DIR as _REPORT, FACTOR_ATTRIBUTION, LLM_SHAP_EXPLAINER,
)
from llm_selfreport import self_report_mtime

st.set_page_config(page_title="反馈闭环", layout="wide")
st.title("🔄 实盘反馈闭环看板")

# 数据统一在 etf/v1/data/ 下，报告在 etf/v1/report/
LIVE_DATA_DIR = _LIVE
RESULTS_DIR = _RESULTS
REPORT_DIR = os.environ.get("ETF_REPORT_DIR") or _REPORT


@st.cache_data(ttl=10)
def load_csv(p):
    return pd.read_csv(p) if os.path.exists(p) else None


@st.cache_data(ttl=10)
def load_json(p):
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


report = load_json(f"{LIVE_DATA_DIR}/feedback_report.json")
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
    df = load_csv(f"{LIVE_DATA_DIR}/slippage_detail.csv")
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
    f = load_csv(f"{LIVE_DATA_DIR}/factor_feedback.csv")
    if f is not None and not f.empty:
        st.dataframe(f, use_container_width=True)

with tabs[5]:
    if os.path.exists(f"{REPORT_DIR}/feedback_report.md"):
        with open(f"{REPORT_DIR}/feedback_report.md", "r",
                  encoding="utf-8") as fp:
            st.markdown(fp.read())

with tabs[6]:
    if os.path.exists(f"{REPORT_DIR}/report_daily.md"):
        with open(f"{REPORT_DIR}/report_daily.md", "r",
                  encoding="utf-8") as fp:
            st.markdown(fp.read())

    # ---------- 模型自述：衰减解释文本（不是判据） ----------
    st.subheader("模型给的衰减解释（文本，不是判据）")
    attr_on = "开" if FACTOR_ATTRIBUTION["enabled"] else "关"
    shap_on = "开" if LLM_SHAP_EXPLAINER["enabled"] else "关"
    attr_csv = f"{RESULTS_DIR}/factor_attribution.csv"
    st.caption(f"数值贡献表（`factor_attribution.csv`）由 `FACTOR_ATTRIBUTION`"
               f"（现{attr_on}）产出、列在研究看板的「🧬 因子」页，最后一次落盘 "
               f"{self_report_mtime(attr_csv)}；文本解释由 `LLM_SHAP_EXPLAINER`"
               f"（现{shap_on}）在衰减环节写进 `report/llm_shap_report.md`。"
               "两段都是**模型对既有数字的说法**，不进准入判据，也不与数值表做核对。")
    shap = load_json(f"{REPORT_DIR}/llm_shap_report.json") or {}
    st.write(f"解释文本 {len(shap)} 段 · 落盘于 "
             f"{self_report_mtime(f'{REPORT_DIR}/llm_shap_report.json')}")
    for name, text in shap.items():
        with st.expander(name):
            st.markdown(text)

with tabs[7]:
    # ---------- 主读数：本线按日期合并的自评历史 ----------
    # 日更每天只评当日 1 份（见 `scheduler.daily_feedback`），共享层那份汇总因此
    # 只反映"最后一次批量"，累计读数只能从本线这份合并文件取。
    st.subheader("四维分数（准确性/完整性/可执行性/逻辑性，各 25 分）")
    dims = load_json(f"{REPORT_DIR}/self_eval_dims.json")
    if dims:
        rows = dims.get("detail") or []
        scores = pd.Series([r.get("final_score") for r in rows]).dropna()
        n_tpl = sum(1 for r in rows if r.get("mode") == "template")
        c1, c2, c3 = st.columns(3)
        c1.metric("已落盘天数", len(rows))
        c2.metric("最近一批份数", dims.get("n_batch", 0))
        c3.metric("累计平均分",
                  round(float(scores.mean()), 1) if len(scores) else None)
        st.caption(f"落盘于 {dims.get('written_at')}；最近一批评法 "
                   f"`mode={dims.get('mode')}`")
        if n_tpl:
            st.error(f"{n_tpl}/{len(rows)} 天是**模板分**（那几天没有 LLM 端点，"
                     "四维恒 20、grade 恒 A），不是模型给的。每行的 `mode` 才是"
                     "那天的评法，顶层 `mode` 只代表最近一批 —— 日更中途端点断过 "
                     "一天，就在那一行上留着。")
        else:
            st.caption("`mode=llm` 只说明**那几天端点在**（判据是 key 或 "
                       "`LLM_BASE_URL` 非空，本线默认就配着），不代表分数可信 —— "
                       "09-25 实测 `qwen2.5:7b` 会把提示词里的示例 JSON 逐字吐回"
                       "（四维 22/18/20/21、total 81）。评的对象是**日报文本质量**，"
                       "不参与任何策略判据。")
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True)
    else:
        st.info("还没有四维落盘：跑 `run_feedback.py --self-eval` 才会写 "
                "`report/self_eval_dims.json`（共享层自己只落 final_score/grade，"
                "且是整表覆写）。")

    # ---------- 最近一批：共享层自己那份汇总 ----------
    summary = load_json(f"{REPORT_DIR}/self_eval_summary.json")
    if summary:
        st.subheader("最近一批（共享层 `self_eval_summary.json`）")
        st.caption("这份每跑一次 `--self-eval` 就被**整表覆写**一次：日更挂 "
                   "`--monthly-days 1` 时它是 1 天的数，别当趋势读。")
        c1, c2, c3 = st.columns(3)
        c1.metric("平均分", summary.get("avg_score", 0))
        c2.metric("中位数", summary.get("median_score", 0))
        c3.metric("报告数", summary.get("n_reports", 0))
        for issue in summary.get("top_issues", []):
            st.markdown(f"- ({issue['count']}) {issue['text']}")

with tabs[8]:
    if os.path.exists(f"{REPORT_DIR}/report_monthly.md"):
        with open(f"{REPORT_DIR}/report_monthly.md", "r",
                  encoding="utf-8") as fp:
            st.markdown(fp.read())

with tabs[9]:
    s = load_json(f"{REPORT_DIR}/voting_eval_summary.json")
    if s:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("平均分", s.get("avg_score", 0))
        c2.metric("平均一致性", f"{s.get('avg_consistency', 0):.3f}")
        c3.metric("高争议", s.get("n_controversial", 0))
        c4.metric("报告数", s.get("n_reports", 0))

with tabs[10]:
    if os.path.exists(f"{REPORT_DIR}/ab_test_report.md"):
        with open(f"{REPORT_DIR}/ab_test_report.md", "r",
                  encoding="utf-8") as fp:
            st.markdown(fp.read())

with tabs[11]:
    if os.path.exists(f"{REPORT_DIR}/report_daily.md"):
        with open(f"{REPORT_DIR}/report_daily.md", "r",
                  encoding="utf-8") as fp:
            st.markdown(fp.read())
    st.caption("多 LLM 生成后，此报告为胜出版本")