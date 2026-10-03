#!/bin/bash
# 戊-B + C（09-30）：刀口 SCREEN_QUANTILE 逐档 × 持有窗 PORT_HOLD 逐档，三层各出一行账单
#
#   跑法   bash stock/v1/temp/run_q_hold_grid_0930.sh
#          只跑其中一场：PASS=negctl|noallow|normal bash .../run_q_hold_grid_0930.sh
#   覆写面 驱动只读面板与三张归档，只写 stock/v1/temp/tmp_q_hold_grid_0930{,_negctl,_noallow}/。
#          「我以为只读」不可信 ⇒ 批次前后各量一次基准的 md5，不相等当场判红。
#   对表基准 = **快照**（`tmp_q_hold_grid_0930_anchor/`，起批前逐字节拷的三张归档）。
#          为什么不用生产那三张原文件：这一批要跑 20~30 分钟，而日更链第④步覆写的正是
#          其中两张（exclusion / buylist）。09-30 11:47 实测到本机有另一会话起过
#          `run_ashare_daily_chain.py`（24 秒即退、三张归档 md5 前后未变）。拿活文件当锚点
#          ⇒ 中途被覆写就是把「样本对不上」读成「尺子坏了」。快照三条规矩：
#            ① 快照必须与活文件**当下逐字节一致**才起批（防引到上一天的陈旧锚点）；
#            ② 批次前后快照 md5 必须一致（证本场只读）；
#            ③ 活文件若批次中途变了 ⇒ 只⚠️报告「外部写手动过归档，本场读的是快照」，
#               不判红——那不是本批的足迹，判红等于把别人的动作算成我的 bug。
#   内存   阈值不猜：沿用 09-30 丙那批**实测**数（全跨度峰值 4,959MB、下界 4,778MiB）
#          ⇒ 起手要 5,600MB 可用；泄漏线 9,000MB 只挡失控；真正保护别人的是
#          「机器可用跌破 2,500MB 就停自己这场」。看门狗只 kill 自己 fork 的那一场，
#          **别人的进程一个都不动**。
#
# 三场各自的验收（红的**那几条 id**必须逐字对上，不是「有红就行」）：
#   第 1 场 GH_NEGCTL=1（八档刀口全钉成 0.80）⇒ RC=1、红 = {P5, P6}，其余 9 行绿（9/11）
#   第 2 场 正常（刀口 8 档 + 持有窗 4 档）⇒ RC=0、12 行全绿（12/12）
#   第 3 场 GH_NOALLOW=1（L2/L3 的保留池换成不剔除那一档）⇒ RC=1，红集**必须含** {P3b, P6}，
#          P4 红不红**不由我拍**：它等于第 2 场落盘的 `L3_席位差_摘闸_场数` 是否 >0。
#          >0 ⇒ 闸真咬到 5 席层 ⇒ 红 = {P3b, P4, P6}（8/11）
#          =0 ⇒ 这一段历史里 5 席层对这道闸免疫 ⇒ 红 = {P3b, P6}（9/11），P4 必须绿。
#          两个分支都写死在这里、当场对表——这不是放宽判据，是因为「摘掉后名单换不换人」
#          本身是本场要量的未知量；拿它的某种结果当唯一期望，等于拿猜测定尺子。
#          而「L3 这一层到底读没读 allow」那一问由驱动里的 **P8（破坏性自证）**钉住：
#          在生产格把「当日轴值最低那一只」从允许池挖掉，名单必须真换人——与闸强度无关。
# 场次顺序 = 负对照 → 正常 → 断线对照：第 3 场的期望要读第 2 场的产物才能算，顺序不能换。
# 前两场只扫刀口（GH_Q_ONLY 由脚本自己置），第 2 场才扫持有窗。
# 任何一道不符 ⇒ 停在这里、不跑后面的场次，退出码传给人。
# 落盘闸：每场核 quantile_ladder.csv 行数（=8）与 hold_ladder.csv 行数（第 2 场 4、其余 0），
#         「没跑出来」与「跑对了」不许给同一个读数。
set -uo pipefail

R=/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git
T=$R/stock/v1/temp
DRIVER=$T/q_hold_grid_0930.py
PANEL=$R/stock/v1/data/results/rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5
SNAP=${SNAP:-$T/tmp_q_hold_grid_0930_anchor}
LIVE_ARCHES="$R/stock/v1/data/results/ashare_portfolio_exclusion.csv
$R/stock/v1/data/results/ashare_portfolio_buylist.csv
$R/stock/v1/data/results/quota_watchlist_m4_0925.csv"
NEED_MB=${NEED_MB:-5600}
STOP_HWM_MB=${STOP_HWM_MB:-9000}
FLOOR_AVAIL_MB=${FLOOR_AVAIL_MB:-2500}
NEED_SWAP_KB=${NEED_SWAP_KB:-102400}

