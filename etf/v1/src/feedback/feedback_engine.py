# -*- coding: utf-8 -*-
"""反馈引擎"""

import os
import json
import pandas as pd
from datetime import datetime
from .slippage_analyzer import SlippageAnalyzer
from .latency_analyzer import LatencyAnalyzer
from .turnover_analyzer import TurnoverAnalyzer
from .config_updater import ConfigUpdater
from .strategy_feedback import StrategyFeedback
from .factor_feedback import FactorFeedback


class FeedbackEngine:
    def __init__(self, auto_update=False):
        self.auto_update = auto_update
        self.report = {}

    def run(self):
        print("\n" + "=" * 60)
        print("  🔄 实盘反馈闭环")
        print("=" * 60)
        print("\n[1/5] 滑点分析...")
        slip = SlippageAnalyzer()
        slip.load()
        slip_stats = slip.analyze() or {}
        print("\n[2/5] 延迟分析...")
        lat_stats = LatencyAnalyzer().analyze()
        print("\n[3/5] 换手分析...")
        turn_stats = TurnoverAnalyzer().analyze()
        print("\n[4/5] 策略级反馈...")
        strat_stats = StrategyFeedback().analyze()
        print("\n[5/5] 因子级反馈...")
        factor_stats = FactorFeedback().analyze()

        self.report = {"generated_at": datetime.now().isoformat(),
                       "slippage": slip_stats, "latency": lat_stats,
                       "turnover": turn_stats, "strategy": strat_stats,
                       "factor": factor_stats}
        self._save_report()
        if self.auto_update and slip_stats.get(
                "suggested_slippage_conservative"):
            updater = ConfigUpdater()
            updater.apply_feedback({
                "slippage": slip_stats["suggested_slippage_conservative"]})
        return self.report

    def _save_report(self):
        os.makedirs("live_data", exist_ok=True)
        with open("live_data/feedback_report.json", "w",
                  encoding="utf-8") as f:
            json.dump(self.report, f, ensure_ascii=False,
                      indent=2, default=str)
        lines = ["# 实盘反馈闭环报告", "",
                 f"> 生成于 {self.report['generated_at']}", ""]
        sp = self.report.get("slippage", {})
        if sp:
            lines.append("## 📊 滑点")
            lines.append(f"- 平均: {sp.get('avg_slippage', 0)*100:.4f}%")
            lines.append(f"- 建议: "
                         f"{sp.get('suggested_slippage_conservative', 0)*100:.4f}%")
        st = self.report.get("strategy", {})
        if st:
            lines.append("")
            lines.append("## 📈 策略")
            lines.append(f"- 实盘夏普: {st.get('live_sharpe', 0):.3f}")
        with open("live_data/feedback_report.md", "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("\n  ✅ 报告已保存: live_data/feedback_report.md")


def run_feedback(auto_update=False):
    engine = FeedbackEngine(auto_update=auto_update)
    return engine.run()