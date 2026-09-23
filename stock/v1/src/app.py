# -*- coding: utf-8 -*-
"""股票线看板：`streamlit run app.py`（在 stock/v1/src 下起）。

只读 CSV，不重算任何东西：所有数字都由四个入口产出——
  run_ashare_daily_signal.py → data/results/daily_signal/signal_YYYYMMDD.csv
  run_ashare_position.py     → data/live/{positions,account}.csv
  run_ashare_factor_eval.py / run_ashare_portfolio_eval.py /
  run_ashare_redundancy_check.py → data/results/ashare_*.csv
所以看板与命令行永远一致；看板上有异议就是入口有 bug，而不是这里算了两套。

本线**没有买入信号**，这是设计而非缺功能：量能族只做剔除（多头腿扣费后不剩
超额，见 ashare_portfolio_eval.csv），组合层也没验过任何买入排序。所以「保留池」
和「持仓里被剔除的票」都不构成买卖指令，页面上每一处都会这样标注。
"""

import glob
import json
import os

import _bootstrap  # noqa: F401  必须先于项目模块导入

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from config import (ASHARE_ACCOUNT_OUT, ASHARE_FILLS_CSV,
                    ASHARE_PORT_LIMIT_UP, ASHARE_PORT_MIN_AMOUNT,
                    ASHARE_PORT_MIN_LISTED, ASHARE_POSITION_OUT,
                    ASHARE_SCREEN_QUANTILE, ASHARE_SIGNAL_DIR, RESULTS_DIR)

st.set_page_config(page_title="A 股量化看板", layout="wide")
st.title("📊 A 股量化系统看板")
st.caption("研究 → 日频剔除名单 → 人工下单 → 手工录入 → 持仓与盈亏。"
           "**全系统只出「哪些不该买」，不出买入建议。**")


@st.cache_data(ttl=30)
def load_csv(p):
    return pd.read_csv(p) if p and os.path.exists(p) else None


@st.cache_data(ttl=30)
def load_json(p):
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def signal_files():
    return sorted(glob.glob(os.path.join(ASHARE_SIGNAL_DIR, "signal_*.csv")))


def num_fmt(df, digits=None):
    """浮点列统一小数位：Streamlit 默认把 float32 原样吐出 11.710000038146973 这种串

    digits 传 {列名: 格式}，其余浮点列走 2 位。分位类列（0~1）给 3 位才有可读精度。
    """
    digits = digits or {}
    return {c: st.column_config.NumberColumn(c, format=digits.get(c, "%.2f"))
            for c in df.columns
            if pd.api.types.is_float_dtype(df[c])}


account = load_csv(ASHARE_ACCOUNT_OUT)
positions = load_csv(ASHARE_POSITION_OUT)
sfiles = signal_files()

# ---------- 顶部指标条 ----------
c = st.columns(6)
if account is not None and len(account):
    a = account.iloc[0].to_dict()
    c[0].metric("总资产", f"{a.get('总资产', 0):,.0f}",
                help="现金 + 持仓市值。未录「入金」时现金是买入占款，此数无意义")
    c[1].metric("持仓市值", f"{a.get('持仓市值', 0):,.0f}")
    c[2].metric("现金", f"{a.get('现金', 0):,.0f}")
    c[3].metric("浮动盈亏", f"{a.get('浮动盈亏', 0):+,.0f}")
    c[4].metric("已实现盈亏", f"{a.get('已实现盈亏', 0):+,.0f}")
    c[5].metric("累计费用", f"{a.get('累计费用', 0):,.0f}",
                help="含按标准费率补算的部分，见账户 CSV 的补算行数")
else:
    st.warning(f"未找到持仓账本 {ASHARE_ACCOUNT_OUT}——先跑 "
               f"`python run_ashare_position.py` 生成（成交录在 {ASHARE_FILLS_CSV}）")

if sfiles:
    latest = pd.read_csv(sfiles[-1])
    cc = st.columns(4)
    cc[0].metric("信号日", os.path.basename(sfiles[-1])[7:15])
    cc[1].metric("可投池", f"{len(latest):,}")
    cc[2].metric("被量能族剔除", f"{int((~latest['keep']).sum()):,}",
                 delta=f"占比 {(~latest['keep']).mean():.1%}", delta_color="inverse")
    cc[3].metric("保留", f"{int(latest['keep'].sum()):,}")
else:
    st.info("还没有日频信号名单——先跑 `python run_ashare_daily_signal.py`")

st.divider()

