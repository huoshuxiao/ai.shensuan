# -*- coding: utf-8 -*-
"""ETF 研究看板：`streamlit run app.py`（工作目录 etf/v1/src）

这一页只读，不产生任何文件；所有数字都来自主线落盘的产物，页上不重算判据。

为什么页首第一行是「数据新鲜度」而不是收益
------------------------------------------
本线的每一条判据（DSR / walk-forward / 策略级 PBO / 规模闸）都建立在"日线已经推到
最新交易日"之上。#19 实测过反面：日更没跑的那几天，池缓存停在 09-18 而全市场镜像
已到 09-22，回测安静地算着一周前的净值，页面上一切看起来正常。所以新鲜度放在收益
之前，落后就把入口写在脸上（`python data/update_etf_daily.py`）。

口径与阈值一律从 `config` / `etf_admission` 现读，不在本页复制常量。
"""

import glob
import json
import os
from datetime import datetime

import _bootstrap  # noqa: F401  必须先于项目模块导入
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from config import (CACHE_DIR, DSR, FACTOR_LIBRARY, FREQ, PORTFOLIO,
                    RESULTS_DIR, RISK_DIR, STRATEGY_PBO, TRIAL_COUNTER_FILE,
                    TRIGGER_LOGIC, UNIVERSE_ALL_DIR, WALK_FORWARD)
from factor_naming import cn_name, METRIC_GLOSSARY

st.set_page_config(page_title="ETF 量化看板", layout="wide")
st.title("📊 ETF 量化系统看板")


@st.cache_data(ttl=30)
def load_csv(p):
    return pd.read_csv(p) if p and os.path.exists(p) else None


@st.cache_data(ttl=30)
def load_json(p):
    if p and os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def artifact(name, freq=None):
    """按当前频率找产物：`equity_daily.csv` 优先，退到无后缀的旧名。"""
    freq = freq or FREQ
    for p in (f"{RESULTS_DIR}/{name}_{freq}.csv", f"{RESULTS_DIR}/{name}.csv"):
        if os.path.exists(p):
            return p
    return None


def _holdings(signals):
    """长表信号里真正带持仓的行（code="" 是空仓哨兵，见 portfolio_engine）"""
    code = signals["code"].astype(str)
    return signals[code.notna() & ~code.isin(["", "nan", "None"])]


def _pct(v, digits=2, signed=True):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "N/A"
    return f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"


# ---------- 数据新鲜度（页首第一件事） ----------

@st.cache_data(ttl=120)
def freshness_table():
    try:
        from update_etf_daily import freshness_report
        return freshness_report()
    except Exception as e:
        st.warning(f"新鲜度读数不可用: {type(e).__name__}: {e}")
        return pd.DataFrame()


fresh = freshness_table()
eq_path = artifact("equity")
eq_df = load_csv(eq_path)
sig_path = artifact("signals")
signals_df = load_csv(sig_path)
trades_df = load_csv(artifact("trades"))
dsr_df = load_csv(artifact("dsr"))
wf_df = load_csv(artifact("walk_forward"))
pbo_res = load_json(f"{RESULTS_DIR}/pbo_result.json")
params = load_json(f"{RESULTS_DIR}/optimized_params_{FREQ}.json")
trial_counter = load_json(TRIAL_COUNTER_FILE)

last_data_day = str(eq_df[eq_df.columns[0]].iloc[-1])[:10] if eq_df is not None else "—"
lag_cols = [c for c in ("落后交易日",) if c in fresh.columns]
worst_lag = int(pd.to_numeric(fresh["落后交易日"], errors="coerce").fillna(0).max()) \
    if len(fresh) and lag_cols else 0
f1, f2, f3, f4 = st.columns([2.2, 1.4, 1.4, 1.6])
f1.metric("日线净值止于", last_data_day,
          delta=None, help="净值/信号/交易三张表的最后一格，即本页所有读数的时间截面")
f2.metric("数据面最大落后", f"{worst_lag} 个交易日",
          delta=None if worst_lag == 0 else "需要日更",
          delta_color="off" if worst_lag == 0 else "inverse")
