#!/bin/bash
# 件2 接力器（10-02 21:5x 写，用户裁「C：件3 让它跑完 + 补一个接力器」）
#
# 为什么要有这个文件：上一版排队器 queue_runs_1001.sh 的外壳在我编辑它本体时被
# kill 掉了（bash 会为「读下一条命令」回到旧字节偏移，脚本一改就可能从半行执行 ⇒
# 必须停外壳保生产场）。停外壳的代价是**自动尾巴全丢**：件3 跑完不会自动出收尾
# 读数、也不会自动接件2。这个接力器就是补那两条尾巴，用的是**新文件**，
# 从此不再编辑任何正在跑的脚本。
#
# 撤销办法（任一）：
#   touch etf/v1/temp/queue_j2.CANCEL             ← 下一个检查点退出
#   kill $(cat etf/v1/temp/queue_j2_1002.pid)     ← 只杀接力器，不动已在跑的场
#
# 三条闸门（与上一版同源，只是多了一条「等 5050 退场」）：
#   ①件3 的主进程 pid 已经不在  ②MemAvailable ≥ 门槛（件2 每循环 LLM 调用 1→3~4 次，
#   按同族最坏档实测峰值 6.52GiB 再加 1.5GiB 余量 = 8192MB）  ③SwapFree ≥ 512MB
#   外加「同入口有没有别人在跑」那一腿，模式用括号锚定 [r]un_daily_backtest\.py
#   ——上一版的 "src/run_daily_backtest.py" 对 `cd src && python run_daily_backtest.py`
#   这种起法**失明**（argv 里没有 src/），已修，这里沿用修好的形式。
set -u
ROOT=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
T=$ROOT/etf/v1/temp
LOG=$T/queue_j2_1002.log
CANCEL=$T/queue_j2.CANCEL
PY=/usr/bin/python3.10
RESULTS=$ROOT/etf/v1/data/results
LIBDIR=$ROOT/etf/v1/data/library
J3LOG=$T/queue_3_j3_default.log
# 件3 的主进程；接力器「等它退场」就等这一个 pid（不靠模式猜，避免自匹配）
J3PID="${QUEUE_J3_PID:-5050}"
[ "${QUEUE_DRY:-0}" = "1" ] || echo $$ > "$T/queue_j2_1002.pid"

say() { echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

check_cancel() {
  if [ -f "$CANCEL" ]; then say "✋ 发现 $CANCEL ⇒ 接力器收工，不起件2"; exit 0; fi
}

# 闸门一：件3 还在不在
j3_busy() {
  if ps -p "$J3PID" > /dev/null 2>&1; then
    say "⏳ 件3（pid $J3PID）还在跑 ⇒ 接力器继续等（再 5 分钟）"; return 0
  fi
  say "✅ 件3（pid $J3PID）已退场"; return 1
}

# 闸门二/三：内存 + swap + 同入口并发。返回 0=放行
gates_ok() {
  local need_mb=$1 avail swapfree busy
  avail=$(( $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) ))
  swapfree=$(( $(awk '/SwapFree/{print int($2/1024)}' /proc/meminfo) ))
  busy=$(pgrep -f "[r]un_daily_backtest\.py" | tr '\n' ' ')
  check_cancel
  if [ "$avail" -lt "$need_mb" ]; then
    say "⏳ 闸门不放行：MemAvailable=${avail}MB < ${need_mb}MB（再等 5 分钟）"; return 1
  fi
  if [ "$swapfree" -lt 512 ]; then
    say "⏳ 闸门不放行：SwapFree=${swapfree}MB < 512MB（再等 5 分钟）"; return 1
  fi
  if [ -n "${busy// /}" ]; then
    say "⏳ 闸门不放行：有 run_daily_backtest 在跑 pid=${busy}（再等 5 分钟）"; return 1
  fi
  say "✅ 闸门放行：MemAvailable=${avail}MB SwapFree=${swapfree}MB 无同入口并发"
  return 0
}

wait_all() {  # 先等件3 退场，再等内存/并发闸门；最长 14 小时（件3 本身就可能是小时级）
  local need_mb=$1 waited=0
  while j3_busy; do sleep 300; waited=$((waited + 300))
    [ "$waited" -lt 50400 ] || { say "🛑 等件3 超 14 小时不放行 ⇒ 接力器认输（不硬起、不降标准）"; exit 3; }
  done
  while ! gates_ok "$need_mb"; do sleep 300; waited=$((waited + 300))
    [ "$waited" -lt 50400 ] || { say "🛑 等闸门超 14 小时不放行 ⇒ 接力器认输（不硬起、不降标准）"; exit 3; }
  done
}

