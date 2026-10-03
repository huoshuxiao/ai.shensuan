# -*- coding: utf-8 -*-
"""股票线看板：`streamlit run app.py`（在 stock/v1/src 下起）。

只读 CSV 出数，不重算任何东西（下面「两个写动作」那段除外）：所有数字都由四个入口产出——
  run_ashare_daily_signal.py → data/results/daily_signal/{signal,buy}_YYYYMMDD.csv
                               + meta_YYYYMMDD.json（当日各环节计数）
  run_ashare_position.py     → data/live/{positions,account}.csv
  run_ashare_factor_eval.py / run_ashare_portfolio_eval.py /
  run_ashare_redundancy_check.py → data/results/ashare_*.csv
所以看板与命令行永远一致；看板上有异议就是入口有 bug，而不是这里算了两套。

**页面上只有两个写动作，两个都不在本页重算判据**：
① 「录一笔成交」（在「💼 持仓」页，09-24 #117）：把一行流水**追加**进
   `manual_fills.csv`，然后用子进程调 `run_ashare_position.py` 原入口出账，再把那一层的
   原始输出贴回来。平均成本、T+1、费率补算、盘外价报警这些判据**一个字都不在页面重算**
   （同一个账有两个判据来源，是这类系统最贵的东西）。为什么用子进程而不是 import 进来跑：
   出账要读 0.8GB 日线面板估值，那是几十秒（本机实测两笔流水 36 秒）和两三个 GB；
   看板是长驻进程，不能为了一次录入把面板常驻在自己内存里。
② 「跑今天这场日更」（页顶那一格，09-24 #118）：`start_new_session` 起一个**分离进程**
   跑 `run_ashare_daily_chain.py`，页面只贴它自己写的那份日志。五步顺序、四道闸、跨步
   验收全在链路入口（㉔），这一页既不判「该不该跑」（盘中点它，让 ① 那道 15:00 收盘闸去拒，
   原话会贴回这一格的日志里），也不判「今天有没有该补的一场」（那一问由链路自己问交易所日历）。

**本页给的是「剔除名单 + 待买入短名单」两份，且全是收盘后日线口径，不做盘中分析**
（判据来自已落库的 daily_pv.h5，页面上一行盘中价都不读；日更没跑，看到的就还是昨天）。
两份名单的证据强度不一样，页面上要分开读。**09-25 名单形状换成板块配额**
（`STOCK_LIST_SCHEME=quota`，主板 20/创业 10/科创 10/北交 10，凑不满回填）：这一档在
「把 50 只都买」那种用法上历史年化超额只有 +0.11%（全局前 50 那一档 +1.55%），换来的是
下单那 5 只从 +11.70% 到 +13.04%、席位天天凑满 ⇒ **这张名单按「前排拿来下单、整张拿来
人工复核」用，不当一篮子买**（两头的钱都记在「📖 口径」页那行「观察名单形状」）。
剔除侧与待买入侧的账分开算：剔除侧是载荷结论（单条最佳 +4.37%/年、
四条并集 +6.46%，`board` 档，扛住四次口径变动）；待买入侧只有一根排序轴（**09-24 换过
轴**，现在是 SMA(Volume,20) 低分侧）：全局前 50 那档样本内 top50/100/200 年化超额
+1.55%/+1.80%/+1.81%（09-24 那批 569 天读数 +1.39%/+1.59%/+1.58%，同序），换手只有旧轴的六成；
**09-25 换成配额名单后连 top50 也归了旧轴**（现档 +0.11%/+1.25%/+1.54% vs 旧轴 +1.00%/+1.73%/+3.69%），全窗口 top100/200 两档两种名单下都归旧轴；与同表「低价股」对照分不开、
且与剔除用的 `level` 是同一个表达式），所以它叫「先人工核这一批」的排队顺序，
不构成收益承诺。
"""

import csv
import glob
import io
import json
import os
import re
import subprocess
import sys
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
                    ASHARE_LIST_SCHEME, ASHARE_LIST_QUOTA,
                    ASHARE_SCREEN_QUANTILE, ASHARE_BUY_EXTRA_QUANTILE,
                    ASHARE_SIGNAL_DIR, ASHARE_ROLL_FOLDS,
                    LOG_DIR, RDAGENT_QLIB_PROVIDER, RESULTS_DIR)
# 口径表里那行「涨停闸吃哪一套阈值」的文字**不让本页自己拼**：回测横幅、日频打印、
# 本页三处都调 ashare_screen.gate_desc()，同一份构造。分开拼迟早出现某一处写的
# 和实跑的不是一档（09-24 真撞过一次：文档说 board、config 里那行重复定义把默认
# 顶成了 flat）。只 import 这一个函数，模块级的规则/装载都不执行。
# 排序轴同理：09-24 换过一次轴（STD(Volume,20) → SMA(Volume,20)），本页那几处
# 「按什么升序取前 50 名」一律写 `{BUY_EXPR}`，不写死表达式，否则下次换轴又要满页找。
from ashare_screen import BUY_EXPR, BUY_NAME, gate_desc, list_desc

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


def archived_list_scheme(fname="ashare_portfolio_buylist.csv"):
    """表内 `list_scheme` 列自报「这一场账单是在哪种名单形状下量的」
    （global = 全局前 N 名；quota = 四段留席位后段内按轴升序、凑不满回填）。
    与 archived_gate 同一个理由：09-25 名单生成规则换过一次，读的人必须能从文件
    本身看出踩的是哪条，不能靠文档。减法腿那张 `_exclusion` 不带这列是故意的 ——
    它剔的是整个可投资域，名单长什么形状与它无关。
    """
    p = os.path.join(RESULTS_DIR, fname)
    if not os.path.exists(p):
        return "（表未生成）"
    d = pd.read_csv(p, nrows=1)
    return (str(d["list_scheme"].iloc[0]) if "list_scheme" in d.columns
            else "global（旧表无 list_scheme 列）")


ARCH_GATE = archived_gate()
ARCH_LIST = archived_list_scheme()


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
        f"2. 点本页「✍️ 录成交 / 出账」里的**只重新出账**（或在 `stock/v1/src` 下跑 "
        "`python run_ashare_position.py`）"
        "—— 一条流水不合法就带行号报错、**整轮不出账**，页面上也就不会看到半对的账。\n"
        "3. 出账成功后本页会自己刷新，「💼 持仓」跟着出持仓表、市值分布和账户汇总。\n\n"
        "现金流水没录期初本金时，现金是买入占款、总资产无意义 —— 先把本金记一行。")


# ---------- 成交录入：本页唯一的写动作（追加一行流水 + 子进程调既有出账入口） ----------
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
FILL_SIDES = ["买入", "卖出", "入金", "出金"]


def fill_candidates():
    """代码下拉的候选 = 今天可能要下的单（观察名单）+ 手上已有的仓。
    只是**给候选不是白名单**：三段板块都有权限，手输任何 SH/SZ/BJ 代码都收。"""
    got = []
    if bfiles:
        try:
            got += [str(x).strip().upper() for x in pd.read_csv(bfiles[-1], dtype=str)["code"]]
        except Exception:
            pass                       # 名单读不动就让候选为空，手输这条路还在
    if positions is not None and len(positions):
        got += [str(x).strip().upper() for x in positions["代码"]]
    return sorted(set(got))


