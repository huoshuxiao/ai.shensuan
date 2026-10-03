#!/bin/bash
# 选项E 落地后的三场重跑（③ 盘前名单入口，一场一枪，串行）
#   A = 关掉这道闸（STOCK_BUY_EXTRA_RULES=""）        → 必须与生产归档**逐字节相同**
#   B = 接上、刀口 0.80（㉟ 当晚那一版）              → 与 wire_extra_0926/on 对表
#   C = 接上、刀口 0.85（本次落地默认值）             → 新形状
set -euo pipefail
ROOT=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
SRC=$ROOT/stock/v1/src
OUT=$ROOT/stock/v1/temp/tmp_buy_extra_knob_0926
mkdir -p "$OUT"
cd "$SRC"

run() {                       # run <目录名> <开关> <刀口>
  local tag=$1 rules=$2 q=$3
  mkdir -p "$OUT/$tag"
  echo "===== $tag  rules='$rules' quantile='$q'  $(date +%T) ====="
  STOCK_SIGNAL_DIR="$OUT/$tag" \
  STOCK_BUY_EXTRA_RULES="$rules" \
  STOCK_BUY_EXTRA_QUANTILE="$q" \
  python3.10 run_ashare_daily_signal.py > "$OUT/$tag.log" 2>&1
  echo "  完成 → $OUT/$tag.log（$(wc -c < "$OUT/$tag.log") 字节）"
}

run a_off   ""     0.85       # 关掉时刀口取值无意义，故意给 0.85 顺带验「空串 = 不挡」
run b_q080  low0   0.80
run c_q085  low0   0.85       # 与 config 默认同值，显式传一遍防 env 漂移
echo "===== 三场跑完 $(date +%T) ====="