snapshot() {  # $1 = 标签：把要被覆写的东西存一份，事后可 diff
  local tag=$1 dst=$T/snap_before_$1
  mkdir -p "$dst"
  cp -p "$RESULTS"/equity_daily.csv "$RESULTS"/equity.csv \
        "$RESULTS"/trades_daily.csv "$RESULTS"/trades.csv \
        "$RESULTS"/signals_daily.csv "$RESULTS"/signals.csv \
        "$RESULTS"/dsr_daily.csv "$RESULTS"/dsr.csv \
        "$RESULTS"/optimized_params_daily.json "$dst/" 2>&1 | tee -a "$LOG"
  cp -p "$LIBDIR"/factor_library_index.json "$LIBDIR"/factor_library.csv \
        "$LIBDIR"/factor_library.md "$dst/" 2>&1 | tee -a "$LOG"
  sha256sum "$dst"/* | tee -a "$LOG"
  cp -p "$ROOT/common/data/etf/cache/trial_counter.json" "$dst/" 2>&1 | tee -a "$LOG"
}

report() {  # $1 = 场次标签 $2 = 该场日志：只数「真覆写了没有」+ 闸拦了几条
  local tag=$1 lf=$2
  say "—— $tag 收尾读数 ——"
  [ -f "$lf" ] || { say "⚠️ 日志不在：$lf（这一场没有读数可报）"; return; }
  say "退出码/峰值：$(grep -E 'Elapsed \(wall|Maximum resident' "$lf" | tr '\n' ' ')"
  say "五张表 mtime：$(ls -l --time-style=+%m-%d\ %H:%M $RESULTS/{equity,trades,signals,dsr}.csv "$RESULTS"/optimized_params_daily.json 2>/dev/null | awk '{print $6,$7}' | tr '\n' ' ')"
  say "写库闸读数：$(grep -E '静态体检|因子库更新' "$lf" | tail -4 | tr '\n' ' ')"
  say "未来函数条目被提及：$(grep -c 'volatility_breakout_momentum' "$lf") 次"
}

say "接力器启动（先收件3 的读数 → 再起件2 多角色闸），pid=$(cat $T/queue_j2_1002.pid 2>/dev/null)"
say "盯的是 pid $J3PID（可用 QUEUE_J3_PID 覆盖）；件3 日志 $J3LOG"

if [ "${QUEUE_DRY:-0}" = "1" ]; then   # 只验闸门有没有牙，不起任何生产场
  # 当前现场应当**不放行**两次：件3 还在（5050 活着）；把阈值提到 999999 让内存腿必然不过
  say "DRY：只体检一次。第一腿应当报「件3 还在跑」"
  j3_busy; say "DRY j3_busy 返回 $?（0=件3 还在＝如实拒绝）"
  gates_ok "${QUEUE_NEED_MB:-999999}"; say "DRY gates_ok 返回 $?（1=不放行＝有牙）"
  gates_ok 1; say "DRY 反向对照：门槛放到 1MB 后 gates_ok 返回 $? ⇒ 期望它**仍然拒绝**，" \
      "但拒绝的原因必须是「有 run_daily_backtest 在跑」而不是内存（上一行已经按顺序过了内存腿）" \
      "＝内存腿不是恒假、并发腿真有牙"
  exit 0
fi

# ---- 尾巴一：件3 跑完后补打它的收尾读数（上一版脚本里这一段随外壳一起丢了） ----
while j3_busy; do sleep 300; done
report "件3 默认档（official 腿这晚真起来了，墙钟不再是 1h17m 那一档）" "$J3LOG"

# ---- 尾巴二：件2 多角色前置假设闸接进生产那一场 ----
# 只在这一个进程里翻开关，仓库默认档一行未改（config_base 仍是 HYPOTHESIS_ROLES=""）
wait_all "${QUEUE_NEED_MB2:-8192}"
tag=2_j2_roles_1002; lf=$T/queue_${tag}.log
snapshot "$tag"
say "▶ 件2 起场：ETF_HYPOTHESIS_ROLES=on + 同一入口（每循环 LLM 调用 1→3~4 次）"
( cd "$ROOT/etf/v1/src" && ETF_HYPOTHESIS_ROLES=on /usr/bin/time -v "$PY" run_daily_backtest.py ) > "$lf" 2>&1
say "件2 结束 rc=$?"
report "件2 多角色闸" "$lf"
say "接力器收工。两场日志：$J3LOG / $lf"
