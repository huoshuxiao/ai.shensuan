# -*- coding: utf-8 -*-
"""分钟线回测（T+0/T+1 + 风控）"""

import pandas as pd
import numpy as np
from config import (
    INIT_CAPITAL, COMMISSION_RATE, MIN_COMMISSION, SLIPPAGE,
    MIN_TRADE_AMOUNT, RISK_CONTROL,
)


class RiskController:
    def __init__(self, params=None):
        self.p = {**RISK_CONTROL, **(params or {})}
        self.reset()

    def reset(self):
        self.peak_equity = 0.0
        self.cooldown_until = None
        self.trades_today = 0
        self.current_day = None
        self.last_trade_bar_idx = -999
        self.daily_start_equity = None

    def on_new_day(self, ts, equity):
        day = pd.Timestamp(ts).date()
        if day != self.current_day:
            self.current_day = day
            self.trades_today = 0
            self.daily_start_equity = equity

    def can_trade(self, ts, bar_idx, equity):
        if self.cooldown_until and pd.Timestamp(ts) < self.cooldown_until:
            return False, "熔断冷却"
        if self.daily_start_equity and self.daily_start_equity > 0:
            r = equity / self.daily_start_equity - 1
            if r <= self.p["daily_stop_loss"]:
                return False, f"日内止损({r*100:.2f}%)"
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            dd = equity / self.peak_equity - 1
            if dd <= self.p["max_drawdown_stop"]:
                self.cooldown_until = pd.Timestamp(ts) + pd.Timedelta(
                    days=self.p["cooldown_days"])
                return False, f"回撤熔断({dd*100:.2f}%)"
        if bar_idx - self.last_trade_bar_idx < self.p["min_bars_between_trades"]:
            return False, "交易间隔不足"
        if self.trades_today >= self.p["max_trades_per_day"]:
            return False, "当日交易次数超限"
        return True, ""

    def on_trade(self, bar_idx):
        self.trades_today += 1
        self.last_trade_bar_idx = bar_idx

    def adjust_position_ratio(self, equity):
        if self.peak_equity <= 0:
            return self.p["single_position_max"]
        dd = equity / self.peak_equity - 1
        return max(0.2, min(self.p["single_position_max"],
                            self.p["single_position_max"] * (1 + dd * 5)))


