# -*- coding: utf-8 -*-
"""WP-2 夹具的三颗牙（10-01 拔牙自检，只在测试进程内改运行时行为，一个字节不写盘）

为什么存在：`tests/test_fold_pit_pool.py` 那 13 格如果换成"改坏生产代码也照样绿"，
就是恒真判据（本仓库的老毛病）。这里把三处逻辑**在内存里**拔掉，跑同一份夹具，
要求每颗牙都至少抓红一格：

  牙 A `vol`     —— `fold_pool.tradable_mask` 去掉 √252（波动闸退回比日频 std）
                    ⇒ 该抓红 `test_vol_gate_reads_annualized_not_raw_std`
  牙 B `wiring`  —— `main.stage_validation` 的调用点不再把宽面板交给主循环
                    ⇒ 该抓红 `test_stage_validation_forwards_wide_pool`
  牙 C `select`  —— `fold_pool.select_pit_pool` 恒返回空池（点时挑池整块空转）
                    ⇒ 该抓红 `test_two_arms_differ_and_are_labelled`

用法（一次一颗，外加一颗空对照）：
  TEETH=none /usr/bin/python3.10 -m pytest -p wp2_teeth_1001 tests/test_fold_pit_pool.py -q
"""
import inspect
import os

import pytest

TEETH = os.environ.get("TEETH", "none")


def _unannualized_mask(df):
    """牙 A：与 fold_pool.tradable_mask 同形，唯一区别是波动那道闸**不年化**"""
    from config import ETF_FILTER
    amt = df["amount"].rolling(ETF_FILTER["amount_window"]).median()
    vol = df["close"].pct_change().rolling(ETF_FILTER["vol_window"]).std()
    age_days = (df.index - df.index[0]).days
    return ((age_days >= ETF_FILTER["min_list_days"])
            & (amt >= ETF_FILTER["min_avg_amount"])
            & (vol >= ETF_FILTER["min_ann_vol"]))


@pytest.fixture(autouse=True)
def _pull_a_tooth():
    if TEETH == "none":
        yield
        return
    import fold_pool
    import main
    if TEETH == "vol":
        real = fold_pool.tradable_mask
        fold_pool.tradable_mask = _unannualized_mask
        yield
        fold_pool.tradable_mask = real
    elif TEETH == "wiring":
        src = inspect.getsource(main.stage_validation)
        mutated = src.replace("wide_pool=_fold_wide_pool(pool)",
                              "wide_pool=None")
        if mutated == src:
            pytest.fail("牙 B 无从下手：调用点里找不到 wide_pool=_fold_wide_pool(pool)")
        ns = {}
        exec(compile(mutated, "<teeth-wiring>", "exec"), vars(main), ns)
        real = main.stage_validation
        main.stage_validation = ns["stage_validation"]
        yield
        main.stage_validation = real
    elif TEETH == "select":
        real = fold_pool.select_pit_pool
        fold_pool.select_pit_pool = lambda panel, a, b, **k: ([], {})
        yield
        fold_pool.select_pit_pool = real
    else:
        pytest.fail(f"不认识这颗牙：{TEETH}")
    print(f"\n[拔牙自检] 牙 {TEETH} 已拔并复原")
