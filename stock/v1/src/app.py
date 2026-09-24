# -*- coding: utf-8 -*-
"""股票线看板：`streamlit run app.py`（在 stock/v1/src 下起）。

只读 CSV，不重算任何东西：所有数字都由四个入口产出——
  run_ashare_daily_signal.py → data/results/daily_signal/{signal,buy}_YYYYMMDD.csv
                               + meta_YYYYMMDD.json（当日各环节计数）
  run_ashare_position.py     → data/live/{positions,account}.csv
  run_ashare_factor_eval.py / run_ashare_portfolio_eval.py /
  run_ashare_redundancy_check.py → data/results/ashare_*.csv
所以看板与命令行永远一致；看板上有异议就是入口有 bug，而不是这里算了两套。

**本页给的是「剔除名单 + 待买入短名单」两份，且全是收盘后日线口径，不做盘中分析**
（判据来自已落库的 daily_pv.h5，页面上一行盘中价都不读；日更没跑，看到的就还是昨天）。
两份名单的证据强度不一样，页面上分开写清楚：剔除侧是载荷结论（单条最佳 +4.36%/年、
四条并集 +6.46%，`board` 档，扛住四次口径变动）；待买入侧只有一根排序轴（**09-24 换过
轴**，现在是 SMA(Volume,20) 低分侧，样本内 top50/100/200 年化超额 +1.39%/+1.59%/+1.58%，
换手只有旧轴的六成，但全窗口 top100/200 两档归旧轴；与同表「低价股」对照分不开、
且与剔除用的 `level` 是同一个表达式），所以它叫「先人工核这一批」的排队顺序，
不构成收益承诺。
"""

import glob
import json
import os
import re
from datetime import date, datetime

import _bootstrap  # noqa: F401  必须先于项目模块导入

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from config import (ASHARE_ACCOUNT_OUT, ASHARE_BOARD_LIMIT_SINCE, ASHARE_BOARD_LIMIT_UP,
                    ASHARE_BUY_TOP_N,
                    ASHARE_BUY_MIN_HITS,
                    ASHARE_FILLS_CSV, ASHARE_PORT_LIMIT_UP, ASHARE_PORT_MIN_AMOUNT,
                    ASHARE_PORT_MIN_LISTED, ASHARE_POSITION_OUT, ASHARE_ORDER_TOP_N,
                    ASHARE_ORDER_MAX_PER_INDUSTRY, ASHARE_ORDER_MAX_PER_BOARD,
                    ASHARE_LIMIT_NEAR, ASHARE_TRADABLE_GATE,
                    ASHARE_SCREEN_QUANTILE, ASHARE_SIGNAL_DIR,
                    RDAGENT_QLIB_PROVIDER, RESULTS_DIR)
# 口径表里那行「涨停闸吃哪一套阈值」的文字**不让本页自己拼**：回测横幅、日频打印、
# 本页三处都调 ashare_screen.gate_desc()，同一份构造。分开拼迟早出现某一处写的
# 和实跑的不是一档（09-24 真撞过一次：文档说 board、config 里那行重复定义把默认
# 顶成了 flat）。只 import 这一个函数，模块级的规则/装载都不执行。
# 排序轴同理：09-24 换过一次轴（STD(Volume,20) → SMA(Volume,20)），本页那几处
# 「按什么升序取前 50 名」一律写 `{BUY_EXPR}`，不写死表达式，否则下次换轴又要满页找。
from ashare_screen import BUY_EXPR, BUY_NAME, gate_desc

st.set_page_config(page_title="A 股量化看板", layout="wide")
st.title("📊 A 股量化系统看板")
st.caption("研究 → 日频「剔除 + 待买入短名单」→ 人工下单 → 手工录入 → 持仓与盈亏。"
           "**收盘后日线口径，不做盘中分析**；待买入那一份是排队顺序不是收益承诺，"
           "详见「🛒 待买入名单」页脚的证据说明。")


@st.cache_data(ttl=30)
def load_csv(p):
    return pd.read_csv(p) if p and os.path.exists(p) else None


@st.cache_data(ttl=30)
def load_json(p):
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def archived_gate(fname="ashare_portfolio_exclusion.csv"):
    # 口径不能靠人记：09-24 撞过「文档写 board、进程实际跑 flat」，所以档位从文件里读
    p = os.path.join(RESULTS_DIR, fname)
    if not os.path.exists(p):
        return "（表未生成）"
    d = pd.read_csv(p, nrows=1)
    return str(d["gate"].iloc[0]) if "gate" in d.columns else "flat（旧表无 gate 列）"


ARCH_GATE = archived_gate()


def signal_files():
    return sorted(glob.glob(os.path.join(ASHARE_SIGNAL_DIR, "signal_*.csv")))


def buy_files():
    return sorted(glob.glob(os.path.join(ASHARE_SIGNAL_DIR, "buy_*.csv")))


def order_for(yyyymmdd):
    """下单名单（B 路，观察名单 → 仓位表）；老日子没这份就 None，页面照常走 50 只"""
    p = os.path.join(ASHARE_SIGNAL_DIR, f"order_{yyyymmdd}.csv")
    return load_csv(p) if os.path.exists(p) else None


def meta_for(yyyymmdd):
    """入口落的当日计数（漏斗各级剔了几只、涨停闸状态）；老名单没这份就返回 None"""
    return load_json(os.path.join(ASHARE_SIGNAL_DIR, f"meta_{yyyymmdd}.json"))


# 时钟阈值：只为「这份名单还在不在可用窗口里」这句话服务，不参与任何选票判据。
# 09:30 是窗口右端（T+1 一开盘，昨天收盘口径的排队顺序就不再给今天用）；
# 15:00 只用来把「盘后名单刚出」和「盘中看到的还是昨天那份」分开说。
MARKET_OPEN_HM = 9 * 60 + 30
MARKET_CLOSE_HM = 15 * 60


