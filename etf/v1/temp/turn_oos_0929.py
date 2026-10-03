# -*- coding: utf-8 -*-
"""#52 的样本外那一刀（选项3，09-29）：把「每 k 天调一次仓」摊到逐年，问"少换更划算"过不过得住

跑法：`/usr/bin/python3.10 etf/v1/temp/turn_oos_0929.py`（实测见最后一行）

大目标与本份的位置
------------------
换手旋钮（`rebalance_every`）在 09-28 的全样本账单上很诱人：k=1（现状）十六年 **−36.20%**，
k=10 **+117.83%**、摩擦费从净值每年 6.38 个点降到 1.74 个点。但那份是**全样本一把挑**：
把十六年的路走一遍再回头说"早该每 10 天换一次"，等于拿答案挑答案。这一份把它摊成**逐年**，
再演一遍人真会怎么做：**只用某年之前的年份挑 k，然后看那一年实际落在谁身上**。

必须先把丑话说在前面（这份读数能证明的与不能证明的）
    ✅ 能证明：「调仓间隔」这根旋钮的收益排序**跨不跨得过段**、以及"只用过去挑"要付多少代价。
    ❌ 不能证明：打分本身是真的样本外。面板 `score_panel.pkl` 里的分数来自**全历史挖出来的
       因子库**（环1/环2/环3 都在全样本上挑过），所以任何切法都救不了信号那一层。
       这一条与股票线 ㉟ 的「只有刀口深度做到只用过去」是同一个坑，措辞不许漂。
    ⚠️ 池子只有 20 只、早年更少：2010~2013 的截面可能只有几只票 ⇒ 早年的"年度最优"含金量低，
       每年那行会打印**当年有数据的只数**，读表时先看在不在。

口径（与 09-28 那份逐字同源，只是把时间轴切成段）
    同面板、同 `select_weights`、同生产回测引擎、同 `annual_turnover`。
    段内 `idx = [i for i in range(len(段内 bar)) if i % k == 0]` ⇒ **每段第一天必调仓、本金重置**，
    所以"逐年收益的几何复合"**不等于**全样本净值路径（切段就把连续复利与回撤路径剪断了），
    两个数都会打印、差多少也打印，不许谁冒充谁。

八道反证（不许恒真，方向都要有）
    G0 裁完 asof 之后时间轴 bar 数 == 钉的那个对象的 bar 数（钉昨天=JSON 的 4064；
       钉归档=归档净值表行数 4065）⇒ 价格面和它是同一个。
    G7 面板最后一天 ≥ asof ⇒ 打分面真的覆盖到钉的那一天。
       （这条是被自己坑出来的：09-29 00:36 我定义了 `OOS_PANEL` 却忘了把读取处换掉，
        拿**旧面板**配 09-28 的 asof 跑出一场 −38.46%/21,022 笔的假读数，看着完全正常。
        光有"钉住 bar 数"不够，还得钉住**分数也长到那一天**。）
    G1 分段不漏 bar：各年段 bar 数之和 == 全样本 bar 数（与 G0 同一个数）。
    G2 每段每档调仓次数 **精确等于** `ceil(段内 bar / k)` ⇒ 换手不是"少喂 bar"喂出来的。
       （初版写成"≈ bar/k 容差 2%"是我自己的错：一段只有 240 bar、k=20 时真值是 12 次向上取整
        的 13 次，2% 容差必然红。09-29 凌晨 00:20 那场 17 年 ×6 档 102 段按精确恒等式全过。）
    G3 无牙对照：同一年内各 k 的年收益 max−min ≥ 0.5pp，否则那一年判"旋钮无牙"、剔出结论并**点名**。
    G4 **全样本**换手随 k 严格单调降（不满足就报是哪两档）。年度层面的反转只点名、
       不进门禁：09-29 凌晨在新面板上实测 2010/2013 各一处，都发生在活着标的只有
       3/9 只的窄截面上；但同一批年份在旧面板上全部单调（截面宽度、空仓比例几乎没变，
       只换了分数版）⇒ 它是窄截面的取样/奇偶噪音，不是引擎恒等式，当门禁会误伤装置。
       新旧两版面板的全样本层都严格单调，详见下面 G4 处的注释。
    G5 全样本 k=1 仍逐位复现归档净值末值与归档 trades（把今天的装置钉在生产产物上）。
       **09-29 凌晨实测：这一条已经红，且红因是版本漂移不是装置**——归档被 09-28 22:04→22:34
       那场 `research_daily`（环3 判重改动落地后的第一场，4065 bar）整轮重跑，变成 19,512 笔/
       末值 4,977.64；本场用的是 09-27 那份面板 pkl（21,012 笔/末值 6,380.39）。
       ⇒ G5 降级为"只报不门"的版本探针，装置的正确性交给 G0 + G6。
       这也意味着**本份与昨天那份账单都挂在 09-27 面板上**，引用绝对水平前要先重量。
    G6 **正对照**：把"分段范围"设成整段跑 k=3，必须逐位复现 09-28 JSON 里的
       `total_return` 与 `annual_turnover`（+86.64% / 2880.7%）。这是另一条代码路径留下的数，
       分段装置写错就红 —— 光有"内部自洽"的自检不算验证。

覆写面：只写 `etf/v1/temp/tmp_conc_0929_oos/`（新目录），`etf/v1/data/` 与昨天的读数**只读**。
（例外：`DataLoader` 的"缓存自愈"会回写 `etf/v1/data/cache/` 里那批过期的日线缓存 ——
本份跑前已把 44 个缓存文件连 md5 存到 `tmp_conc_0929_oos/cache_before/`，跑完逐一对表。）

为什么必须钉 asof（09-29 凌晨实测到的）
    昨晚日更链第①步把价格面推到了 09-28，但**推得不齐**（任务 #54：池缓存 19 只止于
    09-24、只有 159570 到 09-28，而镜像把所有只都推到 09-28）。时间轴因此从 4064 bar
    变成 4065 bar，**同一份判据、一行代码没改**，整段 k=1 的总收益从 −36.20% 变成 −38.46%
    （2.26 个点），换手 5017.0% → 5017.6%。这就是"多一天"的代价。
    ⇒ 本份用 `ASOF`（默认 2026-09-24）把池子裁回昨天那个截面，G5/G6 才有资格逐位对表；
    不钉 asof 的话，G5 会**由构造决定地**红，而不是因为分段装置写错。
    注意 asof 只裁**价格那一侧**：面板分数是全历史挖的，裁日子救不了信号层（见开头 ❌）。

两个面板、两趟跑法（09-29 选项B）
    本份读哪块面板、钉哪个锚，全部由环境变量决定，**默认值就是已经报价那一趟**，
    免得把凭证覆写掉：
        旧面板（09-27 pkl，已报过价的账单）
            /usr/bin/python3.10 -u etf/v1/temp/turn_oos_0929.py
        新面板（09-29 重建成 09-28 生产归档同一版，见 conc_panel_etf_0929.py）
            OOS_PANEL=$PWD/etf/v1/temp/tmp_conc_0929_panel/score_panel.pkl \
            OOS_ASOF=2026-09-28 OOS_OUT=tmp_conc_0929_oos_newpanel \
            OOS_PIN=archive /usr/bin/python3.10 -u etf/v1/temp/turn_oos_0929.py
    `OOS_PIN=prev`（默认）拿昨天 JSON 的整段 k=3 当正对照（G6），G5 只报不门；
    `OOS_PIN=archive` 反过来：拿**生产归档**当正对照（G5，归档是 main.py [7/9] 那条
    代码路径写的，跨路径逐位复现才是真验证），G6 不适用（昨天那份是旧面板的数，
    拿来当恒等式对照只会红得没有信息量）——此时末段会打印「面板漂移账单」，
    把同一档在新旧面板上的差摊开给人看。
"""
import json
import math
import os
import sys
import time

