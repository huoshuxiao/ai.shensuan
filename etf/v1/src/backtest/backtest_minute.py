# -*- coding: utf-8 -*-
"""分钟线回测封装：截面组合引擎的分钟 bar 口径

与日线唯一差别是时间口径（bar 级 ts、同一自然日内多次风控复位）与统计的
年度换算系数；成交约束、换手上限、先卖后买等执行逻辑全在 portfolio_engine。"""

import pandas as pd
import numpy as np
from config import (
    INIT_CAPITAL, RISK_CONTROL, PORTFOLIO, ETF_FILTER, MIN_TRADE_AMOUNT,
)
from data_loader import PointInTimeData
from portfolio_engine import (
    PortfolioEngine, holding_stats, annual_turnover, win_rate,
)


class RiskController:
    """分钟线风控（逐 bar 判定，日内计数按自然日复位）"""

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
        if self.cooldown_until:
            # 冷却到期后以当前净值重新起算峰值，否则触发熔断的那段回撤
            # 会逐 bar 复读，引擎再也开不了仓（与日线侧同一处修正）
            self.cooldown_until = None
            self.peak_equity = equity
            self.daily_start_equity = equity
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
    """分钟线回测：与 DailyBacktester 同接口，共用截面组合引擎"""

    def __init__(self, pool, universe, risk_params=None):
        self.pool = pool
        self.universe = universe
        self.risk_params = risk_params
        # 与日线引擎一致：取价统一走时点访问器（只允许看到 ts 及之前的 bar）
        self.pit = PointInTimeData(pool)
        self.engine = PortfolioEngine(
            pit=self.pit, risk_cls=RiskController,
            risk_params=self.risk_params, universe=universe, pool=pool,
            day_of=lambda ts: pd.Timestamp(ts).date(), is_t0=self._is_t0,
            init_capital=INIT_CAPITAL, min_trade_amount=MIN_TRADE_AMOUNT,
            lot_size=PORTFOLIO["lot_size"],
            max_turnover=PORTFOLIO["max_turnover"],
            min_daily_amount=0.0)  # 分钟 bar 成交额与日线阈值不同口径，不设闸

    def run(self, signals):
        """signals: 截面组合长表契约（index=datetime, columns=[code, weight,
        score]，只在调仓 bar 出行，空仓日出 code="" 哨兵）"""
        equity_df, trades_df = self.engine.run(signals, "datetime")
        return {"equity": equity_df, "trades": trades_df,
                "stats": self._stats(equity_df, trades_df)}

    @staticmethod
    def _is_t0(code):
        return code.startswith(("513", "511", "518"))

    @staticmethod
    def _stats(equity_df, trades_df, adapter=None):
        """绩效统计。夏普 = 年化收益/年化波动；最大回撤取
        min(eq/cummax(eq) - 1)；胜率按同标的 FIFO 配对。"""
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
        turnover = annual_turnover("datetime", trades_df, equity_df,
                                   adapter.bars_per_year)

        return {
            "回测区间": (f"{eq.index[0]} ~ {eq.index[-1]} "
                        f"({len(eq)} bars)"),
            "初始资金": round(eq.iloc[0], 2),
            "最终资金": round(eq.iloc[-1], 2),
            "总收益率": f"{total_ret * 100:.2f}%",
            "年化收益率": f"{ann_ret * 100:.2f}%",
            "年化波动率": f"{ann_vol * 100:.2f}%",
            "夏普比率": round(sharpe, 3),
            "最大回撤": f"{dd.min() * 100:.2f}%",
            "交易次数": len(trades_df),
            "平均持仓只数": round(holding_stats(equity_df), 2),
            "年化换手率": f"{turnover * 100:.1f}%",
            "胜率": f"{win_rate(trades_df) * 100:.2f}%",
            "频率": adapter.freq,
            "bars_per_year": adapter.bars_per_year,
        }


def backtest_with_params(pool, signals, universe, risk_params):
    bt = MinuteBacktester(pool, universe, risk_params)
    return bt.run(signals)["stats"]
