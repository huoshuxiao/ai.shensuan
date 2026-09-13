"""策略引擎：整合数据、因子、评分、选择、风控与状态，输出交易信号

三个运行模式：
- pre  (盘前): 基于最新日线全池评分 -> 生成当日交易计划（BUY/SELL/HOLD/EMPTY）
- intra(盘中): 实时行情检查止盈止损 + 评分复核 -> 高抛低吸信号
- post (盘后): 更新持仓状态与连续亏损 -> 当日总结
"""
import logging
from datetime import datetime

from src.config.etf_pool import ETF_POOL, get_etf
from src.config.settings import settings
from src.core.data_fetcher import DataFetcher
from src.core.data_validator import DataValidator
from src.core.factors.base import BaseFactor
from src.core.factors.downtrend_guard import DowntrendGuardFactor
from src.core.factors.oversold import OversoldFactor
from src.core.factors.position import PositionFactor
from src.core.factors.sentiment import SentimentFactor
from src.core.factors.timing import TimingFactor
from src.core.risk import RiskDecision, RiskManager
from src.core.scorer import Scorer, ScoreResult
from src.core.selector import Selection, Selector
from src.output.models import SIGNAL_BUY, SIGNAL_EMPTY, SIGNAL_HOLD, SIGNAL_SELL, DailyReport, TradeSignal
from src.output.reporter import Reporter
from src.storage.state import Position, StateStore, today_str

logger = logging.getLogger(__name__)


# 因子注册表：配置名 -> 因子类（新增因子时在此注册）
FACTOR_CLASSES = {
    "sentiment": SentimentFactor,
    "oversold": OversoldFactor,
    "timing": TimingFactor,
    "position": PositionFactor,
    "downtrend_guard": DowntrendGuardFactor,
}


def build_factors() -> list[BaseFactor]:
    """按配置构建因子列表：settings.factor_weights 中权重>0 的因子参与评分"""
    factors = []
    for name, weight in settings.factor_weights.items():
        factor_cls = FACTOR_CLASSES.get(name)
        if factor_cls is None:
            logger.warning("配置中存在未注册的因子: %s，已忽略", name)
            continue
        if weight <= 0:
            logger.info("因子 %s 权重<=0，已停用", name)
            continue
        factor = factor_cls()
        factor.weight = weight
        factors.append(factor)
    return factors