@st.cache_data(ttl=300)
def trade_days_after(d, n=2):
    """qlib 交易日历里 d **之后**的前 n 个交易日 → (那些日子, 日历末格)

    三种结果要分开说，混了就变成误报：
      (None, None)  日历文件读不到 —— 这一栏整个失效；
      ([], 末格)     日历没走过 d（今晚日更还没跑）—— T+1 未知，**不判过期**；
      ([T+1, ...], 末格) 正常。

    判 T+1 必须用交易日历而不是日历天数：周五收盘出的名单周一开盘前用完全正当，
    按自然日算会把它报成「已跨过 T+1」。日历就是面板自己用的那一份
    （`RDAGENT_QLIB_PROVIDER/calendars/day.txt`，日更往里 append），读它不引入
    第二口径，只是把「哪天算今天」交给同一个事实源。
    """
    p = os.path.join(RDAGENT_QLIB_PROVIDER, "calendars", "day.txt")
    try:
        with open(p, encoding="utf-8") as fh:
            days = sorted(x.strip() for x in fh if x.strip())
    except OSError:
        return None, None
    iso = d.isoformat()
    return ([date.fromisoformat(x) for x in days if x > iso][:n],
            date.fromisoformat(days[-1]) if days else None)


def usage_window(sig, now=None):
    """名单的时效：(阶段, 是否已过期, 一句话说明)

        出名单：T 日（signal_date）收盘后   →   用它：T+1 开盘（09:30）前

    跨过 T+1 的 09:30 就判过期（标灰）。T+1 未知时不判 —— 宁可少说，不猜。
    `now` 只为测试留的注入口（跨休市那段判据得能离线枚举），线上调用一律不传。
    """
    now = now or datetime.now()
    today, hm = now.date(), now.hour * 60 + now.minute
    after, cal_end = trade_days_after(sig)
    clock = f"{now:%H:%M}"
    if after is None:
        return ("日历缺席", False,
                f"{clock} 信号日 {sig}　读不到 `{RDAGENT_QLIB_PROVIDER}` 的交易日历，"
                f"这一栏不判过期（面板新鲜度见下一行）")
    if not after:
        return ("待用", False,
                f"{clock} 信号日 {sig} = 日历末格（{cal_end}），今晚日更没跑 ⇒ T+1 未知，"
                f"这一栏不判过期；面板新鲜度见下一行")
    t1 = after[0]
    win = f"窗口 {sig} 收盘 → {t1} 09:30"
    if today < t1:
        if today == sig and hm >= MARKET_CLOSE_HM:
            return ("盘后", False, f"{clock} 名单刚出，{win}　还没到用它的时候")
        if today == sig:
            return ("盘中", False, f"{clock} 这份已在今天 09:30 前定稿，{win}"
                                   f"　今日排队顺序不再变")
        return ("待用", False, f"{clock} 休市日（下一个交易日 {t1}），{win}")
    if today == t1:
        if hm < MARKET_OPEN_HM:
            return ("盘前", False, f"{clock} **正在窗口内**，{win}　"
                                   f"这一份就是今天要人工复核的那批")
        return ("已过期", True, f"{clock} {t1} 已开盘，{win} 已过　"
                                f"这份排队顺序不再给今天用")
    return ("已过期", True, f"{clock} 已跨过 T+1（{t1}），{win} 早过")


def num_fmt(df, digits=None):
    """浮点列统一小数位：Streamlit 默认把 float32 原样吐出 11.710000038146973 这种串

    digits 传 {列名: 格式}，其余浮点列走 2 位。分位类列（0~1）给 3 位才有可读精度。
    """
    digits = digits or {}
    return {c: st.column_config.NumberColumn(c, format=digits.get(c, "%.2f"))
            for c in df.columns
            if pd.api.types.is_float_dtype(df[c])}


def brace_fmt(digits):
    """printf 格式串 → str.format 格式串：`%.2f` → `{:.2f}`、`%.2f%%` → `{:.2f}%`

    过期那一支的表走 `Styler.format`，而它**只认 str.format**——直接把 `%.2f` 递给它是
    无占位符的字面量，整表会原样印出 "%.2f"（这一版实测踩过）。所以数字格式仍只有一份
    （dg），到这里换个写法而已，不是第二套精度。
    """
    out = {}
    for k, v in digits.items():
        s = v.replace("%%", "\x00")
        s = re.sub(r"%[-+ #0]*[\d.]*[a-zA-Z]",
                   lambda m: "{:" + m.group(0)[1:] + "}", s)
        out[k] = s.replace("\x00", "%")
    return out


account = load_csv(ASHARE_ACCOUNT_OUT)
positions = load_csv(ASHARE_POSITION_OUT)
sfiles = signal_files()
bfiles = buy_files()

@st.cache_data(ttl=30)
def fills_status(p):
    """成交流水有几笔 → (笔数 or None, 说明)。

    0 笔是**空仓**，一个真实状态，不是「数据缺失」：页面措辞必须把这两种分开，
    否则用户第一次打开看到的就是一个坏掉的系统。模板里那行写法说明以 # 开头，
    靠 `comment='#'` 丢掉 —— 不丢的话空文件会被读成 1 笔。
    """
    if not (p and os.path.exists(p)):
        return None, f"成交文件不存在：{p}"
    try:
        df = pd.read_csv(p, encoding="utf-8-sig", comment="#",
                         dtype={"代码": str}, keep_default_na=False,
                         na_values=[""])
    except Exception as e:
        return None, f"成交文件读不动：{type(e).__name__}: {e}"
    return int(df.dropna(how="all").shape[0]), ""


n_fills, fills_msg = fills_status(ASHARE_FILLS_CSV)
has_book = account is not None and bool(len(account))
flat = (not has_book) and n_fills == 0        # 空仓：没建账也没录过成交


def ledger_hint():
    """建仓怎么录的引导：路径给绝对值，示例给可直接抄的行。

    空态不能只说「没有数据」——第一笔成交怎么录、录完跑什么，都得在这一页问完，
    否则这个系统对新人是关着的。示例里的代码/价格是占位，不是建议。
    """
    return (
        f"1. 把**真实成交**逐笔追加到 `{ASHARE_FILLS_CSV}`"
        "（这文件只放你的财务数据，不进版本库）：\n"
        "   ```\n"
        "   日期,代码,方向,成交价,数量,费用,备注\n"
        "   2026-09-23,SH600519,入金,,1000000,,期初本金\n"
        "   2026-09-23,SH600519,买入,1253.80,100,,第一笔（费用留空按标准费率补）\n"
        "   2026-09-25,SH600519,卖出,1268.00,100,,止盈\n"
        "   ```\n"
        "   代码 = 面板 instrument（SH/SZ/BJ + 6 位）；成交价用**盘面真实价**（非复权价）；"
        "买入数量必须是 100 的整数倍；**分红记成一行「入金」**，不然长持分红股收益会算少。\n"
        f"2. 在 `stock/v1/src` 下跑 `python run_ashare_position.py`"
        "（一条流水不合法就带行号报错、**整轮不出账**，页面上也就不会看到半对的账）。\n"
        "3. 回到本页，「💼 持仓」会跟着出持仓表、市值分布和账户汇总。\n\n"
        "现金流水没录期初本金时，现金是买入占款、总资产无意义 —— 先把本金记一行。")


