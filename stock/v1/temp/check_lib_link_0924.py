#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""因子库 ↔ 盘前判据 接点的离线性检查（不读 0.8GB 面板）"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                                "stock", "v1", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                                "stock", "v1", "src", "strategy"))
import _bootstrap  # noqa: F401
from ashare_screen import active_rules, library_screen_link, screen_benchmarks

link = library_screen_link()
print(f"因子库条数 {len(link)}；主线判据占位 {len(screen_benchmarks())} 条")
for x in link:
    if x["role"] != "未启用":
        print(f"  {x['role']}[{x['rule_name']}] <- {x['name'][:56]}\n"
              f"      expr = {x['expr']}   dsl_ok={x['dsl_ok']}")
print(f"未进判据 {sum(1 for x in link if x['role'] == '未启用')} 条；"
      f"其中 DSL 不可求值 {sum(1 for x in link if x['dsl_ok'] is False)} 条")

print("\n默认启用：", [r[0] for r in active_rules()])
for bad in ("volatility,lib:5-day STD of Volume",     # 与 volatility 同式 -> 应拒
            "volatility,lib:查无此因子",
            "level,lib:5-day VWAP of Price",          # 价格族，合法提名（人负责看证据）
            "bogus",
            "lib:5-day VWAP of Price,lib:5-day SMA of Price"):
    try:
        got = active_rules(bad)
        print(f"  {bad!r} -> 通过：{[(r[0], r[2]) for r in got]}")
    except SystemExit as e:
        print(f"  {bad!r} -> 拒绝：{str(e)[:100]}")
