# -*- coding: utf-8 -*-
"""只读探针：把 09-24 真实读数喂进重挖触发器，比较"全样本 DSR"与
"合并样本外 DSR"两条口径各会判出什么（含 level / Δ 两条腿的分解）。

不碰生产状态文件：`data/cache/remining_state.json` 原样读进来，写成一份临时
副本喂给 `ReminingState(path=...)`，所以本轮探测不会污染连续恶化计数。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "etf", "v1", "src"))
import _bootstrap  # noqa: E401,F401

import pandas as pd  # noqa: E402
from config import TRIGGER_LOGIC  # noqa: E402
from dsr import TrialCounter  # noqa: E402
from trigger_logic import DualIndicatorTrigger  # noqa: E402
from remining_state import ReminingState  # noqa: E402

STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), os.pardir,
                     "etf", "v1", "data", "cache", "remining_state.json")
RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), os.pardir,
                       "etf", "v1", "data", "results")

prod = json.load(open(STATE, encoding="utf-8"))
print(f"生产状态（只读）: consecutive="
      f"{prod['daily'].get('consecutive_bad_rounds')} "
      f"last_dsr={prod['daily'].get('last_dsr')} "
      f"last_pbo={prod['daily'].get('last_pbo')} "
      f"bar={prod['daily'].get('current_bar')}")

oos = pd.read_csv(f"{RESULTS}/walk_forward_oos_daily.csv").iloc[0]
full = pd.read_csv(f"{RESULTS}/dsr_daily.csv").iloc[0]
pbo_res = json.load(open(f"{RESULTS}/pbo_result.json", encoding="utf-8"))
dsr_full, dsr_oos = float(full["dsr"]), float(oos["dsr"])
pbo_now = float(pbo_res["pbo"])
print(f"09-24 读数: 全样本 DSR={dsr_full:.4f}（N={int(full['n_trials'])}）｜"
      f"合并样本外 DSR={dsr_oos:.4f}（N={int(oos['n_trials'])}、"
      f"T={int(oos['n_samples'])}）｜PBO={pbo_now:.4f}"
      f"（{pbo_res['config_family']}×{pbo_res['n_configs']}）")
print(f"判据: mode={TRIGGER_LOGIC['mode']} dsr<{TRIGGER_LOGIC['dsr_threshold']} "
      f"或 Δ<{TRIGGER_LOGIC['dsr_delta_threshold']}；"
      f"pbo>{TRIGGER_LOGIC['pbo_threshold']} 或 Δ>{TRIGGER_LOGIC['pbo_delta_threshold']}；"
      f"连续 {TRIGGER_LOGIC['consecutive_rounds']} 轮才触发")


def run(label, dsr_now, seed_last_dsr=None, seed_last_pbo=None, bar=4063):
    """在临时副本上跑一次 check，返回触发器的完整判定。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    bucket = dict(prod["daily"])
    if seed_last_dsr is not None:
        bucket["last_dsr"] = seed_last_dsr
        bucket["last_pbo"] = seed_last_pbo
    json.dump({"daily": bucket}, open(tmp.name, "w", encoding="utf-8"))
    trig = DualIndicatorTrigger(state=ReminingState(path=tmp.name))
    # Δ 必须在 check 之前取：check 里的 record_indicator 会把 last_dsr
    # 覆成本轮值，事后再读就只能算出恒等于 0 的自减自
    prev_dsr = trig.state.data.get("last_dsr")
    out = trig.check(dsr_now=dsr_now, pbo_now=pbo_now, current_bar=bar)
    dsr_bad = dsr_now < TRIGGER_LOGIC["dsr_threshold"]
    delta = None if prev_dsr is None else dsr_now - float(prev_dsr)
    print(f"  {label:<34} bad={out['bad_this_round']!s:<5} "
          f"连续 {out['consecutive']}/{TRIGGER_LOGIC['consecutive_rounds']} "
          f"触发={out['triggered']!s:<5}｜DSR 水平腿={dsr_bad}"
          f"{'  Δ=%+.4f' % delta if delta is not None else '  Δ=无基准'}"
          f"｜{out['reason']}")
    os.unlink(tmp.name)
    return out


print("\n本轮（bar=4063，PBO=0.6571 已 >0.5 ⇒ 判据只剩 DSR 那条腿）:")
run("喂全样本 DSR（改动前）", dsr_full)
run("喂合并样本外 DSR（改动后）", dsr_oos)

print("\n若上一轮记录的基准不同（看 Δ 腿会不会翻）:")
run("基准=上轮全样本 0.0121，本轮全样本", dsr_full,
    seed_last_dsr=0.0121, seed_last_pbo=0.2429)
run("基准=上轮全样本 0.0121，本轮合并", dsr_oos,
    seed_last_dsr=0.0121, seed_last_pbo=0.2429)
run("基准=上轮合并 0.0390，本轮全样本", dsr_full,
    seed_last_dsr=0.0390, seed_last_pbo=0.6571)

print(f"\n账本现状: N={TrialCounter().get()}（只读，不加计）")
