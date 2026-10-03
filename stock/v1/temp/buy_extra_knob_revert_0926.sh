#!/bin/bash
# 09-26 夜回退验账：用户拍板「刀口留在 0.80、独立旋钮保留」。
# 跑一场 D = **一个 env 都不传刀口**（只覆写输出目录），让 config 的默认值自己生效，
# 再和今天下午那一场 B（显式 STOCK_BUY_EXTRA_QUANTILE=0.80）逐字节对表。
#   D vs B 三张表相同 ⇒ 新旋钮的默认值真的等于 0.80，且回退只动了读数没动判据
#   D vs C 必须**不同**  ⇒ 否则说明这场压根没吃到旋钮（env 泄漏或默认值写错）
set -euo pipefail
ROOT=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
SRC=$ROOT/stock/v1/src
OUT=$ROOT/stock/v1/temp/tmp_buy_extra_knob_0926
cd "$SRC"

mkdir -p "$OUT/d_default"
echo "===== D（默认配置，不传刀口 env）$(date +%T) ====="
STOCK_SIGNAL_DIR="$OUT/d_default" python3.10 run_ashare_daily_signal.py \
  > "$OUT/d_default.log" 2>&1
echo "  完成 → $OUT/d_default.log（$(wc -c < "$OUT/d_default.log") 字节）"
grep -n "名单独立闸" "$OUT/d_default.log" | head -3
