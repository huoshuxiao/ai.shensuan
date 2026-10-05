# -*- coding: utf-8 -*-
"""把引证尺剩下 7 根行号挂回原位（每处必须恰好命中 1 次，命中数不是 1 就整批不落盘）"""
import ast
import pathlib

F = pathlib.Path("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/"
                 "stock/v1/temp/check_agentmd_citations_0925.py")

ROWS = [
    ('("§18.13 乙+ 配置键（10-04 起空串＝派生而非不注入）", "common/src/config_base.py", 482, 482,',
     '("§18.13 乙+ 配置键（10-04 起空串＝派生而非不注入）", "common/src/config_base.py", 492, 492,'),
    ('("§18.13 乙＋铃铛：报警线这把新旋钮（默认 0＝不响）", "common/src/config_base.py", 554, 554,',
     '("§18.13 乙＋铃铛：报警线这把新旋钮（默认 0＝不响）", "common/src/config_base.py", 564, 564,'),
    ('("§18.13 超时那一句走收尸不是 kill 外壳", "common/src/core/official_rdagent.py", 529, 529,',
     '("§18.13 超时那一句走收尸不是 kill 外壳", "common/src/core/official_rdagent.py", 632, 632,'),
    ('("§18.13 回收只查文件存在（B6 未裁、仍是旧档）", "common/src/core/official_rdagent.py", 579, 579,',
     '("§18.13 回收只查文件存在（B6 未裁、仍是旧档）", "common/src/core/official_rdagent.py", 682, 682,'),
    ('("§18.11 丁：mtime 那行只当文字念、不作判据", "common/src/core/official_rdagent.py", 597, 597,',
     '("§18.11 丁：mtime 那行只当文字念、不作判据", "common/src/core/official_rdagent.py", 700, 700,'),
    ('("§18.13 乙+ 外壳透传（10-04 起留空也会派生）", "common/src/core/official_rdagent.py", 92, 92,',
     '("§18.13 乙+ 外壳透传（10-04 起留空也会派生）", "common/src/core/official_rdagent.py", 190, 190,'),
    ('"common/src/core/official_rdagent.py",\n     94, 100, "derived"),',
     '"common/src/core/official_rdagent.py",\n     192, 198, "derived"),'),
]

src = F.read_text(encoding="utf-8")
bad = []
for old, new in ROWS:
    n = src.count(old)
    if n != 1:
        bad.append((n, old[:60]))
if bad:
    print("命中数不是 1，整批不落盘：", bad)
    raise SystemExit(1)

for old, new in ROWS:
    src = src.replace(old, new, 1)
ast.parse(src)
F.write_text(src, encoding="utf-8")
print(f"落盘 {len(ROWS)} 根，语法 OK")
