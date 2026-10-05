# -*- coding: utf-8 -*-
"""AGENT.md 正文 file:line 引用重挂 + §18 追加第 16 条（10-05 甲-2）

每处替换都必须**在指定那一行上恰好命中一次**，命中数不对就整批不落盘——
正文里 `official_rdagent.py:38` 这种串在两个不同条目里各出现一次，按全文替换会改错。
"""
import pathlib
import sys

F = pathlib.Path("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/AGENT.md")
lines = F.read_text(encoding="utf-8").splitlines(keepends=True)

# (1-based 行号, 旧串, 新串)
FIXES = [
    (647, "official_rdagent.py:25-32", "official_rdagent.py:26-33"),
    (776, "official_rdagent.py:459", "official_rdagent.py:557"),
    (879, "official_rdagent.py:221", "official_rdagent.py:319"),
    (910, "config_base.py:553", "config_base.py:572"),
    (945, "config_base.py:470", "config_base.py:480"),
    (948, "official_rdagent.py:38", "official_rdagent.py:105"),
    (954, "official_rdagent.py:106", "official_rdagent.py:204"),
    (967, "official_rdagent.py:579", "official_rdagent.py:682"),
    (967, "`:597`", "`:700`"),
    (1007, "config_base.py:486", "config_base.py:496"),
    (1035, "official_rdagent.py:143", "official_rdagent.py:241"),
    (1123, "config_base.py:482", "config_base.py:492"),
    (1124, "official_rdagent.py:92", "official_rdagent.py:190"),
    (1156, "official_rdagent.py:38", "official_rdagent.py:105"),
    (1175, "config_base.py:546", "config_base.py:556"),
    (1176, "official_rdagent.py:94-100", "official_rdagent.py:192-198"),
    (1202, "config_base.py:554", "config_base.py:564"),
]

bad = []
for ln, old, new in FIXES:
    idx = ln - 1
    n = lines[idx].count(old)
    if n != 1:
        bad.append((ln, old, n))
if bad:
    print("命中数不是 1，整批不落盘：", bad)
    sys.exit(1)
for ln, old, new in FIXES:
    lines[ln - 1] = lines[ln - 1].replace(old, new, 1)

# —— 引证尺那段补一条 10-05 现量（挂在第 1253 行那条 ⚠️ 之后）
SCALE = ("      ⚠️ 10-05 甲-2 之后现量仍＝**193／186／7**（同一批 7 条红、仍全在他人文件）："
         "那批在两个共享层文件里插了版本判据函数与注入段，把 **20 根针**整体顶下\n"
         "      （`official_rdagent.py` 14 根、`config_base.py` 6 根），旧→新映射逐条记在 "
         "`stock/v1/temp/check_agentmd_citations_0925.py` 表内那段注释里。\n")
anchor = "      那批 +14 行把本文件 14 根针整体顶下、已同批重挂，旧→新映射记在 `etf/v1/CHANGELOG.md` 同日那节末。\n"
ai = [i for i, l in enumerate(lines) if l == anchor]
if len(ai) != 1:
    print(f"引证尺锚点命中 {len(ai)} 次，不落盘")
    sys.exit(1)
lines.insert(ai[0] + 1, SCALE)

