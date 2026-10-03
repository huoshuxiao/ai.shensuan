# -*- coding: utf-8 -*-
"""ETF 线 · #15 路径 3（回测起点后移）的真实账单（09-25，只算不写生产产物）

探针 #1（`i29_path_probe_0925.py`）已经把路径 4 算准了，但路径 3 只剩两个数拿不到：
① **近段实测年化夏普**——起点后移会把合并段缩短，门槛随 1/sqrt(T) 抬，可这段的
收益序列本身换掉了，偏度/峰度也跟着换，代理读数只能钉住高阶矩、必然失真；
② **截面厚度**——池按"今日"的成交额/波动准入选出，多数标的 2015 年后才上市，
早期折的训练段可能只剩个位数标的可用，样本外验证验的是一个名都不是的截面。

本脚本因此**真跑 walk-forward**，但把两处代价按住：
- 折内引擎只用 `registry`（不拉容器）。容器那一支是本轮判决的来源，但一次折内
  循环 ≈1.5~2h，四个起点跑下来不可接受；换 registry 后各起点同尺可比，
  与 09-25 那轮"registry+multi_source"的绝对读数**不可直接对照**（这里自带
  2010 基线一次作参照）。
- N 用 `walk_forward.DSR = {"n_trials": N_FIXED}` 钉死：源码里
  `n_trials_now = DSR.get("n_trials") or tc.get()`，注入后各起点共用同一个多重
  检验门槛，剩下的差异全是几何与收益的，不再混进"这次多试了几个因子"。
  试验账本仍换成 tmp 文件，`tc.add()` 绝不碰生产 `trial_counter.json`。

数据只加载一次：20 只主线缓存与全市场镜像都止/起于 2010-01-04~2026-09-23，
起点后移只是把同一份 in-memory pool 切片，既不回源也不覆写缓存。

用法：python3 i30_start_probe_0925.py [YYYY-MM-DD ...]
"""

import os
import sys
import time

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "etf", "v1", "src")
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(SRC)
sys.path.insert(0, SRC)
import _bootstrap  # noqa: F401
from config import FREQ, DSR as CFG_DSR, RISK_CONTROL, RESULTS_DIR  # noqa: E402
from dsr import TrialCounter  # noqa: E402
from etf_universe import get_universe  # noqa: E402
from data_loader import DataLoader  # noqa: E402
import walk_forward  # noqa: E402
from walk_forward import make_splits  # noqa: E402
from main import mine_with_engines  # noqa: E402
from backtest import get_backtester  # noqa: E402

# 与 09-25 那轮合并段读数同尺：产物里存的 n_trials 就是它当时用的 N
N_FIXED = int(pd.read_csv(
    f"{RESULTS_DIR}/walk_forward_oos_{FREQ}.csv").iloc[0]["n_trials"])
walk_forward.DSR = dict(CFG_DSR)
walk_forward.DSR["n_trials"] = N_FIXED

DEFAULT_STARTS = ["2010-01-04", "2014-01-01", "2016-01-01", "2019-01-01"]
starts = sys.argv[1:] or DEFAULT_STARTS

print(f"探针配置: FREQ={FREQ} | 折数={walk_forward.WALK_FORWARD['n_splits']} "
      f"train_ratio={walk_forward.WALK_FORWARD['train_ratio']} "
      f"embargo={walk_forward.WALK_FORWARD['embargo_bars']} "
      f"| 全起点共用 N={N_FIXED}（生产账本当前值={TrialCounter().get()}，本脚本不写它）")

universe = get_universe()
codes = universe.universe["code"].tolist()
full_pool = DataLoader(freq=FREQ).load_pool(codes)
ref_code = max(full_pool, key=lambda c: len(full_pool[c]))
print(f"数据只加载一次：{len(full_pool)}/{len(codes)} 只有日线，"
      f"时间轴参考 {ref_code} {full_pool[ref_code].index[0].date()} ~ "
      f"{full_pool[ref_code].index[-1].date()}（{len(full_pool[ref_code])} bar）")

tmp_ledger = os.path.join(HERE, "i30_trial_counter_tmp.json")
rows = []
for start in starts:
    t0 = time.time()
    pool = {c: df.loc[start:] for c, df in full_pool.items()}
    pool = {c: df for c, df in pool.items() if len(df) >= 60}
    ref = max(pool, key=lambda c: len(pool[c]))
    axis = pool[ref].index
    print(f"\n{'=' * 66}\n起点 {start}: 全样本 {len(axis)} bar "
          f"({axis[0]:%Y-%m-%d} ~ {axis[-1]:%Y-%m-%d}) | 池 {len(pool)} 只")

    # 截面厚度单独先算：它只取决于切片，不需要跑折内，跑失败也要有这一列
    thickness = []
    for tr_s, tr_e, te_s, te_e in make_splits(axis):
        tp = {c: len(df.loc[tr_s:tr_e]) for c, df in pool.items()}
        usable = sum(1 for n in tp.values() if n > 240)
        thickness.append((f"{tr_s:%Y-%m}~{tr_e:%Y-%m}", usable, len(pool)))
    print("  各折训练段可用标的: " + "  ".join(
        f"[{lab}] {u}/{t}" for lab, u, t in thickness))

    def factor_fn(p, idx, fold=None):
        return mine_with_engines(p, ["registry"], tag=f"起点{start[2:4]} 折 {fold}")

    def backtest_fn(p, signals, uni, risk):
        return get_backtester(p, uni, risk or RISK_CONTROL).run(signals)

    if os.path.exists(tmp_ledger):
        os.remove(tmp_ledger)
    wf = walk_forward.walk_forward_run(
        pool, universe, factor_fn, backtest_fn,
        trial_counter=TrialCounter(path=tmp_ledger))
    merged = wf.get("merged_oos_dsr") or {}
    for f in wf["folds"]:
        rows.append({"起点": start, "折": f["fold"], "段": "逐折",
                     "bar": f["测试段bar数"], "夏普": float(f["夏普比率"]),
                     "DSR": f["DSR"], "门槛": f["运气门槛年化"],
                     "因子数": f["折内因子数"], "来源": f["折内因子来源"],
                     "spend_s": None})
    rows.append({"起点": start, "折": 0, "段": "合并",
                 "bar": merged.get("n_samples"), "夏普": round(
                     float(merged.get("sharpe_annual", 0)), 4),
                 "DSR": round(float(merged.get("dsr", 0)), 4),
                 "门槛": merged.get("sr0_annual"),
                 "因子数": None, "来源": "",
                 "偏度": round(float(merged.get("skew", 0)), 2),
                 "峰度": round(float(merged.get("kurt", 0)), 1),
                 "通过": bool(merged.get("passed", False)),
                 "spend_s": round(time.time() - t0, 1)})
    print(f"  本起点耗时 {time.time() - t0:.1f}s")

print(f"\n{'=' * 66}\n=== 路径 3 账单（N 固定 {N_FIXED}、折内引擎 registry）===")
df = pd.DataFrame(rows)
print(df.to_string(index=False))
print("\n说明：逐折 DSR 在现状几何下恒 False（每段 T 短 ⇒ 门槛高），判决只看合并那条；"
      "偏度/峰度那一列是本段自己的高阶矩，不是代理值。")
print("与 09-25 生产轮（registry+multi_source、N 逐折累加）不可直接对照绝对值。")