# ---------- 顶部指标条 ----------
c = st.columns(6)
if has_book:
    a = account.iloc[0].to_dict()
    c[0].metric("总资产", f"{a.get('总资产', 0):,.0f}",
                help="现金 + 持仓市值。未录「入金」时现金是买入占款，此数无意义")
    c[1].metric("持仓市值", f"{a.get('持仓市值', 0):,.0f}")
    c[2].metric("现金", f"{a.get('现金', 0):,.0f}")
    c[3].metric("浮动盈亏", f"{a.get('浮动盈亏', 0):+,.0f}")
    c[4].metric("已实现盈亏", f"{a.get('已实现盈亏', 0):+,.0f}")
    c[5].metric("累计费用", f"{a.get('累计费用', 0):,.0f}",
                help="含按标准费率补算的部分，见账户 CSV 的补算行数")
elif fills_msg:
    st.warning(fills_msg)
elif flat:
    st.caption("当前**空仓**：成交文件 0 笔流水，所以没有持仓与盈亏可显示 —— "
               "这是状态不是故障。下面这些信号数字与有没有建仓无关，照旧是真实日线口径")
else:
    st.caption(f"有 **{n_fills} 笔成交还没出账**（账户文件不存在）—— "
               "在 `stock/v1/src` 下跑 `python run_ashare_position.py` 后本页会跟着更新")

if sfiles:
    dtag = os.path.basename(sfiles[-1])[7:15]
    latest = pd.read_csv(sfiles[-1])
    b_same = [x for x in bfiles if os.path.basename(x)[4:12] == dtag]
    n_buy = len(pd.read_csv(b_same[-1])) if b_same else 0
    cc = st.columns(5)
    cc[0].metric("信号日", dtag)
    cc[1].metric("可投池", f"{len(latest):,}")
    cc[2].metric("被量能族剔除", f"{int((~latest['keep']).sum()):,}",
                 delta=f"占比 {(~latest['keep']).mean():.1%}", delta_color="inverse")
    cc[3].metric("保留", f"{int(latest['keep'].sum())}")
    cc[4].metric("待买入短名单", f"{n_buy} 只",
                 delta="无（先跑日频入口）" if not n_buy else f"等权 {100 / max(n_buy, 1):.1f}%/只",
                 delta_color="off",
                 help=f"保留池内按「{BUY_NAME}」= `{BUY_EXPR}` 升序取前 "
                      f"{ASHARE_BUY_TOP_N} 名。样本内一根轴，不是收益承诺")
    m = meta_for(dtag)
    sig_d = date(int(dtag[:4]), int(dtag[4:6]), int(dtag[6:]))
    stage, stale, why = usage_window(sig_d)
    # 时效状态条：名单是 T 日收盘口径，只有 T+1 开盘前那一段算「今天要用的那份」。
    # 这一条只说话不裁决——判据（剔谁、排队顺序）全在入口，这里一个字都不重算。
    with st.container(border=True):
        bc, tc = st.columns([1, 8])
        bc.badge(f"⏱ {stage}" + (" · 已过期" if stale else ""),
                 color="red" if stale else "green")
        tc.markdown(why)
    if m and m.get("panel_end"):
        pe = date.fromisoformat(m["panel_end"])
        lag = (date.today() - pe).days
        st.caption(f"面板末格 {m['panel_end']}（距今 {lag} 天）　"
                   + ("✅ 收盘后日更已跑" if lag <= 4 else
                      f"⚠️ 面板已 {lag} 天没更新——先跑 "
                      "`python data/update_qlib_bin_daily.py` 再跑日频入口，"
                      "本页不做盘中分析，看到的仍是这份旧名单"))
else:
    st.info("还没有日频信号名单——先跑 `python run_ashare_daily_signal.py`")

st.divider()

# tab 名跟着时效走：跨过 T+1 开盘就把「已过期」写在门口，别让人点进去才发觉
# 手上这份是昨天的排队顺序（判据不变，变的只是这份还能不能用）
_btag0 = os.path.basename(bfiles[-1])[4:12] if bfiles else None
_b0 = usage_window(date(int(_btag0[:4]), int(_btag0[4:6]), int(_btag0[6:])))[1] if _btag0 else False
tabs = st.tabs(["💼 持仓", "🛒 待买入名单" + ("（已过期）" if _b0 else ""),
                "🚫 今日剔除名单", "🧬 因子库",
                "📐 截面评估", "📉 组合层验证", "🔁 准入判重", "📖 口径"])

# ---------- 持仓 ----------
with tabs[0]:
    if positions is None or not len(positions):
        # 三种空分开写：空仓（真状态）≠ 流水录了没出账 ≠ 账本没生成
        if has_book:
            st.info("已按流水建账，当前**空仓**——现金 "
                    f"{account.iloc[0].get('现金', 0):,.0f}，"
                    "下面这一页要等的就是你的下一笔录入。")
        elif flat:
            st.info(f"**当前空仓**：`{os.path.basename(ASHARE_FILLS_CSV)}` "
                    f"里 0 笔流水（`{ASHARE_FILLS_CSV}`）。\n\n"
                    "空仓期这一页真正能用的是另外两件事：**「🛒 待买入名单」**给今天开盘前"
                    "该人工复核的那一批（排队顺序，不是收益承诺），**「🚫 今日剔除名单」**"
                    "给已经确定不该碰的那批。录了第一笔成交并出账之后，这一页才会长出"
                    "持仓表、市值分布与账户汇总。")
            with st.expander("要建仓时：成交怎么录、录完跑什么"):
                st.markdown(ledger_hint())
        else:
            st.warning(fills_msg or
                       f"有 **{n_fills} 笔**流水但账户文件还不存在 —— 在 `stock/v1/src` "
                       "下跑 `python run_ashare_position.py` 出账（不合法的行会带行号报错、"
                       "整轮不出账）")
            st.markdown(ledger_hint())
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
                   f"　账本读的是：`{ASHARE_POSITION_OUT}`"
                   f"（把这两个路径用 `STOCK_POSITION_OUT`/`STOCK_ACCOUNT_OUT` 指到别处，"
                   f"本页显示的就是那份账 —— 先看这一行，别把演示账当真账）"
                   f"　估值口径：面板最后一日的**盘面真实价**（复权价 ÷ factor），"
                   f"与券商行情一致；分红需手工记为「入金」，否则长持分红股收益会被算少。")