f3.metric("试验账本 N",
          (trial_counter or {}).get("count", "—"),
          help="data/cache/trial_counter.json：这条研究线累计试过多少个变体，"
               "DSR 的运气门槛随它收紧（#15 之前每轮被 reset 成 3）")
f4.metric("标的池 / 全市场镜像",
          f"{PORTFOLIO.get('top_k')} / "
          f"{len(glob.glob(os.path.join(UNIVERSE_ALL_DIR, '*_daily.csv')))} 只")
if worst_lag:
    st.error("日线数据没有推到最新交易日。先跑："
             "`cd etf/v1/src && python data/update_etf_daily.py`"
             "（只贴新行、不动历史，实测 871 只 ≈110 秒）")
if len(fresh):
    with st.expander("🗓 各数据面的末日与滞后（只读）", expanded=False):
        st.dataframe(fresh, hide_index=True)
        st.caption("「全市场镜像」是 RD-Agent 侧与 `etf_admission` 的底座；"
                   "「主线池缓存」是 `DataLoader` 的第一优先级；风险长表决定规模闸与"
                   "折溢价能算到哪一天。三个面各按自己的节奏披露，所以分开看。")

st.divider()

# ---------- 业绩概要 ----------
m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
if eq_df is not None and not eq_df.empty:
    eq = eq_df.set_index(eq_df.columns[0])["equity"]
    total = eq.iloc[-1] / eq.iloc[0] - 1
    span_days = max((pd.to_datetime(eq.index[-1]) -
                     pd.to_datetime(eq.index[0])).days, 1)
    ann = (1 + total) ** (365 / span_days) - 1
    rets = eq.pct_change().dropna()
    sharpe = float(rets.mean() / (rets.std() + 1e-9) * np.sqrt(252))
    max_dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    m1.metric("区间", f"{str(eq.index[0])[:7]} ~ {str(eq.index[-1])[:10]}")
    m2.metric("总收益率", _pct(total, signed=False))
    m3.metric("年化收益率", _pct(ann, signed=False))
    m4.metric("最大回撤", _pct(max_dd))
    m5.metric("夏普（年化）", f"{sharpe:.2f}")
    m6.metric("最终资金", f"{eq.iloc[-1]:,.0f}")
if dsr_df is not None and not dsr_df.empty:
    row = dsr_df.iloc[0]
    d = float(row.get("dsr", 0))
    m7.metric("全样本 DSR", f"{d:.4f}",
              delta="过线" if d > 0.95 else f"门槛年化 {row.get('sr0_annual')}",
              delta_color="normal" if d > 0.95 else "off",
              help="Bailey & López de Prado (2014)。运气门槛 SR* 用 Lo(2002) 的"
                   "夏普抽样方差 (1+0.5·SR̂²)/T 折算，与 SR̂ 同为逐 bar 量纲")

# ---------- tabs ----------
tabs = st.tabs(["📈 净值", "💼 持仓与交易", "🎯 统计验证", "🧪 ETF 风险",
                "🧬 因子", "⚙️ 配置与生命周期", "📖 口径"])

with tabs[0]:
    if eq_df is None or eq_df.empty:
        st.info("还没有净值产物：跑 `python main.py`（或 "
                "`python run_daily_backtest.py`）")
    else:
        eq = eq_df.set_index(eq_df.columns[0])["equity"]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.72, 0.28],
                            subplot_titles=("净值", "回撤"))
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name="净值"),
                      row=1, col=1)
        dd = (eq - eq.cummax()) / eq.cummax() * 100
        fig.add_trace(go.Scatter(x=dd.index, y=dd.values, name="回撤 %",
                                 fill="tozeroy"), row=2, col=1)
        fig.update_layout(height=560, hovermode="x unified")
        st.plotly_chart(fig)

        other = ("equity_1min" if FREQ == "daily" else "equity_daily")
        od = load_csv(f"{RESULTS_DIR}/{other}.csv")
        if od is not None and not od.empty:
            o = od.set_index(od.columns[0])["equity"]
            st.subheader("另一种频率的同策略净值（归一化）")
            st.plotly_chart(go.Figure(go.Scatter(
                x=o.index, y=o / o.iloc[0], name=other)))
        comp = load_csv(f"{RESULTS_DIR}/freq_comparison.csv")
        if comp is not None and not comp.empty:
            st.dataframe(comp)