tabs = st.tabs(["💼 持仓", "🚫 今日剔除名单", "🧬 因子库",
                "📐 截面评估", "📉 组合层验证", "🔁 准入判重", "📖 口径"])

# ---------- 持仓 ----------
with tabs[0]:
    if positions is None or not len(positions):
        st.info("空仓，或还没跑 run_ashare_position.py")
    else:
        if "今日信号" in positions.columns:
            hit = positions[positions["今日信号"].astype(str).str.startswith("剔除")]
            if len(hit):
                st.error(f"持仓中有 {len(hit)} 只当日触发量能剔除信号"
                         f"（{('、'.join(hit['代码']))}）。"
                         "**这不是卖出指令**：剔除规则只在多头侧验证过"
                         "（买不进 = 不买），没有验证过「踢掉它能否改善已有持仓」。"
                         "要不要减、减多少，是人工判断。")
        st.dataframe(positions, use_container_width=True, hide_index=True,
                     column_config=num_fmt(positions))
        fig = go.Figure(go.Bar(
            x=positions["代码"], y=positions["市值"],
            text=[f"{v / max(positions['市值'].sum(), 1):.1%}" for v in positions["市值"]],
            textposition="outside",
            marker_color=["crimson" if str(s).startswith("剔除") else "steelblue"
                          for s in positions.get("今日信号", pd.Series([""] * len(positions)))]))
        fig.update_layout(title="持仓市值分布（红=当日触发剔除信号，仅提示非指令）",
                          height=380)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"成交录入文件：`{ASHARE_FILLS_CSV}`"
                   f"　估值口径：面板最后一日的**盘面真实价**（复权价 ÷ factor），"
                   f"与券商行情一致；分红需手工记为「入金」，否则长持分红股收益会被算少。")

# ---------- 今日剔除名单 ----------
with tabs[1]:
    if not sfiles:
        st.info("先跑 run_ashare_daily_signal.py")
    else:
        dates = [os.path.basename(f)[7:15] for f in sfiles]
        fmt = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}"
        pick = st.selectbox("信号日", [fmt(x) for x in dates][::-1])
        d = pd.read_csv(sfiles[dates.index(pick.replace("-", ""))])
        rules = [x for x in d.columns if x not in
                 ("code", "close", "amount20_yi", "excluded_by", "keep")]
        st.markdown(
            f"阈值：每条量能构造在**当日可投池内**的截面分位 ≥ "
            f"{ASHARE_SCREEN_QUANTILE:.0%} 即剔除，四条取并集。"
            f"容量闸门为 20 日均成交额 ≥ {ASHARE_PORT_MIN_AMOUNT:.0e} 元。")
        k = st.columns(3)
        for i, rn in enumerate(rules):
            fired = d[rn] >= ASHARE_SCREEN_QUANTILE
            k[i % 3].metric(rn, f"{int(fired.sum()):,}",
                            delta=f"命中组均额 {d.loc[fired, 'amount20_yi'].median():.2f} 亿",
                            delta_color="off")
        st.dataframe(
            d[~d["keep"]].sort_values("amount20_yi", ascending=False),
            use_container_width=True, hide_index=True,
            column_config=num_fmt(d, {"close": "%.2f", "amount20_yi": "%.2f",
                                      **{r: "%.3f" for r in rules}}))
        st.caption("上表是**剔除**名单（不该买）。保留池不构成买入排序：多头腿没过组合层，"
                   "任何按因子排出来的顺序都不是建议。")
        st.download_button("下载本日完整名单 CSV", d.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"signal_{pick}.csv")

# ---------- 因子库 ----------
with tabs[2]:
    lib = load_json(os.path.join(RESULTS_DIR, "rdagent_output", "factors.json"))
    if not lib:
        st.info("因子库为空（rdagent_output/factors.json）")
    else:
        # 字段就是 factors.json 里有的那四个 IC 指标，不虚构入库时间/来源之类
        df = pd.DataFrame([{k: it.get(k) for k in
                            ("name", "expr", "formulation",
                             "mean_ic", "icir", "rank_ic", "rank_icir")}
                           for it in lib])
        st.metric("在库因子", len(df))
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config=num_fmt(df, {c: "%.4f" for c in df.columns
                                                if c.endswith("_ic") or c.endswith("icir")}))
        st.caption("因子库由 RD-Agent(Q) 循环经准入链（截面评估 → 组合层 → 判重）后写入，"
                   "`[factor-lib]` 自动提交是正常流水线噪声。这里的 IC 是驱动沙箱里"
                   "**模型预测**的 IC，不是 A 股全市场截面 IC（看下一张表）。")

