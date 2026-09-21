# -*- coding: utf-8 -*-
"""反馈引擎"""

import os
import json
import pandas as pd
from datetime import datetime
from config import LIVE_DATA_DIR, REPORT_DIR
from slippage_analyzer import SlippageAnalyzer
from latency_analyzer import LatencyAnalyzer
from turnover_analyzer import TurnoverAnalyzer
from config_updater import ConfigUpdater
from strategy_feedback import StrategyFeedback
from factor_feedback import FactorFeedback
from live_attribution import decompose_live_vs_backtest


class FeedbackEngine:
    def __init__(self, auto_update=False):
        self.auto_update = auto_update
        self.report = {}

    def run(self):
        print("\n" + "=" * 60)
        print("  🔄 实盘反馈闭环")
        print("=" * 60)
        print("\n[1/6] 滑点分析...")
        slip = SlippageAnalyzer()
        slip.load()
        slip_stats = slip.analyze() or {}
        print("\n[2/6] 实盘 vs 回测逐笔分解（时间最近配对）...")
        lvb = decompose_live_vs_backtest()
        lvb_stats = {}
        if not lvb.empty:
            lvb_stats = {
                "n_paired": int(len(lvb)),
                "avg_abs_slip": float(lvb["slippage"].abs().mean()),
                "avg_lag_days": float(lvb["lag_days"].abs().mean()),
                "max_lag_days": float(lvb["lag_days"].abs().max()),
                # 时滞占配对数的比例：>0.5 说明分歧主要由延迟而非价格造成
                "lag_gt_1d_ratio": float(
                    (lvb["lag_days"].abs() > 1).mean())}
            print(f"  配对 {lvb_stats['n_paired']} 笔，"
                  f"平均|价差|={lvb_stats['avg_abs_slip'] * 100:.4f}%，"
                  f"平均时滞={lvb_stats['avg_lag_days']:.2f} 天，"
                  f"时滞>1 天占比={lvb_stats['lag_gt_1d_ratio'] * 100:.0f}%")
        else:
            print("  ℹ️ 无可配对的实盘/回测成交（需先跑实盘与回测）")
        print("\n[3/6] 延迟分析...")
        lat_stats = LatencyAnalyzer().analyze()
        print("\n[4/6] 换手分析...")
        turn_stats = TurnoverAnalyzer().analyze()
        print("\n[5/6] 策略级反馈...")
        strat_stats = StrategyFeedback().analyze()
        print("\n[6/6] 因子级反馈...")
        factor_stats = FactorFeedback().analyze()

        self.report = {"generated_at": datetime.now().isoformat(),
                       "slippage": slip_stats, "latency": lat_stats,
                       "turnover": turn_stats, "strategy": strat_stats,
                       "factor": factor_stats,
                       "live_vs_backtest": lvb_stats}
        self._save_report()
        if self.auto_update and slip_stats.get(
                "suggested_slippage_conservative"):
            updater = ConfigUpdater()
            updater.apply_feedback({
                "slippage": slip_stats["suggested_slippage_conservative"]})
        return self.report

    def _save_report(self):
        os.makedirs(LIVE_DATA_DIR, exist_ok=True)
        os.makedirs(REPORT_DIR, exist_ok=True)
        with open(f"{LIVE_DATA_DIR}/feedback_report.json", "w",
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
        lv = self.report.get("live_vs_backtest", {})
        if lv:
            lines.append("")
            lines.append("## 🔬 实盘 vs 回测逐笔分解")
            lines.append(f"- 配对笔数: {lv.get('n_paired', 0)}"
                         "（明细 data/live/live_vs_backtest.csv）")
            lines.append(f"- 平均绝对价差: {lv.get('avg_abs_slip', 0)*100:.4f}%")
            lines.append(f"- 平均成交时滞: {lv.get('avg_lag_days', 0):.2f} 天，"
                         f"最大 {lv.get('max_lag_days', 0):.2f} 天")
            lines.append(f"- 时滞>1 天占比: "
                         f"{lv.get('lag_gt_1d_ratio', 0)*100:.0f}%"
                         "（占比高说明分歧来自成交时点而非价格）")
        st = self.report.get("strategy", {})
        if st:
            lines.append("")
            lines.append("## 📈 策略")
            lines.append(f"- 实盘夏普: {st.get('live_sharpe', 0):.3f}")
        with open(f"{REPORT_DIR}/feedback_report.md", "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n  ✅ 报告已保存: {REPORT_DIR}/feedback_report.md")


def run_feedback(auto_update=False):
    engine = FeedbackEngine(auto_update=auto_update)
    return engine.run()