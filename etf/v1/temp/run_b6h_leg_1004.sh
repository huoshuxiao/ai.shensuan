#!/bin/bash
# 丙＋B 之后的复验场（10-04 16:3x 写）：kwargs 全开 + 墙钟抬到 21600s，只跑官方支那一腿
#
# 与 12:3x 那场的**唯一差别**＝本线 .env 多了 ETF_RDAGENT_TIMEOUT=21600（6h），加上 recipe 里乙那两条
# 索引硬规则、回收口上丁那两行净增读数。内层复用 run_kwargs_leg_1004.py（它自己会念出
# 「超时上限 = …s」，那一行就是丙接到子进程上的现场证据）。
# ⚠️ 日志/快照/台账全换新文件名，**绝不覆盖上一场的 field_kwargs_1004.log**（那还是本会话
#   引用过的证据底本）。
#
# 大目标：辅助系统每天要靠 RD-Agent 官方支产出新因子；这条腿 10-03 撞过 7200s 墙、
#   自报的回收条数后来被证成假绿。10-04 用户裁「乙」之后，窗口那把旋钮只剩这一腿在吃，
#   ⇒ 现在唯一没读数的一格就是「接进去真跑一轮到底出不出因子」。
# 内层是 etf/v1/temp/run_kwargs_leg_1004.py（直接调生产函数 try_official_rdagent），
#   不跑 run_daily_backtest.py ⇒ **五张归档表/因子库/trial_counter 一概不碰、不产生 commit**。
#
# 覆写面（起场前逐条报给用户）：
#   etf/v1/data/results/rdagent_output/  —— factors.json（先快照）、log/、token_cost/、
#   prompt_cache.db、以及 drivers 自己的 rdagent 工作目录
#   本目录三个文件：field_kwargs_1004.log（stdout + time -v）、ollama_ps_kwargs_1004.log、
#   queue_kwargs_1004.log（起场器自己的台账）
#   ⇒ 不写 common/data/etf/cache/trial_counter.json（10-04 现值见收尾读数，期望不动）
#
# 撤销办法（任一）：
#   touch etf/v1/temp/queue_b6h.CANCEL      ← 下一个检查点退出，不起
#   kill $(cat etf/v1/temp/queue_b6h_1004.pid)  ← 只杀起场器；已起的驱动在
#   official_rdagent 那一层自成进程组，超时会自己 killpg 收尸（10-03 夜修的正是这个）
#
# 三条闸门：①MemAvailable ≥ 门槛（9b 驻留 ≈6.1GB、撑 16384 再 +0.5GB，按同族最坏档 + 余量）
# ②SwapFree ≥ 512MB ③无并发（驱动/日线回测/本场内层，模式用括号锚定防止把自己匹配进去）
set -u
ROOT=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
T=$ROOT/etf/v1/temp
LOG=$T/queue_b6h_1004.log
RUNLOG=$T/field_b6h_1004.log
PSSLOG=$T/ollama_ps_b6h_1004.log
CANCEL=$T/queue_b6h.CANCEL
PY=/usr/bin/python3.10
RESULTS=$ROOT/etf/v1/data/results
OUT=$RESULTS/rdagent_output
NEED_MB="${QUEUE_NEED_MB:-8192}"
SNAP=$T/snap_before_b6h_1004

