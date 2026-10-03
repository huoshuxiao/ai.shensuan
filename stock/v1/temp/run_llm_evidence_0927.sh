#!/usr/bin/env bash
# 两步证据串一条命令：记忆探针在前（它决定回放读数怎么念），然后回放冒烟 3 场，最后 60 场。
# 三段**串行**（本机的红线：不并发第二个本地 LLM），而且冷加载只付一次。
# 冒烟不过就不进 60 场：那 45 分钟 CPU 是拿来出读数的，不是拿来 debug 的。
set -uo pipefail
cd /home/sunwenkun/Developer/agent-workspace/ai.shensuan.git || exit 1
OUT=stock/v1/temp/tmp_llm_evidence_0927
mkdir -p "$OUT"
P=/usr/bin/python3.10

echo "=== 1/3 记忆探针 $(date '+%F %T') ==="
"$P" stock/v1/temp/probe_llm_memory_0927.py 2>&1 | tee "$OUT/probe.log"
rc1=${PIPESTATUS[0]}
echo "rc_probe=$rc1"

echo "=== 2/3 回放冒烟 N=3 $(date '+%F %T') ==="
N_SESSIONS=3 "$P" stock/v1/temp/backtest_llm_picks_0927.py 2>&1 | tee "$OUT/replay_smoke.log"
rc2=${PIPESTATUS[0]}
echo "rc_smoke=$rc2"
if [ "$rc2" -ne 0 ]; then
  echo "❌ 冒烟未通过 ⇒ 不进 60 场（先看 $OUT/replay_smoke.log）"
  exit 1
fi

echo "=== 3/3 回放 N=60 $(date '+%F %T') ==="
N_SESSIONS=60 "$P" stock/v1/temp/backtest_llm_picks_0927.py 2>&1 | tee "$OUT/replay60.log"
echo "rc60=${PIPESTATUS[0]}  # 已答过的场走缓存，这段可以安全重跑"
echo "=== 全部结束 $(date '+%F %T') ==="