os.environ["ETF_FREQ"] = "daily"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "etf", "v1", "src")
RESULTS = os.path.join(REPO, "etf", "v1", "data", "results")
TMP_IN = os.path.join(HERE, "tmp_conc_0927")          # 昨天的读数与面板（只读）
# 面板/落点都可换；默认 = 09-29 凌晨已经报价的那一趟（旧面板 + asof 09-24）
PANEL_PKL = os.environ.get("OOS_PANEL", os.path.join(TMP_IN, "score_panel.pkl"))
TMP_OUT = os.path.join(HERE, os.environ.get("OOS_OUT", "tmp_conc_0929_oos"))
# prev=钉昨天 JSON 的整段 k=3（G6 门、G5 只报）；archive=钉当前生产归档（G5 门、G6 不适用）
PIN = os.environ.get("OOS_PIN", "prev")
assert PIN in ("prev", "archive"), f"OOS_PIN 只认 prev/archive，收到 {PIN!r}"
os.makedirs(TMP_OUT, exist_ok=True)
sys.path.insert(0, SRC)
os.chdir(SRC)

import _bootstrap  # noqa: E402,F401  必须先于项目模块导入（裸模块名 import 的 sys.path 引导）
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from config import (COMMISSION_RATE, MIN_COMMISSION, PORTFOLIO,  # noqa: E402
                    RISK_CONTROL, SLIPPAGE)
