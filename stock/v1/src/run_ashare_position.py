# -*- coding: utf-8 -*-
"""②c 手动成交录入 → 持仓与盈亏账本（股票线，A 股个股）。

系统形态是「出信号 → **人工下单** → 成交后手工录入 → 算账」。本脚本只读流水，
不下单、不接券商接口。

录入文件 ASHARE_FILLS_CSV（默认 stock/v1/data/live/manual_fills.csv），一行一笔：

    日期,代码,方向,成交价,数量,费用,备注
    2026-09-22,SH600519,买入,1253.80,100,31.28,
    2026-09-25,SH600519,卖出,1268.00,100,37.44,止盈
    2026-09-28,SH600519,入金,,25875,,年度分红到账（现金金额填在数量列）

方向取 买入/卖出/入金/出金（也认 buy/sell/deposit/withdraw）。**分红记成入金**：
本账本全部用盘面真实价估值（复权价 / factor），除息日不会凭空产生一笔亏损，
但券商的现金分红也不会自己进账，不手工记就会把长持分红股的收益算少。

账本口径（写死在这里，改口径等于改历史结论）：

    平均成本  C = 持仓成本额 / 持股数          成本额含买入佣金
    已实现    R = Σ[(卖出价 − C_卖时) × 数量 − 卖出费用]
    浮动      U = 最新价 × 持股数 − 成本额
    现金      K = Σ入金 − Σ出金 − Σ(买入金额 + 费用) + Σ(卖出金额 − 卖出费用)
    总资产    A = K + Σ市值

录入价一律与**成交当日**的盘面收盘比，偏离超 ±21%（创业板/科创板涨停 20% 再加
1% 容差）报警——那只用于抓「代码打错 / 小数点点错」，不参与任何投资决策。

错误处理是本脚本的主要行为：**报错带行号、整轮不出账**。手工账最怕静默修正，
一条价格错位在当时看不出来，几个月后就是对不上的盈亏。费用留空则按标准费率补
（佣金万 2.5 双边、最低 5 元；印花税万 5 只收卖出侧）并在汇总里计行数；填了费用
则与计算值比对，偏差超 2% 只提示不改账（各家佣金不同是常态）。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import glob
import os
import re
import sys

import numpy as np
import pandas as pd

from config import (ASHARE_ACCOUNT_OUT, ASHARE_FILL_PRICE_TOL, ASHARE_FILLS_CSV,
                    ASHARE_POSITION_OUT, ASHARE_SIGNAL_DIR, COMMISSION_RATE,
                    LOT_SIZE, MIN_COMMISSION, STAMP_TAX_RATE_SELL)

REQUIRED = ["日期", "代码", "方向", "成交价", "数量"]
SIDES = {"买入": "buy", "卖出": "sell", "入金": "in", "出金": "out",
         "buy": "buy", "sell": "sell", "deposit": "in", "withdraw": "out",
         "in": "in", "out": "out"}
CODE_RE = re.compile(r"^(SH|SZ|BJ)\d{6}$")
FEE_TOL = 0.02             # 手填费用与标准费率的容忍带（过户费 0.001% 落在此内）
_PX = {}                   # 面板价缓存：账本一次运行只读一遍 0.8GB 面板


def template(path):
    """没有账本时生成带头行和写法说明的空模板"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig") as fh:
        fh.write("日期,代码,方向,成交价,数量,费用,备注\n")
        fh.write("# 一行一笔。日期 YYYY-MM-DD；代码 SH/SZ/BJ+6 位（与面板 instrument 一致）；"
                 "方向 买入/卖出/入金/出金；成交价 元（盘面真实价，非复权价）；"
                 "数量 股（买入须为 100 整数倍；现金流水金额填在数量列）；"
                 "费用 元（留空则按 佣金万2.5最低5元 + 印花税卖出万5 计算）\n")