class MinuteBacktester:
    def __init__(self, pool, universe, risk_params=None):
        self.pool = pool
        self.universe = universe
        self.risk_params = risk_params

    @staticmethod
    def _cost(amount):
        return max(amount * COMMISSION_RATE, MIN_COMMISSION) + amount * SLIPPAGE

    def _price(self, code, ts, field="close"):
        if code not in self.pool:
            return None
        df = self.pool[code]
        if ts not in df.index:
            return None
        v = df.loc[ts, field]
        return float(v) if not pd.isna(v) else None

    def run(self, signals):
        cash = INIT_CAPITAL
        position = None
        equity_curve, trades = [], []
        risk = RiskController(self.risk_params)

        for bar_idx, ts in enumerate(signals.index):
            target_code = signals.loc[ts, "target_code"]

            mv = 0.0
            if position is not None:
                cur = self._price(position["code"], ts, "close") or position["cost"]
                mv = position["shares"] * cur
            equity = cash + mv

            risk.on_new_day(ts, equity)
            allowed, reason = risk.can_trade(ts, bar_idx, equity)

            # 卖出
            if position is not None:
                cur_code = position["code"]
                is_t0 = position.get("is_t0", False)
                need_sell = (target_code != cur_code)
                if not allowed and ("止损" in reason or "熔断" in reason):
                    need_sell = True
                if need_sell and not is_t0 and position.get("buy_date") == ts.date():
                    need_sell = False
                cur_price = self._price(cur_code, ts, "close")
                if need_sell and cur_price is not None:
                    amount = position["shares"] * cur_price
                    cost = self._cost(amount)
                    cash += amount - cost
                    trades.append({
                        "datetime": ts, "action": "SELL", "code": cur_code,
                        "price": cur_price, "shares": position["shares"],
                        "amount": amount, "cost": cost,
                        "reason": reason or "换仓"})
                    position = None
                    risk.on_trade(bar_idx)

            # 买入
            if position is None and target_code and allowed:
                tradable = (set(self.universe.get_tradable_at(ts))
                            if self.universe else set(self.pool.keys()))
                if target_code in tradable:
                    bp = self._price(target_code, ts, "close")
                    if bp and bp > 0:
                        ratio = risk.adjust_position_ratio(equity)
                        invest = min(cash * ratio, cash - self._cost(cash * ratio))
                        if invest >= MIN_TRADE_AMOUNT:
                            shares = invest / bp
                            cost = self._cost(invest)
                            cash -= (invest + cost)
                            position = {
                                "code": target_code, "shares": shares,
                                "cost": bp, "buy_date": ts.date(),
                                "is_t0": self._is_t0(target_code)}
                            trades.append({
                                "datetime": ts, "action": "BUY",
                                "code": target_code, "price": bp,
                                "shares": shares, "amount": invest,
                                "cost": cost, "reason": "信号"})
                            risk.on_trade(bar_idx)

            mv = 0.0
            if position is not None:
                cur = self._price(position["code"], ts, "close") or position["cost"]
                mv = position["shares"] * cur
            equity_curve.append({"datetime": ts, "equity": cash + mv,
                                 "cash": cash, "position_value": mv})

        equity_df = pd.DataFrame(equity_curve).set_index("datetime")
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame(
            columns=["datetime", "action", "code", "price", "shares",
                     "amount", "cost", "reason"])
        return {"equity": equity_df, "trades": trades_df,
                "stats": self._stats(equity_df, trades_df)}

    @staticmethod
    def _is_t0(code):
        return code.startswith(("513", "511", "518"))

    @staticmethod
    def _stats(equity_df, trades_df, adapter=None):
        from frequency_adapter import get_adapter
        adapter = adapter or get_adapter()
        if equity_df.empty:
            return {}
        eq = equity_df["equity"]
        rets = eq.pct_change().dropna()
        total_ret = eq.iloc[-1] / eq.iloc[0] - 1
        days = max((eq.index[-1] - eq.index[0]).days, 1)
        ann_ret = (1 + total_ret) ** (365 / days) - 1
        ann_vol = rets.std() * np.sqrt(adapter.bars_per_year)
        sharpe = ann_ret / (ann_vol + 1e-9)
        dd = (eq - eq.cummax()) / eq.cummax()

        win_rate = 0.0
        if not trades_df.empty:
            buys = trades_df[trades_df["action"] == "BUY"].reset_index(drop=True)
            sells = trades_df[trades_df["action"] == "SELL"].reset_index(drop=True)
            n = min(len(buys), len(sells))
            if n > 0:
                wins = sum(1 for i in range(n)
                           if sells.loc[i, "price"] > buys.loc[i, "price"])
                win_rate = wins / n

        return {
            "初始资金": round(eq.iloc[0], 2),
            "最终资金": round(eq.iloc[-1], 2),
            "总收益率": f"{total_ret * 100:.2f}%",
            "年化收益率": f"{ann_ret * 100:.2f}%",
            "年化波动率": f"{ann_vol * 100:.2f}%",
            "夏普比率": round(sharpe, 3),
            "最大回撤": f"{dd.min() * 100:.2f}%",
            "交易次数": len(trades_df),
            "胜率": f"{win_rate * 100:.2f}%",
            "频率": adapter.freq,
            "bars_per_year": adapter.bars_per_year,
        }


def backtest_with_params(pool, signals, universe, risk_params):
    bt = MinuteBacktester(pool, universe, risk_params)
    return bt.run(signals)["stats"]