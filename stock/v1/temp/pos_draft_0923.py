# -*- coding: utf-8 -*-
"""②c 手动成交录入 → 持仓与盈亏账本（股票线，A 股个股）。

系统形态：出信号 → **人工下单** → 成交后把结果录进一个 CSV → 本脚本算持仓。
所以这里不接券商接口、不下单，只做一件事：把人工录的成交流水折成账。

录入文件 ASHARE_FILLS_CSV（默认 stock/v1/data/live/manual_fills.csv），一行一笔：

    日期,代码,方向,成交价,数量,费用,备注
    2026-09-22,SH600519,买入,1253.80,100,31.28,
    2026-09-25,SH600519,卖出,1268.00,100,37.44,止盈

方向取 买入/卖出/入金/出金（也认 buy/sell/deposit/withdraw）。入金/出金是现金
进出，也是**分红到账的记法**：本账本的价全部用盘面真实价（复权价 / factor），
除息日不会凭空产生一笔亏损，但券商现金分红也不会自己进账，所以分红要手工记成
一条「入金」，否则长持分红股的实际收益会被算少。

账本口径（写在代码里，改口径等于改历史结论）：

    平均成本  C = Σ(买入金额 + 买入费用) / Σ数量        加权平均，不分批
    已实现    R = Σ[(卖出价 − C_卖出时) × 数量 − 卖出费用]
    浮动      U = (最新价 − C) × 持股数
    现金      = Σ入金 − Σ出金 − Σ买入金额 − Σ费用 + Σ卖出金额
    可卖数    = 持股数 − 当日买入数量                   T+1

错误一律**报行号并退出**，不猜、不补默认值：手工录入的账最怕的就是静默修正，
一条价格打错位在报告里看不出来，几个月后就是对不上的盈亏。费用留空时按费率算
（佣金万 2.5 双边、最低 5 元，印花税卖出万 5）并在输出里标注「费用=计算」；
填了费用则与计算值比对，偏差超 2% 只报警不改账（各券商佣金不同是常态）。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import glob
import os
import sys

import numpy as np
import pandas as pd

from config import (ASHARE_ACCOUNT_OUT, ASHARE_FILLS_CSV, ASHARE_POSITION_OUT,
                    ASHARE_SIGNAL_DIR, COMMISSION_RATE, LOT_SIZE,
                    MIN_COMMISSION, STAMP_TAX_RATE_SELL)

FILL_COLS = ["日期", "代码", "方向", "成交价", "数量", "费用", "备注"]
BUY = {"买入", "buy", "b"}
SELL = {"卖出", "sell", "s"}
DEPOSIT = {"入金", "deposit", "in"}
WITHDRAW = {"出金", "withdraw", "out"}
SIDES = BUY | SELL | DEPOSIT | WITHDRAW
# 录入价对当日盘面收盘价的容忍偏离：主板涨跌停 ±10%、创业板/科创板 ±20%，
# 超过 21% 基本只能是代码写错、价打错或日期错位，不会是一次真实成交
PRICE_BAND = 0.21
FEE_TOL = 0.02


def template(path):
    """没有账本时给一份带头行的空模板，并把笔数要求讲明白"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig") as fh:
        fh.write(",".join(FILL_COLS) + "\n")
        fh.write("# 一行一笔成交；日期 YYYY-MM-DD，代码 SH/SZ/BJ+6 位，"
                 "方向 买入/卖出/入金/出金，数量单位股（买入须为 100 整数倍），"
                 "费用单位元（留空则按 佣金万2.5+印花税卖出万5 计算）\n")


def load_fills(path):
    """读成交流水；空费用按费率补全并打 fee_auto 标记，其余原样返回"""
    if not os.path.exists(path):
        template(path)
        raise SystemExit(f"[账本] {path} 不存在，已生成空模板。"
                         f"把成交按行录进去再跑本脚本。")
    # encoding='utf-8-sig'：Excel 存的 CSV 带 BOM，不吞掉第一列名会变成 '\ufeff日期'
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"代码": str},
                     comment="#", keep_default_na=False, na_values=[""])
    missing = [c for c in FILL_COLS[:6] if c not in df.columns]
    if missing:
        raise SystemExit(f"[账本] {path} 缺列 {missing}，表头必须是｛｝"
                         .replace("｛｝", "：" + ",".join(FILL_COLS)))
    df = df.dropna(how="all")
    df["代码"] = df["代码"].astype(str).str.strip().str.upper()
    df["方向"] = df["方向"].astype(str).str.strip().str.lower()
    # 行号 +2：表头占第 1 行，注释行已由 comment='#' 剔除
    df["_row"] = df.index + 2
    df = df.reset_index(drop=True)
    return df


