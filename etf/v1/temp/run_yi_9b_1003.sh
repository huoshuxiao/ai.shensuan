#!/bin/bash
# 乙：把 RD-Agent official 支换成 qwen3.5:9b 那一场（10-03 11:0x 写）
#
# 大目标：个人量化辅助系统要在 16G 纯 CPU 的机器上每天跑因子挖掘。
# 现在卡在哪：一次跑批会**同时常驻两个本地大模型**（主线 9b + 官方支 7b ≈ 11.6GB），
#           内存没有余量。统一入口（甲）10-03 已落地，这一场量的是**要不要合并选型**。
# 这一场只回答一个问题：**官方支换 9b 后回收/入库几条**。
#   对表基线 = 10-02 件3 那场（7b）：日志 `queue_3_j3_default.log` 第 156 行
#   `chat_model='ollama_chat/qwen2.5:7b'`、第 13971 行 `⚠️ 超时 7200s 已终止；尝试回收`、
#   第 13978 行 `📚 因子库更新: +2 (来源=official)`。
#   ⇒ 基线数 =「超时 7200s 后回收，入库 2 条」。换 9b 后同一格是多少，就是这一场的产出。
# 副读数（不是判据）：`ollama ps` 每 60s 采一次，数这一场真起过几个聊天模型。
#
# ⚠️ 这不是严格 A/B：两场之间因子库版本不同、面板不同天 ⇒ 只能读「官方支这条腿有没有
#    产出、产出几条、耗时多少」，不能读「换了模型赚多少钱」。
#
# 覆写面（起场前逐条报给用户，此处同时快照到 snap_before_*）：
#   data/results/{equity,trades,signals,dsr}_{daily.csv,固定名.csv}（5 张表 ×2 命名）
#   data/results/optimized_params_daily.json、equity_daily.png
#   data/results/{walk_forward*,pbo*,multi_*,shap_*,factor_decay*,clusters*,auto_remining_log*}
#   data/library/ 三文件（会触发 [factor-lib] 自动 git commit）
#   common/data/etf/cache/trial_counter.json（**只增不减、不可回滚** ⇒ 本场会把 n_trials
#   继续累加，未来 DSR 门槛只会更严；这是这条链任何一次运行都有的副作用）
#
# 撤销办法（任一）：
#   touch etf/v1/temp/queue_yi.CANCEL        ← 下一个检查点退出（起场后不再撤，撤要手工）
#   kill $(cat etf/v1/temp/queue_yi_1003.pid) ← 只杀起场器；已起的子进程要另杀
#
# 三条闸门（与 10-02 接力器同源）：①MemAvailable ≥ 门槛（按同族最坏档实测峰值 + 余量）
# ②SwapFree ≥ 512MB ③同入口无并发（模式括号锚定，避免自匹配把闸门锁死）。
set -u
ROOT=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
T=$ROOT/etf/v1/temp
LOG=$T/queue_yi_1003.log
CANCEL=$T/queue_yi.CANCEL
PY=/usr/bin/python3.10
RESULTS=$ROOT/etf/v1/data/results
LIBDIR=$ROOT/etf/v1/data/library
RUNLOG=$T/queue_1_yi_9b_1003.log          # 这一场的 stdout（/usr/bin/time -v 也写这里）
PSSLOG=$T/ollama_ps_yi_1003.log           # 常驻模型采样
NEED_MB="${QUEUE_NEED_MB:-8192}"
# litellm 的模型名必须带 provider 前缀（工作区 .env 写的是 ollama_chat/qwen2.5:7b）。
# 10-03 10:58 第一次起场时我填了裸名 `qwen3.5:9b` ⇒ 官方支第一次调用就
# `litellm.BadRequestError: LLM Provider NOT provided`、10 连败后退出（日志留在
# queue_1_yi_9b_1003.log.aborted1，trial_counter 已被那场 +4：371→375）。这道前缀闸
# 就是那场买的教训——缺前缀就别起，别再烧一次。
MODEL="${QUEUE_MODEL:-ollama_chat/qwen3.5:9b}"

[ "${QUEUE_DRY:-0}" = "1" ] || echo $$ > "$T/queue_yi_1003.pid"

say() { echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

check_cancel() {
  if [ -f "$CANCEL" ]; then say "✋ 发现 $CANCEL ⇒ 起场器收工，不起乙"; exit 0; fi
}

gates_ok() {
  local avail swapfree busy
  avail=$(( $(awk '/^MemAvailable/{print int($2/1024)}' /proc/meminfo) ))
  swapfree=$(( $(awk '/^SwapFree/{print int($2/1024)}' /proc/meminfo) ))
  busy=$(pgrep -f "[r]un_daily_backtest\.py" | tr '\n' ' ')
  check_cancel
  case "$MODEL" in
    */*) ;;
    *) say "🛑 注入值缺 litellm 的 provider 前缀：MODEL=$MODEL ⇒ 不起场（10-03 10:58 那场就是这么烧的）"
       return 1 ;;
  esac
  if [ "$avail" -lt "$1" ]; then
    say "⏳ 闸门不放行：MemAvailable=${avail}MB < ${1}MB（再 5 分钟）"; return 1
  fi
  if [ "$swapfree" -lt 512 ]; then
    say "⏳ 闸门不放行：SwapFree=${swapfree}MB < 512MB（再 5 分钟）"; return 1
  fi
  if [ -n "${busy// /}" ]; then
    say "⏳ 闸门不放行：有 run_daily_backtest 在跑 pid=${busy}（再 5 分钟）"; return 1
  fi
  say "✅ 闸门放行：MemAvailable=${avail}MB SwapFree=${swapfree}MB 无同入口并发"
  return 0
}

snapshot() {
  local dst=$T/snap_before_1_yi_9b_1003
  mkdir -p "$dst"
  cp -p "$RESULTS"/equity_daily.csv "$RESULTS"/equity.csv \
        "$RESULTS"/trades_daily.csv "$RESULTS"/trades.csv \
        "$RESULTS"/signals_daily.csv "$RESULTS"/signals.csv \
        "$RESULTS"/dsr_daily.csv "$RESULTS"/dsr.csv \
        "$RESULTS"/optimized_params_daily.json "$dst/" 2>&1 | tee -a "$LOG"
  cp -p "$LIBDIR"/factor_library_index.json "$LIBDIR"/factor_library.csv \
        "$LIBDIR"/factor_library.md "$dst/" 2>&1 | tee -a "$LOG"
  cp -p "$ROOT/common/data/etf/cache/trial_counter.json" "$dst/"
  say "快照 sha256：$(sha256sum "$dst"/* | awk '{print substr($1,1,8), $2}' | tr '\n' ' ')"
}

report() {
  say "—— 乙（official=9b）收尾读数 ——"
  [ -f "$RUNLOG" ] || { say "⚠️ 日志不在：$RUNLOG ⇒ 这一场没有读数可报"; return; }
  say "退出码/墙钟/峰值：$(grep -E 'Elapsed \(wall|Maximum resident' "$RUNLOG" | tr -d '\t' | tr '\n' ' ')"
  # 只认这一场新写的行：先切出「本场起点之后」再引用（历史日志里 7b 那行永远在）
  say "官方支真用了哪个模型：$(grep -o "chat_model='[^']*'" "$RUNLOG" | sort -u | tr '\n' ' ')"
  say "official 回收/入库：$(grep -E '来源=[^)]*official|尝试回收既有产物|RD-Agent\(Q\) 本次未运行|前置检查' "$RUNLOG" | tail -6 | tr '\n' ' ')"
  say "来源=official 的入库次数合计：$(grep -oE '\+[0-9]+ \(来源=[^)]*official[^)]*\)' "$RUNLOG" | tr '\n' ' ')"
  say "五张表 mtime：$(ls -l --time-style=+%m-%d\ %H:%M $RESULTS/{equity,trades,signals,dsr}.csv "$RESULTS"/optimized_params_daily.json 2>/dev/null | awk '{print $6,$7}' | tr '\n' ' ')"
  say "库净增：$(grep -E '因子库更新' "$RUNLOG" | tail -3 | tr '\n' ' ')"
  say "trial_counter 现值：$(cat "$ROOT/common/data/etf/cache/trial_counter.json")"
  if [ -f "$PSSLOG" ]; then
    say "常驻过的模型（ollama ps 采样去重）：$(awk '/^[a-z0-9._:-]+ /{print $1}' "$PSSLOG" | sort -u | tr '\n' ' ')"
  else
    say "⚠️ 没有 ollama ps 采样 ⇒ 常驻几个模型这一格无读数"
  fi
}

say "起场器启动（乙＝official 支换 9b），pid=$(cat $T/queue_yi_1003.pid 2>/dev/null)"

if [ "${QUEUE_DRY:-0}" = "1" ]; then   # 只验闸门有没有牙，不起任何生产场
  say "DRY：前缀腿＝裸名 qwen3.5:9b ⇒ 期望**拒绝**"
  _BAK_MODEL=$MODEL; MODEL="qwen3.5:9b"
  gates_ok 1; say "DRY 前缀腿 返回 $?（1=真有牙，裸名不放行）"
  MODEL=$_BAK_MODEL
  say "DRY：门槛提到 999999MB ⇒ 期望**如实拒绝**"
  gates_ok 999999; say "DRY gates_ok(999999) 返回 $?（1=内存腿真有牙）"
  say "DRY：门槛降到 1MB ⇒ 期望放行（除非真有同入口并发）"
  gates_ok 1; say "DRY gates_ok(1) 返回 $?（0=内存腿不是恒假）"
  exit 0
fi

while ! gates_ok "$NEED_MB"; do sleep 300; done

snapshot
say "▶ 乙 起场：ETF_RDAGENT_LLM_MODEL=$MODEL（只注入这一个进程，仓库默认值与两份 .env 一行未改）"
# 采样常驻模型：只读 ollama ps，不加载/不卸载、不动任何 keep_alive 设置
( while true; do
    echo "===== $(date '+%m-%d %H:%M:%S') =====" >> "$PSSLOG"
    timeout 20 ollama ps >> "$PSSLOG" 2>&1
    sleep 60
  done ) &
PSWATCH=$!
say "采样器 pid=$PSWATCH → $PSSLOG"

( cd "$ROOT/etf/v1/src" && ETF_RDAGENT_LLM_MODEL="$MODEL" /usr/bin/time -v "$PY" run_daily_backtest.py ) > "$RUNLOG" 2>&1
rc=$?
kill "$PSWATCH" 2>/dev/null     # 只杀自己起的采样器
say "乙 结束 rc=$rc"
report
say "起场器收工。日志：$RUNLOG；采样：$PSSLOG"
