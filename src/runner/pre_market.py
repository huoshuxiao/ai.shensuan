"""盘前运行入口：基于最新日线生成当日交易计划"""
import logging

from src.core.strategy import StrategyEngine
from src.output.reporter import Reporter

logger = logging.getLogger(__name__)


def run_pre_market(engine: StrategyEngine = None, reporter: Reporter = None) -> None:
    """盘前流程：全池评分 -> 唯一标的或空仓 -> 输出计划"""
    engine = engine or StrategyEngine()
    reporter = reporter or engine.reporter

    reporter.print_header("盘前（生成当日交易计划）")

    try:
        signal = engine.run_pre_market()
        reporter.print_signal(signal)
        if signal.detail.get("ranking"):
            reporter.print_ranking(signal.detail["ranking"])
    except Exception as err:  # noqa: BLE001
        logger.exception("盘前运行失败: %s", err)
        print(f"[错误] 盘前运行失败: {err}")
        reporter.print_footer([f"运行异常: {err}，请检查网络与数据源"])
        return

    reporter.print_footer()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_pre_market()