with tabs[1]:
    if signals_df is None or "code" not in signals_df.columns:
        st.info("signals.csv 不是截面组合长表（缺 code 列）——重跑 `python main.py`")
    else:
        ts_col = signals_df.columns[0]
        held = _holdings(signals_df)
        n_rebal = signals_df[ts_col].nunique()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("调仓次数", n_rebal)
        c2.metric("平均持仓只数", f"{len(held) / max(n_rebal, 1):.1f}",
                  help=f"目标 top_k={PORTFOLIO.get('top_k')}，凑不满是分数门槛"
                       "与可投域闸门共同作用的结果")
        c3.metric("涉及标的数", held["code"].nunique())
        c4.metric("空仓调仓次数", int(n_rebal - held[ts_col].nunique()))
        last_day = held[ts_col].max()
        st.subheader(f"最新一次调仓（{str(last_day)[:10]}）")
        latest = held[held[ts_col] == last_day]
        cols = [c for c in latest.columns if c != ts_col]
        st.dataframe(latest[cols].sort_values(
            "weight", ascending=False) if "weight" in cols else latest)
        counts = held["code"].value_counts()
        if not counts.empty:
            fig = go.Figure(go.Bar(
                x=counts.index, y=counts.values,
                text=[f"{v / n_rebal * 100:.0f}%" for v in counts.values],
                textposition="outside", marker_color="steelblue"))
            fig.update_layout(title="标的入选次数（标注为占调仓次数比例）",
                              xaxis_title="标的", yaxis_title="入选次数",
                              height=380)
            st.plotly_chart(fig)
        st.subheader("交易流水（最近 200 笔）")
        if trades_df is not None and not trades_df.empty:
            st.dataframe(trades_df.tail(200))
        else:
            st.info("无成交")

