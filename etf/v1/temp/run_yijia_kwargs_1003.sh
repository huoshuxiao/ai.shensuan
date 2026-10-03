#!/bin/bash
# 乙+：把「关思考 + 撑窗口」那条通道接上之后，official 支真跑那一场（10-03 23:0x 写）
#
# 大目标：个人量化辅助系统要在 16G 纯 CPU 的机器上每天跑因子挖掘。
# 现在卡在哪：官方支（RD-Agent）在这台机器上**一整场跑不完一轮**——qwen3.5:9b 带着思考把
#           单次调用撑到 1653.7s、正文 0 字符，10-03 那场 7200s 撞墙、回收的是上一场的旧档。
#           10-03 夜探针实测：思考**只能**走 litellm.completion 的顶层 kwargs 关得掉
#           （B1 臂 241.4/79.8/49.6s、正文 3/3 是合法 JSON），当天已把这条通道接进驱动（乙+），
#           闸门默认关 ⇒ **这一场就是那一格唯一的答案**：接上之后真跑一轮到底几分钟、出不出因子。
# 这一场只回答一个问题：**kwargs 开着，official 这一腿自己写不写得进 factors.json、写几条**。
#   对表基线＝同日 11:10→14:29 那场「乙」（9b、kwargs 关）：墙钟 3:19:03、超时 7200s 已终止、
#   `+4 (来源=official)`、而那份 factors.json 的 mtime 是 10:59（＝捞的旧档，本场 0 写）。
#   ⇒ 基线数不是「入库 4 条」，是「**本场写盘 0 次**」。这一场要读的是这一格翻没翻。
#
# ⚠️ 这不是严格 A/B：两场之间因子库版本不同、面板同一天但会被 ③ 前置重建 ⇒
#    只能读「official 这条腿有没有产出、产出几条、耗时多少」，不能读「换通道赚多少钱」。
#
# 覆写面（起场前逐条报给用户，此处同时快照到 snap_before_yijia_1003）：
#   data/results/{equity,trades,signals,dsr}_{daily.csv,固定名.csv}（5 张表 ×2 命名）
#   data/results/optimized_params_daily.json、equity_daily.png
#   data/results/{walk_forward*,pbo*,multi_*,shap_*,factor_decay*,clusters*,auto_remining_log*}
#   data/library/ 三文件（会触发 [factor-lib] 自动 git commit）
#   data/results/rdagent_output/factors.json（官方支自己的产物——本场判据就看它的 mtime/sha 翻不翻）
#   common/data/etf/cache/trial_counter.json（**只增不减、不可回滚** ⇒ 本场会继续累加 n_trials，
#   未来 DSR 门槛只会更严；这是这条链任何一次运行都有的副作用）
#
# 撤销办法（任一）：
#   touch etf/v1/temp/queue_yijia.CANCEL      ← 下一个检查点退出（起场后不再撤，撤要手工）
#   kill $(cat etf/v1/temp/queue_yijia_1003.pid)  ← 只杀起场器；已起的子进程要另杀
#
# 五道闸门（与 10-03 乙那场同源，另加两道本批专属）：①模型名必须带 litellm 的 provider 前缀
# ②**kwargs 必须是合法 JSON 且带 think 与 num_ctx 两把键**（乙+ 的开关本身就是这一场的变量，
#   写错等于跑了一场"关着"的，三小时白烧）③MemAvailable ≥ 门槛 ④SwapFree ≥ 512MB
# ⑤同入口无并发（run_daily_backtest / rdagent_driver / run_etf_daily_chain 三条模式，
#   模式用括号锚定，避免起场器自己的命令行把闸门锁死）⑥ollama 服务 reachable（不 reach 就别起，
#   否则官方支会把 10 连败当成"模型不行"）。
set -u
ROOT=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
T=$ROOT/etf/v1/temp
LOG=$T/queue_yijia_1003.log
CANCEL=$T/queue_yijia.CANCEL
PY=/usr/bin/python3.10
RESULTS=$ROOT/etf/v1/data/results
LIBDIR=$ROOT/etf/v1/data/library
RDOUT=$RESULTS/rdagent_output/factors.json
LIBIDX=$LIBDIR/factor_library_index.json
TC=$ROOT/common/data/etf/cache/trial_counter.json
SNAP=$T/snap_before_yijia_1003
RUNLOG=$T/queue_1_yijia_kwargs_1003.log      # 这一场的 stdout（/usr/bin/time -v 也写这里）
PSSLOG=$T/ollama_ps_yijia_1003.log           # 常驻模型采样
NEED_MB="${QUEUE_NEED_MB:-8192}"             # 同族最坏档：9b@16384 驻留 6.15GB + 本场树 0.6GB + 余量
MODEL="${QUEUE_MODEL:-ollama_chat/qwen3.5:9b}"
KWARGS="${QUEUE_KWARGS:-{\"think\": false, \"num_ctx\": 16384}}"

