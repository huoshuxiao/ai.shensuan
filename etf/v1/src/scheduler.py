# -*- coding: utf-8 -*-
"""定时任务"""

import sys
import time
import subprocess
from datetime import datetime

import _bootstrap  # noqa: F401  必须先于项目模块导入
from log_kit import setup_logging

try:
    import schedule
except ImportError:
    print("pip install schedule")
    exit(1)


def _run(script, *args):
    """用当前解释器跑子任务（避免默认 python 版本不符），输出并入日志"""
    cmd = [sys.executable, script, *args]
    print(f"  ▶ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout)
    if proc.returncode != 0:
        print(f"  ❌ {script} 退出码 {proc.returncode}")
        print(proc.stderr)


def daily_data_update():
    """日线镜像 + 池缓存 + 风险面板拉到源方最新可得日，跑完再进 feedback。

    09-24 17:58 实测：新浪的 T 日日线要到 **T+1** 才出（当日只有 tx 有 09-24，
    而 tx 是整段缩放的口径，过不了 `update_etf_daily` 的重叠残差闸）⇒ 本点位拿到
    的是 T−1，这是源方上限，不是抓取时刻不够晚。"""
    _run("data/update_etf_daily.py")


def daily_feedback():
    _run("run_feedback.py", "--auto", "--llm", "daily")


def weekly_retrain():
    _run("main.py")


def monthly_report():
    _run("run_monthly.py", "--days", "20")


def main():
    setup_logging("scheduler")
    schedule.every().monday.at("09:00").do(weekly_retrain)
    schedule.every().day.at("15:20").do(daily_data_update)
    schedule.every().day.at("15:30").do(daily_feedback)
    schedule.every(30).days.at("09:00").do(monthly_report)
    print("⏰ 定时任务已启动")
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