with tabs[2]:
    st.caption("三条判决各自独立：全样本 DSR 看「这条净值曲线是不是运气」，"
               "walk-forward 看「换段时间还成不成立」，策略级 PBO 看「挑参数这件事"
               "本身有多大概率过拟合」。#15 之前三条恒不产生信息（DSR 恒 0、"
               "n_trials 恒 3、折级 PBO 恒 0.9444），现在读的是真数。")

    st.subheader("① 全样本 DSR")
    if dsr_df is None or dsr_df.empty:
        st.info("缺 dsr 产物")
    else:
        row = dsr_df.iloc[0].to_dict()
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("DSR", f"{float(row.get('dsr', 0)):.4f}",
                  delta="过线（>0.95）" if float(row.get("dsr", 0)) > 0.95
                  else "未过线",
                  delta_color="normal" if row.get("passed") else "off")
        d2.metric("实测夏普 SR̂（年化）",
                  f"{float(row.get('sharpe_annual', 0)):.4f}")
        d3.metric("运气门槛 SR*（年化）",
                  f"{float(row.get('sr0_annual', 0)):.4f}",
                  help=f"N={row.get('n_trials')} 次试验下极值夏普的期望；"
                       f"逐 bar 读数 {row.get('sr0_expected_max')}")
        d4.metric("试验次数 N", row.get("n_trials", "—"))
        d5.metric("样本 bar 数", row.get("n_samples", "—"))
        # 一列里混着 str 与 float（sr_variance_source 是中文串），pyarrow 会
        # 在 st.dataframe 里报错回退，故先统一转文本
        st.dataframe(pd.DataFrame([{"字段": k, "值": str(v)}
                                   for k, v in row.items()]),
                     hide_index=True)
        st.caption(f"门槛方差来源：`{row.get('sr_variance_source')}` = "
                   f"{row.get('sr_variance')}（与 SR̂ 同为逐 bar 量纲；"
                   "写死 1.0 会让日线 DSR 恒等于 0）")

    st.subheader("② Walk-forward 分折")
    st.caption(f"切分：{WALK_FORWARD['n_splits']} 段滚动、训练比例 "
               f"{WALK_FORWARD['train_ratio']}、embargo "
               f"{WALK_FORWARD['embargo_bars']} bar（只留隔离带、不做 purge）。"
               "折与折的测试段互不相交，因此这里**不出 PBO**——CSCV 的前提是 N 个"
               "配置在同一条时间轴上竞争，折不满足（旧实现因此恒得 0.9444）。")
    if wf_df is None or wf_df.empty:
        st.info("缺 walk_forward 产物（本轮跑批未开该阶段，或尚未跑过）")
    else:
        keep = [c for c in ("fold", "回测区间", "总收益率", "夏普比率",
                            "最大回撤", "测试段bar数", "n_trials",
                            "运气门槛年化", "DSR", "DSR通过",
                            "折内因子数", "折内因子来源", "平均持仓只数",
                            "交易次数") if c in wf_df.columns]
        st.dataframe(wf_df[keep], hide_index=True)
        # 汇总一律从折表现算，不再读第二份落盘文件：折表是 walk-forward 唯一的
        # 产物，跨折统计再单独存一份 JSON 就成了同一口径的两个出处（会漂）
        sh = dsr_ok = None
        if "夏普比率" in wf_df.columns:
            sh = pd.to_numeric(wf_df["夏普比率"], errors="coerce")
            thr = pd.to_numeric(wf_df.get("运气门槛年化"), errors="coerce")
            fig = go.Figure()
            fig.add_trace(go.Bar(x=wf_df["fold"], y=sh, name="样本外夏普（年化）",
                                 marker_color="steelblue"))
            if thr is not None and thr.notna().any():
                fig.add_trace(go.Scatter(x=wf_df["fold"], y=thr, name="过 DSR 所需年化夏普",
                                         mode="lines+markers",
                                         line=dict(color="red", dash="dot")))
            fig.update_layout(title="每折样本外夏普 vs 该折的运气门槛",
                              xaxis_title="折", height=340)
            st.plotly_chart(fig)
        if "测试段bar数" in wf_df.columns and sh is not None:
            if "DSR通过" in wf_df.columns:
                dsr_ok = wf_df["DSR通过"].astype(str).str.lower().isin(
                    ("true", "1"))
            n_folds = len(wf_df)
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("验证段合计 bar",
                      int(pd.to_numeric(wf_df["测试段bar数"]).sum()),
                      help="三段不重叠测试区的长度之和：DSR 的功效（能否把门槛压低）"
                           "由它决定，而不是单折长度")
            s2.metric("正夏普折数", f"{int((sh > 0).sum())}/{n_folds}")
            s3.metric("夏普跨折 std",
                      f"{float(sh.std(ddof=1)):.3f}" if n_folds > 1 else "—",
                      help="各折是互不相交的时间段，跨折离散度就是「这条结论换段"
                           "时间还站不站得住」的读数")
            s4.metric("DSR 通过折数",
                      f"{int(dsr_ok.sum())}/{n_folds}" if dsr_ok is not None else "—")
        else:
            st.caption("（这份折表缺 `测试段bar数`/`夏普比率`，算不出跨折汇总）")

    st.subheader("③ 策略级 PBO（CSCV）")
    if not pbo_res:
        st.info("缺 pbo_result.json")
    else:
        fam = pbo_res.get("config_family")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("PBO", f"{float(pbo_res.get('pbo', 1)):.4f}",
                  delta="过线（≤0.5）" if pbo_res.get("passed") else "未过线",
                  delta_color="normal" if pbo_res.get("passed") else "off",
                  help="样本外冠军相对排名 logit ≤ 0 的概率：在配置族里挑过参数之后，"
                       "结论翻转的概率")
        p2.metric("配置族", f"{fam}×{pbo_res.get('n_configs')}",
                  delta=None if fam == "real" else "只测扰动敏感度，不含参数选择偏差",
                  delta_color="off" if fam != "real" else "normal")
        p3.metric("组合数", pbo_res.get("n_combinations", "—"))
        p4.metric("logits 均值 / std",
                  f"{float(pbo_res.get('logits_mean', 0)):.2f} / "
                  f"{float(pbo_res.get('logits_std', 0)):.2f}")
        if fam != "real":
            st.warning("本轮 PBO 来自**伪变体族**（风控参数邻域没能全部回测成功，"
                       "或终态因子/净值长度不够）。它只回答「扰动会不会翻结论」，"
                       "不回答「挑参数是否过拟合」，别按真族的读法读它。")
        lg = pbo_res.get("logits")
        if isinstance(lg, list) and lg:
            st.plotly_chart(go.Figure(go.Histogram(
                x=lg, nbinsx=30, marker_color="steelblue")).update_layout(
                title="各配置组合的 logit ω̂ 分布（≤0 即样本外丢了冠军）",
                height=300))

