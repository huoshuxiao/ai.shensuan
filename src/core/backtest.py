"""回测引擎：历史日线重放，模拟每日交易与收益

与实盘策略（StrategyEngine 盘前逻辑）保持一致的信号规则：
- 持仓：触发止盈/止损线 -> SELL（收盘价成交）；否则 HOLD（不换仓）
- 空仓：冷却期内 -> EMPTY；评分最高且 >= 阈值 -> BUY（收盘价、整手全仓买入）
- 空仓：无达标标的 -> EMPTY

假设与简化：
- 每日仅在收盘后决策一次，以当日收盘价成交（T+0 或日次频次）
- 买入按整手（100 份）满仓买入，扣除单边佣金 settings.commission_pct
- 卖出后当日不立即买入（冷却期 settings.cooldown_days 生效）
"""
import logging
from dataclasses import dataclass, field

import pandas as pd

from src.config.etf_pool import ETF_POOL, get_etf
from src.config.settings import settings
from src.core.data_fetcher import DataFetcher
from src.core.risk import RiskManager
from src.core.scorer import Scorer
from src.core.selector import Selector
from src.output.models import SIGNAL_BUY, SIGNAL_EMPTY, SIGNAL_HOLD, SIGNAL_SELL
from src.storage.state import Position
from src.core.strategy import build_factors

logger = logging.getLogger(__name__)

LOT_SIZE = 100  # ETF 交易最小单位（份）


@dataclass
class BacktestDay:
    """回测单日记录"""
    date: str
    action: str            # BUY/SELL/HOLD/EMPTY
    code: str = ""         # 操作标的（空仓/持有记录持仓标的）
    name: str = ""
    price: float = 0.0     # 当日收盘价（操作价）
    quantity: int = 0      # 当日持有份额
    cash: float = 0.0      # 当日收盘后现金
    position_value: float = 0.0      # 当日收盘后持仓市值
    total_assets: float = 0.0        # 当日收盘后总资产
    daily_return_pct: float = 0.0    # 当日收益率（%）
    cumulative_return_pct: float = 0.0  # 累计收益率（%，相对初始资金）
    drawdown_pct: float = 0.0        # 回撤（%，相对历史最高资产）
    score: float = 0.0     # 当日最优评分（无持仓决策参考）
    reason: str = ""

    def to_row(self) -> dict:
        """转 CSV 行"""
        return {
            "date": self.date, "action": self.action, "code": self.code,
            "name": self.name, "price": f"{self.price:.4f}",
            "quantity": self.quantity, "cash": f"{self.cash:.2f}",
            "position_value": f"{self.position_value:.2f}",
            "total_assets": f"{self.total_assets:.2f}",
            "daily_return_pct": f"{self.daily_return_pct:.4f}",
            "cumulative_return_pct": f"{self.cumulative_return_pct:.4f}",
            "drawdown_pct": f"{self.drawdown_pct:.4f}",
            "score": f"{self.score:.2f}", "reason": self.reason,
        }


@dataclass
class BacktestTrade:
    """单笔成交记录（买入/卖出）"""
    date: str
    action: str            # BUY / SELL
    code: str
    name: str
    price: float
    quantity: int
    amount: float          # 成交金额（不含佣金）
    commission: float      # 佣金
    pnl: float = 0.0       # 卖出时的本笔盈亏（含双边佣金），买入为 0
    return_pct: float = 0.0  # 本笔收益率（%）


@dataclass
class BacktestResult:
    """回测汇总结果"""
    start: str
    end: str
    initial_capital: float
    final_assets: float
    total_return_pct: float          # 总收益率（%）
    annualized_return_pct: float     # 年化收益率（%）
    max_drawdown_pct: float          # 最大回撤（%）
    buy_count: int                   # 买入次数
    sell_count: int                  # 卖出次数
    win_count: int                   # 盈利卖出次数
    total_pnl: float                 # 累计已实现盈亏（含佣金）
    end_position_code: str = ""      # 期末持仓代码（空仓为空）
    end_position_name: str = ""
    days: list[BacktestDay] = field(default_factory=list)      # 每日记录
    trades: list[BacktestTrade] = field(default_factory=list)  # 每笔成交

    @property
    def win_rate_pct(self) -> float:
        """胜率（%）= 盈利卖出次数 / 卖出次数"""
        return round(100.0 * self.win_count / self.sell_count, 2) if self.sell_count else 0.0

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self.trades if t.action == SIGNAL_SELL and t.pnl > 0]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl for t in self.trades if t.action == SIGNAL_SELL and t.pnl <= 0]
        return sum(losses) / len(losses) if losses else 0.0


