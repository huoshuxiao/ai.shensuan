#!/bin/bash
# 09-27 夹具维修后的全量复跑：逐条取**真**退出码（不经管道，上次踩过 $? 取到 tail 的码）
cd /home/sunwenkun/Developer/agent-workspace/ai.shensuan.git || exit 1
LOG=shell/stock/tmp_regression_0927/final_recheck.log
: > "$LOG"
run() {  # run <名字> <相对目录> <脚本...>
  local name="$1" dir="$2"; shift 2
  local out ec
  out=$(cd "$dir" && timeout 1800 python3.10 "$@" 2>&1); ec=$?
  { echo "===== $name  exit=$ec  [cwd=$dir]  ($*) ====="
    echo "$out" | tail -30
    echo; } >> "$LOG"
  echo "$name exit=$ec"
}
R=shell/stock
run T1-order-layer  . $R/check_order_layer_render_0924.py
run T1-p3-render    . $R/check_p3_render_0924.py
run T1-dashboard    . $R/check_dashboard_render_0924.py
run T2-dd-scale     . $R/dd_scale_0926.py
run T3-p0-union     . $R/check_p0_union_0924.py
run T4-p3-min-hits  . $R/check_p3_min_hits_0924.py
run T5-dsl-new-ops  . $R/dsl_new_ops_check_0926.py
run citations       . $R/check_agentmd_citations_0925.py
run etf-pytest      etf/v1 -m pytest tests