def load_fills(path):
    if not os.path.exists(path):
        template(path)
        raise SystemExit(f"[账本] {path} 不存在，已生成空模板。"
                         f"按模板注释把成交逐笔录进去再跑。")
    # utf-8-sig：Excel 存的 CSV 带 BOM，不吞掉的话首列名会变成 '\ufeff日期'
    # comment='#'：模板里那行写法说明是 # 开头，读的时候当注释丢掉
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"代码": str},
                     comment="#", keep_default_na=False, na_values=[""])
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise SystemExit(f"[账本] {path} 缺列 {missing}，表头必须是：日期,代码,方向,成交价,数量[,费用,备注]")
    df = df.dropna(how="all").reset_index(drop=True)
    df["代码"] = df["代码"].astype(str).str.strip().str.upper()
    df["方向原文"] = df["方向"].astype(str).str.strip()
    # 未知方向映射成 NaN，交给 build_book 带行号报错（这里不猜，猜错就是记错账）
    df["side"] = df["方向原文"].str.lower().map(SIDES)
    df["day"] = pd.to_datetime(df["日期"].astype(str).str.strip(), errors="coerce")
    # 行号 +2：表头第 1 行，注释行已被 comment='#' 丢掉，index 从 0 起
    df["row"] = df.index + 2
    return df.sort_values(["day", "row"], kind="stable").reset_index(drop=True)


def panel_prices():
    """面板最后一日的**盘面价**（复权价 / factor）与它的日期；按代码索引

    不走 akshare 实时行情：账本要的是已落库日线，与信号、回测同源，盘中价会让
    浮动盈亏每天对不上。实时行情属于「接实盘数据」那一环。
    """
    if not _PX:
        from ashare_screen import build_matrices, load_panel
        wide, _bench = load_panel()
        mtx = build_matrices(wide)
        raw = mtx["raw_price"]
        _PX["last"] = raw.index[-1]
        _PX["raw"] = raw
    return _PX["raw"], _PX["last"]


def std_fee(side, px, qty):
    """标准费率：佣金万 2.5 双边（单笔最低 5 元）+ 印花税万 5 只收卖出侧

        fee = max(px·qty·0.00025, 5) + [卖出] px·qty·0.0005

    不含滑点（这是真实成交的费，不是回测假设），也不含过户费 0.001%。
    """
    amt = px * qty
    return max(amt * COMMISSION_RATE, MIN_COMMISSION) + (
        amt * STAMP_TAX_RATE_SELL if side == "sell" else 0.0)


