# -*- coding: utf-8 -*-
"""定时任务"""

import time
import subprocess
from datetime import datetime

try:
    import schedule
except ImportError:
    print("pip install schedule")
    exit(1)


def daily_feedback():
    subprocess.run(["python", "run_feedback.py",
                    "--auto", "--llm", "daily"])


def weekly_retrain():
    subprocess.run(["python", "main.py"])


def monthly_report():
    subprocess.run(["python", "run_monthly.py", "--days", "20"])


def main():
    schedule.every().monday.at("09:00").do(weekly_retrain)
    schedule.every().day.at("15:30").do(daily_feedback)
    schedule.every(30).days.at("09:00").do(monthly_report)
    print("⏰ 定时任务已启动")
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()