with tabs[3]:
    @st.cache_data(ttl=600, show_spinner="正在读份额/净值面板并算规模…")
    def risk_bundle():
        import etf_admission as EA
        import fetch_etf_risk_panel as RP
        pool = EA.load_pool(verbose=False)
        m = EA.build_matrices(pool)
        return {"aum": EA.load_scale_matrix(m, verbose=False),
                "streak": EA.clearing_streak(m),
                "prem": EA.premium_matrix(m),
                "nav": RP.load_nav_matrix(),
                "in_universe": EA.universe_mask(m),
                "consts": {"MIN_SCALE": EA.MIN_SCALE, "CLEAR_LINE": EA.CLEAR_LINE,
                           "CLEAR_DAYS": EA.CLEAR_DAYS,
                           "FFILL": EA.SCALE_FFILL, "MIN_CS": EA.MIN_CS}}

    try:
        bundle = risk_bundle()
    except Exception as e:
        bundle = None
        st.warning(f"风险面板读不出来: {type(e).__name__}: {e}")
    if bundle is None:
        st.info("风险面板读不出来：先跑 `python data/fetch_etf_risk_panel.py --daily`")
    else:
        K = bundle["consts"]
        aum, streak = bundle["aum"], bundle["streak"]
        held_codes = [] if signals_df is None or "code" not in (
            signals_df.columns) else sorted(
            _holdings(signals_df)[
                _holdings(signals_df)[signals_df.columns[0]] ==
                _holdings(signals_df)[signals_df.columns[0]].max()]["code"].unique())

        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("规模闸（在买入侧）", f"{K['MIN_SCALE'] / 1e8:.0f} 亿",
                  help="资产规模 A = 交易所份额 × 单位净值（缺净值的过去日期用前复权"
                       "收盘代理）。#17 三档实测后定为 5 亿")
        r2.metric("清盘线条款", f"{K['CLEAR_LINE'] / 1e8:.1f} 亿 × {K['CLEAR_DAYS']} 日",
                  help="连续 CLEAR_DAYS 个交易日 A < 5000 万 ⇒ 触发合同里的清盘/合并程序")
        r3.metric("已越清盘线", int((streak.iloc[-1] >= K["CLEAR_DAYS"]).sum()),
                  delta=None, help="按面板最后一格计；沪市份额是月末快照，"
                                   "连续天数在缺口内按 ffill 限 "
                                   f"{K['FFILL']} 格续着数")
        r4.metric("池内规模中位",
                  f"{aum.iloc[-1].median() / 1e8:.1f} 亿"
                  if aum.iloc[-1].notna().any() else "N/A")
        r5.metric("末日份额可读",
                  f"{aum.iloc[-1].notna().sum()}/{aum.shape[1]} 只")
        st.caption("每只标的各取自己「最近可读到」的那一天：日线镜像两侧已由"
                   "`data/update_etf_daily.py` 拉平，但份额披露节奏仍按市场不同"
                   "（沪市只有月末快照），取同一行会得到一堆假 0。这是体检报告，"
                   "**不是排序因子**，不进任何判据。")

        def _last_valid(col):
            col = col.dropna()
            return col.iloc[-1] if len(col) else np.nan

        per = pd.DataFrame({
            "规模(亿)": (aum.apply(_last_valid) / 1e8).round(2),
            "清盘连续天数": streak.where(aum.notna()).ffill(
                limit=K["FFILL"]).apply(_last_valid),
        })
        per["在最新持仓"] = per.index.isin(held_codes)
        st.subheader("在仓标的的风险体检")
        show = per[per["在最新持仓"]].sort_values("规模(亿)") if held_codes else per
        st.dataframe(show.drop(columns=["在最新持仓"]).sort_values("规模(亿)")
                     .head(30))
        st.subheader("全池规模分布（亿，末日可读）")
        vals = (aum.apply(_last_valid) / 1e8).dropna()
        st.plotly_chart(go.Figure(go.Histogram(
            x=vals, nbinsx=40, marker_color="steelblue")).update_layout(
            title=f"{vals.size} 只｜低于 {K['MIN_SCALE'] / 1e8:.0f} 亿的有 "
                  f"{int((vals < K['MIN_SCALE'] / 1e8).sum())} 只",
            height=320))

        st.subheader("折溢价")
        prem = bundle["prem"]
        pv = prem.apply(_last_valid).dropna()
        if pv.empty:
            st.info("净值长表还没攒够：折溢价 = 收盘/净值 − 1，分母只能逐日向前攒"
                    "（`fetch_etf_risk_panel.py --daily`）")
        else:
            c1, c2, c3 = st.columns(3)
            nav_last = bundle["nav"].apply(
                lambda s: s.last_valid_index())          # 净值自己攒到的那一天
            fresh = sum(1 for c in pv.index
                        if nav_last.get(c) is not None
                        and str(nav_last[c])[:10] == str(prem[c].last_valid_index())[:10])
            c1.metric("可读只数 / 其中同日净值", f"{pv.size} / {fresh}",
                      help="净值长表只有采到过的那些天有行；差额那几只的这一格是"
                           " ffill 顶上来的一天，里面混着当日涨跌")
            c2.metric("中位折溢价", _pct(float(pv.median()), 2))
            c3.metric("最大绝对偏离", f"{float(pv.abs().max()) * 100:.2f}%")
            st.caption("09-24 复核后这是**全池口径**（不是 #14 那版\"只剩 13 只 "
                       "513 段\"——那是镜像比净值晚一天的假稀疏）。中位贴着 0 是"
                       "应该的；尾部全是纳斯达克 QDII 的额度溢价，且 QDII 净值按"
                       "境外**前一交易日**收盘算 ⇒ 溢价与时差错位分不开，"
                       "只当体检看，不进判据。")
            st.dataframe(pv.sort_values(key=abs, ascending=False).head(20)
                         .to_frame("折溢价").assign(
                             折溢价=lambda d: d["折溢价"].map(
                                 lambda v: _pct(float(v), 2))))
        with st.expander("📥 三张长表攒到哪了（只读）"):
            rows = []
            try:
                from fetch_etf_risk_panel import read_long
                from config import RISK_NAV_THS, RISK_SHARES_SSE, RISK_SHARES_SZSE
                for label, p in (("份额·沪", RISK_SHARES_SSE),
                                 ("份额·深", RISK_SHARES_SZSE),
                                 ("单位净值", RISK_NAV_THS)):
                    df = read_long(p)
                    if df.empty:
                        continue
                    df["date"] = pd.to_datetime(df["date"])
                    rows.append({"长表": label, "行数": len(df),
                                 "只数": df["code"].nunique(),
                                 "首日": str(df["date"].min())[:10],
                                 "末日": str(df["date"].max())[:10]})
            except Exception as e:
                st.write(f"读不到长表: {type(e).__name__}: {e}")
            st.dataframe(pd.DataFrame(rows),
                         hide_index=True)