top_occupants() {
  ps -eo rss,pid,etime,cmd --sort=-rss --no-headers | head -3 |
    awk '{printf "    %.2f GB  PID %s  起了 %s  %s\n", $1/1048576, $2, $3, $4}'
}
panel_md5() { md5sum "$PANEL" | awk '{print $1}'; }
live_md5() { echo "$LIVE_ARCHES" | xargs md5sum | awk '{print $1}' | tr '\n' ' '; }
arch_md5() { cd "$SNAP" && md5sum $(basename -a $LIVE_ARCHES) | awk '{print $1}' | tr '\n' ' '; }

snapshot_anchors() {          # 起批前把三张活归档逐字节拷进 SNAP，并要求当场一致
  mkdir -p "$SNAP"
  for f in $LIVE_ARCHES; do
    [ -f "$f" ] || { echo "❌ 对表基准缺失 $f"; exit 2; }
    cp -p "$f" "$SNAP/"
    cmp -s "$f" "$SNAP/$(basename "$f")" \
      || { echo "❌ 快照与活文件不一致 $(basename "$f")（拷贝这一手没成立，锚点不可信）"; exit 2; }
  done
  echo "[快照] $(date '+%T') 三张基准已锁进 $SNAP/｜$(basename -a $LIVE_ARCHES | tr '\n' ' ')"
}