def std_fee(side, px, qty):
    """标准费率：佣金万 2.5 双边（单笔最低 5 元）+ 印花税万 5 只收卖出侧

        fee = max(px·qty·0.00025, 5) + (px·qty·0.0005 if 卖出 else 0)

    不含滑点（这里是真实成交的费，不是回测假设），也不含过户费 0.001%——
    它比佣金小一个数量级，落在 FEE_TOL 的容忍带里。
    """
    amt = px * qty
    return max(amt * COMMISSION_RATE, MIN_COMMISSION) + (
        amt * STAMP_TAX_RATE_SELL if side in SELL else 0.0)


def validate(df, quote):
    """返回 (errors, warnings)。errors 非空则整轮不出账，warnings 只提示"""
    err, warn = [], []
    d = lambda i, m: err.append(f"第 {df.loc[i, '_row']} 行：{m}")

    for i, r in df.iterrows():
        try:
            dt = pd.Timestamp(r["日期"])
        except Exception:
            continue                                   # 日期坏掉单独报，不重复报
        if r["代码"] not in quote.index:
            continue
    for i, r in df.iterrows():
        row = int(r["_row"])
        side = str(r["方向"]).strip()
        if side not in SIDES:
            d(i, f"方向「{side}」不认识，只能是 买入/卖出/入金/出金")
            continue
        if side in DEPOSIT | WITHDRAW:
            if not np.isfinite(_f(r["成交价"] or 0)) and r["成交价"] != "":
                d(i, "现金流水不该填成交价")
            continue
        try:
            dt = pd.Timestamp(str(r["日期"]))
        except Exception:
            d(i, f"日期「{r['日期']}」不是 YYYY-MM-DD")
            continue
        code = r["代码"]
        if not (len(code) == 8 and code[:2] in ("SH", "SZ", "BJ")):
            d(i, f"代码「{code}」不认（要 SH/SZ/BJ + 6 位数字，与面板 instrument 一致）")
        px, qty = _f(r["成交价"]), _f(r["数量"])
        if not np.isfinite(px) or px <= 0:
            d(i, f"{code} 成交价「{r['成交价']}」无效")
            continue
        if not np.isfinite(qty) or qty <= 0:
            d(i, f"{code} 数量「{r['数量']}」无效")
            continue
        if side in BUY and qty % LOT_SIZE:
            d(i, f"{code} 买入 {int(qty)} 股不是 {LOT_SIZE} 的整数倍（A 股整手买入）")
        if code not in quote.index:
            d(i, f"{code} 在面板里没有行情，无法估值（代码写错或已退市/未上市）")
        else:
            q = quote.loc[code]
            if dt.normalize() > q["date"]:
                d(i, f"{code} 成交日 {dt.date()} 晚于数据尽头 {q['date'].date()}，"
                     f"无法估值——先补行情数据或改成实际日期")
            elif np.isfinite(q["close"]) and q["close"] > 0:
                dev = px / q["close"] - 1
                if abs(dev) > PRICE_BAND:
                    warn.append(f"第 {row} 行：{code} 录入价 {px:.2f} 与 "
                                f"{q['date'].date()} 收盘 {q['close']:.2f} 偏离 "
                                f"{dev:+.1%}，请核对是否打错（不阻断，按录入计账）")
    # T+1 与超卖要按时间顺序累计，故单独走一遍
    held, bought_today = {}, {}
    for i, r in df.sort_values("日期", kind="stable").iterrows():
        side, row = str(r["方向"]), int(r["_row"])
        if side not in SELL | BUY:
            continue
        code, qty = r["代码"], _f(r["数量"])
        if not np.isfinite(qty):
            continue
        day = str(pd.Timestamp(str(r["日期"])).date()) if _ok_date(r["日期"]) else ""
        if side in BUY:
            held[code] = held.get(code, 0) + qty
            bought_today[(code, day)] = bought_today.get((code, day), 0) + qty
        else:
            if held.get(code, 0) < qty:
                err.append(f"第 {row} 行：卖出 {code} {int(qty)} 股，"
                           f"但此前只持 {int(held.get(code, 0))} 股 —— 流水缺一笔买入")
                held[code] = 0
                continue
            same_day = bought_today.get((code, day), 0)
            if qty > held[code] - same_day:
                err.append(f"第 {row} 行：T+1 违规，{code} 当日买入 {int(same_day)} 股"
                           f"不可当日卖出（想卖的是前几天的仓，请把这笔排在次日之后）")
            held[code] -= qty
    return err, warn


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return np.nan
    return v


