#!/bin/bash
# 件3 → 件2 排队器（10-01 19:5x 写，用户裁「都做」）
#
# 为什么排队不立刻起：此刻另一会话的 `run_daily_backtest.py`(pid 62442) 与
# `rdagent_driver.py`(pid 62873) 正在写 `etf/v1/data/results/`，且 qwen3.5:9b 的
# llama-server 常驻 5.4GB —— AGENT.md 明文「16G 单机，禁止并发第二个本地 LLM 实例」，
# 覆写面又正好是我要写的那五张生产表 ⇒ 只能等，不 kill 别人、不动别人一个字节。
#
# 撤销办法（任一）：
#   touch etf/v1/temp/queue_runs_1001.CANCEL     ← 下一次检查点退出，不再起下一场
#   kill $(cat etf/v1/temp/queue_runs_1001.pid)  ← 只杀这个排队器，不动它已经起的生产场
set -u
ROOT=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
T=$ROOT/etf/v1/temp
LOG=$T/queue_runs_1001.log
CANCEL=$T/queue_runs_1001.CANCEL
PY=/usr/bin/python3.10
RESULTS=$ROOT/etf/v1/data/results
LIBDIR=$ROOT/etf/v1/data/library
[ "${QUEUE_DRY:-0}" = "1" ] || echo $$ > "$T/queue_runs_1001.pid"

say() { echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# 起场闸门：内存 + 别人在不在写同一个目录。两个都不合格就不起，只念原因。
# 阈值出处=同族最坏档实测峰值 6.52GiB（10-01 环3 归档刷新那场，见项目记忆
# 「没量过峰值的档按同族最坏档算」），件2 再加 1GiB 余量（每循环 LLM 调用 1→3~4 次）。
gates_ok() {
  local need_mb=$1 avail swapfree busy
  avail=$(( $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) ))
  swapfree=$(( $(awk '/SwapFree/{print int($2/1024)}' /proc/meminfo) ))
  # ⚠️ 10-02 修的一个失明：原来这条模式写的是 "src/run_daily_backtest.py"，只能抓到
  # **用绝对路径起**的那类（62442 就是这样被抓到的）；而本脚本自己用 `cd src && python
  # run_daily_backtest.py` 起的子进程，argv 里根本没有 `src/` ⇒ 对同入口的自己人失明。
  # 改成括号模式（`[r]`）：既抓到两种起法，又不会因为本命令行的字符串自匹配。
  busy=$(pgrep -f "[r]un_daily_backtest\.py" | tr '\n' ' ')
  if [ ! -f "$CANCEL" ]; then :; else say "✋ 发现 $CANCEL ⇒ 收工"; exit 0; fi
  if [ "$avail" -lt "$need_mb" ]; then
    say "⏳ 闸门不放行：MemAvailable=${avail}MB < ${need_mb}MB（再等 5 分钟）"; return 1
  fi
  if [ "$swapfree" -lt 512 ]; then
    say "⏳ 闸门不放行：SwapFree=${swapfree}MB < 512MB（再等 5 分钟）"; return 1
  fi
  if [ -n "${busy// /}" ]; then
    say "⏳ 闸门不放行：已有 run_daily_backtest 在跑 pid=${busy}（再等 5 分钟）"; return 1
  fi
  say "✅ 闸门放行：MemAvailable=${avail}MB SwapFree=${swapfree}MB 无同入口并发"
  return 0
}

wait_gate() {  # 一直等到放行，或被取消；最长等 12 小时 then 认输（别把闸门守成永久挂起）
  local need_mb=$1 waited=0
  while ! gates_ok "$need_mb"; do
    sleep 300; waited=$((waited + 300))
    if [ "$waited" -ge 43200 ]; then
      say "🛑 等了 12 小时仍不放行 ⇒ 本排队器认输退出（不硬起、不降标准）"
      exit 3
    fi
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
  # 试验数账本也在这场上会被累加，留个读数
  cp -p "$ROOT/common/data/etf/cache/trial_counter.json" "$dst/" 2>&1 | tee -a "$LOG"
}

report() {    # $1 = 标签 $2 = 日志文件：报「有没有真覆写」+「闸拦了几条」
  local tag=$1 lf=$2
  say "—— $tag 收尾读数 ——"
  say "退出码/峰值：$(grep -E 'Elapsed|Maximum resident' "$lf" | tr '\n' ' ')"
  say "五张表 mtime：$(ls -l --time-style=+%m-%d\ %H:%M $RESULTS/{equity,trades,signals,dsr}.csv "$RESULTS"/optimized_params_daily.json 2>/dev/null | awk '{print $6,$7}' | tr '\n' ' ')"
  say "写库闸读数：$(grep -E '静态体检|因子库更新' "$lf" | tail -4 | tr '\n' ' ')"
  say "未来函数再挖：$(grep -c 'volatility_breakout_momentum' "$lf") 次提及"
}

say "排队器启动（件3 归档回测 → 件2 多角色闸），pid=$(cat $T/queue_runs_1001.pid)"

if [ "${QUEUE_DRY:-0}" = "1" ]; then   # 自检模式：只验闸门有没有牙，不起任何生产场
  say "DRY：只体检闸门一次，然后退出（当前现场应当**不放行**，因为 62442 还在写同一个目录）"
  # 把阈值放到 100MB 是让内存那一腿必然过关，专门验「别人在写同一个目录」那一腿有没有牙
  gates_ok "${QUEUE_NEED_MB:-7168}"; say "DRY 结束，gates_ok 返回 $?"
  exit 0
fi

# ============ 件3：默认档那一场（归档回测 main.py） ============
# 门槛可由 env 覆盖（QUEUE_NEED_MB / 件2 那条 QUEUE_NEED_MB2）。
# 10-02 20:4x 实际起场时**没有降门槛**：等场的这一晚另一会话的 62442/62873 与
# llama-server 全部退场，实测 MemAvailable=12481MB / SwapFree=4095MB ⇒ 原门槛
# （同族最坏档实测峰值 6.52GiB + 余量）自然过关，用不着放宽。
wait_gate "${QUEUE_NEED_MB:-7168}"
tag=3_j3_default; lf=$T/queue_${tag}.log
snapshot "$tag"
say "▶ 件3 起场：$PY etf/v1/src/run_daily_backtest.py（默认档，闸全按仓库默认）"
( cd "$ROOT/etf/v1/src" && /usr/bin/time -v "$PY" run_daily_backtest.py ) > "$lf" 2>&1
say "件3 结束 rc=$?"
report "$tag" "$lf"

# ============ 件2：多角色前置假设闸接进生产那一场 ============
# 只在这一个进程里翻开关，仓库默认档一行未改（config_base 仍是 HYPOTHESIS_ROLES=""）
wait_gate "${QUEUE_NEED_MB2:-8192}"
tag=2_j2_roles; lf=$T/queue_${tag}.log
snapshot "$tag"
say "▶ 件2 起场：ETF_HYPOTHESIS_ROLES=on + 同一入口（每循环 LLM 调用 1→3~4 次）"
( cd "$ROOT/etf/v1/src" && ETF_HYPOTHESIS_ROLES=on /usr/bin/time -v "$PY" run_daily_backtest.py ) > "$lf" 2>&1
say "件2 结束 rc=$?"
report "$tag" "$lf"
say "两场均已跑完，排队器收工。日志：$T/queue_2_j2_roles.log / $T/queue_3_j3_default.log"
