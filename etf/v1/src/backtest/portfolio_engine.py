# -*- coding: utf-8 -*-
"""截面组合回测核心（日线与分钟线共用一份）

信号契约：index=时间戳、columns=[code, weight, score] 的长表，只在调仓 bar
出行；两个调仓日之间沿用上次目标权重（持有不操作）。空仓的那根 bar 也必须
出一行 `code=""` 的哨兵行——它既表示「本次调仓目标为空」，也给引擎提供时间轴
锚点（引擎的逐 bar 序列取「行情日历 ∩ [首个调仓 bar, 最后调仓 bar]」）。

每个 bar 的执行次序：
    估值 -> 风控判定 -> 目标金额 amount_i = equity · exposure_t · w_i
         -> 按 |Δ金额| 从大到小裁剪到换手上限 -> 先卖后买 -> 记录净值

    exposure_t = risk.adjust_position_ratio(equity)      # 回撤线性降仓，全组合共用
    单次换手 turnover = Σᵢ |Δamount_i| / (2 · equity)  ≤ max_turnover

成交约束：卖出要求当日有 bar（停牌/缺价则顺延，与旧引擎一致）且非 T+1 冻结
（当日买入的 T+1 品种不可卖）；买入额外要求当日成交额达标（该标的整列成交
额缺失时不设这道闸，否则无数据品种会整池买不进）；申报份数按 lot_size 取整
（场内 ETF 100 份一档）。风控拒绝开仓（熔断冷却/日止损）时只禁买，
止损与熔断触发的强制清仓除外。"""

import pandas as pd
from config import COMMISSION_RATE, MIN_COMMISSION, SLIPPAGE


def cost_of(amount):
    """单边交易成本 = max(金额×佣金率, 最低佣金) + 金额×滑点"""
    return max(amount * COMMISSION_RATE, MIN_COMMISSION) + amount * SLIPPAGE


def expand_rebalance(signals):
    """长表 -> ({时间戳: {code: weight}}, [调仓时间戳升序])；code 空串=空仓哨兵"""
    rebal, dates = {}, []
    if signals is None or signals.empty:
        return rebal, dates
    for ts, row in signals.iterrows():
        code = str(row["code"])
        if ts not in rebal:
            rebal[ts] = {}
            dates.append(ts)
        if code:
            rebal[ts][code] = float(row["weight"])
    return rebal, sorted(dates)


