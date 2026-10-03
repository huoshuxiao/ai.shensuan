# -*- coding: utf-8 -*-
"""ETF 研究看板：`streamlit run app.py`（工作目录 etf/v1/src）

这一页只读，不产生任何文件；所有数字都来自主线落盘的产物，页上不重算判据。
**不接下单**：`src/live/` 的真实委托代码不在本页的调用链上，最后一节只读影子盘落盘的 csv。

读盘口径：`data/`、`report/`、`log/` 三处都是这一页的数据面，缓存键是**文件戳**
（mtime+size）而不是倒计时 —— 环 2 刚落的 csv 下一次重跑就在屏上，而盘上没动时
一页都不多重读（实测整页 899 次 `read_csv` 只发生一次）。页首那个「自动刷新」是
心跳：到点只比"最新落盘"这一对指纹，变了才整页重跑，所以它不是定时器刷新的动画。

为什么页首第一行是「数据新鲜度」而不是收益
------------------------------------------
本线的每一条判据（DSR / walk-forward / 策略级 PBO / 规模闸）都建立在"日线已经推到
最新交易日"之上。#19 实测过反面：日更没跑的那几天，池缓存停在 09-18 而全市场镜像
已到 09-22，回测安静地算着一周前的净值，页面上一切看起来正常。所以新鲜度放在收益
之前，落后就把入口写在脸上（`python data/update_etf_daily.py`）。

口径与阈值一律从 `config` / `etf_admission` 现读，不在本页复制常量。
"""

import glob
import hashlib
import json
import os
from datetime import datetime

import _bootstrap  # noqa: F401  必须先于项目模块导入
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from config import (CACHE_DIR, DSR, FACTOR_LIBRARY, FREQ, LIVE_DATA_DIR, LOG_DIR,
                    PORTFOLIO, REPORT_DIR, RESULTS_DIR, RISK_DIR, STRATEGY_PBO,
                    TRIAL_COUNTER_FILE, TRIGGER_LOGIC, UNIVERSE_ALL_DIR,
                    UNIVERSE_CACHE, WALK_FORWARD, RDAGENT_OUTPUT_DIR)
from factor_naming import cn_name, METRIC_GLOSSARY
from etf_theme import direction_of
from llm_selfreport import (decision_tally, harvest_vs_library,
                            self_report_mtime)
# 反馈闭环 / 影子盘监控两块各自仍可单独 `streamlit run`；这里只 import 它们的
# render() 当两节用，合一张看板。set_page_config 只能有一次，留给宿主页面。
from app_feedback import render as render_feedback
from app_live import render as render_live

st.set_page_config(page_title="ETF 量化看板", layout="wide")
st.title("📊 ETF 量化系统看板")
# 对外表述是合规边界，不是装饰：这条线曾经有一个 tab 叫「实盘监控」，读起来像在替人下单。
st.caption("**辅助决策，不自动实盘。** ETF 线完全不接下单：本看板与全部 `src/` 入口都不向券商"
           "发委托，`src/live/` 里的 qmt / easytrader 委托代码**没有接进本页**，"
           "`run_live.py` 只跑本机影子盘（paper 账户自己记账，账户号是占位符）。"
           "真实成交一律由人在券商端手工下单、手工录回；不存储券商账号密码；只用公开/授权数据。"
           "页上每个数字都是**已落盘产物**的读数（`data/results/`、`data/live/`），"
           "**收盘后日线口径，不做盘中分析**，也不是收益承诺。")


# ---------- 读盘：缓存键用"文件动过没有"，不用固定倒计时 ----------
# 只用 `ttl=30` 的时候，页面显示的是"上一版"还是"盘上这一版"要看运气：环 2 刚写完
# `equity_daily.csv` 的那 30 秒里它读的还是旧净值，而 ttl 一到又会把 871 个从没变过的
# csv 重读一遍。改成 mtime+字节数当键 —— 文件没动就命中缓存，一动就在下一次重跑生效。

def _file_stamp(path):
    """单个文件的落盘戳：mtime（纳秒）+ 大小。不存在时返回固定串 `absent`。"""
    try:
        s = os.stat(path)
        return f"{s.st_mtime_ns}-{s.st_size}"
    except OSError:
        return "absent"


