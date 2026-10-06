#!/bin/bash
# 甲-C 起场器（10-05 写）：**只跑官方支那一腿**，量一件事——本场「名字净增」到底是不是 0。
#
# 为什么起这一场：10-05 乙-3 给验收表加了第 8 格 H（净增 0 ⇒ 判红 ⇒ 链路非零退出），
#   而**九场日志里至今没有一个「净增 > 0」的真读数**。H 默认紧还是默认松（甲-A/甲-B）
#   没法凭道理定，差的就这一个数 ⇒ 用户 10-05 裁「甲-C」：先把它跑出来。
#   这一场还是**第一场带着 CoSTEER 跨场知识库**跑的（甲-2 于 10-05 00:4x 落地，
#   `etf/v1/.env:112 ETF_RDAGENT_COSTEER_KB_PATH=costeer_kb/knowledge_base_v2.pkl`）。
#
# 内层＝`run_kwargs_leg_1004.py`（沿用 10-04 那支，它直接调生产函数
#   `official_rdagent.try_official_rdagent()`，前置体检/超时收尸/回收全同一套）。
#   不走 `run_daily_backtest.py` ⇒ **五张归档表 / 因子库三文件 / trial_counter 一概不碰，
#   不产生 [factor-lib] 自动 commit**。`[1004]` 只是内层自己的打印前缀，不是日期主张。
#
# 覆写面（起场前逐条报过）：
#   etf/v1/data/results/rdagent_output/  —— factors.json（先快照）、log/、pickle_cache/、
#     token_cost/、prompt_cache.db、costeer_kb/（知识库首次落盘就在这）
#   本目录三个新文件：field_netnew_1005.log（stdout + time -v）、
#     ollama_ps_netnew_1005.log、queue_netnew_1005.log（起场器自己的台账）
#   ⇒ 不写 common/data/etf/cache/trial_counter.json（收尾核对其值不变）
#
# 撤销办法（任一）：
#   touch etf/v1/temp/queue_netnew_1005.CANCEL   ← 下一个检查点退出，不起
#   kill $(cat etf/v1/temp/queue_netnew_1005.pid) ← 只杀起场器；已起的驱动在
#     official_rdagent 那一层自成进程组，超时会自己 killpg 收尸
#   回滚 factors.json：cp -p etf/v1/temp/snap_before_netnew_1005/factors.json 回去
#
# 三条闸门（同 10-04 那把，已用 QUEUE_DRY 证过两向）：
#   ①MemAvailable ≥ 门槛（9b 驻留 ≈6.1GB + 16384 窗口 ≈0.5GB，按同族最坏档留余量）
#   ②SwapFree ≥ 512MB  ③无并发（模式用括号锚定，防把自己匹配进去导致永久锁死）
set -u
ROOT=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
T=$ROOT/etf/v1/temp
LOG=$T/queue_netnew_1005.log
RUNLOG=$T/field_netnew_1005.log
PSSLOG=$T/ollama_ps_netnew_1005.log
CANCEL=$T/queue_netnew_1005.CANCEL
PY=/usr/bin/python3.10
OUT=$ROOT/etf/v1/data/results/rdagent_output
NEED_MB="${QUEUE_NEED_MB:-8192}"
SNAP=$T/snap_before_netnew_1005
INNER=$T/run_kwargs_leg_1004.py
KB=$OUT/costeer_kb/knowledge_base_v2.pkl

[ "${QUEUE_DRY:-0}" = "1" ] || echo $$ > "$T/queue_netnew_1005.pid"
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

# 净增两把尺子，同一屏并排念（避免"只认共享层自己那一行"）：
#   R1 共享层 `_recover_factors` 自己念的那几行（生产 H 格吃的就是这个）
#   R2 起场器独立算的名字差集：读快照 vs 读现文件，纯 set 差
net_new_readings() {
  local plain="$1"
  say "R1 共享层原话（H 格吃的读数）：$(grep -o '本场净增 [0-9]* 个因子.*' "$plain" | tr '\n' '|')"
  say "R1 累计条数原话：$(grep -o '既有产物累计 [0-9]* 个因子[^)]*)' "$plain" | tail -1)"
  $PY - "$SNAP/factors.json" "$OUT/factors.json" <<'PYX' 2>&1 | tee -a "$LOG"
import json, sys
def names(p):
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return [it.get("name", "?") for it in d] if isinstance(d, list) else []
    except Exception as e:
        return f"<读不到：{e}>"
b, a = names(sys.argv[1]), names(sys.argv[2])
if isinstance(b, str) or isinstance(a, str):
    print("R2 独立名字差集：", b, a); raise SystemExit(0)
new = [n for n in a if n not in set(b)]
gone = [n for n in b if n not in set(a)]
print(f"R2 独立名字差集：起场前 {len(set(b))} 名 / 现 {len(set(a))} 名 / "
      f"净增 {len(new)}{'：' + '、'.join(new) if new else '（＝本场 0 写入）'}"
      f"{' / 消失 ' + str(len(gone)) + '：' + '、'.join(gone) if gone else ''}")
PYX
}

