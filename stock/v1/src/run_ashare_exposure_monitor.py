# -*- coding: utf-8 -*-
"""股票线仓位层**前向监控**：每天落一行「四档减仓规则今天收盘各建议拿多少仓位」，**不接下单**（09-27 选项D）

为什么要有这一步：三次量化指向同一件事 —— ㊶ 六条腿最深回撤全锁在 −53.6%~−57.9%（名单
形状与独立闸都买不到安全）、㊷ 名单独立闸搬到 5 席层反号成年均 −8.78pp、㊺㊻ 仓位层最划算
那一档（M4-20）也是「每买回 1pp 回撤付 0.154pp 年化」而缓冲带 18 档无一档更划算。
问题是 **㊺㊻ 那 12 年既是挑参数的样本、又是记账的样本** ⇒ 再调旋钮只是在同一份历史上
换一种切法。所以这一步不再量历史，只往前攒账：每天落一行读数，攒够 ~120 场（半年）再判。

**这一步不参与任何决策**：不改 config 里任何判据、不接下单、不进日更链的验收（⑤ 失败只打
⚠️，链路照跑完）。它是流水账，不是信号。

账本 `ASHARE_EXPOSURE_LEDGER`（默认 `data/results/exposure_forward.csv`）：一行一场次，只往
长里攒；同一场重跑就覆盖那一行（幂等，判据改了也不回改历史行 —— 历史行是那天收盘的读数）。

口径与 ㊺㊻ 那两次量化**逐字同构**（权威实现见 `stock/v1/temp/dd_exposure_0927.py`，那里
的注释记着每一条的来路与五道自检）：
- **篮子腿**：信号日 i 出 5 席名单 → i+1 建仓（`2×15bp×(1−新旧重合席数/5)` 这笔换票费记在
  建仓那天）→ 从 i+2 起连拿 `ASHARE_PORT_HOLD`=5 个交易日的**开盘到开盘**收益，每席 `1/实际只数`。
  ⚠️ 所以行里「日期」那一天的 `篮子日收益` 是**开盘→开盘**，不是收盘→收盘 —— 与 12 年那张表
  同一个定义，读的时候别当成「今天收盘买入的收益」。
- **两列篮子**（建仓日那一天它们不是同一批票，这是这张表最容易读错的地方）：
      `持仓` / `段内第几天` = **今天开盘起手里拿着**的那 5 只，以及它手里的第几天（建仓日=1）
      `篮子日收益`        = 上一行那批票的开盘→开盘（赚它要**昨天开盘**就在手里）
  ⇒ 换仓日那一行：费记今天、收益仍归旧篮子、`段内第几天` 退回 1。
- **市场宽度** = 可投域里「收盘价 > 自己 20 日均线」的只数占比（可投域 = 已有行情 ≥60 天
  且 20 日均成交额 ≥2000 万元，与回测入口同一把尺子）。
- **E ∈ [0,1]** 当天收盘定、**次日生效**（`生效E` = 上一行的 `建议E`）；每动一次仓位付一边
  `15bp × |ΔE|`，与上面那笔换票费相加（真实盘部分重叠 ⇒ 故意算重）。
- 四档判据（E_low 全 = 0，即跌破就空仓）：
      M4-20  篮子满仓净值 < 它自己的 20 日均线
      M2-20  域等权净值     < 它自己的 20 日均线     —— 择市对照
      M7-30 / M7-45  市场宽度 < 30% / 45%
  ⚠️ M4-20 用的是**满仓那条净值**（外生），不是「减完之后自己那条」⇒ 没有反馈回路，与 ㊺ 同口径。

暖机：账本从冷启动，M4-20/M2-20 要 20 行才有均线 ⇒ 头 19 行按满仓走（与 ㊺ 的「无值头几天
按满仓走」同一口径，不是空仓），`备注` 里明写还差几行；M7 两档吃面板自身的 20 日均线，
第一天就有读数。

用法
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_exposure_monitor.py
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_exposure_monitor.py --as-of 2026-09-24  # 重算某一场（账本里不许有更晚的行）
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_exposure_monitor.py --dry-run           # 只算不写
只读面板与当日名单，不写它们；唯一的写动作是账本那一个文件。
"""
import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import argparse
import os
import time

import numpy as np
import pandas as pd

