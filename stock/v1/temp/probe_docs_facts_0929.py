# -*- coding: utf-8 -*-
"""丙 7-A 的读数探针：把 test_docs_facts 六条判据的「正文写了什么 / 磁盘现量多少」摊开打印。

不落断言、只出读数 ⇒ 用来确认 F1~F6 各条到底在比哪两个数、余量多大。
用法：/usr/bin/python3.10 stock/v1/temp/probe_docs_facts_0929.py
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
V1 = HERE.parents[1]
sys.path.insert(0, str(V1 / "tests"))

import test_docs_facts as tdf  # noqa: E402  conftest 会在 import 期把 env 全量隔离

text = tdf.DOC.read_text()
dirs = tdf._tree_dirs(text)

print(f"入口：正文/现量 = {tdf._SRC_LINE_RE.findall(text)} / {tdf.count_entries()}")
print(f"草稿区：现量 .py+.sh = {tdf.count_scratch(tdf.REPO_ROOT / 'stock' / 'v1' / 'temp')}")
print(f"数据层：现量 common/src/data/stock/*.py = "
      f"{len(list((tdf.REPO_ROOT / 'common' / 'src' / 'data' / 'stock').glob('*.py')))}")
for ln_no, ln in enumerate(text.splitlines()):
    for m in tdf._SIZE_RE.finditer(ln):
        stated, unit = float(m.group(1)), m.group(2)
        targets = dirs.get(ln_no, [])
        tot = sum(tdf.dir_bytes(tdf.REPO_ROOT / p) for p in targets) if targets else 0
        got = tot / (1024 ** 2 if unit == "M" else 1024 ** 3)
        print(f"体积：第 {ln_no + 1} 行「{m.group(0)[:14]}…」指到 {'、'.join(targets) or '(解析失败)'}"
              f" = {got:.1f} {unit}iB，正文 {stated}{unit}"
              f" ⇒ 偏差 {abs(got - stated) / stated:.1%}（容差 35%）")
print(f"路径：核了 {len(tdf.checked_path_tokens(text))} 条反引号路径")
for fn in tdf.ALL_CHECKS:
    print(f"{fn.__name__:24s} -> {fn(text) or '绿'}")