# ---------- 待买入名单 ----------
with tabs[1]:
    if not bfiles:
        st.info("先跑 run_ashare_daily_signal.py（该入口同时出剔除与待买入两份名单）")
    else:
        bd = [os.path.basename(f)[4:12] for f in bfiles]
        fmt = lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}"
        bpick = st.selectbox("信号日（待买入）", [fmt(x) for x in bd][::-1], key="buypick")
        btag = bpick.replace("-", "")
        b = pd.read_csv(bfiles[bd.index(btag)])
        bst_stage, bst_stale, bst_why = usage_window(
            date(int(btag[:4]), int(btag[4:6]), int(btag[6:])))
        if bst_stale:
            # 过期不藏表：名单本身是唯一事实，只是不再给今天用。标灰 + 说清为什么
            st.error(f"**这一版已经不能用在今天头上。**{bst_why}")
        bm = meta_for(btag) or {}
        bst = bm.get("buy", {})
        # 排序轴以**这份名单自己的** meta 为准，不用进程常量：09-24 换过轴，
        # 屏上若还是换轴前那一场日更的名单，拿进程常量标它就是把 A 说成 B
        buy_axis = bst.get("expr") or BUY_EXPR
        if buy_axis != BUY_EXPR:
            # 判据在 09-24 换过轴，而屏上这份名单可能是**换轴前那一场**日更留下的：
            # 那时代码常量与文件身份不一致，按常量标它就是拿今天的判据解释昨天的名单。
            # 与其悄悄用对的那套，不如把不一致本身说出来 —— 名单没重跑，轴就没换
            st.warning(f"**本页名单由 `{buy_axis}` 排序**，与当前生效的排序轴 "
                       f"`{BUY_EXPR}` 不是同一根（09-24 换过轴）。这一版的顺序、入线值与"
                       f"「下单名单」都还是旧轴口径，要拿新轴的名单得重跑 "
                       f"`run_ashare_daily_signal.py`；下面「📖 口径」页写的是**当前判据**，"
                       f"不是本页这份文件的来路。")
        n = len(b)
        k = st.columns(5)
        k[0].metric("名单长度", f"{n} 只",
                    delta=f"请求 top_n = {ASHARE_BUY_TOP_N}"
                          if n < ASHARE_BUY_TOP_N else f"等权 {100 / max(n, 1):.1f}%/只",
                    delta_color="off")
        k[1].metric("候选池", f"{bst.get('n_cand', '—')} 只",
                    delta=f"闸门内且未达共识剔除 {bst.get('n_step1', '—')} 只进入排序",
                    delta_color="off")
        k[2].metric("执行闸剔除",
                    f"{bst.get('n_chase', 0) + bst.get('n_st', 0)} 只",
                    delta=f"近涨停 {bst.get('n_chase', 0)} + ST {bst.get('n_st', 0)}",
                    delta_color="off",
                    help="当日收盘涨幅 ≥ 涨停近似（不追）与名称含 ST/*ST（退市风险 + "
                         "±5% 限幅 + 流动性差）。昨收取不到的缺口日按「判不了就不剔」放过")
        k[3].metric("入线安静度", f"{bst.get('quiet_cut', float('nan')):.4g}"
                    if bst.get("quiet_cut") == bst.get("quiet_cut") else "—",
                    delta="`%s` 第 %d 名" % (buy_axis, n), delta_color="off",
                    help="名单最后一名的排序轴取值，表达式取自**这一场日更自己的** "
                         "`meta.buy.expr`。当前进程这根轴 = 量能水平（20 日均量，低 = 没人"
                         "交易）；09-24 换轴之前它是量能波动（低 = 走势平稳）—— 同名不同"
                         "公式，跨日比这一列前先核对表达式再说换血率")
        k[4].metric("与回测选票口径差", f"{bst.get('n_diff_vs_backtest', '—')} 只",
                    delta_color="off",
                    help="组合层回放只过闸门就取低分侧 top_n；本名单多跑三道执行闸，"
                         "这里报「因此换掉了几个」")
        if bst and not bst.get("st_checked", True):
            st.warning("当日用的是 ST 名单缺失的那一版：名称含 ST 的 204 只（09-23 实测）"
                       "没被剔掉，请人工在名单里扫一眼。历史日没有收盘快照时会这样。")
        if bst:
            # 两道剔除阈值分开报，否则「这一页有 4000 多只却被剔了 200 多只的域管着」
            # 看起来像 bug：域用并集（≥1 条判响即剔），名单入口用共识（≥3 条才挡）
            st.caption(
                f"**剔除这一环在本页与「不该买」页用的是同一批判据、两个强度**："
                f"本页挡票要 **≥{bst.get('buy_min_hits', ASHARE_BUY_MIN_HITS)} 条量能构造"
                f"一致判响**（今日 {bst.get('n_consensus', 0)} 只命中），"
                f"「不该买」那张域是 ≥1 条即剔 —— 今日有 "
                f"{bst.get('n_lenient_vs_union', 0)} 只在域里被剔、但因未达共识仍留在"
                f"本名单的候选域内（能不能进前 {ASHARE_BUY_TOP_N} 名还要看安静度排名）。"
                f"依据：并集在减法腿值 +6.46%/年，压在名单上是净负 "
                f"（+0.95% → -0.39%，换手 0.31→0.48，费用 2.49pp > 毛收益 1.15pp；"
                f"三档换闸的钱在 39 行上是中位 0.05pp、最大 0.25pp、零符号翻转），"
                f"见 `data/results/ashare_portfolio_exclusion.csv` 与 `_buylist.csv`"
                f"（这两张表当前存的是 `{ARCH_GATE}` 档，上面这三个 pp 是 `board` 档实测；"
                f"flat 档另存 `*_flat.csv`）")
        # ---------- 下单名单（从下面那张 50 只的观察名单里裁出来的仓位表） ----------
        o = order_for(btag)
        om = (bm.get("order") or {}) if bm else {}
        st.subheader(f"🧾 下单名单：{ASHARE_ORDER_TOP_N} 只 · 每槽 "
                     f"{100 / ASHARE_ORDER_TOP_N:.0f}%")
        if o is None or not len(o):
            st.info("这一版还没有下单名单（`order_YYYYMMDD.csv`）：重跑 "
                    "`run_ashare_daily_signal.py` 即生成。约束是执行层的分散度"
                    "（同行业 ≤1、同板块 ≤3），不改排序轴。")
        else:
            oshow = [c for c in ("code", "name", "板块", "行业", "obs_rank", "close",
                                 "lot_value_yuan", "安静度", "weight") if c in o.columns]
            odg = {"close": "%.2f", "lot_value_yuan": "%.0f", "安静度": "%.1f",
                   "weight": "%.3f"}
            st.dataframe(o[oshow], use_container_width=True, hide_index=True,
                         column_config=num_fmt(o[oshow], odg))
            if om:
                st.caption(
                    f"**这 {om['n_picked']} 只是下面 50 只里名次最前的可下单组合**："
                    f"按观察名单名次自上而下取，同一实体行业 ≤{om['max_per_industry']} 只、"
                    f"同一板块 ≤{om['max_per_board']} 只；每只按**槽位**给权 "
                    f"1/{om['top_n']}，所以凑不满时差额自动留现金（当前 "
                    f"{om['weight_sum']:.0%}）。这一层**不新造判据、不改排序轴**，"
                    f"板块有权限的三段（科创板/创业板/北交所）都不拉黑，只防「5 只全挤一段」。")
                if om.get("shortfall"):
                    st.warning(f"只凑到 {om['n_picked']} 只（缺 {om['shortfall']} 只）。"
                               f"观察名单的板块构成是 {om.get('n_board_in_list')}，"
                               f"每段上限 {om['max_per_board']} —— 是名单太偏还是约束太严，"
                               f"照这两个数判；本层不临时放宽判据。")
                if om.get("n_unknown"):
                    st.warning(f"这 {om['n_unknown']} 只里行业标的是「未知」：外部映射对"
                               f"北交所缺 85%、科创板缺 96%，**未知不参与行业去重**"
                               f"（否则那两段会被变相排除），所以它们的行业约束没生效。")
                im = (bm.get("industry") or {}) if bm else {}
                if im and not im.get("loaded"):
                    st.error("行业映射不可用，本层只有板块约束生效："
                             + str(im.get("reason") or ""))
                elif im.get("stale"):
                    st.warning("行业映射已过期：" + str(im.get("reason") or ""))
            st.download_button("下载本日下单名单 CSV",
                               o.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"order_{btag}.csv")
            st.write("---")
        show = [c for c in ("code", "rank", "name", "板块", "行业", "close",
                            "amount20_yi", "lot_value_yuan", "安静度", "安静度分位",
                            "当日涨幅", "listed_days", "weight") if c in b.columns]
        # 数字格式只有一份（dg）。过期那一支走 Styler 是因为 st.dataframe 吃 Styler
        # 时 column_config 会失效，故把同一份 dg 交给 Styler.format，而不是另起一套
        dg = {"close": "%.2f", "amount20_yi": "%.2f", "lot_value_yuan": "%.0f",
              "安静度": "%.1f", "安静度分位": "%.3f", "当日涨幅": "%.2f%%",
              "weight": "%.3f"}   # weight 是小数占比（0.020 = 2%），不乘 100 免得看错
        if bst_stale:
            st.dataframe(
                b[show].style.format(brace_fmt(dg), na_rep="—")
                             .set_properties(color="#9aa0a6"),
                use_container_width=True, hide_index=True)
        else:
            st.dataframe(
                b[show], use_container_width=True, hide_index=True,
                column_config=num_fmt(b[show], dg))
        fig = go.Figure(go.Bar(
            x=b["code"], y=b["amount20_yi"],
            marker_color="steelblue"))
        fig.update_layout(title="待买入名单的 20 日均成交额（亿元）：这一列不是打分，"
                                "是「买得下多少」", height=320)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            f"**这一页给的是排队顺序，不是收益承诺。**判据链：闸门（次新 ≥"
            f"{ASHARE_PORT_MIN_LISTED} 日、20 日均额 ≥{ASHARE_PORT_MIN_AMOUNT:.0e} 元、"
            f"涨停）→ 量能族剔除（**本页只用共识爆量**：池内分位 ≥"
            f"{ASHARE_SCREEN_QUANTILE:.0%} 的构造命中 ≥{ASHARE_BUY_MIN_HITS} 条才挡，"
            f"「不该买」那张域是 ≥1 条即剔）→ 待买入域内"
            f" **按 `{BUY_EXPR}` 升序**（名字仍叫「{BUY_NAME}」，09-24 换轴后它指的是"
            f"『没人交易』不是『波动小』）取前 {ASHARE_BUY_TOP_N} 名等权。排序轴的证据："
            f"组合层 39 行（13 构造 × 3 档，`board` 档）多头回放中位超额 -0.98%（20 行为负），"
            f"本轴是**为正的少数**（量能族 24 行里只有 7 行为正：本轴与换轴前旧轴各三档，"
            f"剩一档是 `SMA(Volume,10)`@top200 的 +0.26%，两轴 spearman 0.93~0.95）—— top50/100/200 = "
            f"+1.39%/+1.59%/+1.58%（IR +0.11/+0.14/+0.17，单程换手 0.186/0.176/0.162）。"
            f"**09-24 换轴**（用户裁决）：换掉的旧轴 STD(Volume,20) 是 +0.95%/+2.31%/+3.70%、"
            f"换手 0.311/0.282/0.257，**top100/200 两档仍是旧轴更高** ⇒ 要放大持仓数得重量"
            f"本轴；取水平是因为生产名单那一档（top50）三项全胜、换手只有旧轴的六成，"
            f"再加 2021 起六年 4/6 为正（旧轴 2/6、六年均值 -0.7%/年，旧轴的中位 +5.6% 几乎"
            f"全来自 2015~2020）。两条边界一起记住：本轴与剔除用的 `level` 是**同一个表达式**"
            f"（高分端踢出域、低分端买），这根轴一失效，域和名单同时坏；而且它与「低价股」"
            f"对照 MA(Price,5) 分不开（对照三档 +3.56%/+2.73%/+2.18%、换手只有 0.08~0.10，"
            f"换轴之后**三档全部**盖过本轴）。样本内、未过准入链 ⇒ 落地前请人工复核基本面，"
            f"并按 20 日均额与一手金额核对可执行性。")
        st.download_button("下载本日待买入名单 CSV",
                           b.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"buy_{btag}.csv")

