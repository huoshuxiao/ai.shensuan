# -*- coding: utf-8 -*-
"""9b 基线场的「净增」读数：名字集合差，不读驱动那句回收数。

为什么不能用日志里的 `回收 N 个因子表达式`：rdagent_driver 的 harvest 是
**与上一版 factors.json 合并后整体重写**（同名字段回填），那句 N 打的是
合并后的库大小，不是本场新产。判「这一场 9b 到底挖出几个新因子」只能拿
本场前后的名字集合做差。

读侧口径：只读，不写生产路径。锚点 BEFORE = 起循环前抄的生产库名单。
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BEFORE = os.path.join(REPO, "stock/v1/temp/tmp_rdagent_baseline_0929/names_BEFORE.txt")
AFTER = os.path.join(REPO, "stock/v1/data/results/rdagent_output/factors.json")


def names(path):
    with open(path, encoding="utf-8") as f:
        return [e.get("name", "") for e in json.load(f)]


def main():
    before = open(BEFORE, encoding="utf-8").read().splitlines()
    after = names(AFTER)
    old, new = set(before), set(after)
    added = [n for n in after if n not in old]
    gone = sorted(old - new)
    print(f"起点 {len(before)} 条 → 现在 {len(after)} 条（库大小，含合并回填）")
    print(f"净增 {len(added)} 条")
    if added:
        idx = {e.get("name", ""): e for e in json.load(open(AFTER, encoding="utf-8"))}
        for n in added:
            e = idx[n]
            ic = e.get("ic", e.get("mean_ic"))
            has_code = bool(e.get("code") or e.get("factor_description"))
            print(f"  + {n}　ic={ic}　带实现={has_code}")
    print(f"丢失 {len(gone)} 条 {gone if gone else ''}")
    # 合并回填会把旧条目的字段补全，所以「总数没涨」不等于「本场没产出」的
    # 反面——上面两个集合差才是判据；这里只补一条字节级读数防「没跑」冒充「跑对」
    print(f"factors.json mtime={__import__('time').strftime('%m-%d %H:%M:%S', __import__('time').localtime(os.path.getmtime(AFTER)))}")


if __name__ == "__main__":
    sys.exit(main())