from etf_universe import get_universe  # noqa: E402
from data_loader import DataLoader  # noqa: E402
from strategy import select_weights  # noqa: E402
from backtest import get_backtester  # noqa: E402
from portfolio_engine import annual_turnover  # noqa: E402

KS = [1, 2, 3, 5, 10, 20]
# 钉日子：钉昨天 JSON 就裁到昨天那一版价格面（09-24），钉归档就裁到归档那一版（09-28）；
# 想量「不裁」的代价显式覆盖 OOS_ASOF 即可（开头已记下那笔账：多一天 = k=1 少 2.26 个点）。
ASOF = pd.Timestamp(os.environ.get(
    "OOS_ASOF", "2026-09-24" if PIN == "prev" else "2026-09-28"))
PREV = json.load(open(os.path.join(TMP_IN, "turn_readings.json")))
t_all = time.time()

panel = pd.read_pickle(PANEL_PKL)
# G7 面板覆盖面：面板最后一天必须不早于 asof —— 否则最后一个 bar 静悄悄没有分数，
# 整段读数会"看着正常"地少掉一天（09-29 00:36 就真把 OOS_PANEL 传进去却没接到，
# 旧面板配新 asof 跑出了一场 −38.46% 的假读数，靠这条才抓得住）。
g7 = panel.index.max() >= ASOF
universe = get_universe()
raw_pool = DataLoader(freq="daily").load_pool(universe.universe["code"].tolist())
raw_max = max(df.index.max() for df in raw_pool.values())
raw_bars = len(raw_pool[max(raw_pool, key=lambda c: len(raw_pool[c]))].index)
pool = {c: df[df.index <= ASOF] for c, df in raw_pool.items()}
pool = {c: df for c, df in pool.items() if len(df)}
all_ts = pool[max(pool, key=lambda c: len(pool[c]))].index
by_ts = {ts: dict(zip(g["code"], g["score"]))
         for ts, g in panel.groupby(level=0, sort=True)}
YEARS = sorted({ts.year for ts in all_ts})
print(f"面板 {len(panel):,} 行（末 {panel.index.max():%Y-%m-%d}）= {PANEL_PKL}"
      f" | 池 {len(pool)}/{len(raw_pool)} 只 | asof 裁前 "
      f"{raw_bars} bar（止于 {raw_max.date()}）→ 裁后 {len(all_ts)} bar"
      f"（止于 {all_ts[-1].date()}，asof={ASOF.date()}）"
      f" | 年份 {YEARS[0]}~{YEARS[-1]} 共 {len(YEARS)} 个自然年"
      f"| 单边成本 {(COMMISSION_RATE + SLIPPAGE) * 100:.3f}%"
      f"（最低佣 {MIN_COMMISSION} 元）")
print(f"09-27 面板那份报价（读 JSON，只为末段漂移对照；本场整段会自己重跑六档）："
      f"k=1 {PREV['tiers']['k=1']['total_return']:+.2%}"
      f" / k=3 {PREV['tiers']['k=3']['total_return']:+.2%}"
      f" / k=10 {PREV['tiers']['k=10']['total_return']:+.2%}"
      f" | 摩擦费 k=1 {PREV['tiers']['k=1']['fee_pct_of_nav_per_year']:.2%}/年"
      f" → k=10 {PREV['tiers']['k=10']['fee_pct_of_nav_per_year']:.2%}/年")


