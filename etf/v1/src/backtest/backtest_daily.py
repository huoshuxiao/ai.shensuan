# -*- coding: utf-8 -*-
"""日线回测封装：截面组合引擎的多标的日线口径"""

import pandas as pd
import numpy as np
from config import (
    INIT_CAPITAL, COMMISSION_RATE, MIN_COMMISSION,
    SLIPPAGE, MIN_TRADE_AMOUNT, RISK_CONTROL, PORTFOLIO, ETF_FILTER,
)
from frequency_adapter import get_adapter
from data_loader import PointInTimeData
from portfolio_engine import (
    PortfolioEngine, holding_stats, annual_turnover, win_rate,
)


class DailyBacktester:
    """
    日线回测引擎
    与 MinuteBacktester 保持相同接口
    """

    def __init__(self, pool: dict, universe=None, risk_params=None):
        self.pool = pool
        self.universe = universe
        self.risk_params = risk_params or RISK_CONTROL
        self.adapter = get_adapter("daily")
        # 全部取价走时点访问器，杜绝越界读到 date 之后的 bar
        self.pit = PointInTimeData(pool)
        self.engine = PortfolioEngine(
            pit=self.pit, risk_cls=DailyRiskController,
            risk_params=self.risk_params, universe=universe, pool=pool,
            day_of=lambda ts: pd.Timestamp(ts).date(), is_t0=self._is_t0,
            init_capital=INIT_CAPITAL, min_trade_amount=MIN_TRADE_AMOUNT,
            lot_size=PORTFOLIO["lot_size"],
            max_turnover=PORTFOLIO["max_turnover"],
            min_daily_amount=ETF_FILTER.get("min_daily_amount", 0.0))

    @staticmethod
    def _cost(amount):
        """单边交易成本 = max(金额×佣金率, 最低佣金) + 金额×滑点。
        滑点近似成交价冲击（买贵卖贱各半计入）"""
        return max(amount * COMMISSION_RATE, MIN_COMMISSION) + \
            amount * SLIPPAGE

    def _price(self, code, date, field="close"):
        """取某日某列价格；该 ETF 当日无 bar（停牌/未上市）返回 None"""
        return self.pit.get_price(code, date, field)

    def run(self, signals: pd.DataFrame) -> dict:
        """事件循环式日线回测（按时间顺序模拟，杜绝未来函数）。

        signals: 截面组合长表契约（index=date, columns=[code, weight, score]，
        只在调仓日出行，空仓日出 code="" 哨兵）。多标的同时持仓，
        权重和乘全组合降仓比例 = 目标金额。具体执行次序见 portfolio_engine。
        """
        equity_df, trades_df = self.engine.run(signals, "date")
        stats = self._stats(equity_df, trades_df)
        return {"equity": equity_df, "trades": trades_df, "stats": stats}

    @staticmethod
    def _is_t0(code):
        # 日线场景下，跨境(513)/货币债券(511)/黄金(518) ETF 支持 T+0
        # （T+0 品种当日买入可当日卖出，不受 buy_date==date 限制）
        return code.startswith(("513", "511", "518"))

    def _stats(self, equity_df, trades_df):
        """绩效统计。夏普 = 年化收益/年化波动（adapter 按频率折算）；
        最大回撤 dd_t = eq_t / cummax(eq)_t - 1 取最小值；
        胜率按同标的 FIFO 配对（卖价 > 该批买价记为赢）。"""
        if equity_df.empty:
            return {}
        eq = equity_df["equity"]
        rets = eq.pct_change().dropna()
        total_ret = eq.iloc[-1] / eq.iloc[0] - 1
        n_bars = len(eq)
        ann_ret = self.adapter.annualize_return(total_ret, n_bars)
        ann_vol = self.adapter.annualize_vol(rets)
        sharpe = ann_ret / (ann_vol + 1e-9)
        dd = (eq - eq.cummax()) / eq.cummax()
        turnover = annual_turnover("date", trades_df, equity_df,
                                   self.adapter.bars_per_year)

        def _d(ts):
            return ts.date() if hasattr(ts, "date") else str(ts)[:10]
        return {
            "回测区间": f"{_d(eq.index[0])} ~ {_d(eq.index[-1])} "
                      f"({n_bars} bars)",
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
            "频率": "daily",
            "bars_per_year": self.adapter.bars_per_year,
        }


class DailyRiskController:
    """日线风控（比分钟线简化）。规则按优先级短路返回：
    熔断冷却 > 日止损 > 回撤熔断 > 交易间隔 > 当日次数上限。"""

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
        """跨日重置当日计数器，并以开盘时点净值做日内止损基准"""
        day = pd.Timestamp(ts).date()
        if day != self.current_day:
            self.current_day = day
            self.trades_today = 0
            self.daily_start_equity = equity

    def can_trade(self, ts, bar_idx, equity):
        """返回 (是否允许开/加仓, 拒绝原因)。
        日止损：equity/当日起始净值 - 1 <= daily_stop_loss；
        回撤熔断：equity/历史峰值 - 1 <= max_drawdown_stop 时
        冻结交易 cooldown_days 个自然日；
        注意止损/熔断触发时外层引擎会反向强制卖出。"""
        if self.cooldown_until and pd.Timestamp(ts) < self.cooldown_until:
            return False, "熔断冷却"
        if self.cooldown_until:
            # 冷却到期：以当前净值重新起算峰值。不清零就会让触发那次熔断的
            # 同一段回撤逐 bar 复读——净值空仓后不再变化，dd 永远停在阈值下，
            # 引擎此后再也没买过（实测 2010-07 起的 16 年全被锁死）。
            self.cooldown_until = None
            self.peak_equity = equity
            self.daily_start_equity = equity
        if self.daily_start_equity and self.daily_start_equity > 0:
            r = equity / self.daily_start_equity - 1
            if r <= self.p["daily_stop_loss"]:
                return False, f"日止损({r*100:.2f}%)"
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
        """回撤越深仓位越轻的线性缩放：
        ratio = single_position_max · (1 + dd·5)，限幅 [0.2, 上限]
        （dd=-20% 时降到 0 倍→触发下限 2 成仓硬底）。"""
        if self.peak_equity <= 0:
            return self.p["single_position_max"]
        dd = equity / self.peak_equity - 1
        return max(0.2, min(self.p["single_position_max"],
                            self.p["single_position_max"] * (1 + dd * 5)))