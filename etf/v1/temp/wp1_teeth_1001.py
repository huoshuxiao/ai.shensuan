# -*- coding: utf-8 -*-
"""WP-1 段级作用域的两颗牙（10-01，只在测试进程内改运行时行为，不写盘）

  牙 A `scope`     —— `scoped_fingerprint` 变成恒等（两段共用一份整场指纹，
                       = 修复前的行为）⇒ 该抓红 ⑩ `test_fold_only_switch_spares_the_main_line`
                       与 ⑪ `test_main_scope_exemption_is_not_a_blanket_pass`
  牙 B `foldkeys`  —— 把 `pit_pool` 从 `FOLD_ONLY_KEYS` 里摘掉（= 新键忘了登记）
                       ⇒ 该抓红 ⑫ `test_pit_pool_switch_is_fold_scoped`

用法：
  TEETH=none|scope|foldkeys PYTHONPATH=etf/v1/temp /usr/bin/python3.10 \
      -m pytest -p wp1_teeth_1001 tests/test_run_checkpoint.py -q
"""
import os

import pytest

TEETH = os.environ.get("TEETH", "none")


@pytest.fixture(autouse=True)
def _pull_a_tooth():
    if TEETH == "none":
        yield
        return
    import run_checkpoint as rc
    if TEETH == "scope":
        real = rc.scoped_fingerprint
        rc.scoped_fingerprint = lambda fp, scope: dict(fp)
        yield
        rc.scoped_fingerprint = real
    elif TEETH == "foldkeys":
        real = rc.FOLD_ONLY_KEYS
        rc.FOLD_ONLY_KEYS = tuple(k for k in real if k != "pit_pool")
        if "pit_pool" in rc.FOLD_ONLY_KEYS:
            pytest.fail("牙 B 无从下手：FOLD_ONLY_KEYS 里没有 pit_pool")
        yield
        rc.FOLD_ONLY_KEYS = real
    else:
        pytest.fail(f"不认识这颗牙：{TEETH}")