def run_tier(seg_ts, k):
    """在给定时间轴上按「每 k 个 bar 调一次」出一版信号，喂生产引擎，返回一坨读数"""
    idx = [i for i in range(len(seg_ts)) if i % k == 0]
    recs, codes_per_bar = [], []
    for i in idx:
        ts = seg_ts[i]
        w = select_weights(by_ts.get(ts, {}))
        if not w:
            codes_per_bar.append([])
            recs.append({"t": ts, "c": "", "weight": 0.0, "score": np.nan})
            continue
        codes_per_bar.append(list(w))
        for c, wt in w.items():
            recs.append({"t": ts, "c": c, "weight": wt, "score": by_ts[ts][c]})
    sig = pd.DataFrame(recs).set_index("t")[["c", "weight", "score"]]
    res = get_backtester(pool, universe, RISK_CONTROL, freq="daily") \
        .run(sig.rename(columns={"c": "code"}))
    eq, tr = res["equity"]["equity"], res["trades"]
    n_empty = int(sum(1 for c in codes_per_bar if not c))
    if not len(eq) or not len(tr):
        print(f"    ⚠️ k={k} 这一段没有成交或没有净值（空 bar {n_empty}/"
              f"{len(idx)}），读数不可与其余档并列")
    rets = eq.pct_change().dropna()
    years = len(eq) / 252.0
    total = float(eq.iloc[-1] / eq.iloc[0] - 1)
    ann = float((1 + total) ** (1 / years) - 1)
    vol = float(rets.std() * np.sqrt(252))
    fee = float(tr["cost"].sum()) if len(tr) else 0.0
    amount = float(tr["amount"].sum()) if len(tr) else 0.0
    return {
        "bars": int(len(seg_ts)), "n_rebalance": len(idx),
        "n_bars_with_signal": int(sum(1 for c in codes_per_bar if c)),
        "n_empty_bars": int(sum(1 for c in codes_per_bar if not c)),
        "n_trades": int(len(tr)),
        "annual_turnover": float(annual_turnover("date", tr, res["equity"], 252)),
        "total_return": total, "annual_return": ann,
        "max_drawdown": float(((eq - eq.cummax()) / eq.cummax()).min()),
        "sharpe": ann / (vol + 1e-9),
        "fee_total": fee, "fee_pct_of_nav_per_year": fee / float(eq.mean()) / years,
        "floor_pp": (fee - amount * (COMMISSION_RATE + SLIPPAGE)) / float(eq.mean()) / years,
        "final_equity": float(eq.iloc[-1]),
        "avg_holdings": float(np.mean([len(c) for c in codes_per_bar if c]))
        if any(codes_per_bar) else 0.0,
        # 当年**真有日线**的只数（早年的池子只有几只，"年度最优"含金量低）
        "codes_live": int(sum(1 for code in pool
                              if pool[code].index.searchsorted(seg_ts[0], "left")
                              < pool[code].index.searchsorted(seg_ts[-1], "right"))),
    }


# ---------- 钉在"昨天/生产"上的对照：整段六档先跑一遍 ----------
arc_df = pd.read_csv(f"{RESULTS}/equity_daily.csv", encoding="utf-8-sig")
arc = arc_df["equity"]
arc_eq1 = float(arc.iloc[-1])
arc_tr = pd.read_csv(f"{RESULTS}/trades_daily.csv", encoding="utf-8-sig")
# G0 的期望 bar 数按钉法取：钉昨天比昨天 JSON，钉归档比归档行数
EXP_BARS = len(arc_df) if PIN == "archive" else PREV["bars"]
g0 = len(all_ts) == EXP_BARS
print(f"[G0] 裁完 asof 的 bar 数 = {'归档行数' if PIN == 'archive' else '昨天 JSON 的 bars'}"
      f"（{len(all_ts)} vs {EXP_BARS}）"
      f" {'✅' if g0 else '❌ 价格面不是同一个，下面几条对表作废'}")