# ---------- 截面评估 ----------
with tabs[3]:
    ev = load_csv(os.path.join(RESULTS_DIR, "ashare_factor_eval.csv"))
    if ev is None:
        st.info("先跑 run_ashare_factor_eval.py")
    else:
        ic_col = next((x for x in ("cs_rank_ic_mean", "cs_ic_mean", "ts_ic_mean")
                       if x in ev.columns), None)
        st.dataframe(ev, use_container_width=True, hide_index=True,
                     column_config=num_fmt(ev, {c: "%.4f" for c in ev.columns
                                                if "ic" in c.lower()}))
        if ic_col:
            name_col = "name" if "name" in ev.columns else ev.columns[0]
            top = ev.dropna(subset=[ic_col]).sort_values(ic_col, key=abs, ascending=False)
            fig = go.Figure(go.Bar(
                x=top[ic_col], y=top[name_col].astype(str), orientation="h",
                marker_color=["teal" if v > 0 else "indianred" for v in top[ic_col]]))
            fig.update_layout(title=f"{ic_col} 排序（|IC| 降序）", height=560)
            st.plotly_chart(fig, use_container_width=True)
        st.caption("IC 是**全市场截面**的未来收益相关，与沙箱 running 步的模型预测 IC "
                   "不是一回事，不能混用为准入依据。本表与组合层共用同一道收益护栏"
                   "（`ashare_screen.guard_ret`）：护栏前后 21 个因子的 `cs_rank_ic_mean` 位移"
                   " ≤7e-9、`cs_ic_mean` ≤6.1e-5、名次零变化 —— 假台阶脏在**日收益累加**上"
                   "（组合层最大动 1.9pp），摊进 4057 天的 IC 均值里就没了，"
                   "所以 IC 不能当数据质量的体检表。")

# ---------- 组合层验证 ----------
with tabs[4]:
    p = load_csv(os.path.join(RESULTS_DIR, "ashare_portfolio_eval.csv"))
    if p is None:
        st.info("先跑 run_ashare_portfolio_eval.py")
    else:
        show = [x for x in ("signal", "kind", "top_n", "universe", "ann_return",
                            "ann_return_gross", "ann_vol", "sharpe", "max_drawdown",
                            "one_way_turnover", "avg_amount_20d", "avg_blocked_limit_up",
                            "excess_univ_ew_ann", "excess_univ_ew_ir",
                            "excess_sh000300_ann", "excl_worst_ann",
                            "excl_worst_vs_pool_ann", "q5_ann", "expr")
                    if x in p.columns]
        st.dataframe(p[show], use_container_width=True, hide_index=True,
                     column_config=num_fmt(p[show]))
        # 超额对「同一段市场的等权池」取，而不是对指数：闸门剔掉了买不到的票，
        # 基准也必须是闸门之内那批票的等权，否则超额里混着「别人买不到」的部分
        ann_col = next((x for x in ("excess_univ_ew_ann", "excess_sh000300_ann",
                                    "ann_return") if x in p.columns), None)
        fig = go.Figure()
        for sig, g in p.groupby("signal"):
            fig.add_trace(go.Scatter(x=g["top_n"], y=g[ann_col], mode="lines+markers",
                                     name=str(sig)))
        fig.update_layout(title=f"{ann_col} vs 持仓数", height=420,
                          xaxis_title="top_n", yaxis_title=ann_col)
        st.plotly_chart(fig, use_container_width=True)
        if "excl_worst_vs_pool_ann" in p.columns:
            q = p.drop_duplicates("signal")[["signal", "excl_worst_ann",
                                             "excl_worst_vs_pool_ann", "q5_ann"]].dropna()
            if len(q):
                st.subheader("「剔除最差五分位」这条用法单独算")
                st.dataframe(q, use_container_width=True, hide_index=True,
                             column_config=num_fmt(q))
        yr = load_csv(os.path.join(RESULTS_DIR, "ashare_portfolio_eval_yearly.csv"))
        if yr is not None:
            st.subheader("分年度")
            st.dataframe(yr, use_container_width=True, hide_index=True,
                         column_config=num_fmt(yr))
        st.caption("多头腿结论（09-23，**已过复权收益假台阶护栏**，见「数据与口径」）："
                   "做多低量能侧的超额与「低价股」对照同量级（+5% 上下）而换手是其 3~12 倍，"
                   "动量/比值两条更是 -13%~-21%，所以那不是量能自带的信息；短窗那两条在 top50 "
                   "加完护栏直接翻负（STD5 -1.13% / SMA5 -0.58%，护栏前是 +0.15% / +1.01%）。"
                   "本线只用它的**剔除**侧，而剔除增益 0.0439→0.0438 纹丝不动 —— "
                   "载荷结论对数据口径不敏感，多头腿的数字才敏感。")

