#!/bin/bash
# 丙：量 ASHARE_PORT_HOLD（调仓周期，几天换一次篮）到底值多少钱
#
# 这一档是总账里唯一「离线量不了」的形状旋钮：换周期＝换建仓日＝整条持仓路径都变，
# 毛收益本身跟着变，所以必须重跑组合层回放。三场串行，每场只改一个环境变量。
#
#   跑法   PORT_HOLDS="5 3 10" bash stock/v1/temp/run_port_hold_0929b.sh
#          （5 必须跑，它是锚点；3 与 10 是要量的两侧）
#   覆写面 只写 stock/v1/temp/tmp_port_hold_0929b/ 这一个目录。生产三张归档
#          （ashare_portfolio_eval / _exclusion / _buylist.csv）用 env 重定向**绕开**，
#          脚本不接受"不重定向"的情况（见下面的守卫），所以不存在手滑盖掉结论的可能。
#   代价   两场冒烟**实测**：2025 起 3分04秒/峰值 1.52GiB；2026-06 起 1分55秒/1.59GiB
#          ⇒ 固定地板约 1.5GiB。全跨度（2015 起、读 1,217 万行）的峰值现在有了
#          **下界** 4,778MiB（见下面 02:06 那条）。02:17 那场跑完把两个数都钉死了：
#          墙钟 5 日 5m06s / 3 日 6m56s / 10 日 3m25s（整批 15m34s，"15~20 分钟/场"那个
#          线性外推猜大了 3~4 倍），看门狗量到最高峰值 4,959MB。
#          峰值由看门狗边跑边量（越线停自己那一场），不拿猜想去赌别人的进程。
#          ⚠️09-30 02:06 这个设计**先误伤了自己一次**：停止线 4,800MB 猜低了，
#          求值段实测就到 4,778MiB（还是下界），机器当时可用 8,975MB —— 被杀的
#          是正常场次。三件证据留在 tmp_port_hold_0929b/attempt0_watchdog误杀/。
#   失败分支 任一场非零退出、面板 md5 中途变了、内存不足 ⇒ 立即停止且不跑后续场次；
#          停止时打印当前占用者前三名，交回给人决定。**别人的进程一个都不动**：
#          起手闸门只拒绝启动，看门狗只 kill 它自己 fork 出来的那一场。
#
# 验收判据在 stock/v1/temp/check_port_hold_0929b.py（J1 落盘 / J2 网格自洽 /
# J3 三场互异 / J4 锚点对归档 / J5 面板指纹）。三条负对照**已在 09-29 真跑过**：
#   ① 拿同一场冒充三场 ⇒ J2-3、J2-10、J3 判红（EXIT=1）；
#   ② 只给一场 ⇒ 缺场次当场判红（「没跑」与「跑对」不给同一个读数）；
#   ③ 拿名单口径拨错成 global 的那张归档当真锚点 ⇒ J4 判红（|Δ|=0.0144 > 0.01
#      且当场念出 list_scheme=global）⇒ 忘记设 STOCK_LIST_SCHEME=quota 会被抓住。
set -uo pipefail

R=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
O=$R/stock/v1/temp/tmp_port_hold_0929b
ENTRY=$R/stock/v1/src/run_ashare_portfolio_eval.py
PANEL=$R/stock/v1/data/results/rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5
PORT_HOLDS=${PORT_HOLDS:-"5 3 10"}
# 内存闸的取值依据（2026-09-29 实测）：
#   冒烟 A（2025 起，读 272.7 万行）3:04 / 峰值 1.52 GiB
#   冒烟 B（2026-06 起，读约 50 万行）1:55 / 峰值 1.59 GiB
#   ⇒ 有约 1.5GiB 的**固定地板**，且这两点看不出随跨度涨；全跨度（1,217 万行）
#     的峰值**仍未实测**，所以闸门按"地板 + 数据项 4×"留余量，跑起来还有看门狗兜。
NEED_MB=${NEED_MB:-5600}          # 起一场所需的可用内存下限（= 已知峰值下界 + 余量）
# 02:06:50 那场的实测读数（watch_h5.txt + eval_h5.log 的 time -v 报表）：
#   全跨度 12,172,927 行，走到「表达式求值完成 59s」时峰值已 4,893MB，
#   Maximum resident set size = 5,010,924 kB = 4,778 MiB ⇒ 这是全跨度峰值的
#   **下界**、不是峰值（回放/剔除/短名单三段还没走）。当时机器可用 8,975MB。
STOP_HWM_MB=${STOP_HWM_MB:-9000}  # 只挡失控泄漏；4800 那一版在 02:06 误杀了自己
FLOOR_AVAIL_MB=${FLOOR_AVAIL_MB:-2500}   # 真正保护别人进程的是这一条，不是上面那一条
NEED_SWAP_KB=${NEED_SWAP_KB:-102400}