from config import (ASHARE_EXPOSURE_LEDGER, ASHARE_ORDER_TOP_N,
                    ASHARE_PORT_COST_ONE_WAY, ASHARE_PORT_HOLD,
                    ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED, ASHARE_SIGNAL_DIR)
from ashare_screen import build_matrices, load_panel

TRADING_DAYS = 252
MA_WIN = 20                     # 均线窗：M4-20/M2-20 的判据窗，也是「站上均线」那把尺子的窗
BREADTH_X = (0.30, 0.45)        # M7 两档：宽度低于多少就空仓
COST = ASHARE_PORT_COST_ONE_WAY
HOLD = ASHARE_PORT_HOLD
TOP_N = ASHARE_ORDER_TOP_N
LEGS = ("M4-20", "M2-20", "M7-30", "M7-45")
E_COLS = [f"{p}E_{k}" for k in LEGS for p in ("建议", "生效")]
COLS = ["日期", "持仓", "段内第几天", "篮子日收益", "换票费", "域等权日收益",
        "市场宽度", "满仓净值", "域等权净值"] + E_COLS + ["备注"]


def panel_readings(mtx):
    """可投域、域等权日收益、市场宽度、域内「有 20 日均线」的只数 —— 各只在这里定义一次

    公式：univ = (已有行情天数 ≥ ASHARE_PORT_MIN_LISTED) & (20 日均成交额 ≥ ASHARE_PORT_MIN_AMOUNT)
          bench = mean_{c ∈ univ} ret_open(c)；breadth = #(close > MA20(close)) / #univ（都限域内）
    ⚠️ 分母是**域内只数**而不是「有均线的只数」（与 ㊺ 那份实现逐字同构，换了就不是同一把尺子）：
    所以面板头 19 天里 MA20 全是 NaN ⇒ breadth 会读成 0.0 而不是「不知道」。日频链路面板从
    2014-09 起，末格永远在暖机窗之外，但这一格必须看得见 ⇒ `cover` 一起返回，
    `build_row` 里 cover=0 直接拒写、cover 不齐就在备注里报出来。
    """
    univ = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
            & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    bench = mtx["ret_open"].where(univ).mean(axis=1)
    sma = mtx["close"].where(univ).rolling(MA_WIN).mean()
    breadth = (((mtx["close"] > sma) & univ).sum(axis=1)
               / univ.sum(axis=1).replace(0, np.nan))
    cover = (sma.notna() & univ).sum(axis=1)
    return dict(univ=univ, bench=bench, breadth=breadth, cover=cover)


def order_seats(session):
    """③ 那天落盘的 5 席下单名单；缺文件返回 None（调用方必须明说，不许悄悄沿用旧篮子）"""
    path = os.path.join(ASHARE_SIGNAL_DIR, f"order_{session:%Y%m%d}.csv")
    if not os.path.exists(path):
        return None
    return [str(c) for c in pd.read_csv(path)["code"].tolist()]


