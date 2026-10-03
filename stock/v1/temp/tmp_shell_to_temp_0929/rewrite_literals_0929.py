# -*- coding: utf-8 -*-
"""第 2 趟：字面量 `shell/<线>/…`（字符串常量 + 注释里的「来路」引证）→ `<线>/v1/temp/…`。

第 1 趟只管「按自身深度反推」的表达式；这一趟管写死的字符串。
范围与理由：
  · 三条线 temp/ 顶层脚本                —— 改（还要能再跑）
  · temp/ 子目录里的归档副本             —— 不改（当时抓的快照/证据，改了就不是那一份）
  · src/ 与 common/ 代码注释里的来路      —— 改（现状说明书，指不到就成假引证）
  · 两份线 AGENT.md（现状说明书）        —— 改
  · 两份线 CHANGELOG.md（历史流水）      —— **不改**，只数出还有多少处，
    搬家对照关系由本次新增的那一条 CHANGELOG 条目一次性交代。
对拍：改写后（干跑=内存里）逐条把 `…/v1/temp/<尾>` 引证拿去 os.path.exists，
      列仍然指不到的（悬空），不许静默。
"""
import os
import re
import sys

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
MAP = [("shell/stock/", "stock/v1/temp/"), ("shell/etf/", "etf/v1/temp/"),
       ("shell/live2etf/", "live2etf/v1/temp/")]
ARCHIVE = re.compile(r"/(snap_|tmp_|rerun_backups|lib_backup|_sandbox|w40_|ablation)")
TOK = re.compile(r"((?:stock|etf|live2etf)/v1/temp/[A-Za-z0-9_\-./]+)")


def iter_files(group):
    def w(bases, exts):
        for base in bases:
            for root, _d, fs in os.walk(os.path.join(REPO, base)):
                for f in fs:
                    if f.endswith(exts):
                        yield os.path.join(REPO, root, f)
    if group == "temp顶层":
        for p in w(["stock/v1/temp", "etf/v1/temp", "live2etf/v1/temp"], (".py", ".sh")):
            if not ARCHIVE.search("/" + os.path.relpath(p, REPO)):
                yield p
    elif group == "temp归档区":
        for p in w(["stock/v1/temp", "etf/v1/temp", "live2etf/v1/temp"], (".py", ".sh")):
            if ARCHIVE.search("/" + os.path.relpath(p, REPO)):
                yield p
    elif group == "src代码":
        yield from w(["stock/v1/src", "etf/v1/src", "live2etf/v1/src", "common/src"],
                     (".py", ".sh", ".md"))
    elif group == "说明书":
        yield from (os.path.join(REPO, p) for p in
                    ("stock/v1/AGENT.md", "etf/v1/AGENT.md", "live2etf/v1/网格分析报告.md",
                     "live2etf/v1/设计方案.md", "README.md", "shell/docs_split_0929.py"))
    elif group == "历史流水":
        yield from (os.path.join(REPO, p) for p in
                    ("stock/v1/CHANGELOG.md", "etf/v1/CHANGELOG.md", "CHANGELOG.md", "AGENT.md"))


def main():
    do = "--apply" in sys.argv
    after = {}                      # 路径 -> 改写后的文本（干跑也能对拍）
    for group, rewrite in (("temp顶层", True), ("temp归档区", False),
                           ("src代码", True), ("说明书", True), ("历史流水", False)):
        files = sites = 0
        for p in sorted(set(iter_files(group))):
            text = open(p, encoding="utf-8").read()
            n = sum(text.count(a) for a, _ in MAP)
            if not n:
                continue
            files += 1
            sites += n
            new = text
            for a, b in MAP:
                new = new.replace(a, b)
            if not rewrite:
                continue            # 不落的组不进对拍池（否则报的全是假悬空）
            after[p] = new
            if do:
                open(p, "w", encoding="utf-8").write(new)
        print(f"  {group}{'（改）' if rewrite else '（只数不改）'}: {files} 个文件 / {sites} 处")

    dangling = {}
    checked = 0
    for p, new in after.items():
        for m in TOK.finditer(new):
            tok = m.group(1).rstrip(".,;）)）」`\"'")
            if "*" in tok or "…" in tok or new[m.end():m.end() + 1] in ("*", "…"):
                continue
            checked += 1
            if not os.path.exists(os.path.join(REPO, tok)):
                dangling.setdefault(tok, set()).add(os.path.relpath(p, REPO))
    print(f"\n改后引证共 {checked} 条路径 token，其中仍指不到的 {len(dangling)} 种：")
    for tok, where in sorted(dangling.items())[:60]:
        print(f"   ⚠️ {tok}   ← {sorted(where)[:2]}")
    print("模式：", "已落盘" if do else "干跑（内存对拍，未写文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