def build_book(df, raw, last):
    """流水 → (持仓表, 账户 dict, errors, warnings)。语义校验就在这一趟里做

    按日推进而不是按行推进，是为了 T+1：调仓池在**每天开头**快照一次，
    当天的买入不进这个池子，所以「当日买当日卖」会在记账时直接判错，
    而不是悄悄记成一笔凭空多出来的持仓。
    """
    err, warn = [], []
    # 坏日期解析成 NaT，groupby 默认会把 NaN 键**整组丢掉**，那条流水就无声消失了。
    # 手工账最不能有的就是这个，所以先单独把它们点出来。
    for _, r in df[df["day"].isna()].iterrows():
        err.append(f"第 {int(r['row'])} 行：日期「{r['日期']}」不是 YYYY-MM-DD，"
                   f"这一笔没记账")
    st = {}                       # code -> qty/cost/realized/fee/last_buy
    cash = 0.0
    net_in = 0.0
    fee_auto = []
    for day, grp in df[df["day"].notna()].groupby("day", sort=True):
        # T+1：每天开头快照一次可卖池，当天的买入不进这个池子
        sellable = {c: s["qty"] for c, s in st.items()}
        for _, r in grp.iterrows():
            row, code = int(r["row"]), r["代码"]
            side = r["side"]
            if side not in ("buy", "sell", "in", "out"):
                err.append(f"第 {row} 行：方向「{r['方向']}」不认识，"
                           f"只能是 买入/卖出/入金/出金")
                continue
            if not CODE_RE.match(code):
                err.append(f"第 {row} 行：代码「{code}」不认，要 SH/SZ/BJ + 6 位数字")
                continue
            amt_raw, qty_raw = r["成交价"], r["数量"]
            px = _num(amt_raw)
            qty = _num(qty_raw)
            if side in ("in", "out"):
                # 现金流水：金额填数量列；两列都填了就必须一致，否则是录错位
                v = qty if np.isfinite(qty) else px
                if np.isfinite(px) and np.isfinite(qty) and px != qty:
                    err.append(f"第 {row} 行：{r['方向']} 的成交价 {px} 与数量 {qty} "
                               f"不一致，现金流水只填一个数（填在数量列）")
                    continue
                if not np.isfinite(v) or v <= 0:
                    err.append(f"第 {row} 行：{r['方向']} 金额「{r['数量']}」无效")
                    continue
                cash += v if side == "in" else -v
                net_in += v if side == "in" else -v
                continue
            if not np.isfinite(px) or px <= 0:
                err.append(f"第 {row} 行：{code} 成交价「{r['成交价']}」无效")
                continue
            if not np.isfinite(qty) or qty <= 0:
                err.append(f"第 {row} 行：{code} 数量「{r['数量']}」无效")
                continue
            if side == "buy" and qty % LOT_SIZE:
                err.append(f"第 {row} 行：{code} 买入 {int(qty)} 股不是 {LOT_SIZE} "
                           f"的整数倍（A 股按手买，卖出才允许零股）")
            if code not in raw.columns:
                err.append(f"第 {row} 行：{code} 面板里没行情，无法估值"
                           f"（代码写错、已退市或未上市）")
                continue
            if day > last:
                err.append(f"第 {row} 行：成交日 {day.date()} 晚于数据尽头 "
                           f"{last.date()}，账没法估——先补行情数据")
                continue
            p = st.setdefault(code, {"qty": 0.0, "cost": 0.0, "real": 0.0,
                                     "fee": 0.0, "last_buy": "", "n": 0})
            fee = _num(r["费用"])
            calc = std_fee(side, px, qty)
            if not np.isfinite(fee):
                fee = calc
                fee_auto.append(row)
            elif abs(fee / calc - 1) > FEE_TOL:
                warn.append(f"第 {row} 行：{code} 手填费用 {fee:.2f} 与标准费率 "
                            f"{calc:.2f} 差 {fee / calc - 1:+.1%}（按手填记账）")
            ref = raw.loc[day, code]
            if np.isfinite(ref) and ref > 0 and abs(px / ref - 1) > ASHARE_FILL_PRICE_TOL:
                warn.append(f"第 {row} 行：{code} 录入价 {px:.2f} 与当日收盘 "
                            f"{ref:.2f} 偏离 {px / ref - 1:+.1%}，请核对是否打错")
            p["n"] += 1
            p["fee"] += fee
            if side == "buy":
                p["qty"] += qty
                p["cost"] += px * qty + fee
                p["last_buy"] = str(day.date())
                cash -= px * qty + fee
            else:
                if qty > p["qty"]:
                    err.append(f"第 {row} 行：卖出 {code} {int(qty)} 股，此前只持 "
                               f"{int(p['qty'])} 股 —— 流水缺买入或代码录错")
                    continue
                if qty > sellable.get(code, 0):
                    err.append(f"第 {row} 行：T+1 违规，{code} 卖出 {int(qty)} 股，"
                               f"但日初持仓只有 {int(sellable.get(code, 0))} 股"
                               f"（当日买入的 {int(qty - sellable.get(code, 0))} 股"
                               f"不可当日卖出；要卖的是前几天的仓）")
                    continue
                avg = p["cost"] / p["qty"]
                p["real"] += (px - avg) * qty - fee
                p["qty"] -= qty
                p["cost"] -= avg * qty
                cash += px * qty - fee

    rows = []
    for code, p in st.items():
        lp = float(raw.loc[last, code])
        mv = lp * p["qty"]
        rows.append({
            "代码": code, "持股数": int(p["qty"]),
            "平均成本": p["cost"] / p["qty"] if p["qty"] else np.nan,
            "最新价": lp, "市值": mv,
            "浮动盈亏": mv - p["cost"],
            "浮动盈亏率%": (mv / p["cost"] - 1) * 100 if p["cost"] > 0 else np.nan,
            "已实现盈亏": p["real"], "累计费用": p["fee"], "笔数": int(p["n"]),
            "最近买入日": p["last_buy"],
        })
    pos = pd.DataFrame(rows)
    if len(pos):
        pos = pos.sort_values(["持股数", "市值"], ascending=False).reset_index(drop=True)
        sig, sig_file = latest_signal(list(pos["代码"]))
        pos["今日信号"] = pos["代码"].map(sig) if sig else ""
    else:
        sig_file = ""
    mv_total = float(pos["市值"].sum()) if len(pos) else 0.0
    acct = {"数据截至": str(last.date()), "成交笔数": len(df),
            "现金净流入": net_in, "现金": cash, "持仓市值": mv_total,
            "总资产": cash + mv_total,
            "浮动盈亏": float(pos["浮动盈亏"].sum()) if len(pos) else 0.0,
            "已实现盈亏": float(pos["已实现盈亏"].sum()) if len(pos) else 0.0,
            "累计费用": float(pos["累计费用"].sum()) if len(pos) else 0.0,
            "费用留空按计算补的行数": len(fee_auto), "信号名单": sig_file}
    return pos, acct, err, warn