class BacktestEngine:
    """回测引擎：拉取历史日线，逐交易日重放信号

    依赖均可注入（fetcher/scorer/selector/risk），便于测试。
    """

    def __init__(self, fetcher: DataFetcher = None, scorer: Scorer = None,
                 selector: Selector = None, risk: RiskManager = None,
                 start: str = None, end: str = None, capital: float = None):
        self.fetcher = fetcher or DataFetcher()
        self.scorer = scorer or Scorer(build_factors())
        self.selector = selector or Selector()
        self.risk = risk or RiskManager()
        self.start = start or settings.backtest_start
        self.end = end or settings.backtest_end
        self.capital = capital if capital is not None else settings.initial_capital

    # ================= 数据准备 =================
    def _load_history(self) -> dict[str, pd.DataFrame]:
        """拉取全池历史日线（含预热期），失败标的跳过并告警"""
        days = settings.backtest_history_days
        data: dict[str, pd.DataFrame] = {}
        for etf in ETF_POOL:
            try:
                df = self.fetcher.get_etf_daily(etf.code, days=days)
            except Exception as err:  # noqa: BLE001
                logger.warning("回测数据获取失败 %s(%s): %s", etf.code, etf.name, err)
                continue
            if df is None or df.empty:
                logger.warning("回测数据为空，跳过 %s(%s)", etf.code, etf.name)
                continue
            df = df.sort_values("date").reset_index(drop=True)
            df["date"] = pd.to_datetime(df["date"])
            data[etf.code] = df
        return data

    def _load_index(self) -> pd.DataFrame | None:
        """上证指数历史日线（情绪因子用），失败返回 None"""
        try:
            df = self.fetcher.get_market_index_daily(days=settings.backtest_history_days)
        except Exception as err:  # noqa: BLE001
            logger.warning("回测指数数据获取失败，情绪因子降级: %s", err)
            return None
        if df is None or df.empty:
            return None
        df = df.sort_values("date").reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"])
        return df

    def _trading_dates(self, data: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
        """交易日历：优先 510300 的日期，失败则取全部标的日期并集"""
        start = pd.Timestamp(self.start)
        end = pd.Timestamp(self.end)
        primary = data.get("510300")
        if primary is not None and not primary.empty:
            dates = pd.DatetimeIndex(primary["date"])
        else:
            union = pd.DatetimeIndex([])
            for df in data.values():
                union = union.union(pd.DatetimeIndex(df["date"]))
            dates = union
        mask = (dates >= start) & (dates <= end)
        return dates[mask].sort_values()

    def _history_slice(self, df: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
        """取截至指定日期的历史切片（因子计算窗口）"""
        return df[df["date"] <= date]

    # ================= 每日评分 =================
    def _score_day(self, date: pd.Timestamp, data: dict[str, pd.DataFrame],
                   index_daily: pd.DataFrame | None, position: Position):
        """当日评分：每只标的用 <=date 的历史计算因子并选最优"""
        data_map: dict[str, dict] = {}
        index_hist = self._history_slice(index_daily, date) if index_daily is not None else None
        for code, df in data.items():
            hist = self._history_slice(df, date)
            if len(hist) < settings.min_history_bars:
                continue
            data_map[code] = {"daily": hist, "index_daily": index_hist}
        if not data_map:
            return None
        context = {"position": None if position is None or position.is_empty else position}
        scores = self.scorer.score_all(data_map, context)
        return self.selector.select(scores)

    def _close_price(self, df: pd.DataFrame, date: pd.Timestamp) -> float | None:
        """当日收盘价（停牌/缺数据时用最近可得收盘价）"""
        hist = self._history_slice(df, date)
        if hist.empty:
            return None
        return float(hist["close"].iloc[-1])

    # ================= 回测主流程 =================
    def run(self) -> BacktestResult:
        data = self._load_history()
        if not data:
            raise RuntimeError("回测数据全部获取失败，无法运行")
        index_daily = self._load_index()
        dates = self._trading_dates(data)
        if len(dates) == 0:
            raise RuntimeError(f"回测区间 {self.start} ~ {self.end} 内无交易日数据")

        cash = self.capital
        position = Position.empty()  # 模拟持仓（realized_pnl 累计已实现盈亏）
        held_buy_cost = 0.0          # 当前持仓买入总成本（含佣金）
        days: list[BacktestDay] = []
        trades: list[BacktestTrade] = []
        prev_assets = self.capital
        peak_assets = self.capital

        for date in dates:
            d_str = date.strftime("%Y-%m-%d")
            selection = self._score_day(date, data, index_daily, position)
            best = selection.best if selection else None
            best_score = best.total if best else 0.0

            # 持仓标的当日价格（估值与止盈止损用）
            hold_price = None
            if not position.is_empty:
                df = data.get(position.code)
                hold_price = self._close_price(df, date) if df is not None else None

            # 风控复核（冷却期 + 止盈止损）
            risk_decision = self.risk.check(position, hold_price, today=date.to_pydatetime())

            action = SIGNAL_EMPTY
            code, name, price, reason = "", "", 0.0, ""
            quantity = position.quantity

            if not position.is_empty:
                if risk_decision.force_sell:
                    # 卖出：当日收盘价成交（先保存卖出前的累计状态）
                    prev_realized = position.realized_pnl
                    prev_losses = position.consecutive_losses
                    action, code, name, price = SIGNAL_SELL, position.code, position.name, hold_price
                    reason = risk_decision.reason
                    amount = price * position.quantity
                    commission = amount * settings.commission_pct
                    proceeds = amount - commission
                    pnl = proceeds - held_buy_cost
                    cash += proceeds
                    trades.append(BacktestTrade(
                        date=d_str, action=SIGNAL_SELL, code=code, name=name,
                        price=price, quantity=position.quantity,
                        amount=amount, commission=commission, pnl=pnl,
                        return_pct=round(100.0 * pnl / held_buy_cost, 2) if held_buy_cost else 0.0,
                    ))
                    new_position = Position.empty()
                    new_position.realized_pnl = prev_realized + pnl
                    new_position.last_trade_date = d_str
                    new_position.consecutive_losses = prev_losses + 1 if pnl < 0 else 0
                    position = new_position
                    held_buy_cost = 0.0
                    quantity = 0
                else:
                    action, code, name, price = SIGNAL_HOLD, position.code, position.name, hold_price
                    reason = risk_decision.reason or "持仓正常，继续持有（不换仓）"
                    position.hold_days += 1
            else:
                if risk_decision.block_buy:
                    reason = f"风控禁止买入：{risk_decision.reason}"
                elif best is None:
                    reason = selection.reason if selection else "无候选标的"
                else:
                    # 买入：当日收盘价、整手满仓
                    code = best.code
                    name = get_etf(code).name
                    df = data.get(code)
                    price = self._close_price(df, date) if df is not None else 0.0
                    if price and price > 0:
                        lots = int(cash / (price * LOT_SIZE * (1 + settings.commission_pct)))
                        if lots > 0:
                            action = SIGNAL_BUY
                            quantity = lots * LOT_SIZE
                            amount = price * quantity
                            commission = amount * settings.commission_pct
                            held_buy_cost = amount + commission
                            cash -= held_buy_cost
                            position = Position(
                                code=code, name=name, buy_price=price, quantity=quantity,
                                buy_date=d_str, hold_days=0, last_trade_date=d_str,
                                consecutive_losses=position.consecutive_losses,
                                realized_pnl=position.realized_pnl,
                            )
                            trades.append(BacktestTrade(
                                date=d_str, action=SIGNAL_BUY, code=code, name=name,
                                price=price, quantity=quantity,
                                amount=amount, commission=commission,
                            ))
                            reason = selection.reason
                        else:
                            reason = f"资金不足一手 {name}({price:.3f})，空仓"
                    else:
                        reason = f"{code} 当日无价格数据，无法买入"

            # 当日收盘后估值
            position_value = 0.0
            if not position.is_empty:
                df = data.get(position.code)
                px = self._close_price(df, date) if df is not None else position.buy_price
                position_value = (px if px else position.buy_price) * position.quantity
            total_assets = cash + position_value
            daily_return = (total_assets / prev_assets - 1.0) if prev_assets else 0.0
            cumulative_return = total_assets / self.capital - 1.0
            peak_assets = max(peak_assets, total_assets)
            drawdown = total_assets / peak_assets - 1.0 if peak_assets else 0.0

            days.append(BacktestDay(
                date=d_str, action=action, code=code, name=name, price=price,
                quantity=quantity, cash=cash, position_value=position_value,
                total_assets=total_assets,
                daily_return_pct=round(daily_return * 100.0, 4),
                cumulative_return_pct=round(cumulative_return * 100.0, 4),
                drawdown_pct=round(drawdown * 100.0, 4),
                score=best_score, reason=reason,
            ))
            prev_assets = total_assets

        # 汇总指标
        final_assets = days[-1].total_assets if days else self.capital
        total_return = final_assets / self.capital - 1.0
        n_days = len(days)
        annualized = (1.0 + total_return) ** (365.0 / n_days) - 1.0 if n_days and total_return > -1.0 else 0.0
        max_drawdown = min((d.drawdown_pct for d in days), default=0.0)
        buy_count = sum(1 for t in trades if t.action == SIGNAL_BUY)
        sell_count = sum(1 for t in trades if t.action == SIGNAL_SELL)
        win_count = sum(1 for t in trades if t.action == SIGNAL_SELL and t.pnl > 0)
        end_code = position.code if not position.is_empty else ""
        end_name = position.name if not position.is_empty else ""

        return BacktestResult(
            start=self.start, end=self.end, initial_capital=self.capital,
            final_assets=round(final_assets, 2),
            total_return_pct=round(total_return * 100.0, 2),
            annualized_return_pct=round(annualized * 100.0, 2),
            max_drawdown_pct=round(max_drawdown, 2),
            buy_count=buy_count, sell_count=sell_count, win_count=win_count,
            total_pnl=round(position.realized_pnl, 2),
            end_position_code=end_code, end_position_name=end_name,
            days=days, trades=trades,
        )