class StrategyEngine:
    """策略引擎（依赖均可注入，便于测试）"""

    def __init__(self, fetcher: DataFetcher = None, validator: DataValidator = None,
                 scorer: Scorer = None, selector: Selector = None,
                 risk: RiskManager = None, store: StateStore = None,
                 reporter: Reporter = None):
        self.fetcher = fetcher or DataFetcher()
        self.validator = validator or DataValidator()
        self.scorer = scorer or Scorer(build_factors())
        self.selector = selector or Selector()
        self.risk = risk or RiskManager()
        self.store = store or StateStore()
        self.reporter = reporter or Reporter()
        self._index_daily = None  # 大盘指数缓存

    # ================= 数据准备 =================
    def _load_clean_daily(self, code: str):
        """抓取 + 校验日线，失败返回 None"""
        raw = self.fetcher.get_etf_daily(code)
        result = self.validator.validate_daily(raw, code)
        for warn in result.warnings:
            logger.warning(warn)
        for err in result.errors:
            logger.error(err)
        return result.df if result.ok else None

    def _load_index_daily(self):
        """大盘指数日线（情绪因子用），失败返回 None"""
        if self._index_daily is not None:
            return self._index_daily
        try:
            raw = self.fetcher.get_market_index_daily()
            result = self.validator.validate_daily(raw, "上证指数")
            self._index_daily = result.df if result.ok else None
        except Exception as err:  # noqa: BLE001
            logger.warning("大盘指数获取失败，情绪因子降级: %s", err)
            self._index_daily = None
        return self._index_daily

    def _build_data_map(self):
        """全池日线数据 + 校验，返回 {code: {"daily": df}}"""
        data_map = {}
        index_daily = self._load_index_daily()
        for etf in ETF_POOL:
            df = self._load_clean_daily(etf.code)
            if df is not None:
                data_map[etf.code] = {"daily": df, "index_daily": index_daily}
        return data_map

    def _context(self, position: Position, realtime: dict | None = None) -> dict:
        return {"position": None if position.is_empty else position, "realtime": realtime}

    def _ranking_dicts(self, selection: Selection) -> list[dict]:
        """评分排名转可输出结构"""
        out = []
        for s in selection.ranking[:10]:
            etf = get_etf(s.code)
            out.append({
                "code": s.code,
                "name": etf.name,
                "category": etf.category,
                "total": s.total,
                "factors": {k: v.score for k, v in s.factor_results.items()},
            })
        return out

    def _factor_detail(self, score_result: ScoreResult) -> dict:
        return {
            k: {"score": v.score, **v.detail}
            for k, v in score_result.factor_results.items()
        }

    # ================= 盘前 =================
    def run_pre_market(self) -> TradeSignal:
        """盘前：全池评分 -> 唯一标的或空仓"""
        position = self.store.load()
        data_map = self._build_data_map()
        if not data_map:
            signal = TradeSignal(mode="pre", signal=SIGNAL_EMPTY,
                                 reason="全部候选数据校验失败，空仓",
                                 generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self.reporter.save_signal(signal)
            return signal

        scores = self.scorer.score_all(data_map, self._context(position))
        selection = self.selector.select(scores)

        # 风控复核
        risk_decision = self._risk_check(position, selection)
        signal = self._build_signal("pre", selection, risk_decision, position)
        signal.detail = {
            "ranking": self._ranking_dicts(selection),
            "position": None if position.is_empty else position.to_dict(),
        }
        if selection.best:
            signal.detail["factors"] = self._factor_detail(selection.best)
        self.reporter.save_signal(signal)
        return signal

    # ================= 盘中 =================
    def run_intra_day(self) -> TradeSignal:
        """盘中：持仓止盈止损检查 + 无持仓时的评分复核"""
        position = self.store.load()
        realtime = self.fetcher.get_etf_realtime(position.code) if not position.is_empty else None

        # 1) 有持仓：优先检查止盈止损（高抛低吸/截断亏损）
        if not position.is_empty:
            price = realtime.get("price") if realtime else None
            risk_decision = self.risk.check(position, price)
            if risk_decision.force_sell:
                signal = TradeSignal(
                    mode="intra", signal=SIGNAL_SELL,
                    code=position.code, name=position.name,
                    price=price, score=None,
                    reason=risk_decision.reason,
                    detail={"position": position.to_dict(), "realtime": realtime},
                    generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                self.reporter.save_signal(signal)
                return signal
            signal = TradeSignal(
                mode="intra", signal=SIGNAL_HOLD,
                code=position.code, name=position.name,
                price=price, score=None,
                reason=risk_decision.reason or "持仓正常，继续持有",
                detail={"position": position.to_dict(), "realtime": realtime},
                generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            self.reporter.save_signal(signal)
            return signal

        # 2) 无持仓：盘中评分复核（防止错失低吸机会）
        data_map = self._build_data_map()
        if not data_map:
            signal = TradeSignal(mode="intra", signal=SIGNAL_EMPTY,
                                 reason="数据校验失败，空仓",
                                 generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self.reporter.save_signal(signal)
            return signal
        scores = self.scorer.score_all(data_map, self._context(position))
        selection = self.selector.select(scores)
        risk_decision = self._risk_check(position, selection)
        signal = self._build_signal("intra", selection, risk_decision, position)
        signal.detail = {"ranking": self._ranking_dicts(selection)}
        if selection.best:
            signal.detail["factors"] = self._factor_detail(selection.best)
        self.reporter.save_signal(signal)
        return signal

    # ================= 盘后 =================
    def run_post_market(self) -> DailyReport:
        """盘后：更新持仓状态（持有天数/连续亏损）、输出日报"""
        position = self.store.load()
        notes: list[str] = []
        float_pnl = 0.0  # 持仓浮动盈亏

        if not position.is_empty:
            position.hold_days += 1
            price = None
            realtime = self.fetcher.get_etf_realtime(position.code)
            if realtime and realtime.get("price"):
                price = float(realtime["price"])
            else:
                df = self._load_clean_daily(position.code)
                if df is not None:
                    price = float(df["close"].iloc[-1])
            if price:
                float_pnl = (price - position.buy_price) * position.quantity
            notes.append(
                f"持仓 {position.name}({position.code})，持有 {position.hold_days} 交易日，"
                f"成本 {position.buy_price:.3f}，现价 {price:.3f}，浮动盈亏 {float_pnl:+.2f}" if price
                else f"持仓 {position.name}({position.code})，持有 {position.hold_days} 交易日"
            )
            self.store.save(position)
        else:
            notes.append("当前空仓")

        # 账户总资产 = 初始资金 + 累计已实现盈亏 + 持仓浮动盈亏
        total_assets = round(settings.initial_capital + position.realized_pnl + float_pnl, 2)
        notes.append(
            f"账户总资产 {total_assets:.2f} 元（初始资金 {settings.initial_capital:.0f}"
            f" + 已实现盈亏 {position.realized_pnl:+.2f} + 浮动盈亏 {float_pnl:+.2f}）"
        )

        # 评分快照（次日盘前参考）
        data_map = self._build_data_map()
        ranking: list[dict] = []
        signal = None
        if data_map:
            scores = self.scorer.score_all(data_map, self._context(position))
            selection = self.selector.select(scores)
            ranking = self._ranking_dicts(selection)
            risk_decision = self._risk_check(position, selection)
            signal = self._build_signal("post", selection, risk_decision, position)

        report = DailyReport(
            date=today_str(),
            mode="post",
            signal=signal,
            position=position.to_dict(),
            realized_pnl=position.realized_pnl,
            notes=notes,
            ranking=ranking,
        )
        self.reporter.save_report(report)
        return report

    # ================= 状态变更（供外部交易执行后调用） =================
    def apply_buy(self, code: str, price: float, quantity: int = 1000) -> Position:
        """执行买入后更新持仓状态（保留累计盈亏与连续亏损记录）

        资金校验：买入金额不得超过可用资金（初始资金 + 累计已实现盈亏）。
        """
        etf = get_etf(code)
        prev = self.store.load()
        available = settings.initial_capital + prev.realized_pnl
        cost = price * quantity
        if cost > available:
            raise ValueError(
                f"资金不足：买入金额 {cost:.2f} 元超过可用资金 {available:.2f} 元"
            )
        position = Position(
            code=code, name=etf.name, buy_price=price, quantity=quantity,
            buy_date=today_str(), hold_days=0, last_trade_date=today_str(),
            consecutive_losses=prev.consecutive_losses,  # 冷却期状态延续
            realized_pnl=prev.realized_pnl,              # 累计盈亏延续
        )
        self.store.save(position)
        return position

    def apply_sell(self, sell_price: float) -> Position:
        """执行卖出后更新状态（含连续亏损计数）"""
        position = self.store.load()
        if position.is_empty:
            return position
        pnl = (sell_price - position.buy_price) * position.quantity
        new_position = Position.empty()
        new_position.realized_pnl = position.realized_pnl + pnl
        new_position.last_trade_date = today_str()
        new_position.consecutive_losses = (
            position.consecutive_losses + 1 if pnl < 0 else 0
        )
        self.store.save(new_position)
        return new_position

    # ================= 内部工具 =================
    def _risk_check(self, position: Position, selection: Selection) -> RiskDecision:
        """盘前/盘中评分后做风控复核"""
        # 持仓时用实时价做止盈止损检查；空仓时检查冷却期等买入限制
        price = None
        if not position.is_empty:
            realtime = self.fetcher.get_etf_realtime(position.code)
            if realtime and realtime.get("price"):
                price = float(realtime["price"])
        return self.risk.check(position, price)

    def _build_signal(self, mode: str, selection: Selection,
                      risk_decision: RiskDecision, position: Position) -> TradeSignal:
        """根据选择结果 + 风控 + 持仓生成信号"""
        gen_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1) 风控强制卖出
        if risk_decision.force_sell and not position.is_empty:
            return TradeSignal(mode=mode, signal=SIGNAL_SELL,
                               code=position.code, name=position.name,
                               price=None, score=None,
                               reason=risk_decision.reason, generated_at=gen_at)

        # 2) 有持仓：不能买其他标的
        if not position.is_empty:
            if selection.best and selection.best.code == position.code:
                reason = f"继续持有 {position.name}，评分 {selection.best.total}；" + (risk_decision.reason or "")
                return TradeSignal(mode=mode, signal=SIGNAL_HOLD,
                                   code=position.code, name=position.name,
                                   price=None, score=selection.best.total,
                                   reason=reason, generated_at=gen_at)
            reason = f"已持仓 {position.name}，本次不换仓；" + (risk_decision.reason or selection.reason)
            return TradeSignal(mode=mode, signal=SIGNAL_HOLD,
                               code=position.code, name=position.name,
                               price=None, score=None, reason=reason, generated_at=gen_at)

        # 3) 空仓：无达标 -> EMPTY；达标 -> BUY
        if selection.best is None:
            return TradeSignal(mode=mode, signal=SIGNAL_EMPTY, reason=selection.reason,
                               generated_at=gen_at)

        etf = get_etf(selection.best.code)
        if risk_decision.block_buy:
            return TradeSignal(mode=mode, signal=SIGNAL_EMPTY,
                               reason=f"风控禁止买入：{risk_decision.reason}", generated_at=gen_at)

        return TradeSignal(mode=mode, signal=SIGNAL_BUY,
                           code=selection.best.code, name=etf.name,
                           price=None, score=selection.best.total,
                           reason=selection.reason, generated_at=gen_at)
