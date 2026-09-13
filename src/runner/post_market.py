"""盘后运行入口：更新持仓状态、记录盈亏、输出当日总结"""
import logging

from src.core.strategy import StrategyEngine
from src.output.reporter import Reporter

logger = logging.getLogger(__name__)


def run_post_market(engine: StrategyEngine = None, reporter: Reporter = None) -> None:
    """盘后流程：更新持仓 -> 评分快照 -> 日报输出"""
    engine = engine or StrategyEngine()
    reporter = reporter or engine.reporter

    reporter.print_header("盘后（当日复盘）")

    try:
        report = engine.run_post_market()
        if report.signal:
            reporter.print_signal(report.signal)
        if report.ranking:
            reporter.print_ranking(report.ranking)
        reporter.print_footer(report.notes)
    except Exception as err:  # noqa: BLE001
        logger.exception("盘后运行失败: %s", err)
        print(f"[错误] 盘后运行失败: {err}")
        reporter.print_footer([f"运行异常: {err}，请检查网络与数据源"])
        return


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_post_market()