@st.cache_data(ttl=5, show_spinner=False)
def _dir_stamp(path, pattern="*"):
    """整个目录折成一个短哈希（文件增删改名、任何一个被重写都会变）。

    `ttl=5` 是"最多迟到 5 秒"的折衷：`common/data/etf/universe_all/` 有 871 个文件，整页每次
    心跳都 stat 一遍不值当。它喂的是新鲜度表和风险面板这两把重读数，不是净值曲线。
    """
    try:
        entries = sorted(f"{e.name}:{_file_stamp(e.path)}"
                         for e in os.scandir(path) if e.is_file())
    except OSError:
        return "absent"
    digest = hashlib.md5("\n".join(entries).encode()).hexdigest()[:12]
    return f"{len(entries)}-{digest}"


@st.cache_data(ttl=3600, show_spinner=False)
def _read_csv(path, stamp):
    return pd.read_csv(path)


@st.cache_data(ttl=3600, show_spinner=False)
def _read_json(path, stamp):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=3600, show_spinner=False)
def _read_risk_long(path, stamp):
    """风险长表（`shares_szse.csv` 18MB）：裸读一次就是几十毫秒到几百毫秒的解析。"""
    from fetch_etf_risk_panel import read_long
    return read_long(path)


def load_csv(p):
    return _read_csv(p, _file_stamp(p)) if p and os.path.exists(p) else None