def hold_state(prior, days, session):
    """⇒ (手持篮子, 手持第几天, 今天的换票费, 今天赚钱的篮子, 备注[])

    两个篮子分开是必须的：`ret_open(d)` = open(d−1)→open(d)，赚它的是**昨天开盘就拿到今天
    开盘**的那只篮子；今天开盘换进来的那只要从明天才开始赚。所以「今天赚钱的」= 上一行的
    持仓，而「手持」在今天可以已经换人 —— 换入那天付双边费、那天收益仍归旧篮子，与
    `replay_picks` 的「信号 i ⇒ 建仓 i+1（费记 i+1、w=0）⇒ 拿 i+2…i+6 五天」**逐格对齐**
    （差一天就会让这份前向账本与 12 年那张表不在同一把尺子上，那正是这一步要对比的东西）。
    """
    note = []
    i = int(days.get_loc(session))
    if prior.empty:
        # 冷启动：上一场不一定有名单文件（日更链是 09-23 才开始落 order 的），往前找最近一份
        for back in range(1, 11):
            if i - back < 0:
                break
            seats = order_seats(days[i - back])
            if seats is not None:
                if back > 1:
                    note.append(f"冷启动：上一场无名单文件 ⇒ 用 {days[i - back]:%Y-%m-%d} 那份")
                return (seats, 1, 2 * COST, [],
                        note + ["建仓日：今天开盘按满仓买进（双边费记今天），"
                                "收益从下一场起算 ⇒ 今天这一格篮子日收益记 0"])
        return [], 0, 0.0, [], note + [f"{ASHARE_SIGNAL_DIR} 里找不到任何 order_*.csv ⇒ 篮子腿为空"]

    p = prior.iloc[-1]
    seats = [s for s in str(p["持仓"]).split("|") if s]
    k = int(p["段内第几天"])
    if p["日期"] not in days:
        raise SystemExit(f"[⑤ 算不出] 账本上一行 {p['日期']:%Y-%m-%d} 不在面板交易日里"
                         " ⇒ 面板与账本对不上，先查是不是把哪天从 bin 回滚掉了")
    gap = i - int(days.get_loc(p["日期"]))
    pos = k + gap                       # 不换篮子的话，今天手里这几只是第几天
    earn = seats                        # 今天赚的就是上一行手里那几只
    if gap > 1:
        note.append(f"缺场：上一行是 {p['日期']:%Y-%m-%d}，中间 {gap - 1} 场没落账"
                    " ⇒ 段位按交易日推进，那几天账本里没有")
    if seats and pos <= HOLD:
        return seats, pos, 0.0, earn, note
    new = order_seats(days[i - 1])      # 昨天收盘 ③ 落的那份，今天开盘执行
    if new is None:
        # 停在第 HOLD 天不推进 ⇒ 明天再来问一次那份名单（③ 补跑了就自愈），不会漏掉换仓
        return (seats, HOLD, 0.0, earn,
                note + [f"⚠️ 换仓未执行：缺 order_{days[i - 1]:%Y%m%d}.csv ⇒ 沿用旧持仓、"
                        f"段位停在第 {HOLD} 天，明天再试"])
    fee = 2 * COST * (1.0 - len(set(seats) & set(new)) / TOP_N)
    return (new, 1, fee, earn,
            note + ["换仓日：今天开盘换入新篮子（双边费记今天），今天的日收益仍归上一篮子"])


def basket_ret(mtx, session, seats):
    """篮子今天赚多少：每席 1/席数 的开盘→开盘收益直接相加（NaN 按 0，与 `ret_open0` 同口径）"""
    if not seats:
        return 0.0, "无持仓"
    frame = mtx["ret_open"].reindex(columns=seats)
    missing = [c for c in seats if c not in mtx["ret_open"].columns]
    r = float(frame.loc[session].fillna(0.0).sum() / len(seats))
    return r, ("面板里没有 " + "/".join(missing) if missing else "")


def suggest_e(led):
    """四档收盘建议 E（只看 `led` 已有的列 ⇒ 判据在这里只有一份）"""
    nav, nvb, br = led["满仓净值"], led["域等权净值"], led["市场宽度"]
    out = {"M4-20": np.where(nav < nav.rolling(MA_WIN).mean(), 0.0, 1.0),
           "M2-20": np.where(nvb < nvb.rolling(MA_WIN).mean(), 0.0, 1.0)}
    for x in BREADTH_X:
        out[f"M7-{int(x * 100)}"] = np.where(br < x, 0.0, 1.0)
    return {k: pd.Series(v, index=led.index) for k, v in out.items()}