with tabs[4]:
    st.subheader("终态因子与权重")
    if params:
        w = params.get("factor_weights") or {}
        if w:
            fig = go.Figure(go.Bar(
                x=list(w), y=list(w.values()), marker_color="steelblue"))
            fig.update_layout(title="在仓因子的 ICIR 权重", height=300)
            st.plotly_chart(fig)
        st.json({"n_factors": params.get("n_factors"),
                 "risk_params": params.get("risk_params")}, expanded=False)
    attr = load_csv(f"{RESULTS_DIR}/factor_attribution.csv")
    if attr is not None and not attr.empty:
        col = "shapley" if "shapley" in attr.columns else "contribution"
        if col in attr.columns:
            top = attr.head(15)
            st.plotly_chart(go.Figure(go.Bar(
                x=top["factor"].map(cn_name), y=top[col],
                customdata=top["factor"],
                hovertemplate="%{x}<br>原名: %{customdata}<br>%{y:+.4f}<extra></extra>",
                marker_color=["green" if v > 0 else "red"
                              for v in top[col]])).update_layout(
                title="因子贡献", height=340))
    sim = load_csv(f"{RESULTS_DIR}/factor_similarity.csv")
    if sim is not None and not sim.empty:
        sim = sim.set_index(sim.columns[0])
        st.subheader("因子相关性（终态集）")
        st.plotly_chart(go.Figure(go.Heatmap(
            z=sim.values, x=list(sim.columns), y=list(sim.index),
            zmin=-1, zmax=1, colorscale="RdBu", zmid=0)).update_layout(
            height=360))
    st.subheader("衰减与历史")
    decay = load_csv(f"{RESULTS_DIR}/factor_decay.csv")
    pred = load_csv(f"{RESULTS_DIR}/factor_decay_predict.csv")
    for df in (decay, pred):
        if df is not None and "factor" in df.columns:
            df.insert(1, "中文名", df["factor"].map(cn_name))
    if decay is not None and not decay.empty:
        st.dataframe(decay)
    if pred is not None and not pred.empty:
        st.dataframe(pred)
    coords = load_csv(f"{RESULTS_DIR}/factor_clusters.csv")
    if coords is not None and not coords.empty:
        fig = go.Figure()
        for cid in sorted(coords["cluster"].unique()):
            sub = coords[coords["cluster"] == cid]
            fig.add_trace(go.Scatter(
                x=sub["x"], y=sub["y"], mode="markers+text",
                marker=dict(size=10,
                            color=f"hsl({(cid * 45) % 360}, 70%, 50%)"),
                text=sub["name"].map(cn_name), textposition="top center",
                name=f"簇{cid}"))
        fig.update_layout(title="因子空间", height=480)
        st.plotly_chart(fig)
    hist = load_csv(f"{RESULTS_DIR}/factor_git_history.csv")
    if hist is not None and not hist.empty:
        st.dataframe(hist.tail(60))
    lib = load_json(FACTOR_LIBRARY.get("index_path"))
    if lib:
        st.caption(f"因子库 {len(lib)} 条，其中活跃 "
                   f"{sum(1 for v in lib.values() if v.get('status') == 'active')} 条")

