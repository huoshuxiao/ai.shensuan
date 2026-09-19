# -*- coding: utf-8 -*-
"""月度复盘"""

import os
import json
import glob
import numpy as np
import pandas as pd
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from config import LLM_MODEL, LLM_API_KEY_ENV, LIVE_DATA_DIR, REPORT_DIR


MONTHLY_PROMPT = """你是量化投资总监。写月度复盘报告给投委会。

结构：
## 📊 月度摘要
## 💰 业绩回顾
## 🔍 系统健康度
## 🎯 关键发现
## ⚠️ 风险警示
## ✅ 下月行动计划
## 💡 战略建议

长度 1500-2500 字。
"""


class MonthlyReviewGenerator:
    def __init__(self, live_data_dir=None, report_dir=None):
        self.dir = live_data_dir or LIVE_DATA_DIR
        self.out = report_dir or REPORT_DIR
        self.api_key = os.environ.get(LLM_API_KEY_ENV, "")
        self.enabled = bool(self.api_key)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def _chat(self, messages):
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages, temperature=0.5)
        return resp.choices[0].message.content

    def generate(self, days=20):
        print(f"\n  📅 月度复盘（{days} 天）...")
        # 加载数据
        files = sorted(glob.glob(
            f"{self.dir}/feedback_report*.json"))[-days:]
        if not files:
            print("  ❌ 无数据")
            return ""
        reports = []
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    reports.append(json.load(fp))
            except Exception:
                continue
        # 计算指标
        metrics = self._compute_metrics(reports)
        # LLM 生成
        user = (f"# 月度数据\n"
                f"天数: {len(reports)}\n"
                f"健康度: {metrics.get('health', 0)}\n"
                f"收益: {metrics.get('total_return', 0):.2f}%\n"
                f"夏普: {metrics.get('sharpe', 0):.3f}\n"
                f"回撤: {metrics.get('max_dd', 0):.2f}%\n"
                f"请写月度复盘。")
        if self.enabled:
            try:
                text = self._chat([
                    {"role": "system", "content": MONTHLY_PROMPT},
                    {"role": "user", "content": user}])
            except Exception:
                text = self._fallback(metrics)
        else:
            text = self._fallback(metrics)
        # 保存
        os.makedirs(self.out, exist_ok=True)
        with open(f"{self.out}/report_monthly.md", "w",
                  encoding="utf-8") as f:
            f.write(text)
        with open(f"{self.out}/monthly_metrics.json", "w",
                  encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2,
                      default=str)
        print(f"  ✅ 月报: {self.out}/report_monthly.md")
        return text

    def _compute_metrics(self, reports):
        eq_path = f"{self.dir}/live_attribution.csv"
        metrics = {"n_days": len(reports), "health": 60}
        if os.path.exists(eq_path):
            df = pd.read_csv(eq_path)
            if not df.empty:
                eq = df["total_asset"]
                metrics["total_return"] = float(
                    (eq.iloc[-1] / eq.iloc[0] - 1) * 100)
                rets = eq.pct_change().dropna()
                metrics["sharpe"] = float(rets.mean() /
                                          (rets.std() + 1e-9) *
                                          np.sqrt(240 * 252))
                dd = (eq - eq.cummax()) / eq.cummax()
                metrics["max_dd"] = float(dd.min() * 100)
        return metrics

    def _fallback(self, metrics):
        return (f"## 📊 月度摘要\n健康度 {metrics.get('health', 0)}/100\n\n"
                f"## 💰 业绩\n收益 {metrics.get('total_return', 0):.2f}%\n\n"
                f"## 💡 建议\n配置 OPENAI_API_KEY 获取完整报告。")


def generate_monthly_review(days=20):
    return MonthlyReviewGenerator().generate(days=days)