mkdir -p "$O"
: > "$O/panel_md5.txt"          # 每场批次从头记，别让上一场的指纹混进来
md5_of() { md5sum "$PANEL" | awk '{print $1}'; }
top_occupants() {
  ps -eo rss,pid,etime,cmd --sort=-rss --no-headers | head -3 |
    awk '{printf "    %.2f GB  PID %s  起了 %s  %s\n", $1/1048576, $2, $3, $4}'
}

# —— 守卫 0：产物必须全部落在本目录，否则不跑（防盖生产归档）——
for f in "$O" "$ENTRY" "$PANEL"; do
  [ -e "$f" ] || { echo "❌ 缺路径 $f"; exit 2; }
done

echo "[批次] $(date '+%F %T')｜PORT_HOLDS =$PORT_HOLDS｜产物目录 $O"
echo "[面板] md5 $(md5_of)｜$(stat -c '%s 字节 / mtime %y' "$PANEL")"

for H in $PORT_HOLDS; do
  AVAIL=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
  SWAP=$(awk  '/SwapFree/{print int($2)}' /proc/meminfo)
  if [ "$AVAIL" -lt "$NEED_MB" ] || [ "$SWAP" -lt "$NEED_SWAP_KB" ]; then
    echo "❌ 不起 hold=$H：可用 ${AVAIL}MB / SwapFree ${SWAP}KB（门槛 ${NEED_MB}MB / ${NEED_SWAP_KB}KB）"
    echo "  当前占用前三名（脚本不动任何进程，请你自己决定停谁）："
    top_occupants
    exit 3
  fi
  echo "[场次 hold=$H] $(date '+%T') 起，可用 ${AVAIL}MB"
  STOCK_PORT_HOLD="$H" STOCK_TRADABLE_GATE=board STOCK_LIST_SCHEME=quota \
  STOCK_PORT_OUT="$O/eval_h$H.csv" STOCK_EXCL_OUT="$O/excl_h$H.csv" \
  STOCK_BUYLIST_OUT="$O/buylist_h$H.csv" \
    /usr/bin/time -v /usr/bin/python3.10 -u "$ENTRY" > "$O/eval_h$H.log" 2>&1 &
  WP=$!; KILL=""
  # 看门狗：边跑边量，越线只停**自己起的这一场**，别人的进程一个都不动。
  # 02:06 那一版把停止线定在 4,800MB（当时全跨度峰值未知、只能按冒烟外推猜），
  # 结果在一场正常跑的求值段就误杀了自己 —— 现在有了 4,778MiB 这个下界实测，
  # 泄漏线放宽到 9,000MB，保护责任交给「机器可用跌破 2,500MB」那一条。
  while kill -0 "$WP" 2>/dev/null; do
    # 5 秒一采：02:06 实测相邻两次 20 秒采样之间峰值从 2,326MB 跳到 4,893MB
    # （+2.6GB / 20s），20 秒的间隔来不及让「机器可用底线」起作用
    sleep 5
    CP=$(pgrep -P "$WP" | tail -1)
    [ -z "$CP" ] && continue
    HWM=$(awk '/VmHWM/{print int($2/1024)}' "/proc/$CP/status" 2>/dev/null)
    AV=$(awk  '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
    echo "$(date '+%T') hold=$H 本场峰值=${HWM:-?}MB｜机器可用=${AV}MB" | tee -a "$O/watch_h$H.txt"
    [ "${HWM:-0}" -gt "$STOP_HWM_MB" ] && KILL="峰值 ${HWM}MB 越过停止线 ${STOP_HWM_MB}MB"
    [ "${AV:-999999}" -lt "$FLOOR_AVAIL_MB" ] && KILL="机器可用跌到底线 ${AV}MB < ${FLOOR_AVAIL_MB}MB"
    if [ -n "$KILL" ]; then
      echo "❌ 停掉自己起的 hold=$H 这一场：$KILL"
      kill "$CP" 2>/dev/null; sleep 3; kill -9 "$CP" 2>/dev/null; break
    fi
  done
  wait "$WP"; RC=$?
  echo "[场次 hold=$H] $(date '+%T') 退出码 $RC${KILL:+（被看门狗停：$KILL）}"
  grep -E "Maximum resident|Elapsed" "$O/eval_h$H.log" | sed 's/^/  /'
  M=$(md5_of); echo "$H $M" >> "$O/panel_md5.txt"
  if [ "$M" != "$(head -1 "$O/panel_md5.txt" | awk '{print $2}')" ]; then
    echo "❌ 面板在批次中途换了版本（$M ≠ 首场），三场不再可比 ⇒ 停止并作废"
    exit 4
  fi
  [ "$RC" -eq 0 ] || { echo "❌ hold=$H 非零退出，看 $O/eval_h$H.log 尾部"; tail -20 "$O/eval_h$H.log"; exit 5; }
done

/usr/bin/python3.10 -u "$R/stock/v1/temp/check_port_hold_0929b.py" --dir "$O"
CHK=$?          # 必须先存：同一行里 $(date) 的替换会把 $? 冲成 date 的 0
echo "[批次] $(date '+%F %T') 结束，checker 退出码 $CHK"
exit "$CHK"