print(f"[G7] 面板末日 ≥ asof（{panel.index.max():%Y-%m-%d} vs {ASOF:%Y-%m-%d}）"
      f" {'✅' if g7 else '❌ 面板没覆盖到钉的那一天，末尾会静默空仓'}")
whole = {}
for k in KS:
    t = time.time()
    whole[f"k={k}"] = run_tier(all_ts, k)
    print(f"[整段对照] k={k} 用时 {time.time() - t:.1f}s："
          f"总收益 {whole[f'k={k}']['total_return']:+.2%}、换手 "
          f"{whole[f'k={k}']['annual_turnover']:.1%}")
g5 = (abs(whole["k=1"]["final_equity"] - arc_eq1) < 1e-6
      and whole["k=1"]["n_trades"] == len(arc_tr)
      and abs(whole["k=1"]["fee_total"] - float(arc_tr["cost"].sum())) < 1.0)
# G6：整段六档必须逐位复现昨天 JSON（另一条代码路径留下的 6 个点，不是自洽）
g6 = all(abs(whole[f"k={k}"]["total_return"] - PREV["tiers"][f"k={k}"]["total_return"]) < 1e-9
         and abs(whole[f"k={k}"]["annual_turnover"] - PREV["tiers"][f"k={k}"]["annual_turnover"]) < 1e-9
         and whole[f"k={k}"]["n_trades"] == PREV["tiers"][f"k={k}"]["n_trades"] for k in KS)
if PIN == "prev":
    print(f"[G5] 整段 k=1 复现归档净值与 trades 表 {'✅' if g5 else '❌（只报不门）'}"
          f"　本场 {whole['k=1']['n_trades']:,} 笔 / 末值 {whole['k=1']['final_equity']:,.2f}"
          f" vs 归档 {len(arc_tr):,} 笔 / 末值 {arc_eq1:,.2f}"
          f"（归档 {len(arc_df)} 行，止于 {arc_df['date'].iloc[-1]}）")
    if not g5:
        print("     ↑ 09-29 凌晨实测到的**红因**：归档已被 09-28 22:04→22:34 那场 research_daily"
              "（4065 bar、环3 判重改动落地后的第一场）整轮重跑，"
              "本面板是 09-27 那份 pkl ⇒ 这条量的是**面板版本漂移**，与分段装置无关。")
    print(f"[G6] 整段六档逐位复现昨天 JSON（总收益/换手/笔数）{'✅' if g6 else '❌'}"
          f"　最大 |Δ总收益| {max(abs(whole[f'k={k}']['total_return'] - PREV['tiers'][f'k={k}']['total_return']) for k in KS):.2e}"
          f" | k=1 {whole['k=1']['total_return']:+.4%} vs {PREV['tiers']['k=1']['total_return']:+.4%}、"
          f"k=20 {whole['k=20']['total_return']:+.4%} vs {PREV['tiers']['k=20']['total_return']:+.4%}")
else:
    # archive 模式：正对照换成生产归档（main.py [7/9] 那条代码路径写的数）
    print(f"[G5] ★正对照★ 整段 k=1 逐位复现生产归档（笔数/末值/Σcost）{'✅' if g5 else '❌ 面板还不是生产那一版'}"
          f"　本场 {whole['k=1']['n_trades']:,} 笔 / 末值 {whole['k=1']['final_equity']:,.2f}"
          f" vs 归档 {len(arc_tr):,} 笔 / 末值 {arc_eq1:,.2f} / Σcost "
          f"{float(arc_tr['cost'].sum()):,.2f}（归档 {len(arc_df)} 行，止于 {arc_df['date'].iloc[-1]}）")
    print(f"[G6] 不适用（钉的是归档）：昨天 JSON 属旧面板，不作恒等式对照；"
          f"两版面板的差在末段「面板漂移账单」单列。")
    g6 = None