# ---------- 准入判重 ----------
with tabs[5]:
    r = load_csv(os.path.join(RESULTS_DIR, "ashare_redundancy_check.csv"))
    if r is None:
        st.info("先跑 run_ashare_redundancy_check.py")
    else:
        st.dataframe(r, use_container_width=True, hide_index=True,
                     column_config=num_fmt(r, {c: "%.4f" for c in r.columns
                                              if "pearson" in c or "spearman" in c}))
        st.markdown(
            "主判据 = **逐日截面原始值 Pearson 均值**，对齐 rdagent "
            "`deduplicate_new_factors` 的 0.99 硬门槛：≥0.99 会被判重复并 "
            "`Skip loop`，整轮机时白烧。命名闭集为 `<N>-day <OP> of <Column>`，"
            "`<OP> ∈ SMA|STD|MAX|MIN|VWAP|MOM`、`<Column> ∈ Price|Volume`，"
            "所以 open/high/low 根本不可达。")

# ---------- 口径 ----------
with tabs[6]:
    st.markdown(f"""
### 数据与口径

| 项 | 取值 | 为什么 |
|---|---|---|
| 源数据 | `daily_pv.h5`（RD-Agent 循环实现因子时读的同一份） | 研究与实盘同源，结论才可对照 |
| 面板价 | **复权价**；盘面价 = 复权价 ÷ `$factor` | 收益序列必须无除息跳空；报价列给人看，用盘面价 |
| 成交额 | 盘面价 × `$volume` × 100（`$volume` 单位是**手**，主板单票日成交额落在真实量级：茅台 127 亿 / 工行 31 亿 / 浦发 7 亿） | 容量闸门问的是「真钱买得到吗」 |
| 流动性闸门 | 20 日均成交额 ≥ {ASHARE_PORT_MIN_AMOUNT:.0e} 元 | 09-23 修：原先误用复权成交额，09-22 当日误剔 261/5518 只（全是高分红的老股票，方向单一不是噪声） |
| ⚠️ 已知口径缺陷 | 本面板 68/30 段 `$factor` 失真：503 只 盘面价/复权价 > 50（寒武纪 142 倍、中芯 83 倍），致 09-22 全市场成交额算成 **53.7 万亿**，约真实市场 20 倍 | 所以上面两行只在**本数据集内**相对成立，别把成交额绝对值当行情引用；量能族四条构造只用 `$volume`，不含价格与 `$factor`，**不受此失真影响** |
| 收益护栏 | 复权开盘收益与盘面开盘收益对看：**只有复权侧**超 ±30% 者裁回 ±30%（`RET_LIMIT`） | 全样本 326 个 (票,日) 命中（2023-10-16 一天 64 只，等权日收益被凭空抬 +20pp）。真实除权必在盘面价留同幅缺口，新股首周的真暴涨两套价同时越界，故「只有复权侧越界」= `$factor` 的假台阶而非行情 |
| 次新闸门 | 已有行情 ≥ {ASHARE_PORT_MIN_LISTED} 交易日 | — |
| 涨停闸门 | 次日开盘涨幅 < {ASHARE_PORT_LIMIT_UP:.1%} | 面板最后一天**无法前瞻**，留给人工开盘前确认 |
| 剔除阈值 | 池内截面分位 ≥ {ASHARE_SCREEN_QUANTILE:.0%} | 四条量能构造取并集 |
| 费率 | 佣金万 2.5 双边（最低 5 元）+ 印花税万 5（卖出） | 回测用综合单边 15bp（含滑点万 10） |
| T+1 / 整手 | 买入须为 100 股整数倍；当日买入不可当日卖出 | 账本按日初持仓快照校验 |

### 本看板**不能**告诉你的事

- 买什么：多头腿未过组合层验证，保留池不是买入清单。
- 卖什么：剔除规则验证的是「买不进就不买」，没有验证「踢掉已持有的票能否改善组合」。
- 盘中价：估值用已落库日线，实时行情属于「接实盘数据」那一环，尚未接。
""")