class PortfolioEngine:
    """多标的截面组合的事件循环。子类只需给出取价与时间口径的两个钩子：
    `_price(code, ts)` 与 `day_of(ts)`。"""

    def __init__(self, *, pit, risk_cls, risk_params, universe, pool,
                 day_of, is_t0, init_capital, min_trade_amount,
                 lot_size=100, max_turnover=1.0, min_daily_amount=0.0):
        self.pit = pit
        self.risk_cls = risk_cls
        self.risk_params = risk_params
        self.universe = universe
        self.pool = pool
        self.day_of = day_of
        self.is_t0 = is_t0
        self.init_capital = init_capital
        self.min_trade_amount = min_trade_amount
        self.lot_size = max(int(lot_size or 1), 1)
        self.max_turnover = float(max_turnover)
        self.min_daily_amount = float(min_daily_amount or 0.0)
        self._amount_field = {}

    @staticmethod
    def _cost(amount):
        return cost_of(amount)

    # ---------- 取数 ----------
    def _price(self, code, ts):
        return self.pit.get_price(code, ts, "close")

    def _has_amount_field(self, code):
        if code not in self._amount_field:
            df = self.pool.get(code)
            ok = df is not None and "amount" in df.columns \
                and bool(df["amount"].notna().any())
            self._amount_field[code] = ok
        return self._amount_field[code]

    def _buyable(self, code, ts):
        if not self.pit.has_bar(code, ts):
            return False
        if not self.min_daily_amount or not self._has_amount_field(code):
            return True
        amt = self.pit.get_price(code, ts, "amount")
        return amt is None or amt >= self.min_daily_amount

    def _timeline(self, signals, rebal_dates):
        """回测逐 bar 序列 = 行情日历 ∩ [首个调仓 bar, 最后调仓 bar]。
        信号是稀疏的（只在调仓 bar 出行），时间轴必须由行情数据自己给，
        否则两次调仓之间的 bar 全被跳过、净值曲线只剩几个点。"""
        if not rebal_dates:
            return []
        start, end = signals.index.min(), signals.index.max()
        days = set()
        for df in self.pool.values():
            days.update(df.index[(df.index >= start) & (df.index <= end)]
                        .tolist())
        return sorted(days)

    # ---------- 主循环 ----------
    def run(self, signals, time_col):
        """signals 为上面的长表契约；返回 (equity_df, trades_df)。
        time_col 是产物表的时间列名（日线 'date'、分钟 'datetime'）。"""
        rebal, rebal_dates = expand_rebalance(signals)
        timeline = self._timeline(signals, rebal_dates)
        cash = float(self.init_capital)
        positions = {}
        equity_curve, trades = [], []
        risk = self.risk_cls(self.risk_params)
        target, next_rebal = {}, 0

        for bar_idx, ts in enumerate(timeline):
            # 到点才换目标（稀疏调仓 -> 逐 bar 沿用）
            while next_rebal < len(rebal_dates) and rebal_dates[next_rebal] <= ts:
                target = rebal[rebal_dates[next_rebal]]
                next_rebal += 1

            px = {c: self._price(c, ts) for c in positions}
            equity = cash + sum(
                p["shares"] * (px[c] if px[c] is not None else p["cost"])
                for c, p in positions.items())

            risk.on_new_day(ts, equity)
            allowed, reason = risk.can_trade(ts, bar_idx, equity)
            forced_exit = (not allowed) and ("止损" in reason or "熔断" in reason)
            plan = {} if forced_exit else target

            exposure = risk.adjust_position_ratio(equity)
            tradable = self._tradable_codes(ts)
            want = {c: equity * exposure * w for c, w in plan.items()
                    if c in self.pool and c in tradable}
            for c in positions:
                want.setdefault(c, 0.0)

            deltas = []
            for code, tgt_amt in want.items():
                pos = positions.get(code)
                if pos is None:
                    cur = 0.0
                else:
                    # 缺 bar 时用最近成交价估值，与净值口径一致
                    cur = pos["shares"] * (px.get(code) or pos["cost"])
                if abs(tgt_amt - cur) > 1e-9:
                    deltas.append((code, tgt_amt - cur))
            # 换手上限：Σ|Δ金额| ≤ 2 · max_turnover · equity，变动大的优先做
            budget = 2.0 * self.max_turnover * equity
            deltas.sort(key=lambda x: -abs(x[1]))
            accepted = []
            for code, d in deltas:
                if abs(d) > budget:
                    continue
                budget -= abs(d)
                accepted.append((code, d))

            today = self.day_of(ts)
            # 先卖/减仓（d<0 按幅度降序，先腾出资金）
            for code, d in sorted(accepted, key=lambda x: x[1]):
                if d >= 0:
                    continue
                pos = positions.get(code)
                if pos is None:
                    continue
                p = self._price(code, ts)
                if p is None:
                    continue
                if not pos["is_t0"] and pos["buy_date"] == today:
                    continue
                shares = self._floor_lot(min(-d, pos["shares"] * p) / p)
                if shares <= 0:
                    continue
                amount = shares * p
                fee = self._cost(amount)
                cash += amount - fee
                trades.append(self._trade(
                    time_col, ts, "SELL", code, p, shares, amount, fee,
                    "强制清仓" if forced_exit else (reason or "换仓")))
                pos["shares"] -= shares
                risk.on_trade(bar_idx)
                if pos["shares"] <= 0:
                    positions.pop(code)

            # 后买/加仓
            if allowed:
                for code, d in sorted(accepted, key=lambda x: -x[1]):
                    if d <= 0:
                        continue
                    p = self._price(code, ts)
                    if p is None or p <= 0 or not self._buyable(code, ts):
                        continue
                    shares = self._floor_lot(min(d, cash) / p)
                    while shares > 0 and \
                            shares * p + self._cost(shares * p) > cash:
                        shares -= self.lot_size
                    amount = shares * p
                    if amount < self.min_trade_amount:
                        continue
                    fee = self._cost(amount)
                    cash -= amount + fee
                    pos = positions.get(code)
                    if pos is None:
                        positions[code] = {"shares": shares, "cost": p,
                                           "buy_date": today,
                                           "is_t0": self.is_t0(code)}
                    else:
                        new_total = pos["shares"] + shares
                        # 持仓成本取加权均价（缺 bar 日按它估值）
                        pos["cost"] = (pos["cost"] * pos["shares"]
                                       + p * shares) / new_total
                        pos["shares"] = new_total
                    trades.append(self._trade(time_col, ts, "BUY", code, p,
                                              shares, amount, fee, "信号"))
                    risk.on_trade(bar_idx)

            mv = 0.0
            for code, pos in positions.items():
                p = self._price(code, ts)
                mv += pos["shares"] * (p if p is not None else pos["cost"])
            equity_curve.append({time_col: ts, "equity": cash + mv,
                                 "cash": cash, "position_value": mv,
                                 "n_positions": len(positions)})

        equity_df = pd.DataFrame(equity_curve).set_index(time_col)
        cols = [time_col, "action", "code", "price", "shares",
                "amount", "cost", "reason"]
        trades_df = pd.DataFrame(trades)[cols] if trades \
            else pd.DataFrame(columns=cols)
        return equity_df, trades_df

    def _floor_lot(self, shares):
        return int(shares // self.lot_size) * self.lot_size

    @staticmethod
    def _trade(time_col, ts, action, code, price, shares, amount, cost,
               reason):
        return {time_col: ts, "action": action, "code": code, "price": price,
                "shares": shares, "amount": amount, "cost": cost,
                "reason": reason}

    def _tradable_codes(self, ts):
        if not self.universe:
            return set(self.pool.keys())
        try:
            return set(self.universe.get_tradable_at(ts))
        except Exception:
            return set(self.pool.keys())


def holding_stats(equity_df):
    """平均持仓只数（组合分散度，与 DSR 的独立观测数同向）"""
    if equity_df is None or equity_df.empty \
            or "n_positions" not in equity_df.columns:
        return 0.0
    return float(equity_df["n_positions"].mean())


def annual_turnover(time_col, trades_df, equity_df, bars_per_year):
    """年化换手率 = Σ成交金额 / 平均净值 / (成交笔数所属年数)：
    单边口径按 Σ|amount|/(2·equity) 统计，再折算到一年。"""
    if trades_df is None or trades_df.empty or equity_df is None \
            or equity_df.empty or len(equity_df) < 2:
        return 0.0
    traded = float(trades_df["amount"].sum()) / 2.0
    avg_eq = float(equity_df["equity"].mean())
    years = len(equity_df) / float(bars_per_year or 252)
    return traded / avg_eq / max(years, 1e-9)


def win_rate(trades_df):
    """逐笔配对的胜率：同标的按 FIFO 把卖出与买入配对，卖价高于该批买价记为赢。
    旧口径是「第 i 笔 BUY 配第 i 笔 SELL」，多标的同时持仓时会张冠李戴。"""
    if trades_df is None or trades_df.empty:
        return 0.0
    open_lots = {}
    wins = total = 0
    for row in trades_df.itertuples(index=False):
        lots = open_lots.setdefault(row.code, [])
        if row.action == "BUY":
            lots.append([row.shares, row.price])
            continue
        remain = row.shares
        while remain > 0 and lots:
            lot = lots[0]
            matched = min(remain, lot[0])
            total += 1
            if row.price > lot[1]:
                wins += 1
            lot[0] -= matched
            remain -= matched
            if lot[0] <= 0:
                lots.pop(0)
    return wins / total if total else 0.0
