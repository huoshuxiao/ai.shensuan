"""结果输出：终端表格 + Markdown 报告（按日期目录），含数据来源与合规声明"""
import json
import logging
from datetime import datetime
from pathlib import Path

from tabulate import tabulate

from src.config.settings import REPORT_DIR
from src.core.data_fetcher import DATA_SOURCE
from src.output.models import DailyReport, TradeSignal

logger = logging.getLogger(__name__)

# 合规声明
DISCLAIMER = (
    "【合规声明】本系统输出仅供个人研究学习使用，不构成任何投资建议。"
    f"数据来源：{DATA_SOURCE}。投资有风险，入市需谨慎。"
)

SIGNAL_CN = {
    "BUY": "买入",
    "SELL": "卖出",
    "HOLD": "持有",
    "EMPTY": "空仓",
}

MODE_CN = {
    "pre": "盘前（生成当日交易计划）",
    "intra": "盘中（实时监控）",
    "post": "盘后（当日复盘）",
}

# 因子中文名（排名表列头）
FACTOR_CN = {
    "sentiment": "情绪",
    "oversold": "超跌",
    "timing": "择时",
    "position": "仓位",
    "downtrend_guard": "趋势",
}


class Reporter:
    """输出报告器：终端展示 + 落盘"""

    def __init__(self, report_dir: Path = REPORT_DIR):
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 终端输出 ----------
    def print_header(self, mode_cn: str) -> None:
        line = "=" * 60
        print(line)
        print(f"  场内ETF交易策略 - {mode_cn}")
        print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(DISCLAIMER)
        print(line)

    def print_signal(self, signal: TradeSignal) -> None:
        rows = [
            ["信号", f"{SIGNAL_CN.get(signal.signal, signal.signal)} ({signal.signal})"],
            ["标的", f"{signal.name}({signal.code})" if signal.code else "-"],
            ["参考价格", f"{signal.price:.3f}" if signal.price else "-"],
            ["综合评分", f"{signal.score}" if signal.score is not None else "-"],
            ["理由", signal.reason or "-"],
        ]
        print(tabulate(rows, tablefmt="grid"))
        self._print_factor_detail(signal)

    def print_ranking(self, ranking: list[dict]) -> None:
        if not ranking:
            return
        factor_keys = list((ranking[0].get("factors") or {}).keys())
        headers = ["排名", "代码", "名称", "总分"] + [FACTOR_CN.get(k, k) for k in factor_keys]
        rows = []
        for i, r in enumerate(ranking, 1):
            factors = r.get("factors", {})
            rows.append([
                i, r.get("code"), r.get("name"), r.get("total"),
                *[factors.get(k) for k in factor_keys],
            ])
        print("\n全池评分排名（前10）：")
        print(tabulate(rows, headers=headers, tablefmt="simple"))

    def print_footer(self, notes: list[str] | None = None) -> None:
        if notes:
            print("\n备注：")
            for note in notes:
                print(f"  - {note}")
        print(DISCLAIMER)
        print("=" * 60)

    # ---------- Markdown 落盘（按日期目录） ----------
    def _date_dir(self, date_str: str) -> Path:
        """按日期创建报告子目录：reports/YYYY-MM-DD/"""
        path = self.report_dir / date_str
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_signal(self, signal: TradeSignal, filename: str | None = None) -> Path:
        """保存信号 Markdown：reports/YYYY-MM-DD/signal_{mode}_{HHMMSS}.md"""
        day = datetime.now().strftime("%Y-%m-%d")
        ts = datetime.now().strftime("%H%M%S")
        path = self._date_dir(day) / (filename or f"signal_{signal.mode}_{ts}.md")
        path.write_text(self._signal_markdown(signal), encoding="utf-8")
        logger.info("信号已保存: %s", path)
        return path

    def save_report(self, report: DailyReport) -> Path:
        """保存盘后日报 Markdown：reports/YYYY-MM-DD/daily_report.md"""
        path = self._date_dir(report.date) / "daily_report.md"
        path.write_text(self._report_markdown(report), encoding="utf-8")
        logger.info("日报已保存: %s", path)
        return path

    # ---------- Markdown 渲染 ----------
    @staticmethod
    def _signal_markdown(signal: TradeSignal) -> str:
        """渲染交易信号 Markdown"""
        mode_cn = MODE_CN.get(signal.mode, signal.mode)
        signal_cn = SIGNAL_CN.get(signal.signal, signal.signal)
        target = f"{signal.name}({signal.code})" if signal.code else "-"
        lines = [
            f"# 场内ETF交易策略 - {mode_cn}",
            "",
            f"- 运行时间: {signal.generated_at}",
            f"- 数据来源: {DATA_SOURCE}",
            "",
            "## 交易信号",
            "",
            "| 项目 | 内容 |",
            "| --- | --- |",
            f"| 信号 | {signal_cn} ({signal.signal}) |",
            f"| 标的 | {target} |",
            f"| 参考价格 | {signal.price:.3f} |" if signal.price is not None else "| 参考价格 | - |",
            f"| 综合评分 | {signal.score} |" if signal.score is not None else "| 综合评分 | - |",
            f"| 理由 | {signal.reason or '-'} |",
        ]

        if signal.detail:
            lines += ["", "## 因子明细", "", "| 指标 | 数值 |", "| --- | --- |"]
            for key, value in signal.detail.items():
                if key == "ranking":
                    continue  # 排名单独在下方折叠块渲染，避免表格超长行
                if isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        lines.append(f"| {key}.{sub_key} | `{Reporter._md_cell(sub_value)}` |")
                else:
                    lines.append(f"| {key} | `{Reporter._md_cell(value)}` |")

        ranking = signal.detail.get("ranking") if signal.detail else None
        if isinstance(ranking, list) and ranking:
            lines += [
                "", "## 全池评分排名", "",
                "<details>", "<summary>排名明细 (JSON)</summary>", "",
                "```json",
                json.dumps(ranking, ensure_ascii=False, indent=2),
                "```", "", "</details>",
            ]

        lines += ["", "## 合规声明", "", f"> {DISCLAIMER}", ""]
        return "\n".join(lines)

    @staticmethod
    def _md_cell(value) -> str:
        """Markdown 表格单元格内容（dict/list 用紧凑 JSON）"""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    @staticmethod
    def _report_markdown(report: DailyReport) -> str:
        """渲染盘后日报 Markdown"""
        lines = [
            "# 场内ETF交易策略 - 盘后日报",
            "",
            f"- 日期: {report.date}",
            f"- 数据来源: {DATA_SOURCE}",
            "",
            "## 持仓状态",
            "",
            "```json",
            json.dumps(report.position, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 已实现盈亏",
            "",
            f"{report.realized_pnl:.2f}",
        ]

        if report.signal:
            signal_cn = SIGNAL_CN.get(report.signal.signal, report.signal.signal)
            target = f"{report.signal.name}({report.signal.code})" if report.signal.code else "-"
            lines += [
                "", "## 次日参考信号", "",
                "| 项目 | 内容 |",
                "| --- | --- |",
                f"| 信号 | {signal_cn} ({report.signal.signal}) |",
                f"| 标的 | {target} |",
                f"| 综合评分 | {report.signal.score} |" if report.signal.score is not None else "| 综合评分 | - |",
                f"| 理由 | {report.signal.reason or '-'} |",
            ]

        if report.ranking:
            factor_keys = list((report.ranking[0].get("factors") or {}).keys())
            headers = ["排名", "代码", "名称", "总分"] + [FACTOR_CN.get(k, k) for k in factor_keys]
            lines += ["", "## 全池评分排名", "", "| " + " | ".join(headers) + " |",
                      "| " + " | ".join(["---"] * len(headers)) + " |"]
            for i, r in enumerate(report.ranking, 1):
                factors = r.get("factors", {})
                row = [str(i), str(r.get("code")), str(r.get("name")), str(r.get("total"))]
                row += [str(factors.get(k, "")) for k in factor_keys]
                lines.append("| " + " | ".join(row) + " |")

        if report.notes:
            lines += ["", "## 备注", ""]
            lines += [f"- {note}" for note in report.notes]

        lines += ["", "## 合规声明", "", f"> {DISCLAIMER}", ""]
        return "\n".join(lines)

    @staticmethod
    def _print_factor_detail(signal: TradeSignal) -> None:
        if not signal.detail:
            return

        def fmt(v):
            # 列表（如排名）用多行 JSON 避免单行超长；dict 用紧凑 JSON
            if isinstance(v, list):
                return json.dumps(v, ensure_ascii=False, indent=1)
            if isinstance(v, dict):
                return json.dumps(v, ensure_ascii=False)
            return v

        rows = []
        for key, value in signal.detail.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    rows.append([f"{key}.{sub_key}", fmt(sub_value)])
            else:
                rows.append([key, fmt(value)])
        print("\n因子明细：")
        print(tabulate(rows, headers=["指标", "数值"], tablefmt="simple"))