def build_row(prior, days, mtx, session, rd):
    """算出这一场那一行（含四档建议 E 与生效 E）；不碰磁盘"""
    bench, breadth, cover, univ = rd["bench"], rd["breadth"], rd["cover"], rd["univ"]
    b = float(bench.loc[session])
    br = float(breadth.loc[session])
    cv, nu = int(cover.loc[session]), int(univ.sum(axis=1).loc[session])
    if not np.isfinite(br) or cv == 0:
        raise SystemExit(f"[⑤ 算不出] {session:%Y-%m-%d} 的市场宽度读不出来"
                         f"（域内 {nu} 只、有 20 日均线的 {cv} 只，宽度 = {br}）"
                         " ⇒ 面板或可投域的数据不齐，这一行不落，先查 ②③")
    seats, k, fee, earn, note = hold_state(prior, days, session)
    if earn:
        r, miss = basket_ret(mtx, session, earn)
        if miss:
            note.append(f"⚠️ {miss}")
    else:
        r = 0.0                         # 建仓日：手里这几只今天开盘才买进，还没有收益可记
    if cv < nu:
        note.append(f"⚠️ 域内 {nu} 只里只有 {cv} 只算得出 20 日均线 ⇒ 市场宽度被分母压低了 "
                    f"{(nu - cv) / nu:.1%}，M7 那一档今天别当真")
    base = prior.iloc[-1] if not prior.empty else None
    nav = float(base["满仓净值"]) * (1.0 + r - fee) if base is not None else 1.0 + r - fee
    nvb = float(base["域等权净值"]) * (1.0 + b) if base is not None else 1.0 + b
    row = {"日期": pd.Timestamp(session), "持仓": "|".join(seats), "段内第几天": k,
           "篮子日收益": r, "换票费": fee, "域等权日收益": b,
           "市场宽度": br, "满仓净值": nav, "域等权净值": nvb}
    hist = pd.concat([prior, pd.DataFrame([row]).reindex(columns=COLS)], ignore_index=True) \
        if not prior.empty else pd.DataFrame([row]).reindex(columns=COLS)
    dec = suggest_e(hist)
    for lab in LEGS:
        row[f"建议E_{lab}"] = float(dec[lab].iloc[-1])
        row[f"生效E_{lab}"] = 1.0 if base is None else float(base[f"建议E_{lab}"])
    warm = MA_WIN - len(hist)
    if warm > 0:
        note.append(f"M4-20/M2-20 暖机中：净值满 {MA_WIN} 日均线才判得出，还差 {warm} 场"
                    "（这些天按满仓走）")
    row["备注"] = "；".join(note)
    today = pd.DataFrame([row])[COLS]
    # 交出去的 `hist` 必须带上今天这一行的 E：调用方拿它算「各档到今天为止」那张表，
    # 用没填 E 的那一份会把最后一行当成 NaN ⇒ 调仓税与火过没都少算今天这一场
    return today, (pd.concat([prior, today], ignore_index=True)
                   if not prior.empty else today)


def running_table(hist):
    """把账本折算成「各档到今天为止的年化与最深回撤」——只给读数，不给结论"""
    r = hist["篮子日收益"].to_numpy(dtype="float64") - hist["换票费"].to_numpy(dtype="float64")
    rows = []
    for lab in ("M1",) + LEGS:
        if lab == "M1":
            e = np.ones(len(r))
            tax = np.zeros(len(r))
        else:
            e = hist[f"生效E_{lab}"].to_numpy(dtype="float64")
            tax = COST * np.abs(np.diff(e, prepend=e[0]))
        net = pd.Series(r * e - tax, index=hist["日期"])
        nav = (1.0 + net).cumprod()
        rows.append({"档": lab, "天数": len(net), "年化": float(net.mean() * TRADING_DAYS),
                     "最深回撤": float((nav / nav.cummax() - 1.0).min()),
                     "空仓天数": int((e < 1e-9).sum()),
                     "调仓税": float(tax.sum())})
    t = pd.DataFrame(rows).set_index("档")
    t["Δ年化vs满仓"] = t["年化"] - t.loc["M1", "年化"]
    t["Δ回撤vs满仓"] = t["最深回撤"] - t.loc["M1", "最深回撤"]
    t["每买回1pp回撤付多少pp年化"] = np.where(
        t["Δ回撤vs满仓"] > 1e-12, t["Δ年化vs满仓"] / t["Δ回撤vs满仓"] * -1.0, np.nan)
    return t


