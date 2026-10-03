# -*- coding: utf-8 -*-
"""一次性：撤销「人工下架」——把 09-29 丁2 标成 inactive 的 9 条放回 active（用户 09-29 裁「甲」）

动的是**生产因子库**（`etf/v1/data/library/`），走的是库自己的写入路径
（`FactorLibrary.mark_status` + `save_markdown`），所以落盘必然带一次 `[factor-lib]`
提交（09-27 起那个提交带 pathspec，只圈 library 那三个文件）。

命中口径（只认这一族，宁漏不误）：`status_reason` 以 `丁2 库内判重` 开头 **且** 当前
`status == "inactive"`。别的条目一个字节不许动 ⇒ 脚本自己逐键对表取证。

跑法：
    /usr/bin/python3.10 etf/v1/temp/undo_manual_delist_0929.py           # 只念数，不落盘
    /usr/bin/python3.10 etf/v1/temp/undo_manual_delist_0929.py --apply   # 真按「甲」落盘
"""
import hashlib
import json
import os
import subprocess
import sys

SRC = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "src"))
sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402,F401
import config  # noqa: E402
from factor_library import FactorLibrary  # noqa: E402

TAG = "丁2 库内判重"
REASON = ("09-29 撤销人工下架功能：判重不再由链路代管，状态回到 active"
          "（理由留档见 CHANGELOG 丁2 同名条目）")
APPLY = "--apply" in sys.argv[1:]


def artifacts():
    """库的三个落盘产物 = 那次 [factor-lib] 提交圈定的 pathspec 本身，不另抄一份"""
    g = config.FACTOR_LIBRARY_GIT
    return [os.path.join(g["git_dir"], *f.split("/")) for f in g["tracked_files"]]


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else "缺"


def snap_index():
    """名字 -> 整条记录（读 json 那份，即库的权威状态；深拷贝语义由 json.load 天然满足）"""
    return json.load(open(config.FACTOR_LIBRARY["index_path"], encoding="utf-8"))


def head_commit():
    return subprocess.run(["git", "-C", os.path.dirname(os.path.dirname(SRC)),
                           "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


before_files = {p: sha(p) for p in artifacts()}
before = snap_index()
head0 = head_commit()
c0 = sum(1 for v in before.values() if v.get("status") == "active")
hit = sorted(n for n, v in before.items()
             if str(v.get("status_reason", "")).startswith(TAG)
             and v.get("status") == "inactive")
print(f"[动笔前] {len(before)} 总 / {c0} 活跃｜HEAD {head0}")
print(f"[命中]   {len(hit)} 条带「{TAG}」前缀且当前 inactive：{'、'.join(hit) or '（无）'}")
print(f"[不该动] {len(before) - len(hit)} 条（含本来就是 active 的带前缀条目，若有）")

if not APPLY:
    print("[只读] 没带 --apply ⇒ 一个字节未动。真落盘：加 --apply")
    sys.exit(0)

lib = FactorLibrary(config.FACTOR_LIBRARY)
for n in hit:
    lib.mark_status(n, "active", REASON)
# 命中 0 条时不落盘：`save_markdown()` 会重写三个文件并可能空转一次提交判定，
# 而"没有要撤的条目"本来就该是一个字节不动的终态（幂等第二次走这条路）。
if hit:
    lib.save_markdown()

after_files = {p: sha(p) for p in artifacts()}
after = snap_index()
head1 = head_commit()
c1 = sum(1 for v in after.values() if v.get("status") == "active")

# ───────── 取证：三条都得是"能红的"判据 ─────────
errs = []
# 1) 变化面恰好等于命中集合：非命中条目逐键一字不变
changed = sorted(n for n in before if before[n] != after.get(n))
if changed != hit:
    errs.append(f"变化面 {len(changed)} 条 != 命中 {len(hit)} 条"
                f"｜多动的：{sorted(set(changed) - set(hit))}"
                f"｜没动到的：{sorted(set(hit) - set(changed))}")
for n in set(changed) - set(hit):
    errs.append(f"越界条目 {n} 被改了")
# 2) 命中条目只许动 status / status_reason / status_time 这三格
for n in hit:
    keys = {k for k in set(before[n]) | set(after[n]) if before[n].get(k) != after[n].get(k)}
    if not keys <= {"status", "status_reason", "status_time"}:
        errs.append(f"{n} 动了不该动的字段：{sorted(keys - {'status', 'status_reason', 'status_time'})}")
    if after[n].get("status") != "active":
        errs.append(f"{n} 落盘后仍不是 active（{after[n].get('status')}）")
# 3) 库回到「全 active」，且不再有 丁2 前缀的 inactive 条目
if c1 != len(after):
    errs.append(f"活跃数 {c1} != 总数 {len(after)}")
leftover = [n for n, v in after.items()
            if str(v.get("status_reason", "")).startswith(TAG)
            and v.get("status") != "active"]
if leftover:
    errs.append(f"仍带丁2前缀且非 active：{leftover}")
if hit and config.FACTOR_LIBRARY_GIT["enabled"] \
        and config.FACTOR_LIBRARY_GIT["auto_commit"] and head1 == head0:
    errs.append(f"落盘了却没有 [factor-lib] 提交（HEAD 还是 {head0}）"
                "⇒ 说明 save_markdown 被 enabled/auto_commit 挡住了")
if not hit and head1 != head0:
    errs.append(f"命中 0 条却起了提交（{head0} → {head1}）⇒ 落盘闸没拦住空转")

print(f"[动笔后] {len(after)} 总 / {c1} 活跃｜HEAD {head1}")
for p in artifacts():
    print(f"         {os.path.basename(p):<32}{before_files[p][:12]} → {after_files[p][:12]}"
          + ("　（字节不变）" if before_files[p] == after_files[p] else ""))
if errs:
    print("[FAIL] " + "\n[FAIL] ".join(errs))
    sys.exit(1)
print(f"[PASS] 命中 {len(hit)} 条"
      + (f"全部放回 active" if hit else "0 条 ⇒ 幂等第二遍，三个文件字节未变")
      + f"；其余 {len(before) - len(hit)} 条逐键未变；HEAD {head0} → {head1}")