def last_fill_line():
    """账本里**物理最后一条数据行**的文本（不含表头、不含 `#` 注释、不含空行）

    判重为什么要读到文件里而不是记在 `st.session_state`：session_state 是**每次刷新
    重来**的，第一次点击写下的那一行在下一次 rerun 还在，但只要人换一页、按一下 F5
    就清零 ⇒ 隔一次刷新把同一笔录两遍，页面挡不住（09-24 ㉕ 遗留第 ② 条）。而「这一行
    和账本最后一行逐字相同」这个判据不受页面生命周期影响，而且和手工编辑 CSV 的形态
    一致 —— 手工追加时肉眼比对的就是这一行。
    """
    if not os.path.exists(ASHARE_FILLS_CSV):
        return None
    with open(ASHARE_FILLS_CSV, encoding="utf-8", errors="replace") as fh:
        body = [ln.strip() for ln in fh.read().splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
    # 第一行是表头，不是流水 —— 与出账入口 `load_fills` 数行号时用的是同一条规则
    return body[-1] if len(body) > 1 else None


def append_fill(cells):
    """追加一行流水。返回 (写进去的那行文本, 说明)；被挡下时第一样是 None。

    出账失败**不撤这一行**：`run_ashare_position.py` 的语义是「报错带行号、整轮不出账，
    坏行留在文件里等人改」，页面在这儿替它把行删掉，反而会吃掉一笔真成交（比如成交日
    晚于面板尽头那种「数据还没更」的情况）。要挡的只有录两遍这一种。
    """
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(cells)      # 与 out 里同样的最小引号规则
    line = buf.getvalue()
    if line == last_fill_line():
        return None, ("这一行与**账本里最后一条流水**逐字相同 ⇒ 已挡下（防一次双击或刷新后"
                      "再点一次，把一笔成交录成两笔）。真的有两笔一模一样的成交：把备注写开"
                      "再点，比如「第二笔」——备注算这一行的一部分，写了就不算重复。")
    if not os.path.exists(ASHARE_FILLS_CSV):
        # 表头与那行 # 写法说明由出账入口自己生成 —— 页面不重抄一遍表头
        import run_ashare_position as pos_entry
        pos_entry.template(ASHARE_FILLS_CSV)
    with open(ASHARE_FILLS_CSV, "a", encoding="utf-8", newline="") as fh:
        fh.write(line + "\n")
    return line, ""


def settle_book(live=None):
    """子进程跑得出账入口，**逐行**回显它的 stdout，拿回退出码 + 全文

    为什么是 `Popen` 逐行读而不是 `subprocess.run(stdout=PIPE)`：这一趟实测 36 秒，
    整段捕获等于在这 36 秒里页面一个字节都看不到，人只能对着转圈的 spinner 猜它是
    卡了还是在算（09-24 ㉕ 遗留第 ③ 条）。逐行读、每行刷新一次调用方给的
    `st.empty()` 容器，跑的过程中就看得到它走到哪一步。
    仍然不 import 进来跑：出账要读 0.8GB 日线面板给持仓估值，那是几十秒和两三个 GB，
    看板是长驻进程，不该为一次录入把面板常驻。判据单点在入口那一层，这里零重算。
    """
    buf = []
    with subprocess.Popen([sys.executable, os.path.join(SRC_DIR, "run_ashare_position.py")],
                          cwd=SRC_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace", bufsize=1) as p:
        for ln in p.stdout:
            buf.append(ln.rstrip("\n"))
            if live is not None:
                live.code("\n".join(buf[-14:]) or "（还没打出第一行：正在读 0.8GB 面板）",
                          language="text")
        rc = p.wait()
    return rc, "\n".join(buf).strip()


def show_fill_result():
    """把上一次出账的结果贴在这一页顶部（rerun 之后 session_state 里那条是一次性的）"""
    r = st.session_state.pop("fill_result", None)
    if not r:
        return
    rc, out = r
    (st.error if rc else st.success)(
        f"出账入口退出码 {rc} ⇒ " + ("**本轮没出账**：持仓/账户 CSV 一个字节都没改，"
                                    "上面/下面是它原话，按行号改完再点「只重新出账」。"
                                    if rc else "持仓表与账户汇总已刷新（本段下方就是它那一轮的原始输出）。"))
    st.code(out or "（这一层没有任何输出）", language="text")


# ---------- 日更链路唤起：按钮只做「起一个分离进程 + 贴回它自己的日志」 ----------
CHAIN_ENTRY = os.path.join(SRC_DIR, "run_ashare_daily_chain.py")
CHAIN_LOG = os.path.join(LOG_DIR, "chain_from_dash.log")       # 只留最近那一场
LINE_DATA_DIR = os.path.abspath(os.path.join(SRC_DIR, "..", "data"))
# ② 那一层的落点**不让本页重新拼**：直接拿链路入口自己用的那个常量（它是从
# `RDAGENT_OUTPUT_DIR` 拼出来的，不吃 `STOCK_DAILY_H5` 覆写 ⇒ 在这里自己拼一遍就会
# 出现「页面念的路径和实际被写的路径不是同一个」，而这一格唯一的作用就是说清写到哪）
# 取消标记同理：语义（在**步与步之间**停、不在一步中间动手）在链路入口里，这里只写文件
from run_ashare_daily_chain import CANCEL_FLAG, H5 as CHAIN_H5   # noqa: E402


def chain_targets():
    """这一场会写的三个落点，**读进程里真实生效的那三个值**，不读环境变量名

    为什么不用 `any(k.startswith("STOCK_"))` 当判据：本线 config 自己会往进程里注入
    `STOCK_LLM_BASE_URL` / `STOCK_LLM_MODEL`（LLM 兼容开关那条），所以「看到 STOCK_ 就当
    冒烟档」会在**每一次生产渲染**上报假警（09-24 无覆写 AppTest 实测就是这个红法）。
    子进程确实继承本进程环境 ⇒ 覆写会跟着下去，但要看穿它只能念落点本身。
    注意 ② 那一行**不吃 `STOCK_*` 覆写**（路径由 `RDAGENT_OUTPUT_DIR` 定）⇒ 冒烟档里它照样
    指生产，这不是 bug 是 ㉔ 就量下的事实（那一层唯一躲不开的共享写）。
    """
    return (("① bin 与日历", RDAGENT_QLIB_PROVIDER),
            ("② 面板 h5", CHAIN_H5),
            ("③④ 当日名单", ASHARE_SIGNAL_DIR))


def chain_procs():
    """现在有哪些进程在跑这条链路 —— **不分谁起的**，命令行手敲的那一场也算

    不另记一份 pid 状态文件：页面自己记，就会有「记的和真实在跑的对不上」那一刻，
    而这一格要防的恰恰是两场同时跑（② 每天全量重生成 0.8GB 面板，两遍并起就是内存
    事故）。进程表本身就是唯一权威，扫一遍比维护一份文件便宜也不会说谎。
    """
    name = os.path.basename(CHAIN_ENTRY)
    got = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/cmdline", "rb") as fh:
                argv = [a for a in fh.read().decode("utf-8", "replace").split("\x00") if a]
        except OSError:                      # 读的瞬间那一进程退了 / 不是自己的进程
            continue
        if any(os.path.basename(a) == name for a in argv):
            got.append((int(d), " ".join(argv)))
    return got


def start_chain(dry_run):
    """分离进程起链路：`start_new_session` 让它不在这个会话/终端退出时被一起带走

    不用 `Popen(...).wait()`，也不放 `st.spinner` 里同步跑：这一场要**一两分钟起、机器忙的时候
    四分钟**（09-24 从这一格实跑两遍：② 36/81s、③ 43/74s、④ 42/65s ⇒ 整链 115s 与 211s；
    ㉔ 命令行那一次 120/79/61s。同一套判据三遍三个数，花的是页缓存冷热和机器上还有谁 ⇒
    **别把任何一个当常量**），同步跑等于把整个看板冻在那儿几分钟，而且**关掉页面那一进程就断**。
    子进程的退出码从另一个进程 wait 不到，所以判成没成就只看日志尾巴上那句 `[链路完成]`
    （链路的规矩是任一验收不过就当场 exit≠0，原话走 stderr，这里并流进同一份日志）。
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    # 上一场遗留的中止请求不带进这一场（链路在步边界看到这张条子就会停，而这一场是
    # 人刚刚按下去要跑的 ⇒ 那张条子一定是旧的）。链路自己只在触发时删它，删旧账这件事归起手续
    if os.path.exists(CANCEL_FLAG):
        os.remove(CANCEL_FLAG)
    cmd = [sys.executable, CHAIN_ENTRY] + (["--dry-run"] if dry_run else [])
    with open(CHAIN_LOG, "wb") as fh:        # 起点截断：别把上一场的尾巴混进这一场
        subprocess.Popen(cmd, cwd=SRC_DIR, stdin=subprocess.DEVNULL,
                         stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True)


@st.fragment(run_every=15 if chain_procs() else None)
def chain_panel():
    with st.container(border=True):
        procs = chain_procs()
        t1, t2 = st.columns([3, 5])
        t1.markdown("🌙 **收盘后日更链路**　① 快照→bin → ② 面板 → ③ 名单 → ④ 次日真账 → ⑤ 仓位层前向账本")
        t2.caption("五步顺序、四道闸（含「休市日不落未来场次」与「复用缓存前先对表」）、「该补哪一场」"
                   "问交易所日历、跨步验收全在 `run_ashare_daily_chain.py`（㉔+选项F）；⑤ 是仓位层前向"
                   "流水账（09-27 选项D），**不进验收**：它失败只打 ⚠️，名单照出。这一格只起进程、只贴日志。")
        cancel_pending = os.path.exists(CANCEL_FLAG)
        if procs:
            st.info("　".join(f"pid {p}" for p, _ in procs)
                    + " 正在跑这一场 ⇒ 按钮锁住（两场并起会把 ② 那 0.8GB 面板重生成跑两遍）。"
                    "关掉本页不影响它。"
                    + ("" if len(procs) == 1 else "　注意：同时不止一场，先去命令行看一眼"))
            # 取消的语义在链路入口里（`check_cancel`）：**不再起下一步**，正在跑的那一步
            # 把它自己的验收跑完。这里只写那张条子，不复制一份判据 —— 命令行起的那一场
            # 也认同一个文件，谁按的都行。不做「立刻 kill」：① 在按票 append bin、
            # ② 在重写 0.8GB 面板，拦腰打断留的是半成品，而这条链值钱就值钱在
            # 「要么整条走完、要么半条链的产物不留给看板」（㉔）
            x1, x2 = st.columns([2, 8])
            if x1.button("请求中止", disabled=cancel_pending,
                         help="跑完当前这一步就不再起下一步；已跑完的步骤产物照旧有效"):
                with open(CANCEL_FLAG, "w", encoding="utf-8") as fh:
                    fh.write(f"requested from dashboard at {datetime.now().isoformat()}\n")
                st.rerun()
            if cancel_pending:
                x2.caption(f"中止请求已写下（"
                           f"{datetime.fromtimestamp(os.path.getmtime(CANCEL_FLAG)):%H:%M:%S}）"
                           "⇒ 链路会在下一个步边界看到这张条子后停下，最坏等一步的实测时长"
                           "（② 36~120s、③ 43~79s、④ 42~61s）。要收回这个请求：删掉 "
                           f"`{CANCEL_FLAG}`")
            else:
                x2.caption("这一场已经在跑 ⇒ 想停只能等它跑完或按「请求中止」；关掉本页不影响它。")
        tail = []
        if os.path.exists(CHAIN_LOG):
            with open(CHAIN_LOG, encoding="utf-8", errors="replace") as fh:
                tail = [ln for ln in fh.read().splitlines() if ln.strip()]
            st.caption(f"本页最近一场：起于 "
                       f"{datetime.fromtimestamp(os.path.getmtime(CHAIN_LOG)):%m-%d %H:%M}"
                       f"（日志 {CHAIN_LOG}）　"
                       + ("🟢 还在跑" if procs else
                          ("✅ 打出了 `[链路完成]`" if "[链路完成]" in "\n".join(tail)
                           else ("⏹ 按请求中止 ⇒ 后面的步骤没跑（已跑完的那几步产物有效）"
                                 if "[已按请求中止]" in "\n".join(tail)
                                 else "⛔ 已结束但**没打出** `[链路完成]` ⇒ 看下面的原话"))))
        else:
            st.caption("还没从本页跑过。第一次点之前先看一眼下面这三行落点：链路会把当日快照 "
                       "append 进 qlib bin（写前自动备份，坏了用 "
                       "`data/update_qlib_bin_daily.py --rollback` 还原）、重写面板 h5、"
                       "覆盖当日 `signal/buy/order` 三份名单。")
        targets = chain_targets()
        off = [(k, p) for k, p in targets
               if not os.path.abspath(p).startswith(LINE_DATA_DIR + os.sep)]
        for k, p in targets:
            st.caption(("⚠️ " if any(k2 == k for k2, _ in off) else "　") + f"{k}：" + f"`{p}`")
        if off:
            st.error("从这里起的链路会写到**本线 `data/` 目录之外**（上面打 ⚠️ 的那几个落点）⇒ "
                     "这是冒烟档，不是生产那一场。子进程继承本看板进程的环境，覆写会跟着下去；"
                     "要跑生产那一场，请用无覆写的看板实例或直接命令行。")
        dry = st.toggle("只跑 ① 的 dry-run（快照只算不写；① 无事可做时整链就停）",
                        value=False, disabled=bool(procs),
                        help="链路自带的排练档 `--dry-run`：一个字节都不动")
        b1, b2 = st.columns([2, 8])
        if b1.button("跑今天这场日更", type="primary", disabled=bool(procs),
                     help="15:00 之前点会被 ① 的收盘闸当场拒掉，原话就贴在下面这段日志里"):
            start_chain(dry)
            st.rerun()
        if tail:
            marks = [ln for ln in tail if ln.startswith(("[前置体检]", "[验收", "[链路完成]",
                                                          "[链路中断]", "[已按请求中止]", "[① 跳过]",
                                                          "[停在这里]", "[② 自报]", "[产物 ③]",
                                                          "Traceback"))]
            for ln in (marks or tail)[-9:]:
                st.code(ln, language="text")
            with st.expander(f"这一场的完整日志（{len(tail)} 行）"):
                st.code("\n".join(tail), language="text")
        if not procs and tail and "[链路完成]" in "\n".join(tail):
            if b2.button("名单已更新 ⇒ 刷新整页", help="清掉本页的 CSV 缓存，重读新名单"):
                load_csv.clear()
                st.rerun()


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
               "到「💼 持仓」那一页点「只重新出账」，或跑 `run_ashare_position.py`")

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
                 help=f"保留池内按「{BUY_NAME}」= `{BUY_EXPR}` 升序取 {ASHARE_BUY_TOP_N} 只"
                      f"（名单形状：{list_desc()}）。样本内一根轴，不是收益承诺")
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
                      f"⚠️ 面板已 {lag} 天没更新——点下面那一格的「跑今天这场日更」，"
                      "本页不做盘中分析，看到的仍是这份旧名单"))
else:
    st.info("还没有日频信号名单——点下面那一格的「跑今天这场日更」"
            "（或命令行跑 `python run_ashare_daily_signal.py`）")

chain_panel()

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
    show_fill_result()
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
                    "持仓表、市值分布与账户汇总。（往下那一块「✍️ 录成交 / 出账」"
                    "就是录第一笔的地方，不用去终端。）")
        else:
            st.warning(fills_msg or
                       f"有 **{n_fills} 笔**流水但账户文件还不存在 —— 点下面那一块里的"
                       "「只重新出账」就出账（不合法的行会带行号报错、整轮不出账）")
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

    # ---- 录一笔成交：下单动作本身在券商 App 里做，这里收的是「下完之后那一步」 ----
    with st.container(border=True):
        st.markdown("#### ✍️ 录成交 / 出账")
        st.caption(f"这一页唯一的写动作：把一行流水**追加**进 `{ASHARE_FILLS_CSV}`，"
                   "再用子进程调 `run_ashare_position.py` 出账 —— 平均成本、T+1、费率补算、"
                   "盘外价报警那些判据**一个都不在这里重算**，页面只负责写一行和贴回原话。"
                   "这个文件是你的财务数据，不进版本库。")
        cands = fill_candidates()
        with st.form("fill_form", clear_on_submit=True):
            q1, q2, q3, q4 = st.columns(4)
            f_day = q1.date_input("成交日期", value=date.today(),
                                  help="必须 ≤ 面板最后一日，否则出账会报「账没法估」")
            pick = q2.selectbox("代码", ["（手输）"] + cands,
                                help="候选 = 今日观察名单 + 现有持仓；不是白名单，手输也收")
            f_code = q2.text_input("代码 SH/SZ/BJ + 6 位", value="",
                                   disabled=pick != "（手输）")
            f_side = q3.selectbox("方向", FILL_SIDES)
            f_qty = q4.number_input("数量（股）/ 现金金额", min_value=0.0, step=100.0,
                                    format="%.2f",
                                    help="买入须是 100 的整数倍；入金/出金这一列填现金额")
            w1, w2, w3 = st.columns(3)
            f_px = w1.number_input("成交价（盘面真实价，非复权价）", min_value=0.0,
                                   step=0.01, format="%.3f",
                                   help="入金/出金留 0")
            f_fee = w2.number_input("费用（元）", min_value=0.0, step=1.0, format="%.2f",
                                    help="留 0 = 按标准费率补（佣金万 2.5 最低 5 元 + 卖出印花税万 5），"
                                         "交割单下来后请把实际费用回填")
            f_note = w3.text_input("备注")
            if st.form_submit_button("录入并出账", type="primary"):
                code = (pick if pick != "（手输）" else f_code).strip().upper()
                if not code:
                    st.error("代码是空的 ⇒ 这一行没写进流水。")
                else:
                    g = lambda v: f"{v:.10g}" if v > 0 else ""
                    cells = [f_day.isoformat(), code, f_side, g(f_px), g(f_qty),
                             g(f_fee), f_note.strip()]
                    line, why = append_fill(cells)
                    if line is None:
                        st.warning(why)
                    else:
                        box = st.empty()
                        box.caption("出账中（子进程要读一遍日线面板给持仓估值，实测两笔流水"
                                    " 36 秒）……下面这段是它的原话，一行一行长出来")
                        rc, out = settle_book(live=box)
                        load_csv.clear()
                        st.session_state["fill_result"] = (rc, out)
                        st.rerun()
        b1, b2 = st.columns([1, 5])
        if b1.button("只重新出账", help="改完流水文件之后用这个，不再追加新行"):
            box2 = st.empty()
            box2.caption("出账中（同上，36 秒量级）……")
            rc, out = settle_book(live=box2)
            load_csv.clear()
            st.session_state["fill_result"] = (rc, out)
            st.rerun()
        b2.caption("出账失败时那一行**留在流水里**（入口的语义是「带行号报错、整轮不出账，"
                   "等人改完再跑」），按报错里的行号编辑文件后再点「只重新出账」。"
                   "要手工批量录，仍然直接编辑那个 CSV，两边判据一致。")
        with st.expander("流水文件的写法（与 CSV 手工录入同一套）"):
            st.markdown(ledger_hint())

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
                    delta=f"闸门内且过共识闸+独立闸 {bst.get('n_step1', '—')} 只进入排序",
                    delta_color="off")
        k[2].metric("执行闸剔除",
                    f"{bst.get('n_chase', 0) + bst.get('n_st', 0)} 只",
                    delta=f"近涨停 {bst.get('n_chase', 0)} + ST {bst.get('n_st', 0)}",
                    delta_color="off",
                    help="当日收盘涨幅 ≥ 涨停近似（不追）与名称含 ST/*ST（退市风险 + "
                         "±5% 限幅 + 流动性差）。昨收取不到的缺口日按「判不了就不剔」放过")
        k[3].metric("入线安静度", f"{bst.get('quiet_cut', float('nan')):.4g}"
                    if bst.get("quiet_cut") == bst.get("quiet_cut") else "—",
                    delta="`%s` 本名单第 %d 名（配额档 ≠ 全局第 %d 名）" % (buy_axis, n, n),
                    delta_color="off",
                    help="名单最后一名的排序轴取值，表达式取自**这一场日更自己的** "
                         "`meta.buy.expr`。当前进程这根轴 = 量能水平（20 日均量，低 = 没人"
                         "交易）；09-24 换轴之前它是量能波动（低 = 走势平稳）—— 同名不同"
                         "公式，跨日比这一列前先核对表达式再说换血率")
        k[4].metric("与回测选票口径差", f"{bst.get('n_diff_vs_backtest', '—')} 只",
                    delta_color="off",
                    help="组合层回放只过闸门就取低分侧 top_n（名单形状同一套配额，"
                         "走同一个 `apply_board_quota`）；本名单多跑三道执行闸，"
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
                f"本名单的候选域内（能不能进名单最后一名还要看安静度排名）。"
                f"依据：并集在减法腿值 +6.46%/年，压在名单上是净负 "
                f"（名单口径 = `{ARCH_LIST}` 这一档实测 +0.11% → -2.79%、单程换手 "
                f"0.223 → 0.465；全局前 50 那一档 +1.55% → -3.78%、换手 0.186 → 0.433；"
                f"三档换闸的钱在 39 行上是中位 0.05pp、最大 0.25pp、零符号翻转），"
                f"见 `data/results/ashare_portfolio_exclusion.csv` 与 `_buylist.csv`"
                f"（这两张表当前存的是 `{ARCH_GATE}` 档 + 名单 `{ARCH_LIST}` 形状，"
                f"上面「换闸的钱」那三个 pp 是 `board` 档实测；"
                f"flat 档另存 `*_flat.csv`、全局名单那批另存 `*_globalrank.csv`）")
            if bst.get("buy_extra_fired"):
                # 名单独立闸：不进 n_hit 计数、只挡名单入口（读数取自这一场 meta，不重算）。判据用 `buy_extra_fired`（关闭那场是空 dict）而不是「键在不在」——`n_extra_net` 关掉时照样写 0，拿它当开关这段文字永远藏不掉
                # 名字别写成「第三道闸」——下面 `buy_candidates` 那三道是**执行层**的闸，
                # 这一道属于**筛选层**，两套编号混着念一定有人读错
                st.caption(
                    f"**剔除这一环在名单上其实有两处强度 + 一道独立闸**（独立闸 09-26 接、"
                    f"**这一场启用**，`STOCK_BUY_EXTRA_RULES`；09-27 起生产默认关）：它**不参与**上面「命中几条」的计数，"
                    f"而是在共识闸放行之后单独再挡一次 —— 表达式在池内取高分端 ≥"
                    f"{bm.get('buy_extra_quantile', bm.get('quantile', ASHARE_SCREEN_QUANTILE)):.0%}"
                    f"（**独立旋钮** `STOCK_BUY_EXTRA_QUANTILE`，与域那根 "
                    f"{bm.get('quantile', ASHARE_SCREEN_QUANTILE):.0%} 分开拨），今日命中 "
                    + "、".join(f"{k} {v} 只" for k, v in
                               (bst.get("buy_extra_fired") or {}).items())
                    + f"，其中 **{bst.get('n_extra_net', 0)} 只是共识闸放行、只被它挡掉的**"
                    f"（净新增）。账单（配额档 top50、570 个调仓日、扣双边 15bp、"
                    f"**全窗样本内**）：不接此闸 +0.28%/年，并进并集会掉到 +0.04%，"
                    f"独立叠上来在刀口 0.80/0.85/0.90/0.95 分别是 "
                    f"+1.04/+1.96/+1.20/+0.19%。它不改「不该买」那张域、也不改减法腿；"
                    f"⚠️ 但这**全是名单层**的账：搬到真正掏钱的 5 席下单层符号翻掉（@0.80 年均 "
                    f"-8.78pp/年、四档全负且不单调，㊷㊸）⇒ **09-27 起生产默认关**，细账见口径页")
        # ---------- 下单名单（从下面那张 50 只的观察名单里裁出来的仓位表） ----------
        o = order_for(btag)
        om = (bm.get("order") or {}) if bm else {}
        st.subheader(f"🧾 下单名单：目标 {ASHARE_ORDER_TOP_N} 只 · "
                     f"实际 {om.get('n_picked', ASHARE_ORDER_TOP_N)} 只等权满仓")
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
                    f"同一板块 ≤{om['max_per_board']} 只；权重是**等权满仓** —— 每只拿 "
                    f"1/{om['n_picked']} = {om.get('weight_each', 0):.1%}，凑不满时缺的席位"
                    f"摊给已挑到的票，不留现金（合计 {om['weight_sum']:.0%}）。这一层"
                    f"**不新造判据、不改排序轴**，板块有权限的三段（科创板/创业板/北交所）"
                    f"都不拉黑，只防「5 只全挤一段」。")
                if om.get("shortfall"):
                    st.warning(f"目标 {om['top_n']} 只、只凑到 {om['n_picked']} 只"
                               f"（缺 {om['shortfall']} 只）：观察名单的板块构成是 "
                               f"{om.get('n_board_in_list')}，每段上限 {om['max_per_board']} "
                               f"—— 是名单太偏还是约束太严，照这两个数判。本层不临时放宽"
                               f"判据，缺的席位摊给已挑到的 {om['n_picked']} 只，所以每只"
                               f"权重升到 {om.get('weight_each', 0):.1%}"
                               f"（旧口径这里留现金，09-25 起改满仓：在**全局前 50** 那批"
                               f"名单上实测 5 席有 24.7% 的调仓日凑不满、平均空着 6.6% 资金，"
                               f"改完历史超额 +9.90% → +11.70%/年而最大回撤一字未变；"
                               f"换成配额名单后 570 天回放里席位天天凑满，但**当天实盘照样"
                               f"可能缺票**（执行闸会剔掉贴涨停的），所以这条警告不是纸面的）。")
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
            f"『没人交易』不是『波动小』）取 {ASHARE_BUY_TOP_N} 只等权，名单形状 = "
            f"`{ARCH_LIST}`（{list_desc()}）。排序轴的证据："
            f"组合层 39 行（13 构造 × 3 档，`board` 档 + 名单 `{ARCH_LIST}` 档）多头回放"
            f"中位超额 -1.25%（20 行为负），"
            f"本轴是**为正的少数**（量能族 24 行里只有 7 行为正：本轴与换轴前旧轴各三档，"
            f"剩一档是 `SMA(Volume,10)`@top200 的 +0.20%，两轴 spearman 0.93~0.95）—— top50/100/200 = "
            f"+0.11%/+1.25%/+1.54%（IR +0.01/+0.12/+0.17，单程换手 0.223/0.191/0.168）。"
            f"**09-24 换轴**（用户裁决）：换掉的旧轴 STD(Volume,20) 配额档是 +1.00%/+1.73%/+3.69%、"
            f"换手 0.341/0.297/0.263，**top100/200 两档仍是旧轴更高** ⇒ 要放大持仓数得重量"
            f"本轴。⚠️ 但**09-25 换名单形状把「top50 三项全胜」这一腿翻掉了**：同日同一网格"
            f"只差名单形状的对照（`ashare_portfolio_eval_globalctrl_0925.csv`）里本轴 top50 = "
            f"+1.55% vs 旧轴 +1.09%（本轴赢），配额档变成 +0.11% vs 旧轴 +1.00%（**旧轴赢**），"
            f"本轴在 top50 只剩「换手只有旧轴的六成半」这一条优势；换轴的另一条证据"
            f"（中间档 2021 起六年 4/6 为正）配额档下没变（旧轴仍 2/6、六年均值 -1.3%/年）。"
            f"两条边界一起记住：本轴与剔除用的 `level` 是**同一个表达式**"
            f"（高分端踢出域、低分端买），这根轴一失效，域和名单同时坏；而且它与「低价股」"
            f"对照 MA(Price,5) 分不开（配额档三档 +3.10%/+3.12%/+2.71%、换手只有 0.08~0.11，"
            f"两种名单形状下都是**三档全部**盖过本轴）。样本内、未过准入链 ⇒ 落地前请人工复核基本面，"
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
                   f"（`{BUY_EXPR}` 低分侧，09-24 换过轴；名单形状 09-25 换成板块配额），"
                   f"且明说了它是样本内证据；"
                   f"**剔除那道闸在"
                   f"那一页更松**（要 ≥{ASHARE_BUY_MIN_HITS} 条构造一致判响才挡，"
                   "计数见 `n_hit` 列），因为并集压在名单上实测是净负的"
                   f"（现轴现名单口径：+0.11% → -2.79%，见「📖 口径」页）。"
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

        # 「稳不稳」秤的读数（run_ashare_rolling_ic.py 的产物）。只读展示：
        # 这一栏没接进任何判据，红条与准入线一律在上游的环 3 / 筛选层里。
        rl = load_csv(os.path.join(RESULTS_DIR, "ashare_rolling_ic.csv"))
        if rl is None:
            st.info("「稳不稳」还没量：跑 `run_ashare_rolling_ic.py`（全市场扫一遍约 11 分钟，"
                    "只写 `ashare_rolling_ic` / `ashare_ic_daily` / `ashare_ic_yearly` 三个新文件，"
                    "上面那张表一个字节不动）")
        else:
            # 归档自己带 `fold_n`（那一次真的切了几段）⇒ 折数是配置项、这张表是跑出来的，
            # 两者对不上就是不同版，当场说破，别让人拿旧口径的读数当现档念。
            if "fold_n" in rl.columns:
                _fn = sorted(set(rl["fold_n"].dropna().astype("int64")))
                if _fn and _fn != [ASHARE_ROLL_FOLDS]:
                    st.warning(f"⚠️ **归档与配置不同版**：`fold_*` 那三列是按 {_fn} 段切的，而当前配置是 "
                               f"{ASHARE_ROLL_FOLDS} 段 ⇒ 那一栏的读数不是现在这把尺子的读数，"
                               "重跑 `run_ashare_rolling_ic.py` 才追平（实测扫面板 945s ≈ 16 分钟）。")
            show_r = [x for x in ("name", "status", "rank_ic_full", "roll_last",
                                  "roll_same_pct", "year_same_pct", "lo_ic_year", "lo_ic",
                                  "hi_ic_year", "hi_ic",
                                  "fold_same_pct", "fold_last", "fold_n", "recent_ic", "recent_sign_ok",
                                  "drift_ratio", "n_days", "span") if x in rl.columns]
            _pct = ("roll_same_pct", "year_same_pct", "fold_same_pct")
            st.markdown("**稳不稳** —— 上面那个数是把逐日 IC 摊平成 16 年的平均；"
                        "下面这栏用**同一段代码**（面板与逐日截面 IC 全部 import 环 1）"
                        "把同一批逐日 IC 按时间窗口切开重算")
            st.dataframe(rl[show_r], use_container_width=True, hide_index=True,
                         column_config=num_fmt(
                             rl[show_r],
                             {c: "%.4f" for c in rl[show_r].columns
                              if ("ic" in c.lower() and not c.endswith("_year"))
                              or c in _pct or c == "drift_ratio"}))
            st.caption("怎么念：**同号占比**（`roll_/year_/fold_same_pct`）= 把这段历史按"
                       f"滚动一年 / 自然年 / 按时间等分 {ASHARE_ROLL_FOLDS} 段（**这是当前配置的折数**）各算一次 IC，其中有多少窗口和"
                       "全样本同正负号，**1.0 = 没有任何一个窗口打脸**（等分折那一格 09-30 拨过十五档、"
                       f"最后把折数定在 {ASHARE_ROLL_FOLDS}：段长只有一百多天，21 条里 7 条开始打脸，"
                       "这 7 条与自然年腿、滚动一年腿打脸的那批**逐条相同**，而且起点前后挪 0~9 天读数不搬家"
                       "⇒ 它不是「越细越严」的新刀，是把本来就存在的不稳定量出来；⚠️ 打脸**条数**本身不判："
                       "把时间顺序打乱 200 次的噪声底，中位 4 条、最多 7 条 —— 真序那 7 条正好压在噪声的"
                       "最大值上（经验 p 0.010，10-01 归档刷新后实测）⇒ 只认「哪几条被打脸」，不认「共几条」）；`lo_ic_year/lo_ic` 与 "
                       "`hi_ic_year/hi_ic` = 逐年 IC **最低与最高**的那两年及其值 —— 只说高低、"
                       "不说好坏，因为这批因子 IC 全为负，**最高的那年反而是反转最不打用的年份**；"
                       "`recent_ic` 与 `recent_sign_ok` = 近 3 自然年还站不站得住；"
                       "`drift_ratio` = 整段趋势走过的漂移 ÷ 自身水平，**绝对值 > 1 说明末期"
                       "数值主要由趋势决定**而不是由水平决定。五条边界：① 这些是**读数**，"
                       "一条判据都没挂；② 在库因子是全样本选出来的，所以量的不是真样本外，"
                       "是「分段稳不稳 + 近期衰减没有」；③ **同号 ≠ 好** —— 这批因子 IC 全为负"
                       "（次日反转方向），1.0 只表示「稳定地朝一个方向排」；④ 本表与上面那张"
                       "环 1 表的差若落在 1e-5~1e-4，那是**归档表落后于面板**（面板每天被日更 ②"
                       "全量重生成），入口会把这条差值和两边落盘时间一起打出来，不是两套算法；"
                       "⑤ `fold_*` 那三列是**归档那一次**用的折数切出来的，归档里带一列 `fold_n` 记下"
                       "当时真的切了几段 ⇒ 它和上面这个当前值不一致时本页会直接黄条警告（09-30 从 5 拨到 25 "
                       "那三天就是靠人肉才发现不同版，10-01 起这列补上了）；但这台秤**不在日更链上**，"
                       "再拨这根旋钮要自己重跑 `run_ashare_rolling_ic.py`（实测扫面板 945s ≈ 16 分钟）。")

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
        # 这一段里的统计**全部从表里现算**，不写死：09-25 一天之内名单形状换了一档，
        # 写死的中位数当天就变成了假账（同 archived_gate/archived_list_scheme 的理由）
        _ex = p["excess_univ_ew_ann"]
        _px = p[p["signal"].str.contains("Price")]
        # 本轴那三行按 **expr** 找，不按显示名：表里 `signal` 列写的是「量能水平
        # SMA(Volume,20)」这种构造名，「安静度」是名单列名，拿它筛会筛出空集
        _ax = p[p["expr"] == BUY_EXPR]
        st.caption(f"多头腿结论（本表 `gate` = `{ARCH_GATE}`、名单形状 = `{ARCH_LIST}` "
                   f"两档下的读数，已过**复权收益假台阶护栏** + "
                   "**成交额口径修正**，见「数据与口径」）：做多低量能侧的超额中位 "
                   f"**{_ex.median():+.2%}**（{len(_ex)} 行里 {int((_ex < 0).sum())} 行为负），"
                   f"而「低价股」对照（MA/VWAP/MAX of Price，与量能无关）同段超额 "
                   f"{_px['excess_univ_ew_ann'].min():+.1%}~{_px['excess_univ_ew_ann'].max():+.1%}、"
                   f"换手 {_px['one_way_turnover'].min():.2f}~{_px['one_way_turnover'].max():.2f}"
                   f"（本轴 {_ax['one_way_turnover'].min():.2f}~{_ax['one_way_turnover'].max():.2f}，"
                   f"是对照的一倍多） —— 低量能侧那点收益是「买冷门/低价一角」"
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
            "主判据 = **逐日截面原始值 Pearson 均值**，09-30 起与 rdagent 统一为**带符号**比较："
            "`pearson_signed_max` ≥ 0.99 才会被判重复并 `Skip loop`（整轮机时白烧）——"
            "那一格只回答「循环会不会丢它」。|corr| 那把尺（`pearson_max`）没有删，降级成读数，"
            "并继续喂本线**自己**的政策闸 0.90：反号两根是同一条信息的两个符号，对本线库算同簇 "
            "⇒ 跟在库反号的**镜像**因子仍不进 CANDIDATE LIST，只是罪名从「循环会丢」换成「同簇」。"
            "两道尺子差几条由入口自报（`[两道尺子之差]` 那行；09-30 复跑默认 7 条候选 = **0/7**，"
            "产物在 `stock/v1/temp/tmp_signed_bar_0930/prod_rerun/`；10-01 已用**同一入口**把本表"
            "刷成带符号那一列（第 3 列 `pearson_signed_max`，判词一条没翻）。"
            "命名闭集为 `<N>-day <OP> of <Column>`，"
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
| 成交额 | **复权价 × `$volume` × 100**。`$volume` 不是手，是**复权成交量** = 真实手数 ÷ `$factor` | 绝对判据（09-23 探针 `stock/v1/temp/probe_live_sources4_0923.py`）：拿新浪自报成交额对着除，此式 54/54 只票比值落 0.99~1.01（抽样按 `$factor` 十分位分层，覆盖 0.0070~1.24），「盘面价×`$volume`」那版逐票散布 **177 倍、0/54 命中**。09-22 全市场按此口径 = **2.14 万亿** |
| 流动性闸门 | 20 日均成交额 ≥ {ASHARE_PORT_MIN_AMOUNT:.0e} 元 | 这条 09-23 折过一次返：中途把公式「修」成盘面价×量，方向反了（等于给成交额再乘 1/`$factor`），闸门对老票放水。按闸门用的 **20 日均额**口径，09-22 实测 **261 只**真成交额不足 2000 万元的票被错放行、反向只错挡 3 只（与日频名单 5484→5226 行的逐行差一一对上）；按单日成交额则是 359 / 1 |
| ⚠️ 已知口径缺陷 | `$volume` 内含 1/`$factor`：票级 1/`$factor` 中位 **8.4**、p99 **143**、最大 **1152** ⇒ 「放量」里混着复权基准，`$factor` 小（涨幅大或分红多）的票被系统性看成高量能 | **成交额修对 ≠ 量能构造干净。**四条构造仍按面板口径（`STOCK_VOL_BASIS=adj`，与历史基线和 RD-Agent 沙箱同源），真手数口径的对照复核走 `STOCK_VOL_BASIS=real`。本模块先前那句「量能族只用 `$volume`，不含价格与 `$factor`，不受失真影响」**是错的，已作废** |
| 收益护栏 | 复权开盘收益与盘面开盘收益对看：**只有复权侧**超 ±30% 者裁回 ±30%（`RET_LIMIT`） | 生产切片 90 个 (票,日) 命中（BJ 69 / SZ 15 / SH 6；09-23 记的 326 是错数，已按生产代码重算）。**2023-10-16 一天 64 只**，等权日收益被凭空抬 **+19.89pp**（未裁剪 +19.82% vs 当日中位 −0.52%），全历史没有第二天抬过 0.5pp。真实除权必在盘面价留同幅缺口，新股首周的真暴涨两套价同时越界，故「只有复权侧越界」= `$factor` 的假台阶而非行情 |
| 次新闸门 | 已有行情 ≥ {ASHARE_PORT_MIN_LISTED} 交易日 | — |
| 涨停闸门（回测） | 次日开盘涨幅 < 阈值；阈值口径与日频执行闸**共用一个开关** `STOCK_TRADABLE_GATE`，三档：`flat` 全线单一 {ASHARE_PORT_LIMIT_UP:.1%} / `board` 板块限幅 × {ASHARE_LIMIT_NEAR:.0%} / `dated` 阈值同 board，但每段限幅**按买入日那天已生效的那一版**取（见下一行）。本页读的是进程里**当前生效档：`{ASHARE_TRADABLE_GATE}`**，与 config 同一次 import，不是写死的；下面这行口径文字由 `ashare_screen.gate_desc()` 单点构造，回测横幅、日频打印、看板三处同一份：**{gate_desc()}** | 面板最后一天**无法前瞻**，留给人工开盘前确认。归档基线三张表（`ashare_portfolio_eval*.csv` / `_exclusion` / `_buylist`）**已是 `board` 档**；`flat` 档那一批另存 `*_flat.csv`、`dated` 档另存 `*_dated.csv`，行内 `gate` 列自报是哪一档 |
| 板块限幅**生效日**（`dated` 档专用） | {' / '.join(f'{k} {ASHARE_BOARD_LIMIT_UP[k] * ASHARE_LIMIT_NEAR:.1%}（{v[0]} 起，之前 {v[1] * ASHARE_LIMIT_NEAR:.1%}）' for k, v in sorted(ASHARE_BOARD_LIMIT_SINCE.items(), key=lambda kv: kv[1][0]))} | `board` 档拿**今天**的限幅去判 2016 年的买入日，那是时代错置：那段创业板只有 ±10%，用 19% 当阈值会把真封死买不进的票判成买得进。`dated` 补的就是这一笔。能咬到的范围比想象窄（`instruments/all.txt` 6160 行盘点）：只有**创业板**有 837/1449 只在 2020-08-24 之前已上市，科创板 0（首批上市即该板块开板日 2019-07-22）、北交所 1 ⇒ 与 board 的全部差异只可能落在 2015-01-05~2020-08-23 的创业板，2021 年以后两档逐位相同。**日频侧用不到 dated**：今天没有「生效日之前」可言，两档对当日名单完全等价，实盘保持 board |
| 剔除阈值（「不该买」这张域） | 池内截面分位 ≥ {ASHARE_SCREEN_QUANTILE:.0%}，启用构造取**并集**（≥1 条判响即剔） | 09-24 组合层实测（`board` 档，表内 `gate` 列当前 = `{ARCH_GATE}`）：并集在减法腿值 **+6.46%/年**，最佳单条只有 +4.37%（边际 水平 +1.11 > 动量 +0.72 > 波动 +0.25 > 比 -0.03pp）⇒ 这张域保持并集，一条都不撤。**09-25 换名单形状后这张表 13 行一字未动**（它剔的是整个可投资域，与名单怎么排无关），所以这一条不需要重新裁。`data/results/ashare_portfolio_exclusion.csv` |
| 待买入剔除闸 | 同一批构造、同一个分位阈值，但要 **≥{ASHARE_BUY_MIN_HITS} 条一致判响**才挡（`STOCK_BUY_MIN_HITS`，拨回 1 = 两处统一） | 把上面那张并集掩码直接压在名单上是**净负**：现轴 + 现名单形状（配额档 570 天）实测 不挡 +0.11% → 挡到 ≥1 档 **-2.79%**、单程换手 0.223 → 0.465；同网格的全局名单档是 +1.55% → -3.78%、换手 0.186 → 0.433（`ashare_portfolio_buylist_globalctrl_0925.csv`），换轴前那根 `STD($volume,20)` 只从 +0.95% 打到 -0.39% ⇒ **方向和「挡得越狠越亏」的顺序换过两次口径都没变，但这一半的钱从 5.3pp 缩到 2.9pp**。机制要说准（09-24 那句「恒空操作」在配额档下**作废**）：排序轴与「量能水平」那条构造是同一个表达式，全局档下「单条·量能水平」与不剔除**逐字相同**（差 0.0e+00）、两个「留一」行与 ≥1 那行也逐字相同；配额档下这两行反而**略高于参照**（+0.51% / +0.30%）—— 不是构造变强了，是剔除改变了「回填席位补进谁」，把最吵闹的票拿掉后主板席位补回来的是更安静的票。量级 0.2~0.4pp，在 IR 0.01~0.05 的噪声水位上读不出方向，只够用来否定「恒空操作」这句死话。两个「留一」行仍贴着 ≥1 那行（差 0.08pp / 0.10pp）⇒ 拆掉水平/波动两条毫无损失；名单上的伤害**全部**来自动量（相对参照 -1.96pp）与比值（-2.19pp）这两条近乎正交的构造。取 ≥3：比不挡高 0.17pp、≥4 高 0.01pp、换手 0.224 vs 0.223 ⇒ 挪动的只数读不出方向，仍然不是调出来的参数。`ashare_portfolio_buylist.csv`（旧轴账单另存 `_std20axis.csv`、全局档同网格对照另存 `_globalctrl_0925.csv`） |
| 名单独立闸（09-26 接、**09-27 起默认关**） | 表达式 `(-1.0*((low / close)))`（页面上叫「下影深度」，排序等价于 1 − 当日最低价/收盘，分位越高 = 盘中砸得越深）在**池内**分位 ≥ {ASHARE_BUY_EXTRA_QUANTILE:.0%} 者挡在待买入名单之外。它**不进**上一行「命中几条」的计数、**不改**「不该买」那张域、**不动**减法腿。**这一场没启用**（config 默认 = 空串，`buy_extra_fired` 为空 ⇒ 上面那段独立闸说明也不会显示）。开关 `STOCK_BUY_EXTRA_RULES`（空串 = 关掉 = **现在的默认**，传 `low0` 零代码再打开）、刀口 `STOCK_BUY_EXTRA_QUANTILE`（09-26 晚另立的独立旋钮，**默认 0.80**、与上面两行那根 {ASHARE_SCREEN_QUANTILE:.0%} 同值但**各拨各的** —— 共用的话调这道闸会连「不该买」那张域一起收紧；另立换来的只是「以后想单独收浅不必改代码」这一个自由度），判据本体只有 `ashare_screen.BUY_EXTRA_RULES` 一处 | 来路：Alpha158 全市场穷举 157 条里唯一走到接线这一步的一条（40 条吃 high/low 的候选过判重环**全部可提名**，最大截面相关 0.7914 < 门槛 0.90；8 条高 |IC| 者进组合层长腿被换手税杀掉，单程换手 0.81~0.96 vs 现轴 0.223）。⚠️ 表达式吃 `low`：09-26 之前求值环境把 high/low 绑成收盘的占位，这条会被压成常量 −1.0，接线排在这次修复之后才成立。**三种接法只有一种为正**（配额档 top50、570 个调仓日、扣双边 15bp，`stock/v1/temp/a158_five_rule_ctrl_0926.py` → 产物 `stock/v1/temp/tmp_alpha158/five/`）：现口径 +0.28%/年 → 并进那四条 +0.04%（≥1 档 -3.22%）→ **独立叠在名单层 +1.04%/年**。并进会掉的机制：并集挡的是「命中几条」，一条一踢池内 20% 的构造会把 n_hit 整体抬高、把「≥3 条一致判响」冲淡。**刀口深浅另量了一遍**（`stock/v1/temp/buy_extra_gate_shallow_0926.py`，㊲）：0.80/0.85/0.90/0.95 四刀 × 四档 = 20 行，top50 那档超额 **+1.04（现踩）**/+1.96/+1.20/+0.19%、换手 0.334/0.304/0.275/0.247 ⇒ 收浅把「毛增益被税吃光」治住了（费耗增量 +1.67→+0.35pp 严格单调）但毛差非单调。**样本外复核**（`buy_extra_gate_oos_0926.py`，㊳）：0.85 是 16 个档格里唯一前后两半同正的（+1.83/+1.52pp），钉死九年 +1.23%/年、四个固定切点的训练窗一致挑它；而 0.80 在 2018 年后翻负（后 −0.21pp）。**09-26 夜拍板：留在 0.80**——0.85 那笔的正号来自「样本内选中、样本外确认」而不是纯样本外，且收浅换来的 +0.92pp 与刀口之间的跳变（±0.9pp）同量级 ⇒ 当作**待样本外观察的候选**记着，不进生产。边界要念全：这些都是**同一批 2015~2026 数据**上量的（刀口是「样本内选中、样本外确认」，因子本身全样本挑的），只量过 top50 那一档，2023 年四刀一起 -7.1~-10.0pp，且那一腿的口径**不含**下面三道执行闸；实盘首场（09-24 那一场重跑）它咬掉 **10/50** 只观察名单，下单那 5 席一只没换。**⚠️ 09-27 把它从生产撤下来（㊷㊸）**：同一个信号搬到真正掏钱的 **5 席下单层符号翻掉** —— @0.80 年均超额从 +12.50% 掉到 +3.58%（**-8.78pp/年**）、12 个自然年只有 2 年为正（2015、2019）、337/570 场整篮换人、单程换手 0.272 → 0.419；四档刀口 0.80/0.85/0.90/0.95 **全负且不单调**（-8.78/-4.10/-1.57/-5.14pp ⇒ 换手税确实随收浅单调好转 0.419→0.301，治不住的是选股），峰值位置还从名单层的 0.85 漂到下单层的 0.90 ⇒ 读不出可信最优刀口。2023 单年净差 -50.03pp 拆账（同一回放只改 cost，恒等式 净差=毛差−税差 验到 1e-12）= **毛差 -47.79%、税差只 +2.25% ⇒ 96% 是「换进来的那几只不行」**，与名单层那笔「毛增益被换手税吃光」是两种病；最狠一场 2023-11-21 单场 -27.16%（挤掉的两只那 5 日平均 +68.5%）。回撤这一头它也没牙：㊶㊷ 七条腿的最深回撤全部锁在 -53.6%~-57.9%，没有一档压到 -40% 以内 ⇒ **降回撤的路只剩仓位/减仓层**。判据本体（`BUY_EXTRA_RULES`、`buy_extra_block`、③ 的打印）一行没删，想再打开 `STOCK_BUY_EXTRA_RULES=low0` |
| 观察名单形状 | `{list_desc()}`（开关 `STOCK_LIST_SCHEME`=`{ASHARE_LIST_SCHEME}`，席位 {' / '.join(f'{k} {v}' for k, v in ASHARE_LIST_QUOTA.items())}） | 09-25 落地（用户拍「改」）。**这一行改的是「谁有资格进名单」，排序轴一个字没动**，但它同时是**一笔反号的交易**：下单那 5 只 **+11.70% → +13.04%/年**、5 席凑满率 75.3% → **100%**、换手 0.307 → 0.270、负超额年数 4 → 3、最深回撤不变（−53.6%）；而 50 只观察篮子**本身**的历史成绩从 +1.55% → **+0.11%/年**（IR +0.12 → +0.01，只有最大回撤从 −52.5% 变好到 −49.1%）。机制：配额花「安静度」买「板块分散」，尾部换进来的创业板票没那么安静，50 只平摊时代价摊满整张篮子；下单层只扫最前十几名，配额恰好把三段票提到前排所以拿到好处。**真实身份要念准**：科创板 570/570 天填不满 10 席、北交所 494/570 填不满 ⇒ 这份配额实际等于「给创业板留 10 个保底席位」，名单单一板块占比只从 0.94 降到 0.80。三个配额形状（20/10/10/10、每段 12、不回填）收益**逐格相同**，唯一的自由度是创业板上限。产物行内 `list_scheme` 列自报，全局档同日对照（570 天同网格）另存 `ashare_portfolio_*_globalctrl_0925.csv`、9-24 那批 569 天的另存 `*_globalrank.csv`。`data/results/quota_watchlist_m4_0925.csv` 与 `quota_watchlist_m4_0925_yearly.csv` |
| 待买入排序轴 | 待买入域内 `{BUY_EXPR}` **升序**取 {ASHARE_BUY_TOP_N} 只，等权；名单长什么形状见上一行（**这根轴本身没被名单形状改过**）。**09-24 换过轴**（用户裁决）：旧轴 `STD($volume,20)` | 当时换的理由是生产名单那一档（top50）三项全胜、且换手只有旧轴的六成：全局档 570 天实测 `SMA(Vol,20)` top50/100/200 = +1.55%/+1.80%/+1.81%、换手 0.186/0.176/0.162，旧轴 = +1.09%/+2.49%/+3.96%、换手 0.311/0.282/0.257（`ashare_portfolio_eval_globalctrl_0925.csv`；09-24 那批 569 天读数是 +1.39%/+1.59%/+1.58%，同序）。⚠️ **两处措辞现在要更正**：① 「三档上都不差于旧轴」当时就不成立 —— top100/200 两档从来都是旧轴更高（上面那行数字自己就顶着）；② 09-25 换名单形状后**连 top50 也翻了** —— 配额档 `SMA(Vol,20)` = +0.11%/+1.25%/+1.54% vs 旧轴 +1.00%/+1.73%/+3.69%（`ashare_portfolio_eval.csv`，`list_scheme` 列 = `{ARCH_LIST}`），本轴在 50 只这一层只剩换手优势（0.223 vs 0.341）。换轴的另一条证据（中间档 2021 起六年 4/6 为正、旧轴 2/6）两种名单形状下都没变。⚠️ 两点边界：① 换轴是**改判据**，所以 ⑮ 那套剔除强度账单已按新轴全窗口重跑，别拿旧账单的钱读新名单；② 名单入口那一层只在 top50 档量过，拨大 `STOCK_BUY_TOP_N` 要连同剔除闸重量。样本内、未过准入链，且与表内「低价股」对照分不开（对照 top50：全局档 +4.19%、配额档 +3.10%，两种形状下都盖过本轴且换手只有其一半）⇒ 它交出来的是**待人工复核的排队顺序**，不是收益承诺。详见「🛒 待买入名单」页脚 |
| 待买入执行闸 | 在排序之前再叠三道：当日无成交/停牌、收盘涨幅 ≥ **本板块限幅 × {ASHARE_LIMIT_NEAR:.0%}**（不追）、名称含 ST/*ST | 前两道是「买不买得到」，第三道判据来自当日收盘快照的「名称」列（历史日无快照则不跑，页顶会黄条提示）。限幅按板块分档：{' / '.join(f'{k} {v * ASHARE_LIMIT_NEAR:.1%}' for k, v in ASHARE_BOARD_LIMIT_UP.items())}。09-24 探针 `stock/v1/temp/probe_board_limits_0924.py` 实测：过去用单一 {ASHARE_PORT_LIMIT_UP:.1%} 时，历史被挡下的三段票里 **81~85%** 离自己的涨停还远得很（科创 10066/12419、创业 46859/55367、北交 5382/6533）——那笔误伤恰好落在三个有权限的板块上。回测的第 4 道闸走**同一个开关** `STOCK_TRADABLE_GATE`，两条路径不许各拿一份判据。三档里 `dated` 是回测复现历史规则用的，日频那一侧它和 `board` 逐字相同（见上两行） |
| 下单层 | 从待买入名单按名次往下扫，同板块 ≤ {ASHARE_ORDER_MAX_PER_BOARD} 只、同行业 ≤ {ASHARE_ORDER_MAX_PER_INDUSTRY} 只，凑满 {ASHARE_ORDER_TOP_N} 只即停；**权重等权满仓 = 每只 1/实际只数**（凑不满既不放宽判据、也不留现金。09-25 由 `1/{ASHARE_ORDER_TOP_N}` 换过来） | 这一层**只解决「一次下得完」**，不参与判据：名单的排序轴一个字没改（09-25 改的是**名单形状**，见上一行；那一改的钱正好落在这层 —— 席位 75.3% → 100% 凑满、+11.70% → +13.04%/年）。板块与行业都是分散度约束、不是白名单——科创板/创业板/北交所均有权限，所以没有任何一段被拉黑；映射缺的行业（值「未知」）也不参与去重，否则北交所会被 85% 的映射缺口代理排除。**换满仓的依据**（这一批是在**全局前 50** 的名单上量的，㉘ 换名单形状前）：570 个调仓日回放里 5 席有 **24.7%** 凑不满、平均 **6.6%** 资金闲置，改成满仓给权后历史超额 **+9.90% → +11.70%/年**、最大回撤**一字未变**（−53.57%，最深那次回撤发生在 5 席全满的区间）⇒ 留现金没买到安全；当时的代价是只挑到 3 只时每只 33%（历史最差就是缺 2 席）。`data/results/order_layer_fullinvest_0925b.csv`。**换成配额名单后这一层顺带被治好**：席位凑不满率 24.7% → **0%**（570/570 天满 5 只），历史超额 +11.70% → **+13.04%/年**、最深回撤仍是 −53.57%，所以「缺席位摊权重」那条代价在配额档下读不到 —— 但**实盘当天照样可能只挑到 3~4 只**（㉗ 的 e2e 三分支实测过），每只权重照样升到 25%/33%，这条不是纸面风险 |
| 费率 | 佣金万 2.5 双边（最低 5 元）+ 印花税万 5（卖出） | 回测用综合单边 15bp（含滑点万 10） |
| T+1 / 整手 | 买入须为 100 股整数倍；当日买入不可当日卖出 | 账本按日初持仓快照校验 |

### 本看板**不能**告诉你的事

- **盘中**：全部判据来自已落库的日线（`daily_pv.h5`），本线暂不做盘中分析。
  日更没跑，页面给的就是上一场的名单。
- 待买入名单**不是**收益承诺：它的排序轴只有样本内证据（分年度按中间档 top100 统计，
  新轴 `{BUY_EXPR}` 在**当前这份配额名单**下是 2015~2020 五正、2021 起四正、最近一年为负；
  被换掉的旧轴 `STD($volume,20)` 是 2015~2020 五正、2021 起只有两正且近两年连负 —— 换轴换的就是
  这一段）。⚠️ 09-25 换名单形状给这一条加了个新代价，要说白：**「把名单这 50 只都买」这种
  用法的历史成绩从 +1.55%/年 掉到 +0.11%/年**（配额换来的是板块分散，花掉的是安静度），
  只有「从名单最前面挑 5 只下单」那一层是净赚（+11.70% → +13.04%）。所以这张名单该按
  「前排拿来下单、整张拿来人工复核」用，不该当成一篮子买；它和「低价股」对照也分不开
  （对照在两种名单形状下都三档盖过本轴）。它回答的是「人工先复核这 50 只」，不回答「买它会涨」。
- 名单形状与涨停闸是**两个独立开关**（`STOCK_LIST_SCHEME` / `STOCK_TRADABLE_GATE`），
  账单只对自己那一档成立：产物行内 `gate` + `list_scheme` 两列就是这两件事的自报，
  跨档引数字前先对这两列。
- 卖什么：剔除规则验证的是「买不进就不买」，没有验证「踢掉已持有的票能否改善组合」。
""")