def main():
    ap = argparse.ArgumentParser(description="仓位层前向监控：一天一行各档建议仓位，不接下单")
    ap.add_argument("--as-of", default="", help="重算这一场（YYYY-MM-DD，默认 = 面板末格）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写账本")
    a = ap.parse_args()

    t0 = time.time()
    wide, _ = load_panel()
    mtx = build_matrices(wide)
    del wide
    for k in ("high", "low"):       # 本步只用 open/close/volume/factor，这两张各 ~70MB
        mtx.pop(k, None)
    days = mtx["ret_open"].index
    session = pd.Timestamp(a.as_of) if a.as_of else days[-1]
    if session not in days:
        raise SystemExit(f"[⑤ 起不来] {session:%Y-%m-%d} 不在面板交易日里"
                         f"（面板区间 {days[0]:%Y-%m-%d} ~ {days[-1]:%Y-%m-%d}）")
    if session > pd.Timestamp.now().normalize():
        raise SystemExit(f"[⑤ 起不来] {session:%Y-%m-%d} 晚于今天 ⇒ 面板或 --as-of 有问题，"
                         "这一行不落")
    print(f"[场次] {session:%Y-%m-%d}　面板末格 {days[-1]:%Y-%m-%d}"
          + ("　(指定日)" if a.as_of else ""))

    rd = panel_readings(mtx)
    led = (pd.read_csv(ASHARE_EXPOSURE_LEDGER, parse_dates=["日期"])
           if os.path.exists(ASHARE_EXPOSURE_LEDGER) else pd.DataFrame(columns=COLS))
    if list(led.columns) != COLS:
        raise SystemExit(f"[⑤ 拒绝写入] 账本表头与当前判据不齐：\n  文件 {ASHARE_EXPOSURE_LEDGER}\n"
                         f"  现有 {list(led.columns)}\n  应为 {COLS}\n"
                         "⇒ 换判据等于换账本（老行是那一版判据的读数，不许混在一列里）。"
                         "要么把老档改名另存，要么改回来")
    if not led.empty:
        for c in E_COLS + ["篮子日收益", "换票费", "域等权日收益", "市场宽度",
                           "满仓净值", "域等权净值"]:
            led[c] = pd.to_numeric(led[c], errors="coerce")
    if not led.empty and not a.as_of:
        last = led["日期"].max()
        if last > days[-1]:
            raise SystemExit(f"[⑤ 起不来] 账本末行 {last:%Y-%m-%d} 比面板末格 {days[-1]:%Y-%m-%d} 还新"
                             " ⇒ 有哪天把没发生的日子写进了账本，先查再跑")
    newer = led[led["日期"] > session]
    if not newer.empty:
        raise SystemExit(f"[⑤ 拒绝覆写] 账本里已经有 {len(newer)} 行晚于 {session:%Y-%m-%d}"
                         f"（最晚 {newer['日期'].max():%Y-%m-%d}）⇒ 前向流水只许往长里攒，"
                         "要重算历史请先手工删掉那些行（那等于承认那些天没看过）")
    prior = led[led["日期"] < session]
    dup = int((led["日期"] == session).sum())
    row, hist = build_row(prior, days, mtx, session, rd)

    with pd.option_context("display.width", 240, "display.max_columns", 40):
        print("\n===== 这一行要落进账本 =====")
        print(row.to_string(index=False))
        print("\n===== 各档到今天为止（样本越短越没有读数意义，别据此调旋钮）=====")
        print(running_table(hist).to_string(float_format=lambda v: f"{v:+.4f}"))
        print("末列 = ㊺ 那把汇率：每买回 1pp 最深回撤要付掉多少 pp 年化（越小越划算）。"
              "负数 = 多赚还顺手把回撤做浅（不要钱反而倒贴）；空格 = 这档到今天没动过仓位，"
              "与满仓一字不差")
    n_fire = {lab: int((hist[f"建议E_{lab}"] < 1e-9).sum()) for lab in LEGS}
    print("[火过没] " + "　".join(f"{lab} 收盘建议空仓 {n_fire[lab]}/{len(hist)} 场" for lab in LEGS))
    if len(hist) < 60:
        print(f"[提醒] 账本才 {len(hist)} 场 ⇒ 上面那两年化/回撤是**噪声**，"
              "攒到 ~120 场（半年）再看；这一步不参与下单，也不改任何判据")

    if a.dry_run:
        print(f"\n[dry-run] 账本没动：{ASHARE_EXPOSURE_LEDGER}")
        return
    out = row if prior.empty else pd.concat([prior, row], ignore_index=True)
    if os.path.dirname(ASHARE_EXPOSURE_LEDGER):
        os.makedirs(os.path.dirname(ASHARE_EXPOSURE_LEDGER), exist_ok=True)
    out.to_csv(ASHARE_EXPOSURE_LEDGER, index=False)
    print(f"\n[落盘] {ASHARE_EXPOSURE_LEDGER}　共 {len(out)} 行"
          + (f"（覆盖已有的 {session:%Y-%m-%d} 那一行）" if dup else "（新增一行）")
          + f"　用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
