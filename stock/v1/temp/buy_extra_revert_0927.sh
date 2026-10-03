#!/bin/bash
# ㊹ 退闸落地验账（09-27）：把 config 的默认值从 low0 改成空串，跑两场 ③ 做**双向**对表。
#   E = 一个 env 都不传（让新默认值自己生效）    → 三张 CSV 必须与 ㉟/㊴ 那批的 a_off **逐字节相同**
#   F = 显式传 STOCK_BUY_EXTRA_RULES=low0        → 三张 CSV 必须与 b_q080 **逐字节相同**
# 双向都成立才叫「只改了默认值、判据本体一行没动」：
#   E 不同 ⇒ 默认值没生效或还有别的地方在读 low0；F 不同 ⇒ 这场没吃到闸（env 泄漏）。
# 输出只进 shell/（STOCK_SIGNAL_DIR 覆写），**不碰** stock/v1/data/results/daily_signal/。
set -euo pipefail
ROOT=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
SRC=$ROOT/stock/v1/src
OUT=$ROOT/stock/v1/temp/tmp_buy_extra_revert_0927
REF=$ROOT/stock/v1/temp/tmp_buy_extra_knob_0926
mkdir -p "$OUT"
cd "$SRC"

run() {                       # run <目录名> <rules env，缺省不传>
  local tag=$1 rules=${2-}
  mkdir -p "$OUT/$tag"
  echo "===== $tag  $(date +%T) ====="
  if [ -z "$rules" ]; then
    STOCK_SIGNAL_DIR="$OUT/$tag" python3.10 run_ashare_daily_signal.py \
      > "$OUT/$tag.log" 2>&1
  else
    STOCK_SIGNAL_DIR="$OUT/$tag" STOCK_BUY_EXTRA_RULES="$rules" \
      python3.10 run_ashare_daily_signal.py > "$OUT/$tag.log" 2>&1
  fi
  echo "  完成 → $OUT/$tag.log（$(wc -c < "$OUT/$tag.log") 字节）"
}

run e_off   ""       # 新默认（空串 = 关闸）
run f_low0  low0     # 显式打开，回到 ㉟ 那一版
echo "===== 两场跑完 $(date +%T) ====="
