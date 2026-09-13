"""回测运行器：初始化回测引擎 -> 执行 -> 输出报告"""
import logging

from src.config.settings import settings
from src.core.backtest import BacktestEngine, BacktestResult
from src.output.backtest_report import print_summary, save_daily_csv, save_report
from src.output.reporter import DISCLAIMER

logger = logging.getLogger(__name__)


def run_backtest(engine=None, start: str = None, end: str = None) -> BacktestResult:
    """执行回测并输出报告（Markdown 汇总 + CSV 每日明细）

    Args:
        engine: 可复用外部 StrategyEngine 的数据抓取器（限频器）；None 则新建
        start: 回测开始日期（默认 settings.backtest_start）
        end:   回测结束日期（默认 settings.backtest_end）
    """
    fetcher = engine.fetcher if engine is not None else None
    backtester = BacktestEngine(
        fetcher=fetcher,
        start=start or settings.backtest_start,
        end=end or settings.backtest_end,
    )
    logger.info("开始回测 %s ~ %s", backtester.start, backtester.end)
    result = backtester.run()

    csv_path = save_daily_csv(result)
    report_path = save_report(result)
    print_summary(result)
    print(f"\n每日明细: {csv_path}")
    print(f"汇总报告: {report_path}")
    print(DISCLAIMER)
    return result
