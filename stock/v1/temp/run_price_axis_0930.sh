#!/bin/bash
# 甲（09-30）：价格族搬到「5 席下单层 + 配额」那把秤，两场串行、退出码都取证
#
#   跑法   bash stock/v1/temp/run_price_axis_0930.sh
#   覆写面 只写 stock/v1/temp/tmp_price_axis_ctrl_0930{,_negctl}/ 两个目录。
#          驱动只读面板与归档、一个生产文件都不写 ⇒ 批次前后各量一次生产归档 md5，
#          不相等就当场判红（防「我以为只读」）。
#   内存   阈值不猜：沿用 09-30 丙那批**实测**数（全跨度峰值 4,959MB、
#          下界 4,778MiB）⇒ 起手要 5,600MB 可用，泄漏线 9,000MB 只挡失控，
#          真正保护别人的是「机器可用跌破 2,500MB 就停自己这场」。
#          看门狗只 kill 自己 fork 的那一场，**别人的进程一个都不动**。
#
# 两场的判据不一样，别混：
#   第 1 场 PRICE_NEGCTL=1（把四条价格臂全钉成现轴）⇒ **必须** RC=1，
#     且红的必须恰好是 8 条、全部是 A3/A4，A0/A1/A2 三条口径闸必须绿。
#     这一场是在量尺子的牙：尺子要是「注入了假臂还退 0」，第 2 场的绿就一文不值。
#   第 2 场正常五臂 ⇒ 必须 RC=0、十条判据全绿。
# 任何一道不符 ⇒ 停在这里、不跑第 2 场（或作废第 2 场），退出码传给人。
set -uo pipefail

R=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
DRIVER=$R/stock/v1/temp/price_axis_ctrl_0930.py
PANEL=$R/stock/v1/data/results/rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5
ARCH=$R/stock/v1/data/results/quota_watchlist_m4_0925.csv
NEED_MB=${NEED_MB:-5600}
STOP_HWM_MB=${STOP_HWM_MB:-9000}
FLOOR_AVAIL_MB=${FLOOR_AVAIL_MB:-2500}
NEED_SWAP_KB=${NEED_SWAP_KB:-102400}

top_occupants() {
  ps -eo rss,pid,etime,cmd --sort=-rss --no-headers | head -3 |
    awk '{printf "    %.2f GB  PID %s  起了 %s  %s\n", $1/1048576, $2, $3, $4}'
}
arch_md5() { md5sum "$ARCH" | awk '{print $1}'; }

run_one() {            # $1=标签 $2=日志目录 $3=额外的 env 赋值
  local tag="$1" dir="$2" envpre="$3"
  mkdir -p "$dir"
  local avail swap
  avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
  swap=$(awk '/SwapFree/{print int($2)}' /proc/meminfo)
  if [ "$avail" -lt "$NEED_MB" ] || [ "$swap" -lt "$NEED_SWAP_KB" ]; then
    echo "❌ 不起 $tag：可用 ${avail}MB / SwapFree ${swap}KB（门槛 ${NEED_MB}MB / ${NEED_SWAP_KB}KB）"
    echo "  当前占用前三名（脚本不动任何进程，请你自己决定停谁）："
    top_occupants
    return 3
  fi
  echo "[场次 $tag] $(date '+%T') 起，可用 ${avail}MB"
  # /usr/bin/time -v 顺手把这一场的全跨度峰值量下来（丙那批只量过它自己的入口）
  env $envpre STOCK_TRADABLE_GATE=board STOCK_LIST_SCHEME=quota \
    /usr/bin/time -v /usr/bin/python3.10 -u "$DRIVER" > "$dir/$tag.log" 2>&1 &
  local wp=$! kill=""
  while kill -0 "$wp" 2>/dev/null; do
    sleep 5
    local cp hwm av
    cp=$(pgrep -P "$wp" | tail -1); [ -z "$cp" ] && continue
    hwm=$(awk '/VmHWM/{print int($2/1024)}' "/proc/$cp/status" 2>/dev/null)
    av=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
    echo "$(date '+%T') $tag 峰值=${hwm:-?}MB｜机器可用=${av}MB" | tee -a "$dir/watch_$tag.txt"
    [ "${hwm:-0}" -gt "$STOP_HWM_MB" ] && kill="峰值 ${hwm}MB 越过 ${STOP_HWM_MB}MB"
    [ "${av:-999999}" -lt "$FLOOR_AVAIL_MB" ] && kill="机器可用跌到 ${av}MB < ${FLOOR_AVAIL_MB}MB"
    if [ -n "$kill" ]; then
      echo "❌ 停掉自己起的 $tag：$kill"; kill "$cp" 2>/dev/null; sleep 3; kill -9 "$cp" 2>/dev/null; break
    fi
  done
  wait "$wp"; local rc=$?
  echo "[场次 $tag] $(date '+%T') 退出码 $rc${kill:+（被看门狗停：$kill）}"
  grep -E "Maximum resident|Elapsed" "$dir/$tag.log" | sed 's/^/  /'
  return "$rc"
}