# ---------- 今日剔除名单 ----------
with tabs[2]:
    if not sfiles:
        st.info("先跑 run_ashare_daily_signal.py")
    else:
        dates = [os.path.basename(f)[7:15] for f in sfiles]
        fmt = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}"
        pick = st.selectbox("信号日", [fmt(x) for x in dates][::-1])
        d = pd.read_csv(sfiles[dates.index(pick.replace("-", ""))])
        # n_hit 是「当日有几条构造一致判响」的计数列，不是一条构造，别混进规则列
        rules = [x for x in d.columns if x not in
                 ("code", "close", "amount20_yi", "excluded_by", "n_hit", "keep")]
        # 本日到底跑了哪几条构造，认入口写在 meta 里的 screen_rules，不认列名猜
        # （列名会随 STOCK_SCREEN_RULES 收窄、或被人提名进因子库条目而变）
        srs = ((meta_for(pick.replace("-", "")) or {}).get("screen_rules")) or []
        rule_note = "、".join(
            f"{x.get('name')}"
            + ("（因子库提名）" if str(x.get("key", "")).startswith("lib:") else "")
            + (f" 剔 {x['fired']:,}" if x.get("fired") is not None else "")
            for x in srs) or f"{len(rules)} 条量能构造"
        st.markdown(
            f"阈值：每条构造在**当日可投池内**的截面分位 ≥ "
            f"{ASHARE_SCREEN_QUANTILE:.0%} 即剔除，取并集（≥1 条判响即剔，"
            f"这是「不该买」这张域的口径；待买入名单的入口闸另按 "
            f"≥{ASHARE_BUY_MIN_HITS} 条一致判响，见那一页脚注）。"
            f"容量闸门为 20 日均成交额 ≥ {ASHARE_PORT_MIN_AMOUNT:.0e} 元。")
        st.caption(f"本日启用构造：{rule_note}")
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
        st.caption("上表是**剔除**名单（不该买，本日启用的构造取并集：≥1 条判响即剔）。"
                   "买入侧的排队在「🛒 待买入名单」页：那里只有一根排序轴"
                   f"（`{BUY_EXPR}` 低分侧，09-24 换过轴），且明说了它是样本内证据；"
                   f"**剔除那道闸在"
                   f"那一页更松**（要 ≥{ASHARE_BUY_MIN_HITS} 条构造一致判响才挡，"
                   "计数见 `n_hit` 列），因为并集压在名单上实测是净负的"
                   f"（现轴口径：+1.39% → -3.98%，见「📖 口径」页）。"
                   "本页这张表本身不产生买入指令——被剔的票也**不会**因为「反过来」"
                   "而变成可买。")
        st.download_button("下载本日完整名单 CSV", d.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"signal_{pick}.csv")