[ "${QUEUE_DRY:-0}" = "1" ] || echo $$ > "$T/queue_b6h_1004.pid"
say() { echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

check_cancel() {
  if [ -f "$CANCEL" ]; then say "✋ 发现 $CANCEL ⇒ 收工，不起场"; exit 0; fi
}

gates_ok() {
  local avail swapfree busy
  avail=$(( $(awk '/^MemAvailable/{print int($2/1024)}' /proc/meminfo) ))
  swapfree=$(( $(awk '/^SwapFree/{print int($2/1024)}' /proc/meminfo) ))
  busy=$(pgrep -f "[r]dagent_driver\.py|[r]un_daily_backtest\.py|[r]un_kwargs_leg_1004\.py" | tr '\n' ' ')
  check_cancel
  if [ "$avail" -lt "$1" ]; then
    say "⏳ 闸门不放行：MemAvailable=${avail}MB < ${1}MB（再 5 分钟）"; return 1
  fi
  if [ "$swapfree" -lt 512 ]; then
    say "⏳ 闸门不放行：SwapFree=${swapfree}MB < 512MB（再 5 分钟）"; return 1
  fi
  if [ -n "${busy// /}" ]; then
    say "⏳ 闸门不放行：已有在跑的同类进程 pid=${busy}（再 5 分钟）"; return 1
  fi
  say "✅ 闸门放行：MemAvailable=${avail}MB SwapFree=${swapfree}MB 无并发"
  return 0
}

report() {
  say "—— #172（kwargs 全开・只跑官方支）收尾读数 ——"
  [ -f "$RUNLOG" ] || { say "⚠️ 日志不在：$RUNLOG ⇒ 这一场没有读数可报"; return; }
  # 先看数，再剥 ANSI 色码（10-03 的教训：不剥码正则恒零命中）
  local plain; plain=$(mktemp)
  sed -e 's/\x1b\[[0-9;]*m//g' "$RUNLOG" > "$plain"
  say "退出码/墙钟/峰值：$(grep -E 'Elapsed \(wall|Maximum resident' "$plain" | tr -d '\t' | tr '\n' ' ')"
  say "G1 kwargs 实际值：$(grep -o "RDAGENT_LLM_KWARGS = .*" "$plain" | head -1)"
  say "补丁生效行（驱动自己念的）：$(grep -o 'LLM kwargs 补丁已生效: .*' "$plain" | head -1)"
  say "循环启动次数：$(grep -c 'rdagent factor 循环启动' "$plain")"
  say "正文为空次数：$(grep -c '正文为空\|内容为空\|Empty' "$plain")"
  say "Token count 行数：$(grep -c 'Token count' "$plain")｜最大值：$(grep -o 'Token count: *[0-9]*' "$plain" | awk '{print $3}' | sort -n | tail -1)"
  say "超时/收尸：$(grep -E '超时 [0-9]+s 已终止|收尸读数|残留容器' "$plain" | tr '\n' ' ')"
  say "内层结论：$(grep -E '^\[1004\] (回收返回|落盘净增|对账|自报有产出)' "$plain" | tr '\n' ' ')"
  say "官方支真用了哪个模型：$(grep -o "chat_model='[^']*'" "$plain" | sort -u | tr '\n' ' ')"
  say "主线侧铃铛响过没有：$(grep -c '超窗铃铛' "$plain") 次（期望 0＝本场只跑官方支，不经主线客户端）"
  say "factors.json 与起场前 cmp：$(cmp -s "$SNAP/factors.json" "$OUT/factors.json" && echo '逐字节相同 ⇒ 本场写入 0 条' || echo '不同 ⇒ 本场改写过')"
  say "trial_counter 现值：$(cat "$ROOT/common/data/etf/cache/trial_counter.json")（期望与起场前同）"
  if [ -f "$PSSLOG" ]; then
    say "常驻过的模型（ollama ps 采样去重）：$(awk '/^[a-z0-9._:-]+ /{print $1}' "$PSSLOG" | sort -u | tr '\n' ' ')"
  else
    say "⚠️ 没有 ollama ps 采样 ⇒ 常驻几个模型这一格无读数"
  fi
  rm -f "$plain"
}

say "起场器启动（丙＋B 复验场・kwargs 全开・墙钟 21600s・只跑官方支），pid=$(cat $T/queue_kwargs_1004.pid 2>/dev/null)"

if [ "${QUEUE_DRY:-0}" = "1" ]; then   # 只验闸门有没有牙，不起任何场
  say "DRY：门槛提到 999999MB ⇒ 期望**如实拒绝**"
  gates_ok 999999; say "DRY gates_ok(999999) 返回 $?（1＝内存腿真有牙）"
  say "DRY：门槛降到 1MB ⇒ 期望放行（除非真有并发）"
  gates_ok 1; say "DRY gates_ok(1) 返回 $?（0＝不是恒假）"
  exit 0
fi

while ! gates_ok "$NEED_MB"; do sleep 300; done

mkdir -p "$SNAP"
cp -p "$OUT/factors.json" "$SNAP/" 2>/dev/null
cp -p "$ROOT/common/data/etf/cache/trial_counter.json" "$SNAP/"
say "快照 sha256：$(sha256sum "$SNAP"/* | awk '{print substr($1,1,8), $2}' | tr '\n' ' ')"
say "▶ 起场：不注入任何环境变量， kwargs 由 etf/v1/.env 默认派生（这正是 10-04 之后的生产形态）"

( while true; do
    echo "===== $(date '+%m-%d %H:%M:%S') =====" >> "$PSSLOG"
    timeout 20 ollama ps >> "$PSSLOG" 2>&1
    sleep 60
  done ) &
PSWATCH=$!
say "采样器 pid=$PSWATCH → $PSSLOG"

( cd "$ROOT/etf/v1/src" && /usr/bin/time -v "$PY" -u "$T/run_kwargs_leg_1004.py" ) > "$RUNLOG" 2>&1
rc=$?
kill "$PSWATCH" 2>/dev/null       # 只杀自己起的采样器
say "#172 之后的复验场结束 rc=$rc"
  say "  rc 口径（10-04 分层定音，别再念成\"跑完\"）：0＝内层判定跑完**且** factors.json 真被本场改写；"
  say "  3＝驱动自报有回收但文件逐字节没动＝本场 0 写入（反假绿判词）；2＝G1 kwargs 闸拒起；"
  say "  被墙钟掐这一层**不在 rc 里**，看日志那句「超时 Ns 已终止」（N 现在该是 21600）"
report
say "起场器收工。日志：$RUNLOG；采样：$PSSLOG"
