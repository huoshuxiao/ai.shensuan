#!/bin/bash
# 乙：环2/环3 归档刷新（09-27）——覆写面上是 6 张基线表（board 档），
# _flat / _dated / _globalrank 等变体是**另存的文件名**，这一步碰不到（已核对）。
# 动前的底与 md5 在 shell/stock/tmp_ring23_refresh_0927/backup/。
# 两步**串行**：环2 峰值内存 5.01GB，不可并发。
cd /home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src || exit 1
LOG=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/shell/stock/tmp_ring23_refresh_0927/refresh.log
: > "$LOG"
echo "[起手] $(date +%F\ %T) 环2 run_ashare_portfolio_eval.py" >> "$LOG"
/usr/bin/python3.10 run_ashare_portfolio_eval.py >> "$LOG" 2>&1
echo "EXIT_RING2=$?" >> "$LOG"
echo "[起手] $(date +%F\ %T) 环3 run_ashare_redundancy_check.py" >> "$LOG"
/usr/bin/python3.10 run_ashare_redundancy_check.py >> "$LOG" 2>&1
echo "EXIT_RING3=$?" >> "$LOG"
echo "[终] $(date +%F\ %T)" >> "$LOG"