def latest_signal(codes):
    """把最近一份日频剔除名单贴到持仓上：手里哪些票今天的信号是「不该继续拿/买」"""
    files = sorted(glob.glob(os.path.join(ASHARE_SIGNAL_DIR, "signal_*.csv")))
    if not files:
        return None, ""
    f = files[-1]
    d = pd.read_csv(f, dtype={"code": str}).set_index("code")
    sig = {}
    for c in codes:
        if c not in d.index:
            sig[c] = "不在可投池"
        else:
            r = d.loc[c]
            sig[c] = "保留" if bool(r["keep"]) else f"剔除({r['excluded_by']})"
    return sig, os.path.basename(f)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def main():
    df = load_fills(ASHARE_FILLS_CSV)
    if not len(df):
        raise SystemExit(f"[账本] {ASHARE_FILLS_CSV} 里还没有成交记录（只有表头）")
    raw, last = panel_prices()
    pos, acct, err, warn = build_book(df, raw, last)
    for w in warn:
        print("[警告]", w)
    if err:
        print(f"\n[账本] 录入有 {len(err)} 处错误，本轮不出持仓：")
        for e in err:
            print("  -", e)
        print("[账本] 全部改完再跑。持仓不落盘，免得半对的账被看板读出去。")
        return 1

    os.makedirs(os.path.dirname(ASHARE_POSITION_OUT), exist_ok=True)
    pos.to_csv(ASHARE_POSITION_OUT, index=False, encoding="utf-8-sig")
    pd.DataFrame([acct]).to_csv(ASHARE_ACCOUNT_OUT, index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 220)
    pd.set_option("display.unicode.east_asian_width", True)
    print(f"\n===== 持仓（估值日 {acct['数据截至']}，信号名单 "
          f"{acct['信号名单'] or '缺'}） =====")
    print(pos.to_string(index=False, float_format=lambda v: f"{v:,.2f}")
          if len(pos) else "（空仓）")
    print("\n===== 账户 =====")
    for k, v in acct.items():
        print(f"  {k:14s} {v:,.2f}" if isinstance(v, float) else f"  {k:14s} {v}")
    if acct["费用留空按计算补的行数"]:
        print(f"\n[提示] 有 {acct['费用留空按计算补的行数']} 笔费用留空，已按标准费率补。"
              f"交割单下来后请把实际费用填回，否则费用合计是估算值。")
    if acct["现金"] < 0 and acct["现金净流入"] == 0:
        print("[提示] 未录「入金」，现金为负是买入占款，总资产无意义。"
              "把初始本金记一行 入金 后，总资产与收益率才对得上。")
    print(f"\n[输出] {ASHARE_POSITION_OUT}\n[输出] {ASHARE_ACCOUNT_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