[ "${QUEUE_DRY:-0}" = "1" ] || echo $$ > "$T/queue_yijia_1003.pid"

say() { echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# 一条 json 的条数。取不到必须原样吐「取不到」，不许折成 0 或空串（空对照会被读成「没长」）。
n_of() {
  $PY -c "import json,sys
try:
    d = json.load(open(sys.argv[1]))
    print(len(d))
except Exception as e:
    print('取不到(%s)' % type(e).__name__)" "$1"
}

check_cancel() {
  if [ -f "$CANCEL" ]; then say "✋ 发现 $CANCEL ⇒ 起场器收工，不起这一场"; exit 0; fi
}

gates_ok() {
  local avail swapfree busy
  avail=$(( $(awk '/^MemAvailable/{print int($2/1024)}' /proc/meminfo) ))
  swapfree=$(( $(awk '/^SwapFree/{print int($2/1024)}' /proc/meminfo) ))
  busy=$(pgrep -f "[r]un_daily_backtest\.py|[r]dagent_driver\.py|[r]un_etf_daily_chain\.py" | tr '\n' ' ')
  check_cancel
  case "$MODEL" in
    */*) ;;
    *) say "🛑 注入值缺 litellm 的 provider 前缀：MODEL=$MODEL ⇒ 不起场（10-03 10:58 那场就是这么烧的）"
       return 1 ;;
  esac
  # 乙+ 专属那道：开关本身写错 ⇒ 这一场等于跑了一场"闸门关着"的，必须当场拒绝
  if ! $PY -c "
import json,sys
d = json.loads(sys.argv[1])
assert isinstance(d, dict), '不是 JSON 对象'
assert d.get('think') is False, 'think 必须是 false（这一场的目的就是关掉思考）'
assert int(d.get('num_ctx', 0)) >= 16384, 'num_ctx 至少要 16384（真提示词 5502 token）'
" "$KWARGS" 2>>"$LOG"; then
    say "🛑 kwargs 不合格：KWARGS=$KWARGS ⇒ 不起场（要的是 think=false 且 num_ctx≥16384 的扁平 JSON）"
    return 1
  fi
  if [ "$avail" -lt "$1" ]; then
    say "⏳ 闸门不放行：MemAvailable=${avail}MB < ${1}MB（再 5 分钟）"; return 1
  fi
  if [ "$swapfree" -lt 512 ]; then
    say "⏳ 闸门不放行：SwapFree=${swapfree}MB < 512MB（再 5 分钟）"; return 1
  fi
  if [ -n "${busy// /}" ]; then
    say "⏳ 闸门不放行：同入口/同写盘目标在跑 pid=${busy}（再 5 分钟）"; return 1
  fi
  if ! timeout 15 ollama list >/dev/null 2>&1; then
    say "⏳ 闸门不放行：ollama 服务 15s 内没答话 ⇒ 不起（官方支会把连败当成模型不行）"; return 1
  fi
  say "✅ 闸门放行：MemAvailable=${avail}MB SwapFree=${swapfree}MB 无同入口并发 ollama 可达"
  return 0
}

snapshot() {
  mkdir -p "$SNAP"
  # 这份 factors.json 是本场判据的对照物：拷不到就别起（起了这一格也无对照）
  if ! cp -p "$RDOUT" "$SNAP/" 2>>"$LOG"; then
    say "🛑 拷不到对照物 $RDOUT ⇒ 不起场（本场判据是它的 sha 翻不翻，没有对照等于没这一格）"
    return 1
  fi
  cp -p "$RESULTS"/equity_daily.csv "$RESULTS"/equity.csv \
        "$RESULTS"/trades_daily.csv "$RESULTS"/trades.csv \
        "$RESULTS"/signals_daily.csv "$RESULTS"/signals.csv \
        "$RESULTS"/dsr_daily.csv "$RESULTS"/dsr.csv \
        "$RESULTS"/optimized_params_daily.json "$SNAP/" 2>&1 | tee -a "$LOG"
  cp -p "$LIBIDX" "$LIBDIR"/factor_library.csv \
        "$LIBDIR"/factor_library.md "$SNAP/" 2>&1 | tee -a "$LOG"
  cp -p "$TC" "$SNAP/"
  # B6（拒绝回收上一场旧档）用户**未选** ⇒ 判据只看这一枚 sha 翻不翻，不认「文件存在」
  say "快照 sha256：$(sha256sum "$SNAP"/* | awk '{print substr($1,1,8), $2}' | tr '\n' ' ')"
  say "起场前读数：factors.json mtime=$(stat -c %y "$RDOUT" | cut -d. -f1) 条数=$(n_of "$RDOUT")；库=$(n_of "$LIBIDX") 条；trial_counter=$(cat "$TC")"
  return 0
}

report() {
  say "—— 乙+（kwargs 开）收尾读数 ——"
  [ -f "$RUNLOG" ] || { say "⚠️ 日志不在：$RUNLOG ⇒ 这一场没有读数可报"; return; }
  say "退出码/墙钟/峰值：$(grep -E 'Elapsed \(wall|Maximum resident' "$RUNLOG" | tr -d '\t' | tr '\n' ' ')"
  # 这一场最要紧的三行：补丁到底生效没有（驱动自己念的）
  say "kwargs 补丁生效行：$(grep -o 'LLM kwargs 补丁已生效[^ ]*.\{0,90\}' "$RUNLOG" | head -2 | tr '\n' ' ')"
  say "未设置那行（出现＝闸门其实关着）：$(grep -c '未设置 ⇒ LLM 通道一字未改' "$RUNLOG")"
  say "早绑定核验抛错（出现就别信这一场）：$(grep -c '不要跑这一场' "$RUNLOG")"
  say "官方支真用了哪个模型：$(grep -o "chat_model='[^']*'" "$RUNLOG" | sort -u | tr '\n' ' ')"
  say "official 回收/入库：$(grep -E '来源=[^)]*official|尝试回收既有产物|RD-Agent\(Q\) 本次未运行|前置检查' "$RUNLOG" | tail -6 | tr '\n' ' ')"
  say "来源=official 的入库次数合计：$(grep -oE '\+[0-9]+ \(来源=[^)]*official[^)]*\)' "$RUNLOG" | tr '\n' ' ')"
  say "五张表 mtime：$(ls -l --time-style=+%m-%d\ %H:%M $RESULTS/{equity,trades,signals,dsr}.csv "$RESULTS"/optimized_params_daily.json 2>/dev/null | awk '{print $6,$7}' | tr '\n' ' ')"
  say "库净增：$(grep -E '因子库更新' "$RUNLOG" | tail -3 | tr '\n' ' ')"
  say "trial_counter 现值：$(cat "$TC")"
  # 这一行才是本场判据：factors.json 自己写没写（B6 未裁 ⇒「存在」不等于「本场写的」）
  say "factors.json 起场后：mtime=$(stat -c %y "$RDOUT" 2>/dev/null | cut -d. -f1) sha=$(sha256sum "$RDOUT" 2>/dev/null | cut -c1-12) 条数=$(n_of "$RDOUT")"
  if [ ! -f "$SNAP/factors.json" ]; then
    say "与起场前对表：⚠️ 对照物不在（$SNAP/factors.json）⇒ 这一格**无对照**，不许折成「本场真写过」"
  else
    say "与起场前那份是否逐字节相同：$(cmp -s "$RDOUT" "$SNAP/factors.json" && echo '相同 ⇒ 本场官方支 0 写（捞的是旧档）' || echo '不同 ⇒ 本场真写过')；对照那份 sha=$(sha256sum "$SNAP/factors.json" | cut -c1-12) 条数=$(n_of "$SNAP/factors.json")"
  fi
  if [ -f "$PSSLOG" ]; then
    say "常驻过的模型（ollama ps 采样去重）：$(awk '/^[a-z0-9._:-]+ /{print $1}' "$PSSLOG" | sort -u | tr '\n' ' ')"
    say "最大一次常驻体积：$(grep -oE '[0-9.]+[GM]i?B' "$PSSLOG" | sort -u | tr '\n' ' ')"
  else
    say "⚠️ 没有 ollama ps 采样 ⇒ 常驻几个模型这一格无读数"
  fi
}

say "起场器启动（乙+＝kwargs 通道开着真跑一场），pid=$(cat $T/queue_yijia_1003.pid 2>/dev/null)"
say "本场注入：MODEL=$MODEL KWARGS=$KWARGS（只进这一个进程，两份 .env 与仓库默认值一行未改）"

if [ "${QUEUE_DRY:-0}" = "1" ]; then   # 只验闸门有没有牙，不起任何生产场
  _BAK_MODEL=$MODEL; _BAK_KWARGS=$KWARGS
  say "DRY 前缀腿＝裸名 qwen3.5:9b ⇒ 期望**拒绝**"
  MODEL="qwen3.5:9b"; gates_ok 1; say "DRY 前缀腿 返回 $?（1＝真有牙）"; MODEL=$_BAK_MODEL
  say "DRY kwargs 腿＝think 没关（{\"think\": true}）⇒ 期望**拒绝**"
  KWARGS='{"think": true, "num_ctx": 16384}'; gates_ok 1
  say "DRY kwargs 腿(关不掉思考) 返回 $?（1＝真有牙）"
  say "DRY kwargs 腿＝坏 JSON（少个引号）⇒ 期望**拒绝**"
  KWARGS='{think: false}'; gates_ok 1; say "DRY kwargs 腿(坏 JSON) 返回 $?（1＝真有牙）"; KWARGS=$_BAK_KWARGS
  say "DRY：门槛提到 999999MB ⇒ 期望**如实拒绝**"
  gates_ok 999999; say "DRY gates_ok(999999) 返回 $?（1＝内存腿真有牙）"
  say "DRY：门槛降到 1MB ⇒ 期望放行（除非真有同入口并发）"
  gates_ok 1; say "DRY gates_ok(1) 返回 $?（0＝内存腿不是恒假）"
  exit 0
fi

while ! gates_ok "$NEED_MB"; do sleep 300; done

snapshot || { say "🛑 快照未成 ⇒ 不起场"; exit 1; }
say "▶ 起场：official 支带 kwargs 真跑（7200s 那堵墙按用户裁决**没拨大**＝B1 未选）"
( while true; do
    echo "===== $(date '+%m-%d %H:%M:%S') =====" >> "$PSSLOG"
    timeout 20 ollama ps >> "$PSSLOG" 2>&1
    sleep 60
  done ) &
PSWATCH=$!
say "采样器 pid=$PSWATCH → $PSSLOG"

( cd "$ROOT/etf/v1/src" \
  && ETF_RDAGENT_LLM_MODEL="$MODEL" ETF_RDAGENT_LLM_KWARGS="$KWARGS" \
     /usr/bin/time -v "$PY" run_daily_backtest.py ) > "$RUNLOG" 2>&1
rc=$?
kill "$PSWATCH" 2>/dev/null     # 只杀自己起的采样器
say "本场结束 rc=$rc"
report
say "起场器收工。日志：$RUNLOG；采样：$PSSLOG"