run_one() {            # $1=标签 $2=输出目录 $3=env 前缀
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
  env $envpre GH_ANCHOR_DIR="$SNAP" STOCK_TRADABLE_GATE=board STOCK_LIST_SCHEME=quota \
    /usr/bin/time -v /usr/bin/python3.10 -u "$DRIVER" > "$dir/$tag.log" 2>&1 &
  local wp=$! kill=""
  while kill -0 "$wp" 2>/dev/null; do
    sleep 5
    local cp hwm av
    cp=$(pgrep -P "$wp" | tail -1); [ -z "$cp" ] && continue
    hwm=$(awk '/VmHWM/{print int($2/1024)}' "/proc/$cp/status" 2>/dev/null)
    av=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
    echo "$(date '+%T') $tag 峰值=${hwm:-?}MB｜机器可用=${av}MB" >> "$dir/watch_$tag.txt"
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

expect() {           # $1=日志 $2=期望红集（无红填「无」） $3=期望「通过/总」 $4=hold_ladder 期望行数
  local log="$1" want_red="$2" want_cnt="$3" want_hrows="$4"
  local dir redline cnt qrows hrows
  dir=$(dirname "$log")
  redline=$(grep -o '\[RED_IDS\] [^｜]*' "$log" | sed 's/\[RED_IDS\] //')
  cnt=$(grep -o '汇总：[0-9]*/[0-9]* 通过' "$log" | sed 's/汇总：//;s/ 通过//')
  qrows=$(awk 'END{print (NR>0?NR-1:0)}' "$dir/quantile_ladder.csv" 2>/dev/null || echo 0)
  hrows=$(awk 'END{print (NR>0?NR-1:0)}' "$dir/hold_ladder.csv" 2>/dev/null || echo 0)
  echo "  取证：红集 [${redline:-读不到}]｜通过 ${cnt:-读不到}｜刀口表 ${qrows} 行｜周期表 ${hrows} 行"
  [ "${redline:-}" = "$want_red" ] || { echo "❌ 红集是 [${redline:-读不到}]，期望 [$want_red]"; return 7; }
  [ "${cnt:-}" = "$want_cnt" ] || { echo "❌ 通过行数是 ${cnt:-读不到}，期望 $want_cnt"; return 7; }
  [ "${qrows:-0}" = "8" ] || { echo "❌ quantile_ladder.csv 是 ${qrows:-0} 行，期望 8 行"; return 7; }
  [ "${hrows:-0}" = "$want_hrows" ] || { echo "❌ hold_ladder.csv 是 ${hrows:-0} 行，期望 $want_hrows 行"; return 7; }
  return 0
}

# 从正常场落盘的那一行里取「摘掉整道剔除闸，5 席名单换了几场」——断线对照的期望由它算，不由我拍
seat_diff_from_csv() {   # $1=quantile_ladder.csv
  awk -F, 'NR==1{for(i=1;i<=NF;i++) if($i=="L3_席位差_摘闸_场数") c=i; next}
           $1=="0.8" && $2=="5" && c {print $c+0; exit}' "$1"
}

echo "[批次] $(date '+%F %T')｜驱动 $DRIVER"
P0MD5=$(panel_md5); echo "[面板] md5 $P0MD5｜$(stat -c '%y' "$PANEL")"
L0=$(live_md5); echo "[活归档] 起批 md5 $L0"
snapshot_anchors
M0=$(arch_md5); echo "[对表基准] 快照三张 md5 $M0"

PASS=${PASS:-all}

# ---------- 第 1 场：刀口全钉成一档 ⇒ 尺子必须自己判红 ----------
if [ "$PASS" = all ] || [ "$PASS" = negctl ]; then
  run_one negctl "$T/tmp_q_hold_grid_0930_negctl" "GH_NEGCTL=1"
  RC=$?
  [ "$RC" -eq 1 ] || { echo "❌ 负对照退出码 $RC（应为 1）⇒ 尺子没牙，后面的绿一文不值"; tail -20 "$T/tmp_q_hold_grid_0930_negctl/negctl.log"; exit 6; }
  expect "$T/tmp_q_hold_grid_0930_negctl/negctl.log" "P5、P6" "9/11" 0 || exit 7
  echo "✅ 第 1 场取证完成：刀口全钉成一档 ⇒ 恰好 P5/P6 两条红、退出码 1，P8 那条破坏性自证仍绿"
fi

# ---------- 第 2 场：正常五档×八档 ⇒ 必须全绿，且它落盘的读数定第 3 场的期望 ----------
if [ "$PASS" = all ] || [ "$PASS" = normal ]; then
  run_one normal "$T/tmp_q_hold_grid_0930" ""
  RC=$?
  [ "$RC" -eq 0 ] || { echo "❌ 正常场退出码 $RC"; tail -30 "$T/tmp_q_hold_grid_0930/normal.log"; exit 8; }
  expect "$T/tmp_q_hold_grid_0930/normal.log" "无" "12/12" 4 || exit 7
  echo "✅ 第 2 场：12 行判据全绿（含持有窗四档的网格算术闸 P7）"
fi

# ---------- 第 3 场：把剔除闸摘掉 ⇒ 期望红集由第 2 场实测的「摘闸换几场」决定 ----------
if [ "$PASS" = all ] || [ "$PASS" = noallow ]; then
  [ -f "$T/tmp_q_hold_grid_0930/quantile_ladder.csv" ] || { echo "❌ 断线对照要引的基准缺失（第 2 场产物）"; exit 5; }
  D=$(seat_diff_from_csv "$T/tmp_q_hold_grid_0930/quantile_ladder.csv")
  case "${D:-}" in ''|*[!0-9]*) echo "❌ 从正常场 csv 里读不到 L3_席位差_摘闸_场数（读到 [${D:-空}]）"; exit 5;; esac
  if [ "$D" -gt 0 ]; then WANT_RED="P3b、P4、P6"; WANT_CNT="8/11"
  else WANT_RED="P3b、P6"; WANT_CNT="9/11"; fi
  echo "[第 3 场期望] 正常场实测「摘闸换 ${D} 场」⇒ 5 席层$( [ "$D" -gt 0 ] && echo '读得到这道闸' || echo '对这道闸免疫' ) ⇒ 红集期望 [$WANT_RED]、通过 $WANT_CNT"
  run_one noallow "$T/tmp_q_hold_grid_0930_noallow" "GH_NOALLOW=1"
  RC=$?
  [ "$RC" -eq 1 ] || { echo "❌ 断线对照退出码 $RC（应为 1）⇒ L2 其实没接进剔除"; tail -20 "$T/tmp_q_hold_grid_0930_noallow/noallow.log"; exit 6; }
  expect "$T/tmp_q_hold_grid_0930_noallow/noallow.log" "$WANT_RED" "$WANT_CNT" 0 || exit 7
  echo "✅ 第 3 场取证完成：摘掉剔除 ⇒ 名单腿锚点 P3b 红、减法腿那三行仍绿、红集与实测换场数逐字对上"
fi

M1=$(arch_md5)
P1MD5=$(panel_md5)
echo "[归档复核] 跑完快照 md5 $M1（基准 $M0）⇒ $([ "$M0" = "$M1" ] && echo '逐字节没动，本场只读基准成立' || echo '❌ 连快照都被改了！')"
[ "$M0" = "$M1" ] || exit 9
L1=$(live_md5)
echo "[活文件] 批次 $( [ "$L0" = "$L1" ] && echo '前后一致⇒本批确实零生产足迹' || echo "前 $L0 → 后 $L1 ⇒ ⚠️ 外部写手动过归档（本场读的是快照，读数不受影响，不判红）" )"
echo "[面板复核] 起 $P0MD5 → 完 $P1MD5 ⇒ $([ "$P0MD5" = "$P1MD5" ] && echo '同一版' || echo '⚠️ 中途有人全量重生成过面板（09-29 实测：翻 md5 不翻数据层，且本批已裁到 2026-09-24，锚点样本不受影响——只报不判）')"
echo "[批次] $(date '+%F %T') 结束"
exit 0