# ---------- 逐年 × 逐档 ----------
yearly = {}
for y in YEARS:
    seg = all_ts[(all_ts.year == y)]
    if not len(seg):
        continue
    yearly[y] = {k: run_tier(seg, k) for k in KS}
    r = yearly[y]
    best = max(KS, key=lambda k: r[k]["total_return"])
    worst = min(KS, key=lambda k: r[k]["total_return"])
    spread = r[best]["total_return"] - r[worst]["total_return"]
    print(f"\n【{y}】{len(seg)} bar | 当年有日线的只数 {r[1]['codes_live']}/{len(pool)}"
          f" | 该年最优 k={best} "
          f"{r[best]['total_return']:+.2%} | 最差 k={worst} "
          f"{r[worst]['total_return']:+.2%} | 档间极差 {spread:.2%}")
    print("        " + "  ".join(f"k{k}:{r[k]['total_return']:+6.2%}" for k in KS))
    print("        " + "  ".join(f"换手{k}:{r[k]['annual_turnover']:5.0%}" for k in KS)
          + f" | 费/净值 k1 {r[1]['fee_pct_of_nav_per_year']:.2%} →"
            f" k20 {r[20]['fee_pct_of_nav_per_year']:.2%}")

# ---------- 反证 G1~G4 ----------
g1 = sum(v[1]["bars"] for v in yearly.values()) == len(all_ts)
bad2 = [f"{y}/k{k}:{v[k]['n_rebalance']}≠ceil({v[k]['bars']}/{k})={math.ceil(v[k]['bars']/k)}"
        for y, v in yearly.items() for k in KS
        if v[k]["n_rebalance"] != math.ceil(v[k]["bars"] / k)]
g2 = not bad2
spread_min = min(max(v[k]["total_return"] for k in KS) -
                 min(v[k]["total_return"] for k in KS) for v in yearly.values())
toothy = [y for y, v in yearly.items()
          if max(v[k]["total_return"] for k in KS) -
          min(v[k]["total_return"] for k in KS) >= 0.005]
g3 = len(toothy) == len(yearly)
bad4_whole = [f"k{a}→k{b}: {whole[f'k={a}']['annual_turnover']:.1%}→"
              f"{whole[f'k={b}']['annual_turnover']:.1%}"
              for a, b in zip(KS, KS[1:])
              if not whole[f"k={b}"]["annual_turnover"] < whole[f"k={a}"]["annual_turnover"]]
g4 = not bad4_whole
# 年度反转只当读数，不当门禁。09-29 凌晨在新面板上实测到 2010/2013 各一处：
#   2010 k1→k2（827%→869%）、2013 k2→k3（1156%→1319%）
# 这两年是全场最窄的截面（活着 3 只 / 9 只），k=1 时 46% / 21% 的调仓点凑不出持仓，
# 一次"空仓↔重建"就是单边 100% 换手，而换 k 会换采样日期奇偶、进而换落到哪一侧。
# **但这条机制解释不能当结论写进账单**：同一批年份在旧面板（09-27 pkl @asof 09-24）上
# 全部单调（2010 1120→786→692→570→429→223、2013 2028→1434→1284→883→625→562），
# 活着的标的数与空仓比例几乎没变，只是换了一版分数。⇒ 反转是**窄截面的取样/奇偶噪音**，
# 不是引擎恒等式，也不是稳定的"窄面必反"规律；拿它当门禁会把读数长这样错当成装置错了。
# 全样本那一层（4065 bar / 末段 100 只截面）新旧两版面板都严格单调，门禁放在那里：
# 若 k 根本没接进引擎（每 bar 都调仓），整段 k=2 的换手会与 k=1 相等，G4 必红。
inv4 = {y: [f"k{a}→k{b}（{v[a]['annual_turnover']:.0%}→{v[b]['annual_turnover']:.0%}）"
            for a, b in zip(KS, KS[1:])
            if not v[b]["annual_turnover"] < v[a]["annual_turnover"]]
        for y, v in yearly.items()}
inv4 = {y: x for y, x in inv4.items() if x}
print(f"\n──── 反证 ────\n[G1] 各年段 bar 之和 = 全样本 bar"
      f"（{sum(v[1]['bars'] for v in yearly.values())} vs {len(all_ts)}）"
      f" {'✅' if g1 else '❌'}")