with tabs[5]:
    st.subheader("策略参数与生命周期")
    if params:
        st.json(params.get("risk_params") or {}, expanded=True)
    multi = load_csv(f"{RESULTS_DIR}/multi_summary.csv")
    if multi is not None and not multi.empty:
        st.dataframe(multi)
    life = load_csv(f"{RESULTS_DIR}/multi_lifecycle_state.csv")
    if life is not None and not life.empty:
        st.dataframe(life)
    st.subheader("重挖触发器状态（持久账本）")
    st.caption(f"判据在 `common/src/optimizer/trigger_logic.py`：DSR 与 PBO "
               f"**同时**恶化（mode={TRIGGER_LOGIC['mode']}）才记一轮，连续 "
               f"{TRIGGER_LOGIC['consecutive_rounds']} 轮才触发重挖；指标缺失时不"
               f"计入。本页只读，不改这个文件。")
    st.json(load_json(os.path.join(CACHE_DIR, "remining_state.json")) or {},
            expanded=False)
    rem_log = load_csv(f"{RESULTS_DIR}/auto_remining_log.csv")
    if rem_log is not None and not rem_log.empty:
        st.dataframe(rem_log.tail(30))
    st.subheader("本次跑批的环境回执")
    c1, c2 = st.columns(2)
    c1.json({"结果目录": RESULTS_DIR, "风险面板": RISK_DIR,
             "频率": FREQ, "池缓存": CACHE_DIR}, expanded=False)
    newest = max((os.path.getmtime(p) for p in glob.glob(
        f"{RESULTS_DIR}/*_{FREQ}.csv")), default=None)
    c2.metric("最新产物落盘时间",
              datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
              if newest else "—")

