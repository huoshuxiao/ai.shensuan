# -*- coding: utf-8 -*-
"""WP-2 接完线的对拍：同一份数据、同一套折几何，只把「候选池挑法」这一档拨开/关。

两臂（顺序跑，各自一份临时账本）：
  M1 今日池（现状）  wide_pool=None ⇒ walk_forward 逐字节走老路径
  M2 点时池（本轮）  wide_pool=镜像全量 ⇒ 每折在本折训练段内点时挑

⚠️ 三根读数水位，引这张表必须一起念：
  1. 折内引擎只开 `registry`（注册表基线，秒级）⇒ **这不是生产的折表**
     （生产折内还跑 multi_source/GP，那要拉容器、单折 83~120 分钟）。
     这一场验的是"接线是否真的换了池子、折表是否会因此移动"，不是判决本身。
  2. 账本写在 `temp/`，一行不碰生产 `trial_counter.json`（DSR 的分母）。
  3. 不写 `data/results/` 任何表；生产折表仍是上一场那份。

用法：/usr/bin/python3.10 etf/v1/temp/wf_pit_verify_0930.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BYPASS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(BYPASS, "src"))
import _bootstrap  # noqa: E402

import pandas as pd                                        # noqa: E402
from config import (FREQ, RISK_CONTROL, WALK_FORWARD)       # noqa: E402
from data_loader import DataLoader                          # noqa: E402
from dsr import TrialCounter                                # noqa: E402
from etf_universe import get_universe                       # noqa: E402
from backtest import get_backtester                         # noqa: E402
from main import mine_with_engines                          # noqa: E402
from run_checkpoint import make_fingerprint                 # noqa: E402
from walk_forward import walk_forward_run                   # noqa: E402
import run_checkpoint as rc                                 # noqa: E402

LEDIR = os.path.join(HERE, "wf_pit_verify_0930")


def _factor_fn(p, idx, fold=None):
    # 只开注册表：这条腿负责"能不能挖出东西"，容器那一腿本轮不验
    return mine_with_engines(p, ["registry"], tag=f"对拍折 {fold}",
                              path_prefix=f"verify_{fold}", fold=fold)


def _backtest_fn(p, signals, uni, risk):
    bt = get_backtester(p, uni, risk or RISK_CONTROL)
    return bt.run(signals)


def run_arm(tag, pool, universe, wide, ledger):
    """每臂一份**临时**账本（从零起算），且开跑前删掉旧文件 ⇒ 两臂的 N 可比"""
    path = os.path.join(LEDIR, ledger)
    if os.path.exists(path):
        os.unlink(path)
    tc = TrialCounter(path=path)
    t0 = time.time()
    wf = walk_forward_run(pool, universe, _factor_fn, _backtest_fn,
                          trial_counter=tc, wide_pool=wide)
    print(f"  ⏱️ {tag} 墙钟 {time.time() - t0:.0f}s｜本臂账本 N={tc.get()}")
    return wf


def brief(wf):
    rows = []
    for s in wf["folds"]:
        rows.append({k: s.get(k) for k in
                     ("fold", "池口径", "挖掘层宽度", "日均厚", "截面有牙天%",
                      "点时新面孔", "折内因子数", "测试段bar数", "总收益率",
                      "夏普比率", "DSR", "n_trials")})
    return pd.DataFrame(rows)


def main():
    os.makedirs(LEDIR, exist_ok=True)
    print("=" * 78)
    print("  WP-2 两臂对拍（只读判据，不写 data/results）")
    print("=" * 78)
    uni = get_universe()
    codes = uni.universe["code"].tolist()
    loader = DataLoader(freq=FREQ)
    pool = loader.load_pool(codes)
    ref = max(pool, key=lambda c: len(pool[c]))
    all_ts = pool[ref].index
    print(f"  今日池 {len(pool)} 只｜时间轴 {all_ts[0]:%Y-%m-%d} ~ "
          f"{all_ts[-1]:%Y-%m-%d}（{len(all_ts)} bar）")
    print(f"  折几何 n_splits={WALK_FORWARD['n_splits']} "
          f"train_ratio={WALK_FORWARD['train_ratio']} "
          f"pit_pool={WALK_FORWARD.get('pit_pool')}")
    fp_on = make_fingerprint(list(pool), all_ts)
    print(f"  指纹 pit_pool={fp_on['pit_pool']!r}（这颗键必须在折内作用域里）")
    assert fp_on["pit_pool"] != "off", "夹具前提：点时池开关得开着才有对拍可言"
    assert "pit_pool" in rc.FOLD_ONLY_KEYS, "pit_pool 必须是折内键 ⇒ 不许拖主线作废"

    t0 = time.time()
    wide = None
    try:
        from fold_pool import load_wide_panel
        wide = load_wide_panel(DataLoader(freq=FREQ), pool)
    except Exception as e:
        print(f"❌ 宽面板加载失败：{type(e).__name__}: {e}")
        return 1
    print(f"  ⏱️ 宽面板 {time.time() - t0:.0f}s / {len(wide)} 只")

    print("\n──────── 臂 M1：今日池（现状路径）────────")
    wf_cur = run_arm("M1", pool, uni, None, "tc_m1.json")
    print("\n──────── 臂 M2：点时池（本轮接线）────────")
    wf_pit = run_arm("M2", pool, uni, wide, "tc_m2.json")

    b1, b2 = brief(wf_cur), brief(wf_pit)
    print("\n===== M1 今日池 =====\n", b1.to_string(index=False))
    print("\n===== M2 点时池 =====\n", b2.to_string(index=False))
    print("\n===== 合并样本外（唯一可判定那条）=====")
    for tag, wf in (("M1", wf_cur), ("M2", wf_pit)):
        m = wf.get("merged_oos_dsr") or {}
        print(f"  {tag}: DSR={m.get('dsr')} 通过={m.get('passed')} "
              f"bar={m.get('bars')} 年化夏普={m.get('sharpe_annual')} "
              f"运气门槛年化={m.get('sr0_annual')} N={m.get('n_trials')}")

    # 差异闸：两臂折表必须真的不同（同一路径空转就说明接线没生效）
    same = b1.fillna("-").to_string() == b2.fillna("-").to_string()
    if same:
        print("\n❌ 两臂折表逐格相同 ⇒ 点时池接线是空操作，本轮验证不成立")
        return 1
    print("\n✅ 两臂折表有差 ⇒ 接线生效（差异内容见上表；水位：只开注册表引擎，"
          "不是生产折表）")
    print("探针结束：未写 data/results 任何表，账本在 temp/wf_pit_verify_0930/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