print(f"[G2] 每段每档调仓次数 == ceil(段内 bar/k)（精确） {'✅' if g2 else '❌ ' + str(bad2[:8])}")
print(f"[G3] 每一年旋钮都**有牙**（档间极差 ≥0.5pp）：{len(toothy)}/{len(yearly)} 年有牙"
      f" {'✅' if g3 else '⚠️ 无牙年份 ' + str([y for y in yearly if y not in toothy])}"
      f"　最小极差 {spread_min:.2%}")
print(f"[G4] 全样本换手随 k 单调降 {'✅' if g4 else '❌ ' + str(bad4_whole)}")
print(f"[G4 读数] 年度反转 {len(inv4)}/{len(yearly)} 年"
      + ("：" + "；".join(f"{y} {' '.join(x)}（活 {yearly[y][1]['codes_live']} 只、"
                          f"k=1 时 {yearly[y][1]['n_empty_bars'] / yearly[y][1]['n_rebalance']:.0%} 空仓）"
                          for y, x in inv4.items()) if inv4 else ""))

# ---------- walk-forward：只用 y 之前的年份挑 k，读 y 年实际 ----------
AXES = {"净值最高": lambda d: d["total_return"],
        "夏普最高": lambda d: d["sharpe"],
        "费最省": lambda d: -d["fee_pct_of_nav_per_year"]}
pick_hist, regret = {a: [] for a in AXES}, {a: [] for a in AXES}
win_count = {k: 0 for k in KS}
for y in yearly:
    win_count[max(KS, key=lambda k: yearly[y][k]["total_return"])] += 1
print("\n──── 只用过去挑 k，随后一年实际落在谁身上 ────")
print(f"{'挑k的年份':<8}" + "".join(f"{a:>12}" for a in AXES) + f"{'该年最优':>10}")
for yi, y in enumerate(sorted(yearly)):
    if yi < 3:                        # 前三年样本太短，只当热身不参与决策
        continue
    past = [yy for yy in yearly if yy < y]
    row = {}
    for a, f in AXES.items():
        cum = {k: float(np.prod([1 + yearly[yy][k]["total_return"] for yy in past])) - 1
               for k in KS}
        shp = {k: float(np.mean([yearly[yy][k]["sharpe"] for yy in past])) for k in KS}
        fee = {k: -float(np.mean([yearly[yy][k]["fee_pct_of_nav_per_year"] for yy in past]))
               for k in KS}
        src = {"净值最高": cum, "夏普最高": shp, "费最省": fee}[a]
        chosen = max(KS, key=lambda k: src[k])
        best_now = max(KS, key=lambda k: yearly[y][k]["total_return"])
        pick_hist[a].append(chosen)
        regret[a].append(yearly[y][best_now]["total_return"] - yearly[y][chosen]["total_return"])
        row[a] = chosen
    best_now = max(KS, key=lambda k: yearly[y][k]["total_return"])
    print(f"{y:<12}" + "".join(f"{('k=' + str(row[a])):>12}" for a in AXES)
          + f"{('k=' + str(best_now)):>10}")
for a in AXES:
    print(f"    {a}：挑中次数分布 {dict((k, pick_hist[a].count(k)) for k in KS)}"
          f" | 平均落后当年最优 {np.mean(regret[a]) * 100:.2f}pp"
          f" | 中位 {np.median(regret[a]) * 100:.2f}pp"
          f" | 最惨一年 {max(regret[a]) * 100:.2f}pp")
print(f"    各档拿下「年度最优」的次数：{win_count}")

# ---------- 两份口径的差：逐年复合 vs 全样本路径（同面板基准，不借昨天的数） ----------
print("\n──── 切段把连续复利剪断了，两个数不可互冒充 ────")
for k in KS:
    comp = float(np.prod([1 + yearly[y][k]["total_return"] for y in yearly])) - 1
    path = whole[f"k={k}"]["total_return"]
    print(f"  k={k:<3} 逐年收益复合 {comp:+9.2%}   vs   全样本净值路径 {path:+9.2%}"
          f"   差 {(comp - path) * 100:+7.1f}pp")