with tabs[6]:
    st.subheader("指标口径")
    st.markdown("| 指标 | 中文名 | 含义与参考口径 |\n|---|---|---|\n"
                + "\n".join(f"| `{k}` | {l} | {d.replace('|', chr(92) + '|')} |"
                            for k, l, d in METRIC_GLOSSARY))
    st.subheader("判据与阈值（现读 config / etf_admission，本页不复制数值）")
    st.markdown(f"""
- **DSR**（Bailey & López de Prado 2014）：
  `DSR = Φ((SR̂ − SR* − SR_b)·√(T−1) / √(1 − γ₁·SR̂ + (γ₂−1)/4·SR̂²))`，
  门槛 `SR* = √Var[SR̂]·[(1−γ)·z(1−1/N) + γ·z(1−1/(N·e))]`，
  `Var[SR̂]` 取 Lo(2002) 的 `(1 + 0.5·SR̂²)/T`。**SR̂ 与 Var[SR̂] 必须同为逐 bar
  量纲**——写死 `sr_variance=1.0` 时日线 DSR 恒为 0（#15 之前的失效原因）。
  N 取持久试验账本 `trial_counter.json`，跑完不归零。门槛
  `DSR_THRESHOLD={0.95}`，配置里 `n_trials={DSR.get('n_trials')}`
  （`None` = 用账本实际值）。
- **Walk-forward**：{WALK_FORWARD['n_splits']} 段滚动、训练比例
  `train_ratio={WALK_FORWARD['train_ratio']}`、
  `embargo={WALK_FORWARD['embargo_bars']}` bar。折内只喂训练段挖因子，
  折级**不出 PBO**（测试段互不相交会让 CSCV 退化成日历归属，恒 0.9444）。
- **策略级 PBO**（López de Prado 2017 CSCV）：终态因子集 × OFAT 风控邻域
  （`1 + Σᵢ(|空间ᵢ|−1)` 档）逐档全样本回测，比较样本内冠军在样本外的相对排名，
  `PBO = P(logit ω̂ ≤ 0)`，门槛 `{STRATEGY_PBO.get('pbo_threshold')}`。
  族退化（档数 <3、前置条件不满足）时页面顶部会显形为 `pseudo`。
- **截面组合**：`top_k={PORTFOLIO.get('top_k')}`、
  `max_turnover={PORTFOLIO.get('max_turnover')}`、
  `lot_size={PORTFOLIO.get('lot_size')}`；信号是长表，
  `code=""` 的行是空仓哨兵。
- **可投域闸门**（`etf_admission.universe_mask`，四道 AND）：当日有行情、
  成立满 `min_listed`、20 日均成交额过 `min_amount` 且连续 `low_run_days` 日不塌、
  规模 `A ≥ MIN_SCALE`。基准与回放共用这一处定义。
- **收益载荷护栏** `RET_LIMIT`：日线镜像里份额折算/净值回补会造出 ±400% 这类
  假 bar，进任何统计前先夹掉；台阶是永久的，所以护栏在读取侧而不是清洗侧。
""")
    st.caption("本页不产生文件、不改判据。所有数字来自 `data/results/` 与 "
               "`data/risk/`，口径以 `src/` 里的实现为准。")