report() {
  say "—— 甲-C（净增先验之战・只跑官方支・首带 CoSTEER 知识库）收尾读数 ——"
  [ -f "$RUNLOG" ] || { say "⚠️ 日志不在：$RUNLOG ⇒ 这一场没有读数可报"; return; }
  local plain; plain=$(mktemp)
  sed -e 's/\x1b\[[0-9;]*m//g' "$RUNLOG" > "$plain"   # 不剥 ANSI 码正则一条都抓不到（踩过两次）
  say "退出码/墙钟/峰值：$(grep -E 'Elapsed \(wall|Maximum resident' "$plain" | tr -d '\t' | tr '\n' ' ')"
  say "kwargs 实际值：$(grep -o 'RDAGENT_LLM_KWARGS = .*' "$plain" | head -1)"
  say "补丁生效行（驱动自己念的）：$(grep -o 'LLM kwargs 补丁已生效: .*' "$plain" | head -1)"
  say "墙钟上限那行（丙接进子进程的现场证据）：$(grep -o '超时上限 = [0-9]*s[^|]*' "$plain" | head -1)"
  say "循环启动次数：$(grep -c 'rdagent factor 循环启动' "$plain")"
  say "Skip loop 次数：$(grep -c 'Skip loop' "$plain")｜正文为空：$(grep -c '正文为空\|内容为空\|Empty' "$plain")"
  say "Token count 行数：$(grep -c 'Token count' "$plain")｜最大值：$(grep -o 'Token count: *[0-9]*' "$plain" | awk '{print $3}' | sort -n | tail -1)"
  say "CoSTEER 知识库：$(grep -o '知识库[^|]\{0,90\}' "$plain" | sort -u | tr '\n' '|')"
  say "超时/收尸：$(grep -E '超时 [0-9]+s 已终止|收尸读数|残留容器' "$plain" | tr '\n' ' ')"
  net_new_readings "$plain"
  say "内层结论：$(grep -E '^\[1004\] (回收返回|落盘净增|对账|自报有产出)' "$plain" | tr '\n' ' ')"
  say "官方支真用了哪个模型：$(grep -o "chat_model='[^']*'" "$plain" | sort -u | tr '\n' ' ')"
  say "前置检查红项：$(grep -c '^  ❌' "$plain") 条（期望 0＝体检没红才起得来）"
  say "factors.json 与起场前 cmp：$(cmp -s "$SNAP/factors.json" "$OUT/factors.json" && echo '逐字节相同 ⇒ 本场写入 0 条' || echo '不同 ⇒ 本场改写过（改写 ≠ 净增，看 R1/R2）')"
  say "知识库落盘：$(stat -c '%s 字节 / %y' "$KB" 2>/dev/null || echo '仍不存在')｜起场前：$( [ "$KB_BEFORE" = 1 ] && echo '已在' || echo '无此文件' )"
  say "trial_counter 现值：$(cat "$ROOT/common/data/etf/cache/trial_counter.json")（期望与起场前逐字相同＝本场没污染试验数）"
  if [ -f "$PSSLOG" ]; then
    say "常驻过的模型（ollama ps 采样去重）：$(awk '/^[a-z0-9._:-]+ /{print $1}' "$PSSLOG" | sort -u | tr '\n' ' ')"
  else
    say "⚠️ 没有 ollama ps 采样 ⇒ 常驻几个模型这一格无读数"
  fi
  rm -f "$plain"
}

say "起场器启动（甲-C・只跑官方支・量净增・kwargs 由 .env 默认派生・墙钟 21600s），pid=$$"
KB_BEFORE=0; [ -f "$KB" ] && KB_BEFORE=1     # 必须在起场前定值：报告里要说"这场是不是首次落盘"

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
[ -f "$KB" ] && cp -p "$KB" "$SNAP/"
say "快照 sha256：$(sha256sum "$SNAP"/* | awk '{print substr($1,1,8), $2}' | tr '\n' ' ')"
say "▶ 起场：不注入任何环境变量，kwargs/超时/知识库三键全由 etf/v1/.env 默认派生（＝生产形态）"

( while true; do
    echo "===== $(date '+%m-%d %H:%M:%S') =====" >> "$PSSLOG"
    timeout 20 ollama ps >> "$PSSLOG" 2>&1
    sleep 60
  done ) &
PSWATCH=$!
say "采样器 pid=$PSWATCH → $PSSLOG"

( cd "$ROOT/etf/v1/src" && /usr/bin/time -v "$PY" -u "$INNER" ) > "$RUNLOG" 2>&1
rc=$?
kill "$PSWATCH" 2>/dev/null       # 只杀自己起的采样器
say "本场结束 rc=$rc"
  say "  rc 口径（10-04 分层定音，别再念成\"跑完\"）：0＝内层判定跑完**且** factors.json 真被本场改写；"
  say "  3＝驱动自报有回收但文件逐字节没动＝本场 0 写入（反假绿判词）；2＝kwargs 闸拒起；"
  say "  被墙钟掐这一层**不在 rc 里**，看日志那句「超时 Ns 已终止」（N 现在该是 21600）"
report
say "起场器收工。日志：$RUNLOG；采样：$PSSLOG"
