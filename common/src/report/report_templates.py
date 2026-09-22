# -*- coding: utf-8 -*-
"""报告模板"""

import json


class ReportTemplate:
    @staticmethod
    def _format_slippage(sp):
        if not sp:
            return "无数据"
        return (f"- 平均滑点: {sp.get('avg_slippage', 0)*100:.4f}%\n"
                f"- P95: {sp.get('p95_slippage', 0)*100:.4f}%")

    @staticmethod
    def _format_strategy(st):
        if not st:
            return "无数据"
        return (f"- 实盘夏普: {st.get('live_sharpe', 0):.3f}\n"
                f"- 回测夏普: {st.get('bt_sharpe', 0):.3f}")

    @staticmethod
    def build_daily_prompt(report):
        lines = [f"## 反馈数据 ({report.get('generated_at', '')})", ""]
        lines.append("### 滑点")
        lines.append(ReportTemplate._format_slippage(
            report.get("slippage", {})))
        lines.append("")
        lines.append("### 策略")
        lines.append(ReportTemplate._format_strategy(
            report.get("strategy", {})))
        lines.append("")
        lines.append("请写日报。")
        return "\n".join(lines)