ITEM = """16. **官方支的「跨场知识库」已按裁「甲-2」接上（10-05 00:0x）：它只治「重复劳动」，不治「没有新想法」**
    （用户在三选项里点的原话＝「甲-2」，选它的理由是**改的是配置不是判据**）。
    - **接缝在哪**：rdagent 自己那两个环境变量（前缀 `CoSTEER_`，`knowledge_base_path`＝读、
      `new_knowledge_base_path`＝写，site-packages `components/coder/CoSTEER/config.py`）。10-04 那场
      日志 3196／3205／5817 行连着三遍 `Dump knowledge base path is not set, skip dumping.`
      ＝**这一路从来没开过**：每一场的 coding 阶段都不记得上一场写过什么，同一个数学形状被反复重写。
    - **落地三处（行号＝本批重挂后的现值）**：配置键 `RDAGENT_COSTEER_KB_PATH`
      （`common/src/config_base.py:474`，默认空串＝一个键都不发、股票线逐字节不变；本线在 `etf/v1/.env`
      写了 `ETF_RDAGENT_COSTEER_KB_PATH=costeer_kb/knowledge_base_v2.pkl`，**相对路径挂在本线
      `RDAGENT_OUTPUT_DIR` 底下**＝仓库 09-29 搬过一次家，写死绝对路径会把知识库悄悄建到老位置）；
      注入与降级在 `_driver_env()`（`common/src/core/official_rdagent.py:156-180`）；
      起场那行状态读数在 `:596-600`。
    - **为什么读数不进前置体检表**：那张表任何一行 ❌ 都会让 `try_official_rdagent` 直接 `return None`
      ＝整场不跑（这意味着什么：把一个优化项冒充成依赖）。知识库坏版本的正确处置是「降为只写不读、
      继续跑」，所以状态只打在起场那几行里。
    - **降级怎么判（本批最重要的一笔工具层读数）**：起场前用 `pickletools.genops` 只读字节——不 import
      rdagent（父进程里根本没有它）、也不执行 pickle 里的 `__reduce__`——看顶层类名是不是
      `CoSTEERKnowledgeBaseV2`；不是就只发写路径（本场从空库起步，收场 dump 覆写坏档，下一场自愈）。
      ⚠️ protocol 5 发的是 `STACK_GLOBAL`（模块名与类名各自一条 unicode），protocol ≤3 发的是 `GLOBAL`
      （py3.10 里它的 arg 是一整串 `"模块名 类名"`）——**判据的第一版只认前者**，于是生产档（默认协议 5）
      认得出、任何旧协议落盘的档案会被误判成「读不出来」⇒ 恒降级、知识库永远开不上、而且一句错都不报。
      夹具里 protocol 5／protocol 2 那两臂就是为了不让这个错活到第二天。
    - **牙**：`etf/v1/tests/test_rdagent_wiring.py` 甲-2 七格（本线 pytest 312→319，现量 319 格全绿、
      rc=0）。其中一格走真消费侧＝拿 conda 环境里的解释器起子进程，在设／不设那两个键下各实例化一次
      真 `CoSTEERSettings()` 读字段，再 dump 一份真 `CoSTEERKnowledgeBaseV2` 字节档回传给父进程用上面
      那把判据读（≈7.2s）——「这两个键名真认」与「读回来的类名真判得出」都有独立读数，不是自说自话。
    - **代价三笔**（探针 `etf/v1/temp/check_costeer_kb_env_1004.py` 六臂实测）：① V2 每加一个组件节点
      都要打一次嵌入 API（F 臂：无凭据时 litellm 10 连败后 `RuntimeError`）⇒ 开闸的账里含模型调用；
      ② 版本放错本来会把整场崩在 coding 之前（D 臂实测抛 `ValueError`），现在由降级吸收；
      ③ 它不产生新想法 ⇒ **净增的先验仍是 0**（10-04 19:05 那场 5 轮走完、9b 全场只出 3 个想法、
      后两轮被相似闸判退、名字净增 0——这一批改的是「下一场别再写第 4 遍同一个形状」）。
    - **还没有读数的那一格**：开闸之后 coding 阶段的行为到底变不变（`Skip loop` 次数、墙钟、本场净增）
      要下一场才有数——这一批只有夹具级读数，**没有真场读数**。

"""

# —— 追加第 16 条：插在 §18 末（§19 之前那道 `---` 分隔线上方）
h19 = [i for i, l in enumerate(lines) if l.startswith("## 19.")]
if len(h19) != 1:
    print(f"## 19. 锚点命中 {len(h19)} 次，不落盘")
    sys.exit(1)
seps = [i for i in range(h19[0]) if lines[i].rstrip("\n") == "---"]
if not seps:
    print("§19 之前找不到 `---` 分隔线，不落盘")
    sys.exit(1)
ins = seps[-1]
# 分隔线前应有空行；把条目插在它前面（item 之间靠空行隔开）
if lines[ins - 1].strip():
    lines.insert(ins, "\n")
lines.insert(ins, ITEM)

F.write_text("".join(lines), encoding="utf-8")
print(f"落盘：引用重挂 {len(FIXES)} 处 + 引证尺 1 条 + §18 第 16 条")