# ---------- 因子库 ----------
with tabs[3]:
    lib = load_json(os.path.join(RESULTS_DIR, "rdagent_output", "factors.json"))
    if not lib:
        st.info("因子库为空（rdagent_output/factors.json）")
    else:
        # 字段就是 factors.json 里有的那四个 IC 指标，不虚构入库时间/来源之类
        df = pd.DataFrame([{k: it.get(k) for k in
                            ("name", "expr", "formulation",
                             "mean_ic", "icir", "rank_ic", "rank_icir")}
                           for it in lib])
        # 盘前接点：哪几条库因子就是现在盘前名单实际在跑的判据。匹配由入口算好写进
        # meta 的 library.linked（按**表达式**匹配，库里的 name 与 expr 会错标），
        # 看板只读这份结论，不在这里再匹配一遍 —— 那是第二口径
        sf = signal_files()
        mt = meta_for(os.path.basename(sf[-1])[7:15]) if sf else None
        linked = ((mt or {}).get("library") or {}).get("linked") or []
        by_expr = {str(x.get("expr", "")).replace(" ", ""):
                   f"{x.get('role')}·{x.get('rule_name')}" for x in linked}
        df["盘前接点"] = (df["expr"].astype(str).str.replace(" ", "")
                            .map(by_expr).fillna("未进判据"))
        n_in = int((df["盘前接点"] != "未进判据").sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("在库因子", len(df))
        c2.metric("已进盘前判据", n_in,
                  help="表达式与在跑的剔除构造/排序轴逐字相同。进判据的通路只有 "
                       "STOCK_SCREEN_RULES，且是人工提名")
        c3.metric("未进判据", len(df) - n_in,
                  help="没进名单不等于没用：要不要提名看下一张表的全市场截面 IC "
                       "与组合层回放，不看本表那四个沙箱 IC")
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config=num_fmt(df, {c: "%.4f" for c in df.columns
                                                if c.endswith("_ic") or c.endswith("icir")}))
        st.caption("因子库由 RD-Agent(Q) 循环经准入链（截面评估 → 组合层 → 判重）后写入，"
                   "`[factor-lib]` 自动提交是正常流水线噪声。这里的 IC 是驱动沙箱里"
                   "**模型预测**的 IC，不是 A 股全市场截面 IC（看下一张表）。")
        if not linked:
            st.caption("⚠️ 没读到当日 meta 里的因子库接点（名单比这行代码旧？）——"
                       "重跑 run_ashare_daily_signal.py 后即有。")
        else:
            st.caption("提名一条库因子进剔除并集："
                       "`STOCK_SCREEN_RULES=level,volatility,momentum,ratio,lib:\"<因子库 name>\"`"
                       "。这会改变保留池与待买入名单，属于改结论而不是改展示，"
                       "跑之前先确认那条的全市场截面 IC 与组合层回放。")

