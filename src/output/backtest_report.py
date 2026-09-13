"""回测报告输出：Markdown 汇总报告 + CSV 每日明细"""
import csv
import logging
from pathlib import Path

from src.config.settings import REPORT_DIR, settings
from src.core.backtest import BacktestResult
from src.storage.state import today_str

logger = logging.getLogger(__name__)

CSV_HEADERS = [
    "date", "action", "code", "name", "price", "quantity", "cash",
    "position_value", "total_assets", "daily_return_pct",
    "cumulative_return_pct", "drawdown_pct", "score", "reason",
]


def report_dir() -> Path:
    """回测报告输出目录：output/reports/{运行日期}/backtest/"""
    path = REPORT_DIR / today_str() / "backtest"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_daily_csv(result: BacktestResult) -> Path:
    """保存每日明细 CSV，返回文件路径"""
    path = report_dir() / f"backtest_daily_{result.start}_{result.end}.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for day in result.days:
            writer.writerow(day.to_row())
    logger.info("回测每日明细已保存: %s", path)
    return path


def _trade_table(result: BacktestResult) -> str:
    """交易明细 Markdown 表格"""
    lines = [
        "| 日期 | 动作 | 代码 | 名称 | 价格 | 份额 | 金额 | 佣金 | 盈亏 | 收益率 |",
        "|------|------|------|------|------|------|------|------|------|--------|",
    ]
    for t in result.trades:
        pnl = f"{t.pnl:+.2f}" if t.action == "SELL" else "-"
        ret = f"{t.return_pct:+.2f}%" if t.action == "SELL" else "-"
        lines.append(
            f"| {t.date} | {t.action} | {t.code} | {t.name} | {t.price:.4f} | "
            f"{t.quantity} | {t.amount:.2f} | {t.commission:.2f} | {pnl} | {ret} |"
        )
    if not result.trades:
        lines.append("| - | - | - | 无成交记录 | - | - | - | - | - | - |")
    return "\n".join(lines)


def save_report(result: BacktestResult) -> Path:
    """保存回测汇总 Markdown 报告，返回文件路径"""
    end_position = (f"{result.end_position_name}({result.end_position_code})"
                    if result.end_position_code else "空仓")
    lines = [
        f"# ETF策略回测报告（{result.start} ~ {result.end}）",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 初始资金 | {result.initial_capital:.2f} 元 |",
        f"| 期末资产 | {result.final_assets:.2f} 元 |",
        f"| 总收益率 | {result.total_return_pct:+.2f}% |",
        f"| 年化收益率 | {result.annualized_return_pct:+.2f}% |",
        f"| 最大回撤 | {result.max_drawdown_pct:.2f}% |",
        f"| 累计已实现盈亏（含佣金） | {result.total_pnl:+.2f} 元 |",
        f"| 买入次数 | {result.buy_count} 次 |",
        f"| 卖出次数 | {result.sell_count} 次 |",
        f"| 胜率 | {result.win_rate_pct:.2f}%（{result.win_count}/{result.sell_count}） |",
        f"| 平均单笔盈利 | {result.avg_win:+.2f} 元 |",
        f"| 平均单笔亏损 | {result.avg_loss:+.2f} 元 |",
        f"| 期末状态 | {end_position} |",
        "",
        "## 交易明细",
        "",
        _trade_table(result),
        "",
        "## 每日净值与收益",
        "",
        "| 日期 | 动作 | 代码 | 名称 | 收盘价 | 份额 | 现金 | 持仓市值 | 总资产 | 当日收益率 | 累计收益率 | 回撤 | 评分 | 备注 |",
        "|------|------|------|------|--------|------|------|----------|--------|------------|------------|------|------|------|",
    ]
    for d in result.days:
        lines.append(
            f"| {d.date} | {d.action} | {d.code} | {d.name} | {d.price:.4f} | {d.quantity} | "
            f"{d.cash:.2f} | {d.position_value:.2f} | {d.total_assets:.2f} | "
            f"{d.daily_return_pct:+.4f}% | {d.cumulative_return_pct:+.4f}% | "
            f"{d.drawdown_pct:.4f}% | {d.score:.2f} | {d.reason} |"
        )
    lines += [
        "",
        "## 回测假设",
        "",
        f"- 数据源：按配置 `{settings.data_sources}` 顺序回退，日线窗口 {settings.backtest_history_days} 个自然日（含因子预热期）",
        f"- 成交：每日收盘后决策一次，以当日收盘价成交；买入整手（100份）满仓，单边佣金 {settings.commission_pct:.2%}",
        "- 信号规则与实盘盘前一致：持仓不换仓；止盈止损线触发即卖出；连续亏损冷却期内禁止买入",
        f"- 评分：与实盘相同的因子组合 `{settings.factor_weights}`，阈值 {settings.score_threshold}",
        "",
        "> 本报告由回测引擎自动生成，仅供策略研究参考，不构成投资建议。",
        "",
    ]
    path = report_dir() / f"backtest_report_{result.start}_{result.end}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("回测报告已保存: %s", path)
    return path


def print_summary(result: BacktestResult) -> None:
    """终端打印回测汇总"""
    print("=" * 64)
    print(f"ETF策略回测（{result.start} ~ {result.end}）")
    print("=" * 64)
    print(f"初始资金       {result.initial_capital:>12,.2f} 元")
    print(f"期末资产       {result.final_assets:>12,.2f} 元")
    print(f"总收益率       {result.total_return_pct:>+12.2f}%")
    print(f"年化收益率     {result.annualized_return_pct:>+12.2f}%")
    print(f"最大回撤       {result.max_drawdown_pct:>12.2f}%")
    print(f"累计已实现盈亏 {result.total_pnl:>+12.2f} 元")
    print(f"买入 {result.buy_count} 次 / 卖出 {result.sell_count} 次"
          f" / 胜率 {result.win_rate_pct:.2f}%")
    print(f"平均单笔盈利 {result.avg_win:+.2f} 元 / 平均单笔亏损 {result.avg_loss:+.2f} 元")
    print(f"期末状态       "
          + (f"{result.end_position_name}({result.end_position_code})"
             if result.end_position_code else "空仓"))
    print("=" * 64)
