# -*- coding: utf-8 -*-
"""盲点检测"""

import os
import json
import glob
import pandas as pd
from collections import Counter


class BlindSpotDetector:
    def __init__(self, live_data_dir="live_data"):
        self.dir = live_data_dir

    def detect(self, days=20):
        daily_files = sorted(glob.glob(
            f"{self.dir}/report_daily*.json"))[-days:]
        if not daily_files:
            return {}
        all_texts = []
        for f in daily_files:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                all_texts.append(data.get("text", "").lower())
            except Exception:
                continue
        combined = " ".join(all_texts)

        key_indicators = {
            "滑点": ["滑点", "slippage"],
            "延迟": ["延迟", "latency"],
            "换手": ["换手", "turnover"],
            "夏普": ["夏普", "sharpe"],
            "回撤": ["回撤", "drawdown"],
            "胜率": ["胜率", "win rate"],
            "因子淘汰": ["因子淘汰", "淘汰因子"],
            "集中度": ["集中度", "concentration"],
            "相关性": ["相关性", "correlation"],
            "交易成本": ["交易成本", "trading cost"],
            "容量": ["容量", "capacity"],
            "持仓时间": ["持仓时间", "holding period"],
        }

        never, rarely = [], []
        for name, kws in key_indicators.items():
            count = sum(combined.count(kw.lower()) for kw in kws)
            if count == 0:
                never.append(name)
            elif count <= 2:
                rarely.append({"name": name, "count": count})

        unanalyzed = []
        events_path = f"{self.dir}/live_risk_events.csv"
        if os.path.exists(events_path):
            events = pd.read_csv(events_path)
            halts = events[events["level"] == "HALT"]
            for _, h in halts.iterrows():
                if h.get("reason", "").lower() not in combined:
                    unanalyzed.append({"time": h.get("time", ""),
                                       "reason": h.get("reason", "")})

        return {"never_mentioned": never,
                "rarely_mentioned": rarely,
                "unanalyzed_events": unanalyzed,
                "n_days": len(all_texts)}

    def save(self, result):
        path = f"{self.dir}/blind_spots.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        lines = ["# 🔍 盲点检测报告", "",
                 f"分析 {result.get('n_days', 0)} 天日报", ""]
        if result.get("never_mentioned"):
            lines.append("## ❌ 从未提及的指标")
            lines.append("")
            for item in result["never_mentioned"]:
                lines.append(f"- {item}")
            lines.append("")
        if result.get("rarely_mentioned"):
            lines.append("## ⚠️ 很少提及")
            lines.append("")
            for item in result["rarely_mentioned"]:
                lines.append(f"- {item['name']} (出现 {item['count']} 次)")
            lines.append("")
        md = "\n".join(lines)
        with open(f"{self.dir}/blind_spots.md", "w",
                  encoding="utf-8") as f:
            f.write(md)
        return md


def detect_blind_spots(days=20):
    detector = BlindSpotDetector()
    result = detector.detect(days)
    if result:
        detector.save(result)
    return result