def _ok_date(x):
    try:
        pd.Timestamp(str(x))
        return True
    except Exception:
        return False


def build_book(df, quote):
    """流水 → (持仓表, 账户汇总)。口径见模块 docstring 的公式"""
    rows = {}
    cash = 0.0
    realized = fees_total = 0.0
    fee_auto_rows = []
    dep_wit = 0.0
    for i, r in df.sort_values("日期", kind="stable").iterrows():
        side, code = str(r["方向"]), r["代码"]
        px, qty = _f(r["成交价"]), _f(r["数量"])
        day = str(pd.Timestamp(str(r["日期"])).date())
        if side in DEPOSIT | WITHDRAW:
            amt = qty if np.isfinite(qty) else px     # 现金流水数量/价格任填一格即可
            dep_wit += amt if side in DEPOSIT else -amt
            cash += amt if side in DEPOSIT else -amt
            continue
        fee = _f(r["费用"])
        if not np.isfinite(fee):
            fee = std_fee(side, px, qty)
            fee_auto_rows.append(int(r["_row"]))
        p = rows.setdefault(code, {"持股数": 0.0, "成本额": 0.0, "可卖数": 0.0,
                                   "最近买入日": "", "已实现盈亏": 0.0, "费用": 0.0})
        p["费用"] += fee
        fees_total += fee
        if side in BUY:
            p["持股数"] += qty
            p["成本额"] += px * qty + fee
            p["可卖数"] = p.get("_prev_sellable", 0)
            p["最近买入日"] = day
            cash -= px * qty + fee
        else:
            avg = p["成本额"] / p["持股数"] if p["持股数"] else 0.0
            pl = (px - avg) * qty - fee
            p["已实现盈亏"] += pl
            realized += pl
            p["持股数"] -= qty
            p["成本额"] -= avg * qty
            p["可卖数"] -= qty
            cash += px * qty - fee
        p["_prev_sellable"] = p["持股数"]
    # T+1：可卖 = 持股 − 当日买入。上面按时间顺序记了 last buy day，直接扣
    today = max(pd.Timestamp(str(x)).normalize() for x in df["日期"])
    for i, r in df.iterrows():
        if str(r["方向"]) in BUY:
            code = r["代码"]
            day = str(pd.Timestamp(str(r["日期"])).date())
            if day == str(today.date()) and code in rows:
                rows[code]["可卖数"] -= _f(r["数量"])

    pos = []
    for code, p in rows.items():
        if p["持股数"] <= 0 and abs(p["已实现盈亏"]) < 1e-9:
            continue
        last_px = float(quote.loc[code, "close"]) if code in quote.index else np.nan
        cost = p["成本额"] / p["持股数"] if p["持股数"] else np.nan
        mv = last_px * p["持股数"]
        pos.append({
            "代码": code, "持股数": p["持股数"], "可卖数": max(p["持股数"] + p["可卖数"]
                                                          - p["持股数"], 0.0),
            "平均成本": cost, "最新价": last_px, "估值日": quote.loc[code, "date"]
            if code in quote.index else pd.NaT,
            "市值": mv, "浮动盈亏": mv - p["成本额"],
            "浮动盈亏率": mv / p["成本额"] - 1 if p["成本额"] > 0 else np.nan,
            "已实现盈亏": p["已实现盈亏"], "费用": p["费用"], "最近买入日": p["最近买入日"],
        })
    out = pd.DataFrame(pos)
    if len(out):
        out = out.sort_values("市值", ascending=False).reset_index(drop=True)
    mv_total = float(out["市值"].sum()) if len(out) else 0.0
    account = {"数据截至": str(today.date()), "笔数": len(df),
               "现金收支净额": dep_wit, "现金": cash, "持仓市值": mv_total,
               "总资产": cash + mv_total,
               "浮动盈亏": float(out["浮动盈亏"].sum()) if len(out) else 0.0,
               "已实现盈亏": realized, "累计费用": fees_total,
               "费用为计算值的行数": len(fee_auto_rows)}
    return out, account, fee_auto_rows