# ---------- 截面评估 ----------
with tabs[4]:
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
with tabs[5]:
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
        st.caption("多头腿结论（09-24 在 `board` 档重跑，本表已过**复权收益假台阶护栏** + "
                   "**成交额口径修正**，见「数据与口径」）：做多低量能侧的超额中位 "
                   "**-0.98%**（39 行里 20 行为负），"
                   "而「低价股」对照（MA/VWAP/MAX of Price，与量能无关）同段中位 +2.3%~+3.1%、"
                   "换手 0.08~0.10（本轴 0.26~0.31，约 1/3） —— 低量能侧那点收益是「买冷门/低价一角」"
                   "的 beta，不是量能自带的信息。口径修正前这一栏中位是 +2.23%，"
                   "即多头腿的「像有」里有一部分是闸门虚高 25 倍放进来的脏样本。"
                   "剔除侧则扛住了四道口径（护栏 → 闸门 → 真手数 → 涨停闸档）：闸门修正后增益只让 0.14pp"
                   "（STD5 0.0450→0.0436），把 `$volume` 换成真手数（`STOCK_VOL_BASIS=real`，"
                   "对照表 `ashare_portfolio_eval_realvol.csv`）只让掉 0.2pp（0.0436→0.0418）"
                   "且 Q5 仍是最差组（-0.0405→-0.0333）。**载荷结论在剔除侧，多头侧没有。**")