def load_json(p):
    if not p or not os.path.exists(p):
        return None
    try:
        return _read_json(p, _file_stamp(p))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 采集进程写 json 不是原子的：正好赶上它落半截，这轮先当没有，
        # 下一轮戳一定变了（文件在长），就读到完整的了。
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _read_tail(path, stamp, n_lines):
    """只取文件尾部 n 行，按 256KB 一块从末尾回退读。

    `log/research_daily_20260925.log` 有 9.6MB（是那次重挖把 LLM 请求全打出来的
    下场），整读进 Streamlit 会把页面冻住；日更日志是 append-only，尾部才是新东西。
    """
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        buf = b""
        while pos > 0 and buf.count(b"\n") <= n_lines:
            step = min(1 << 18, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
    lines = buf.decode("utf-8", "replace").splitlines()
    if pos > 0 and lines:
        lines = lines[1:]          # 首行可能被块边界切成半截
    return lines[-n_lines:]        # 全文件不足 n 行时等价于整读


def _mtime(p):
    """列清单途中文件被人删掉/轮换也不炸这一页。"""
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def _files_by_mtime(patterns):
    """按 (目录, glob) 列清单，mtime 从新到旧。"""
    got = []
    for d, pat in patterns:
        got += [p for p in glob.glob(os.path.join(d, pat)) if os.path.isfile(p)]
    return sorted(set(got), key=_mtime, reverse=True)


def _human(n_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024 or unit == "GB":
            return f"{n_bytes:.0f} {unit}"
        n_bytes /= 1024.0
    return f"{n_bytes:.0f} GB"


def _ago(mtime):
    """「多久没动过」：日更在跑时这一格就是活体证据，不用猜。"""
    sec = max(0, int(datetime.now().timestamp() - mtime))
    if sec < 60:
        return f"{sec} 秒前"
    if sec < 3600:
        return f"{sec // 60} 分钟前"
    if sec < 86400:
        return f"{sec // 3600} 小时前"
    return f"{sec // 86400} 天前"


# ---------- 自动刷新（页首第一件事） ----------
# 心跳只做一件事：每隔 N 秒看一眼盘上最新的落盘动过没有，动过才整页重跑。
# 不无脑定时重跑整页 —— 十个页签的图全要重画一遍，而绝大多数时候盘上什么都没变。
AUTO_LABELS = ("关", "15 秒", "30 秒", "1 分钟", "5 分钟")
AUTO_SECONDS = {"关": None, "15 秒": 15, "30 秒": 30, "1 分钟": 60, "5 分钟": 300}
DISK_SOURCES = ((RESULTS_DIR, "*"), (f"{RESULTS_DIR}/rdagent_output", "*"),
                (LOG_DIR, "*.log*"), (REPORT_DIR, "*.md"), (REPORT_DIR, "*.json"),
                (LIVE_DATA_DIR, "*"), (CACHE_DIR, "*.json"))


def disk_fingerprint():
    """`DISK_SOURCES` 里最新那一次落盘的 (mtime 秒, 文件名)——心跳比的就是这一对。"""
    files = _files_by_mtime(DISK_SOURCES)
    if not files:
        return (0.0, "")
    p = files[0]
    return (round(_mtime(p), 3), os.path.basename(p))


_r1, _r2, _r3 = st.columns([1.25, 1.55, 4.2])
_choice = _r1.selectbox("自动刷新", AUTO_LABELS, index=3,
                        help="心跳只查「盘上有没有新文件」，不重算任何判据。选「关」时，"
                             "点页签、改控件这些交互仍会重读文件（缓存键是文件戳，不是倒计时）。")
if _r2.button("🔄 立刻重读盘上文件", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
_fp_now = disk_fingerprint()
st.session_state["_hb_fp"] = _fp_now
_r3.caption(
    f"本页重跑于 {datetime.now():%H:%M:%S} ｜ 盘上最新落盘：`{_fp_now[1] or '—'}`"
    f"{' ' + datetime.fromtimestamp(_fp_now[0]).strftime('%m-%d %H:%M:%S') if _fp_now[0] else ''}"
    f"（{_ago(_fp_now[0])}）｜ 心跳：{'每 ' + _choice + '查一次盘，变了才整页重跑' if _choice != '关' else '已关，只在看板被操作时重读'}"
    "。读数口径是**收盘后日线**，不做盘中分析。")

if AUTO_SECONDS[_choice] is not None:

    @st.fragment(run_every=AUTO_SECONDS[_choice])
    def _heartbeat():
        """到点只比指纹：一致就原地留一行时间戳，不一致才 `st.rerun()` 整页重读。

        `st.rerun()` 在 fragment 里默认作用域是整个 app，所以必须靠这把指纹闸住，
        否则"整页重跑 → 又跑到这一格 → 又 rerun"会转成死循环。
        """
        cur = disk_fingerprint()
        if st.session_state.get("_hb_fp") != cur:
            st.session_state["_hb_fp"] = cur
            st.rerun()
        st.empty()

    _heartbeat()


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

@st.cache_data(ttl=3600, show_spinner="正在核对各数据面的末日…")
def _freshness_report(key):
    from update_etf_daily import freshness_report
    return freshness_report()


# 新鲜度读的是镜像/池缓存/风险长表三处的末日，键就用这三处的目录戳。
# 读失败不进缓存（否则一次偶发的 import 失败会被记一小时）：
try:
    fresh = _freshness_report(f"{_dir_stamp(UNIVERSE_ALL_DIR)}|{_dir_stamp(CACHE_DIR)}"
                              f"|{_dir_stamp(RISK_DIR)}")
except Exception as e:
    fresh = pd.DataFrame()
    st.warning(f"新鲜度读数不可用: {type(e).__name__}: {e}")
eq_path = artifact("equity")
eq_df = load_csv(eq_path)
sig_path = artifact("signals")
signals_df = load_csv(sig_path)
trades_df = load_csv(artifact("trades"))
dsr_df = load_csv(artifact("dsr"))
wf_df = load_csv(artifact("walk_forward"))
# 合并样本外 DSR：这条读数是各折原始逐 bar 收益的函数，折表里没有，
# 只能由 walk_forward 单独落一行（walk_forward_oos_*.csv）
wf_oos_df = load_csv(f"{RESULTS_DIR}/walk_forward_oos_{FREQ}.csv")
pbo_res = load_json(f"{RESULTS_DIR}/pbo_result.json")
params = load_json(f"{RESULTS_DIR}/optimized_params_{FREQ}.json")
trial_counter = load_json(TRIAL_COUNTER_FILE)
# 候选池缓存：`etf_universe.build()` 的产物，每只指数一条代表（最早上市且过闸）
pool_df = load_csv(UNIVERSE_CACHE)
if pool_df is not None and "code" in pool_df.columns:
    pool_df["code"] = pool_df["code"].astype(str).str.zfill(6)


def _decorate_codes(df, code_col="code"):
    """给回测长表里的六位代码补上名称与所属指数组（池缓存里没有就留空）。"""
    if pool_df is None or code_col not in df.columns:
        return df
    names = pool_df[[code_col, "name"] + [c for c in ("index_group", "list_date")
                                          if c in pool_df.columns]].copy()
    out = df.copy()
    out[code_col] = out[code_col].astype(str).str.extract(r"(\d{6})")[0]
    return out.merge(names, on=code_col, how="left")


def _theme_view(sig_df):
    """篮子按「方向」层的集中度读数 —— **只读观察项，不进任何判据**。

    为什么另起一层分类：候选池本来就是每个指数组留一条代表，100 只 ↔ 100 个
    `index_group` ⇒ 按组算"同一组最多 N 席"恒等于每组 1 席，是空判据。往下合并
    成 10 个方向（`etf_theme.direction_of`，名字正则）才读得出"这一篮押在几类
    资产上"。分类错了只会让这格读数难看，选股与权重仍由 `select_weights` 决定。

    读数口径（与 09-28 那次档位实测 `etf/v1/temp/conc_tiers_dir_etf_0927.py` 同一把
    尺子，可互相核对）：每次调仓把该篮各方向的权重（占净值，含现金所以和 <1）
    相加，取其中最大的那个方向 ⇒ 平均最大方向权重 / 最坏最大方向权重 /
    平均在场方向数。返回 (末场方向表, 读数 dict)；signals 缺 weight 列返回 None。
    """
    if sig_df is None or sig_df.empty or "weight" not in sig_df.columns:
        return None, {}
    h = _decorate_codes(_holdings(sig_df).copy(), "code")
    ts_col = h.columns[0]
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce")
    names = h["name"] if "name" in h.columns else pd.Series([""] * len(h))
    groups = h["index_group"] if "index_group" in h.columns \
        else pd.Series([""] * len(h))
    h["direction"] = [direction_of(n, g) for n, g in zip(names, groups)]
    agg = h.groupby([ts_col, "direction"])["weight"].sum()
    per_bar_max = agg.groupby(level=0).max()
    per_bar_n = agg.groupby(level=0).size()
    last_day = h[ts_col].max()
    tbl = (agg.loc[last_day].sort_values(ascending=False)
           .to_frame("权重（占净值）").reset_index())
    tbl["权重（占净值）"] = tbl["权重（占净值）"].astype(float)
    kpi = {"last_day": str(last_day)[:10],
           "bars": int(len(per_bar_max)),
           "avg_max": float(per_bar_max.mean()),
           "worst_max": float(per_bar_max.max()),
           "avg_directions": float(per_bar_n.mean()),
           "last_top_direction": str(tbl.iloc[0]["direction"]),
           "last_top_weight": float(tbl.iloc[0]["权重（占净值）"])}
    return tbl, kpi


last_data_day = str(eq_df[eq_df.columns[0]].iloc[-1])[:10] if eq_df is not None else "—"
lag_cols = [c for c in ("落后交易日",) if c in fresh.columns]
worst_lag = int(pd.to_numeric(fresh["落后交易日"], errors="coerce").fillna(0).max()) \
    if len(fresh) and lag_cols else 0
f1, f2, f3, f4, f5, f6 = st.columns([2.0, 1.3, 1.1, 1.2, 1.2, 1.3])
f1.metric("日线净值止于", last_data_day,
          delta=None, help="净值/信号/交易三张表的最后一格，即本页所有读数的时间截面")
f2.metric("数据面最大落后", f"{worst_lag} 个交易日",
          delta=None if worst_lag == 0 else "需要日更",
          delta_color="off" if worst_lag == 0 else "inverse")
f3.metric("试验账本 N",
          (trial_counter or {}).get("count", "—"),
          help="common/data/etf/cache/trial_counter.json：这条研究线累计试过多少个变体，"
               "DSR 的运气门槛随它收紧（#15 之前每轮被 reset 成 3）")
f4.metric("候选池", f"{len(pool_df) if pool_df is not None else 0} 只",
          delta=None,
          help="**每期能从哪些标的里挑**：`common/data/etf/cache/etf_universe_cache.csv`，"
               "按指数组分桶、每只指数只留一条最早上市且过规模闸的代表 ⇒ "
               "「100 只」读作「100 个指数组各一只」，不是 100 只随便挑。"
               "由 `etf_universe.build()` 生成，不每天重算")
f5.metric("同期持仓上限", f"{PORTFOLIO.get('top_k')} 只",
          delta=None,
          help="**同时持有几只**（`PORTFOLIO['top_k']`）。旧版把这格误标成「标的池」，"
               "看着像只有 10 只可选；凑不满是分数门槛与可投域闸门共同作用的结果")
f6.metric("全市场镜像",
          f"{len(glob.glob(os.path.join(UNIVERSE_ALL_DIR, '*_daily.csv')))} 只",
          delta=None,
          help="`common/data/etf/universe_all/`：RD-Agent 侧与 `etf_admission` 的底座，"
               "候选池就是从它里面按闸门挑出来的")
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

# ---------- 两个池：能挑什么 / 正在持有什么 ----------
with st.expander("🅿 候选池与最新持仓（只读）", expanded=False):
    st.caption("两件事分开看：**候选池**是每期能从哪些标的里挑（按指数组分桶，"
               "每组一条代表），**持仓**是这一期实际挑中的那几只。两者都来自"
               "已落盘产物，本页不改任何一个。")
    if pool_df is None or pool_df.empty:
        st.info("没有候选池缓存：跑 `cd etf/v1/src && python data/etf_universe.py`"
                "（或任意一次 `main.py`，它会顺带建池）")
    else:
        cols = [c for c in ("code", "name", "index_group", "list_date",
                            "median_amount", "avg_amount", "ann_vol")
                if c in pool_df.columns]
        show = pool_df[cols].copy()
        for c in ("median_amount", "avg_amount"):
            if c in show.columns:
                show[c] = pd.to_numeric(show[c], errors="coerce") / 1e8
                show = show.rename(columns={c: f"{c}(亿元)"})
        st.markdown(f"**候选池 {len(show)} 只**（每个指数组一条代表）")
        st.dataframe(show, hide_index=True, height=420)
        st.caption("`median_amount` 是池缓存里那条指数的**中位日成交额**（换成亿元），"
                   "规模闸看的就是它；`ann_vol` 是年化波动。")
    if signals_df is not None and "code" in signals_df.columns:
        held_last = _holdings(signals_df)
        ts_col = signals_df.columns[0]
        held_last = held_last[held_last[ts_col] == held_last[ts_col].max()]
        held_last = _decorate_codes(held_last, "code").drop(columns=[ts_col])
        st.markdown(f"**持仓（标的池）**：最新一次调仓 "
                    f"`{str(_holdings(signals_df)[ts_col].max())[:10]}` 实际持有的 "
                    f"{len(held_last)} 只")
        st.dataframe(held_last.sort_values("weight", ascending=False)
                     if "weight" in held_last.columns else held_last,
                     hide_index=True)
        grp = (_decorate_codes(_holdings(signals_df), "code")
               ["index_group"].value_counts().head(10))
        st.caption("入选次数最多的 10 个指数组（整段回测累计，用来看篮子是不是"
                   "押在同一类资产上）：")
        st.dataframe(grp.to_frame("入选次数"), hide_index=False)

        theme_tbl, theme_kpi = _theme_view(signals_df)
        if theme_tbl is not None and theme_kpi:
            st.markdown(
                f"**篮子押在几类资产上（方向层，只读）**：末场 "
                f"`{theme_kpi['last_day']}` 最大的一格是 "
                f"`{theme_kpi['last_top_direction']}` 占净值 "
                f"{theme_kpi['last_top_weight']:.1%}；整段 "
                f"{theme_kpi['bars']:,} 次调仓平均最大方向权重 "
                f"{theme_kpi['avg_max']:.1%}、最坏 "
                f"{theme_kpi['worst_max']:.1%}、平均同时押 "
                f"{theme_kpi['avg_directions']:.2f} 个方向")
            st.dataframe(theme_tbl, hide_index=True)
            st.caption("方向由 `etf_theme.direction_of` 从 ETF 名字抠出（10 个方向"
                       "：**成长系把半导体/芯片/AI软件通信/军工航天/新能源高端制造/"
                       "科技综合/科创创业成长风格/平台互联网合成一格，因为它们在"
                       "同一个市场因子上同涨同跌**）。这一格是**观察项**：09-28 两族"
                       "档位实测（每类最多 N 席 / 每方向最多 N 席 / 每方向权重封顶）"
                       "**没有一档在性价比上打得过现状**，所以没接任何集中度判据；"
                       "读数在 `etf/v1/temp/tmp_conc_0927/`。权重之和 <100% 的那部分是"
                       "现金（单标的上限 30% 压不住的空档）。")

st.divider()

# ---------- tabs ----------
# 回测的三块（业绩概要 / 净值 / 持仓与交易）合进同一个 tab 并标"纸面"：它们全部是
# `main.py` 拿真实历史日线重放出来的假账户，不是任何真实成交。以前"净值""持仓与交易"
# 各占一 tab，紧挨着 ETF 风险那些真实读数，容易被读成"实盘赚了多少"。
tabs = st.tabs(["📉 回测（纸面重放）", "🎯 统计验证", "🧪 ETF 风险",
                "🧬 因子", "⚙️ 配置与生命周期", "📖 口径",
                "🗂 日志与报告", "🔄 反馈闭环", "📡 影子盘监控"])

with tabs[0]:
    st.caption("这一页全是**回测**：用真实历史日线 + 当前因子 + 当前仓位规则重放一遍，"
               "资金账户是纸面的（初始 1 万）。真实成交一律由人在券商端手工下单、"
               "手工录回，本页不产生也不显示任何委托。")
    if eq_df is None or eq_df.empty:
        st.info("还没有净值产物：跑 `python main.py`（或 "
                "`python run_daily_backtest.py`）")
    else:
        eq = eq_df.set_index(eq_df.columns[0])["equity"]
        p1, p2, p3, p4, p5, p6, p7 = st.columns(7)
        total = eq.iloc[-1] / eq.iloc[0] - 1
        span_days = max((pd.to_datetime(eq.index[-1]) -
                         pd.to_datetime(eq.index[0])).days, 1)
        ann = (1 + total) ** (365 / span_days) - 1
        rets = eq.pct_change().dropna()
        sharpe = float(rets.mean() / (rets.std() + 1e-9) * np.sqrt(252))
        max_dd = float(((eq - eq.cummax()) / eq.cummax()).min())
        p1.metric("区间", f"{str(eq.index[0])[:7]} ~ {str(eq.index[-1])[:10]}")
        p2.metric("总收益率", _pct(total, signed=False))
        p3.metric("年化收益率", _pct(ann, signed=False))
        p4.metric("最大回撤", _pct(max_dd))
        p5.metric("夏普（年化）", f"{sharpe:.2f}")
        p6.metric("最终资金", f"{eq.iloc[-1]:,.0f}")
        if dsr_df is not None and not dsr_df.empty:
            row = dsr_df.iloc[0]
            d = float(row.get("dsr", 0))
            p7.metric("全样本 DSR", f"{d:.4f}",
                      delta="过线" if d > 0.95 else
                      f"门槛年化 {row.get('sr0_annual')}",
                      delta_color="normal" if d > 0.95 else "off",
                      help="Bailey & López de Prado (2014)。运气门槛 SR* 用 "
                           "Lo(2002) 的夏普抽样方差 (1+0.5·SR̂²)/T 折算，"
                           "与 SR̂ 同为逐 bar 量纲")
        st.divider()
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

# 持仓与交易接在同一张回测 tab 的下面（净值 → 持仓 → 流水，按"怎么赚的"顺序读）
with tabs[0]:
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
        latest = _decorate_codes(held[held[ts_col] == last_day], "code")
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

with tabs[1]:
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

        # 合并样本外：把互不相交的几段测试收益拼成一条长序列再检验。
        # 单折的 T 太短会让运气门槛高到任何策略都够不着（402 bar → 需年化
        # 4.33），拼接后 T 是各段之和 ⇒ 这才是这条链上唯一可判定的 DSR
        if wf_oos_df is not None and not wf_oos_df.empty:
            o = wf_oos_df.iloc[0]
            st.markdown("**合并样本外 DSR**（各折测试段按时间拼接）")
            o1, o2, o3, o4, o5 = st.columns(5)
            o1.metric("拼接段数 / bar",
                      f"{int(float(o.get('n_segments', 0)))} / "
                      f"{int(float(o.get('n_samples', 0)))}",
                      help=f"train_ratio={o.get('train_ratio')}；每段的"
                           "首根 bar 是净值起点、无收益，不计入")
            o2.metric("年化夏普", f"{float(o.get('sharpe_annual', 0)):.3f}")
            o3.metric("DSR", f"{float(o.get('dsr', 0)):.4f}",
                      delta="通过（>0.95）" if str(o.get("passed")).lower()
                      in ("true", "1") else "未通过",
                      delta_color="normal" if str(o.get("passed")).lower()
                      in ("true", "1") else "off")
            o4.metric("运气门槛年化", f"{float(o.get('sr0_annual', 0)):.3f}",
                      help="N 次试验纯运气能挣到的年化夏普；年化夏普要跨过"
                           "它 DSR 才过 0.95")
            o5.metric("累计试验 N", int(float(o.get("n_trials", 0))),
                      help="取检验时点的账本，即本折链挖完之后的计数")
            o6, o7 = st.columns(2)
            o6.caption(f"该条收益的偏度 {float(o.get('skew', 0)):.2f} / "
                       f"峰度 {float(o.get('kurt', 0)):.2f}：厚尾会抬高夏普"
                       "估计量的方差，等价于把门槛再往上推")
            o7.caption("逐折那栏仍是各段自己的 T ⇒ 现状几何下恒 False，"
                       "两条读数并列看，不要互相替换")

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

with tabs[2]:
    @st.cache_data(ttl=3600, show_spinner="正在读份额/净值面板并算规模…")
    def risk_bundle(key):
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
        # 这一格吃的是池缓存 + 风险长表（19MB 份额/净值），是整页最重的一次读数：
        # 原来 ttl=600 让它每 10 分钟无条件重算一遍，现在换成目录戳，日更没跑就一次不算
        bundle = risk_bundle(f"{_dir_stamp(CACHE_DIR)}|{_dir_stamp(RISK_DIR)}")
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
                from config import RISK_NAV_THS, RISK_SHARES_SSE, RISK_SHARES_SZSE
                for label, p in (("份额·沪", RISK_SHARES_SSE),
                                 ("份额·深", RISK_SHARES_SZSE),
                                 ("单位净值", RISK_NAV_THS)):
                    df = _read_risk_long(p, _file_stamp(p))
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

with tabs[3]:
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
    # ---------- 容器/LLM 自述（只加读数，不收判据） ----------
    st.subheader("容器/LLM 自述（不是判据）")
    st.caption("两块都不是本系统的准入依据：① `Final Decision` 是 RD-Agent 容器"
               "（CoSTEER）判**代码实现**对不对，与因子 alpha 无关，`common/src` 从不读"
               "它，数值只从子进程日志文本里数出来，且只覆盖落在这个目录的场次——"
               "stdout 被重定向到别处的场次不在表里；② 下表的“容器自报 IC”来自"
               "`factors.json`，准入永远用**本池重算**的那一列"
               "（`main.recount_foreign_ic`，判据=逐标的 IC 全等即视为自报值），"
               "这里只显示两者差多少。")
    tally = decision_tally(RDAGENT_OUTPUT_DIR)
    c3, c4 = st.columns(2)
    if tally:
        c3.dataframe(pd.DataFrame(tally), hide_index=True)
        c4.metric("历场自报 SUCCESS/FAIL 合计",
                  f"{sum(r['decision_success'] for r in tally)} / "
                  f"{sum(r['decision_fail'] for r in tally)}",
                  help="按日志里的 `This implementation is …` 逐行计数")
    else:
        c3.info(f"{RDAGENT_OUTPUT_DIR} 下没有可解析的容器循环日志")
    harvest = f"{RDAGENT_OUTPUT_DIR}/factors.json"
    hv = harvest_vs_library(harvest, FACTOR_LIBRARY.get("index_path"))
    st.caption(f"自报产物落盘时间：{self_report_mtime(harvest)}"
               f"（比它新的库内读数以库为准）")
    if not hv.empty:
        st.dataframe(hv, hide_index=True)

with tabs[4]:
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
               f"计入。本页只读，不改这个文件。"
               f"**喂进来的 DSR 是上面那条「合并样本外 DSR」**（walk-forward 没跑出"
               f"该读数时才退回全样本 DSR），PBO 只认真实配置族、退化族按缺失处理。"
               f"注意 DSR 那条腿的门槛是 `{TRIGGER_LOGIC['dsr_threshold']}`：这条线"
               f"迄今任何一档 DSR 都没接近过它，所以判据实际由 PBO 那条腿驱动"
               f"（`>{TRIGGER_LOGIC['pbo_threshold']}` 或 Δ`>{TRIGGER_LOGIC['pbo_delta_threshold']}`）。")
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

with tabs[5]:
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
  DSR 出**两道并列**读数：逐折各用本段 bar 数（T 短 ⇒ 门槛高，现状几何下
  恒 False），与把几段不重叠测试收益拼接后的合并样本外检验（T=各段之和，
  这条才是可判定的；拼接隐含"样本外收益可复利串联"这一假定）。
- **策略级 PBO**（López de Prado 2017 CSCV）：终态因子集 × OFAT 风控邻域
  （`1 + Σᵢ(|空间ᵢ|−1)` 档）逐档全样本回测，比较样本内冠军在样本外的相对排名，
  `PBO = P(logit ω̂ ≤ 0)`，门槛 `{STRATEGY_PBO.get('pbo_threshold')}`。
  族退化（档数 <3、前置条件不满足）时页面顶部会显形为 `pseudo`，且**不喂给
  重挖触发器**：触发器按"指标缺失"处理，退化轮不参与 DSR/PBO 联动判据。
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
               "`common/data/etf/risk/`，口径以 `src/` 里的实现为准。")

with tabs[6]:
    # 这一节的存在理由：日更和三环都在往 `log/`、`report/` 落东西，以前要看只能
    # 开终端 tail。看板既然每 1 分钟查一次盘，就把这两处也接进来——仍然**只读文件**，
    # 不在这里起进程、不重跑脚本。
    st.caption("只把 `etf/v1/log/` 与 `etf/v1/report/` 里**已经落盘**的文件摊开看："
               "日志从文件尾往前读（最大的一份 9.6MB，整读会把页面冻住），报告原样渲染。"
               "这一节不产生文件、不触发任何脚本。")
    _log_col, _rep_col = st.columns([1.05, 1.45], gap="large")

    with _log_col:
        st.subheader("🧾 运行日志")
        logs = _files_by_mtime(((LOG_DIR, "*.log*"),))
        if not logs:
            st.info(f"`{LOG_DIR}` 里还没有日志")
        else:
            def _log_label(p):
                return (f"{_ago(_mtime(p))} · {_human(os.path.getsize(p))} · "
                        f"{os.path.basename(p)}")
            pick_log = st.selectbox("选一份（按落盘时间从新到旧）", logs,
                                    format_func=_log_label,
                                    help=f"目录：{LOG_DIR}。带 `.run2-xxx`、"
                                         "`.old-run` 后缀的是同一场实验的历史存档，没接进日更。")
            n_lines = st.selectbox("读尾部多少行", (200, 500, 1000, 3000), index=1)
            try:
                tail = _read_tail(pick_log, _file_stamp(pick_log), n_lines)
            except OSError as e:
                tail = []
                st.warning(f"读不到这份日志: {type(e).__name__}: {e}")
            marks = [ln for ln in tail
                     if ln.startswith(("[前置体检]", "[验收", "[链路完成]", "[ERROR]",
                                       "Traceback", "[因子库]", "[完成]", "[超时]"))]
            st.caption(f"`{os.path.basename(pick_log)}` · 这里显示最后 {len(tail)} 行"
                       f"（文件 {_human(os.path.getsize(pick_log))}）")
            if marks:
                st.markdown("**这一场的关键行**")
                for ln in marks[-12:]:
                    st.code(ln, language="text")
            st.code("\n".join(tail) or "（空文件）", language="text")

    with _rep_col:
        st.subheader("📄 报告与自评产物")
        reports = _files_by_mtime(((REPORT_DIR, "*.md"), (REPORT_DIR, "*.json"),
                                   (REPORT_DIR, "*.csv"),
                                   (f"{REPORT_DIR}/archive", "*")))
        if not reports:
            st.info(f"`{REPORT_DIR}` 里还没有报告")
        else:
            root = os.path.abspath(REPORT_DIR)

            def _rep_label(p):
                rel = os.path.relpath(os.path.abspath(p), root)
                return f"{_ago(_mtime(p))} · {rel}"
            pick_rep = st.selectbox("选一份（按落盘时间从新到旧）", reports,
                                    format_func=_rep_label,
                                    help=f"目录：{REPORT_DIR}（含 archive/ 归档）")
            stamp = _file_stamp(pick_rep)
            st.caption(f"最后一次落盘 {_ago(_mtime(pick_rep))} · "
                       f"{_human(os.path.getsize(pick_rep))}")
            if pick_rep.endswith(".md"):
                st.markdown("\n".join(_read_tail(pick_rep, stamp, 20000)))
            elif pick_rep.endswith(".json"):
                st.json(load_json(pick_rep) or {}, expanded=False)
            else:
                rdf = load_csv(pick_rep)
                if rdf is None or rdf.empty:
                    st.info("这份是空表")
                else:
                    st.dataframe(rdf, hide_index=True)
            st.caption("`.md` 与 `.json` 是**报告文本**：里面的结论是模型或脚本对既有"
                       "数字的说法，不进任何准入判据（判据在 `src/` 的三环入口里）。")

# ---------- 以下两节原样来自 app_feedback.py / app_live.py ----------
# 页内还各自带一层子页签（反馈 12 个 / 影子盘 3 个），所以整块交给 render() 画。
with tabs[7]:
    render_feedback()

with tabs[8]:
    render_live()