echo "[批次] $(date '+%F %T')｜驱动 $DRIVER"
echo "[面板] md5 $(md5sum "$PANEL" | awk '{print $1}')｜$(stat -c '%s 字节 / mtime %y' "$PANEL")"
M0=$(arch_md5); echo "[归档基准] quota_watchlist_m4_0925.csv md5 $M0"

# ---------- 第 1 场：负对照，退出码必须是 1 ----------
run_one negctl "$R/stock/v1/temp/tmp_price_axis_ctrl_0930_negctl" "PRICE_NEGCTL=1"
RC1=$?
NLOG=$R/stock/v1/temp/tmp_price_axis_ctrl_0930_negctl/negctl.log
if [ "$RC1" -ne 1 ]; then
  echo "❌ 负对照退出码 $RC1（应为 1）⇒ 尺子没牙，正常场就算全绿也不可引"; tail -25 "$NLOG"; exit 6
fi
RED=$(grep -c '  ❌ ' "$NLOG"); RED_OK=$(grep -c '  ❌ \(A3\|A4\)' "$NLOG")
GREEN_ANCHOR=$(grep -c '  ✅ \(A0\|A1\|A2\)' "$NLOG")
echo "  取证：红 $RED 条，其中 A3/A4 $RED_OK 条；口径闸 A0/A1/A2 绿 $GREEN_ANCHOR 条"
if [ "$RED" -ne 8 ] || [ "$RED_OK" -ne 8 ] || [ "$GREEN_ANCHOR" -ne 3 ]; then
  echo "❌ 负对照红的不是那 8 条（或口径闸没绿）⇒ 注入没被隔离干净，停止"; exit 7
fi
echo "✅ 第 1 场取证完成：注入假臂 ⇒ 恰好 8 条 A3/A4 红、退出码 1，三条口径闸仍绿"

# ---------- 第 2 场：正常五臂 ----------
run_one normal "$R/stock/v1/temp/tmp_price_axis_ctrl_0930" "PRICE_NEGCTL="
RC2=$?
LOG=$R/stock/v1/temp/tmp_price_axis_ctrl_0930/normal.log
[ "$RC2" -eq 0 ] || { echo "❌ 正常场退出码 $RC2"; tail -30 "$LOG"; exit 8; }

M1=$(arch_md5)
echo "[归档复核] 跑完 md5 $M1（基准 $M0）⇒ $([ "$M0" = "$M1" ] && echo '逐字节没动，本批零生产足迹' || echo '❌ 生产归档被改了！')"
[ "$M0" = "$M1" ] || exit 9
awk '/===== 自检/,/\[判据\]/' "$LOG"
echo "[批次] $(date '+%F %T') 结束，负对照 rc=$RC1（应为 1）｜正常场 rc=$RC2（应为 0）"
exit "$RC2"