# ---------- 面板漂移账单：同一档换一版打分面板差多少 ----------
print("\n──── 面板漂移账单（本场整段 vs 09-27 面板那份报价，同 asof 才有意义）────")
for k in KS:
    old, new = PREV["tiers"][f"k={k}"], whole[f"k={k}"]
    print(f"  k={k:<3} 总收益 {old['total_return']:+9.2%} → {new['total_return']:+9.2%}"
          f"（{(new['total_return'] - old['total_return']) * 100:+7.1f}pp）"
          f" | 换手 {old['annual_turnover']:6.1%} → {new['annual_turnover']:6.1%}"
          f" | 笔数 {old['n_trades']:>6,} → {new['n_trades']:>6,}"
          f" | 费/年 {old['fee_pct_of_nav_per_year']:.2%} → {new['fee_pct_of_nav_per_year']:.2%}")
if PIN == "archive":
    print("  ⚠️ 这一栏的差里有**两笔账**，不能全归给面板：钉的对象从昨天 JSON"
          "（旧面板 @asof 09-24，4064 bar）换成了生产归档（@asof 09-28，4065 bar）。"
          "只多一根 bar 的那笔账已经单独量过：同一份旧面板，asof 09-24→09-28 让 k=1 从"
          " −36.20% 变成 −38.46%（−2.26pp，见 tmp_conc_0929_oos/run_0929.log）。"
          "那是旧面板上的实测值，**不可加**到新面板上当扣除项，这里只用来提醒量级。")

json.dump({"pin": PIN, "panel": PANEL_PKL,
           "asof": str(ASOF.date()), "raw_bars": raw_bars,
           "raw_max_date": str(raw_max.date()),
           "clipped_bars": int(len(all_ts)), "n_pool_loaded": len(pool),
           "years": {str(y): {str(k): v[k] for k in KS} for y, v in yearly.items()},
           "whole_control": whole,
           "guards": {"G0_bars_match_pin": g0, "G7_panel_covers_asof": g7,
                      "G1_bars_sum": g1,
                      "G2_rebalance_count": g2,
                      "G3_all_years_toothy": g3, "toothy_years": toothy,
                      "G4_whole_turnover_monotone": g4,
                      "G4_whole_inversions": bad4_whole,
                      "G4_year_inversions_reading": inv4,
                      "G5_k1_matches_archive": g5,
                      "G6_whole_matches_prev_json": g6},
           "walk_forward": {a: {"picks": [int(x) for x in pick_hist[a]],
                                "regret_pp": [round(x * 100, 3) for x in regret[a]]}
                            for a in AXES},
           "yearly_best_wins": {str(k): v for k, v in win_count.items()}},
          open(os.path.join(TMP_OUT, "turn_oos_readings.json"), "w",
               encoding="utf-8"), ensure_ascii=False, indent=2)
# G5 与 G6 按 PIN 互换门禁角色：prev 模式拿昨天 JSON（另一条代码路径的 6 个点）当正对照，
# archive 模式拿生产归档（main.py [7/9] 写的）当正对照——两者都是**跨代码路径**的逐位复现，
# 不是"内部自洽"。G3 量的是「旋钮有没有牙」，无牙年份点名即可，不进门禁；
# G4 同理，只是把"点名"从年份换成了**全样本那一层当门禁、年份反转当读数**（理由见 G4 注释）。
gate = g6 if PIN == "prev" else g5
pass_all = g0 and g7 and g1 and g2 and g4 and gate
print(f"\n{'✅' if pass_all else '❌ 有反证不过，读数作废'}"
      f" 门禁=G0/G7/G1/G2/G4（全样本层） + {'G6（钉昨天 JSON）' if PIN == 'prev' else 'G5（钉生产归档）'}"
      f"　G3 {len(toothy)}/{len(yearly)} 年有牙；"
      f"G4 年度反转 {len(inv4)}/{len(yearly)} 年（只报不门）；"
      f"{'G5 只报不门' if PIN == 'prev' else 'G6 不适用'}"
      f" | 全程 {time.time() - t_all:.0f}s | 读数 {TMP_OUT}/turn_oos_readings.json")
sys.exit(0 if pass_all else 1)
