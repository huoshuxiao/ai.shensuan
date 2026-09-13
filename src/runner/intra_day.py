"""盘中运行入口：实时行情止盈止损检查 + 高抛低吸信号"""
import logging

from src.core.strategy import StrategyEngine
from src.output.reporter import Reporter

logger = logging.getLogger(__name__)


def run_intra_day(engine: StrategyEngine = None, reporter: Reporter = None) -> None:
    """盘中流程：
    - 有持仓：实时价检查止盈止损 -> SELL/HOLD
    - 无持仓：实时评分复核 -> BUY/EMPTY
    """
    engine = engine or StrategyEngine()
    reporter = reporter or engine.reporter

    reporter.print_header("盘中（实时监控）")

    try:
        signal = engine.run_intra_day()
        reporter.print_signal(signal)
        if signal.detail.get("ranking"):
            reporter.print_ranking(signal.detail["ranking"])
    except Exception as err:  # noqa: BLE001
        logger.exception("盘中运行失败: %s", err)
        print(f"[错误] 盘中运行失败: {err}")
        reporter.print_footer([f"运行异常: {err}，请检查网络与数据源"])
        return

    reporter.print_footer()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_intra_day()
