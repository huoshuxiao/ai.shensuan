#!/bin/bash
# 一次性夹具（10-03）：造一个「同入口正在跑」的假象，用来验起场器第三条腿有没有牙。
# 这个进程除了睡觉什么都不做——它只是把入口脚本名放进自己的 argv，让 pgrep 抓得到。
# 用法：nohup bash decoy_busy_1003.sh & ; 跑完 kill 掉（是自己造的，清理归自己）。
exec -a "/usr/bin/python3.10 run_daily_backtest.py" sleep 40