# ---------- 准入判重 ----------
with tabs[6]:
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
with tabs[7]:
    st.markdown(f"""
### 数据与口径

| 项 | 取值 | 为什么 |
|---|---|---|
| 源数据 | `daily_pv.h5`（RD-Agent 循环实现因子时读的同一份） | 研究与实盘同源，结论才可对照 |
| 面板价 | **复权价**；盘面价 = 复权价 ÷ `$factor` | 收益序列必须无除息跳空；报价列给人看，用盘面价 |
| 成交额 | **复权价 × `$volume` × 100**。`$volume` 不是手，是**复权成交量** = 真实手数 ÷ `$factor` | 绝对判据（09-23 探针 `shell/probe_live_sources4_0923.py`）：拿新浪自报成交额对着除，此式 54/54 只票比值落 0.99~1.01（抽样按 `$factor` 十分位分层，覆盖 0.0070~1.24），「盘面价×`$volume`」那版逐票散布 **177 倍、0/54 命中**。09-22 全市场按此口径 = **2.14 万亿** |
| 流动性闸门 | 20 日均成交额 ≥ {ASHARE_PORT_MIN_AMOUNT:.0e} 元 | 这条 09-23 折过一次返：中途把公式「修」成盘面价×量，方向反了（等于给成交额再乘 1/`$factor`），闸门对老票放水。按闸门用的 **20 日均额**口径，09-22 实测 **261 只**真成交额不足 2000 万元的票被错放行、反向只错挡 3 只（与日频名单 5484→5226 行的逐行差一一对上）；按单日成交额则是 359 / 1 |
| ⚠️ 已知口径缺陷 | `$volume` 内含 1/`$factor`：票级 1/`$factor` 中位 **8.4**、p99 **143**、最大 **1152** ⇒ 「放量」里混着复权基准，`$factor` 小（涨幅大或分红多）的票被系统性看成高量能 | **成交额修对 ≠ 量能构造干净。**四条构造仍按面板口径（`STOCK_VOL_BASIS=adj`，与历史基线和 RD-Agent 沙箱同源），真手数口径的对照复核走 `STOCK_VOL_BASIS=real`。本模块先前那句「量能族只用 `$volume`，不含价格与 `$factor`，不受失真影响」**是错的，已作废** |
| 收益护栏 | 复权开盘收益与盘面开盘收益对看：**只有复权侧**超 ±30% 者裁回 ±30%（`RET_LIMIT`） | 生产切片 90 个 (票,日) 命中（BJ 69 / SZ 15 / SH 6；09-23 记的 326 是错数，已按生产代码重算）。**2023-10-16 一天 64 只**，等权日收益被凭空抬 **+19.89pp**（未裁剪 +19.82% vs 当日中位 −0.52%），全历史没有第二天抬过 0.5pp。真实除权必在盘面价留同幅缺口，新股首周的真暴涨两套价同时越界，故「只有复权侧越界」= `$factor` 的假台阶而非行情 |
| 次新闸门 | 已有行情 ≥ {ASHARE_PORT_MIN_LISTED} 交易日 | — |
| 涨停闸门（回测） | 次日开盘涨幅 < 阈值；阈值口径与日频执行闸**共用一个开关** `STOCK_TRADABLE_GATE`，三档：`flat` 全线单一 {ASHARE_PORT_LIMIT_UP:.1%} / `board` 板块限幅 × {ASHARE_LIMIT_NEAR:.0%} / `dated` 阈值同 board，但每段限幅**按买入日那天已生效的那一版**取（见下一行）。本页读的是进程里**当前生效档：`{ASHARE_TRADABLE_GATE}`**，与 config 同一次 import，不是写死的；下面这行口径文字由 `ashare_screen.gate_desc()` 单点构造，回测横幅、日频打印、看板三处同一份：**{gate_desc()}** | 面板最后一天**无法前瞻**，留给人工开盘前确认。归档基线三张表（`ashare_portfolio_eval*.csv` / `_exclusion` / `_buylist`）**已是 `board` 档**；`flat` 档那一批另存 `*_flat.csv`、`dated` 档另存 `*_dated.csv`，行内 `gate` 列自报是哪一档 |
| 板块限幅**生效日**（`dated` 档专用） | {' / '.join(f'{k} {ASHARE_BOARD_LIMIT_UP[k] * ASHARE_LIMIT_NEAR:.1%}（{v[0]} 起，之前 {v[1] * ASHARE_LIMIT_NEAR:.1%}）' for k, v in sorted(ASHARE_BOARD_LIMIT_SINCE.items(), key=lambda kv: kv[1][0]))} | `board` 档拿**今天**的限幅去判 2016 年的买入日，那是时代错置：那段创业板只有 ±10%，用 19% 当阈值会把真封死买不进的票判成买得进。`dated` 补的就是这一笔。能咬到的范围比想象窄（`instruments/all.txt` 6160 行盘点）：只有**创业板**有 837/1449 只在 2020-08-24 之前已上市，科创板 0（首批上市即该板块开板日 2019-07-22）、北交所 1 ⇒ 与 board 的全部差异只可能落在 2015-01-05~2020-08-23 的创业板，2021 年以后两档逐位相同。**日频侧用不到 dated**：今天没有「生效日之前」可言，两档对当日名单完全等价，实盘保持 board |
| 剔除阈值（「不该买」这张域） | 池内截面分位 ≥ {ASHARE_SCREEN_QUANTILE:.0%}，启用构造取**并集**（≥1 条判响即剔） | 09-24 组合层实测（`board` 档，表内 `gate` 列当前 = `{ARCH_GATE}`）：并集在减法腿值 **+6.46%/年**，最佳单条只有 +4.37%（边际 水平 +1.11 > 动量 +0.72 > 波动 +0.25 > 比 -0.03pp）⇒ 这张域保持并集，一条都不撤。`data/results/ashare_portfolio_exclusion.csv` |
| 待买入剔除闸 | 同一批构造、同一个分位阈值，但要 **≥{ASHARE_BUY_MIN_HITS} 条一致判响**才挡（`STOCK_BUY_MIN_HITS`，拨回 1 = 两处统一） | 把上面那张并集掩码直接压在名单上是**净负**：按现轴 `{BUY_EXPR}` 实测 +1.39% → **-3.98%**、单程换手 0.186 → 0.433（换轴前那根 `STD($volume,20)` 只从 +0.95% 打到 -0.39%，所以**换轴之后这道分强度比换轴前更吃重**）。机制也换了：排序轴现在**就是**「量能水平」那条构造，一根轴的低分端不可能同时是自己的高分端 ⇒ 水平/波动两条对这份名单实测为零贡献（「单条·量能水平」与不剔除逐字相同，两个「留一」行都和 ≥1 那一行逐字相同），名单上的伤害 **100% 来自动量（-2.03%）与比值（-1.89%）**。≥3 那档与不剔除差在小数点第 6 位（≥4 才逐字相同），≥2 就开始付钱（-0.84%）。`ashare_portfolio_buylist.csv`（旧轴账单另存 `_std20axis.csv`） |
| 待买入排序轴 | 待买入域内 `{BUY_EXPR}` **升序**取前 {ASHARE_BUY_TOP_N} 名，等权。**09-24 换过轴**（用户裁决）：旧轴 `STD($volume,20)` | 换的理由是同簇那条在三档上都不差于旧轴且换手只有其一半多：`SMA(Vol,20)` top50/100/200 = +1.39%/+1.59%/+1.58%、换手 0.186/0.176/0.162，旧轴 = +0.95%/+2.31%/+3.70%、换手 0.311/0.282/0.257（`ashare_portfolio_eval.csv`，`gate` 列当前 = `{ARCH_GATE}`）。⚠️ 两点边界：① 换轴是**改判据**，所以 ⑮ 那套剔除强度账单已按新轴全窗口重跑，别拿旧账单的钱读新名单；② 名单入口那一层只在 top50 档量过，拨大 `STOCK_BUY_TOP_N` 要连同剔除闸重量。样本内、未过准入链，且与表内「低价股」对照分不开（对照 top50 +3.56%、换手只有 1/3）⇒ 它交出来的是**待人工复核的排队顺序**，不是收益承诺。详见「🛒 待买入名单」页脚 |
| 待买入执行闸 | 在排序之前再叠三道：当日无成交/停牌、收盘涨幅 ≥ **本板块限幅 × {ASHARE_LIMIT_NEAR:.0%}**（不追）、名称含 ST/*ST | 前两道是「买不买得到」，第三道判据来自当日收盘快照的「名称」列（历史日无快照则不跑，页顶会黄条提示）。限幅按板块分档：{' / '.join(f'{k} {v * ASHARE_LIMIT_NEAR:.1%}' for k, v in ASHARE_BOARD_LIMIT_UP.items())}。09-24 探针 `shell/probe_board_limits_0924.py` 实测：过去用单一 {ASHARE_PORT_LIMIT_UP:.1%} 时，历史被挡下的三段票里 **81~85%** 离自己的涨停还远得很（科创 10066/12419、创业 46859/55367、北交 5382/6533）——那笔误伤恰好落在三个有权限的板块上。回测的第 4 道闸走**同一个开关** `STOCK_TRADABLE_GATE`，两条路径不许各拿一份判据。三档里 `dated` 是回测复现历史规则用的，日频那一侧它和 `board` 逐字相同（见上两行） |
| 下单层 | 从待买入名单按名次往下扫，同板块 ≤ {ASHARE_ORDER_MAX_PER_BOARD} 只、同行业 ≤ {ASHARE_ORDER_MAX_PER_INDUSTRY} 只，凑满 {ASHARE_ORDER_TOP_N} 只即停，每槽 {100 / ASHARE_ORDER_TOP_N:.0f}%（凑不满的余量是现金，不自动放宽） | 这一层**只解决「一次下得完」**，不参与判据：名单的排序轴一个字没改。板块与行业都是分散度约束、不是白名单——科创板/创业板/北交所均有权限，所以没有任何一段被拉黑；映射缺的行业（值「未知」）也不参与去重，否则北交所会被 85% 的映射缺口代理排除 |
| 费率 | 佣金万 2.5 双边（最低 5 元）+ 印花税万 5（卖出） | 回测用综合单边 15bp（含滑点万 10） |
| T+1 / 整手 | 买入须为 100 股整数倍；当日买入不可当日卖出 | 账本按日初持仓快照校验 |

### 本看板**不能**告诉你的事

- **盘中**：全部判据来自已落库的日线（`daily_pv.h5`），本线暂不做盘中分析。
  日更没跑，页面给的就是上一场的名单。
- 待买入名单**不是**收益承诺：它的排序轴只有样本内证据（分年度按中间档 top100 统计，
  新轴 `{BUY_EXPR}` 是 2015~2020 四正、2021 起四正、最近一年为负；被换掉的旧轴
  `STD($volume,20)` 是 2015~2020 五正、2021 起只有两正且近两年连负 —— 换轴换的就是
  这一段），且与「低价股」对照分不开。它回答的是「人工先复核哪 50 只」，不回答「买它会涨」。
- 卖什么：剔除规则验证的是「买不进就不买」，没有验证「踢掉已持有的票能否改善组合」。
""")