def today_signal(codes):
    """把最近一份日频剔除名单贴到持仓上：手里哪些票今天的信号是「不该买」

    名单来自 run_ashare_daily_signal.py，按文件名里的日期取最新那份。缺文件不报错
    ——账本和信号是两件事，能对上就对上。
    """
    files = sorted(glob.glob(os.path.join(ASHARE_SIGNAL_DIR, "signal_*.csv")))
    if not files:
        return None, ""
    f = files[-1]
    d = pd.read_csv(f, dtype={"code": str}).set_index("code")
    sig = {}
    for c in codes:
        if c in d.index:
            sig[c] = "保留" if bool(d.loc[c, "keep"]) else \
                f"剔除({d.loc[c, 'excluded_by']})"
        else:
            sig[c] = "不在可投池"
    return sig, os.path.basename(f)


def main():
    df = load_fills(ASHARE_FILLS_CSV)
    if not len(df):
        raise SystemExit(f"[账本] {ASHARE_FILLS_CSV} 里还没有成交记录（只有表头）")
    err, warn = validate(df, _quotes(df))
    for w in warn:
        print("[警告]", w)
    if err:
        print(f"[账本] 录入有 {len(err)} 处错误，未生成持仓：")
        for e in err:
            print("  -", e)
        return 1

    quote = _quotes(df)
    pos, acct, auto = build_book(df, quote)
    sig, sig_file = today_signal(list(pos["代码"])) if len(pos) else (None, "")
    if sig:
        pos["今日信号"] = pos["代码"].map(sig)
    os.makedirs(os.path.dirname(ASHARE_POSITION_OUT), exist_ok=True)
    pos.to_csv(ASHARE_POSITION_OUT, index=False, encoding="utf-8-sig")
    pd.DataFrame([acct]).to_csv(ASHARE_ACCOUNT_OUT, index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print(f"\n===== 持仓（估值日 {acct['数据截至']}，名单 {sig_file or '无'}） =====")
    print(pos.to_string(index=False) if len(pos) else "（空仓）")
    print("\n===== 账户 =====")
    for k, v in acct.items():
        print(f"  {k:12s} {v:,.2f}" if isinstance(v, float) else f"  {k:12s} {v}")
    if auto:
        print(f"\n[提示] 第 {auto} 行费用留空，已按标准费率计算（佣金万2.5 最低5元 + "
              f"印花税卖出万5）。真实交割单下来后请把实际费用填回去。")
    print(f"\n[输出] {ASHARE_POSITION_OUT}\n[输出] {ASHARE_ACCOUNT_OUT}")
    return 0


_CACHE = {}


def _quotes(df):
    """持仓标的的最新盘面价（复权价 / factor）。一次读全市场面板，只取最后一行

    刻意不走 akshare 实时行情：账本要的是**已落库的日线收盘价**，与信号、回测同源，
    盘中价会让浮动盈亏每天对不上。实时行情是「接实盘数据」那一环的事。
    """
    if "q" in _CACHE:
        return _CACHE["q"]
    import _bootstrap  # noqa: F401
    from ashare_screen import build_matrices, load_panel
    wide, _b = load_panel()
    mtx = build_matrices(wide)
    idx = mtx["raw_price"].index
    last = idx[-1]
    codes = sorted(set(df["代码"]))
    q = pd.DataFrame({"close": mtx["raw_price"].loc[last].reindex(codes),
                      "date": pd.Series(last, index=codes, dtype="datetime64[ns]"),
                      "amount20": mtx["amount20"].loc[last].reindex(codes)})
    _CACHE["q"] = q
    return q


if __name__ == "__main__":
    sys.exit(main())
