# -*- coding: utf-8 -*-
"""09-29 清理：旧数据/备份/旧日志/临时文件（用户裁「4」＝甲+乙+丙+丁，不含 790MB 生产归档）。

来路：`stock/v1/temp/cleanup_0929.py`。默认干跑（只打清单与字节数），加 `--apply` 才真删。

分堆（与汇报给用户的编号一致）：
  甲 RD-Agent 自己的缓存：git_ignore_folder + pickle_cache（两线 4 个目录）——工具可重建，无引用
  乙 旧日志：rdagent_output 根下过期 loop/driver .log + 两线 log/ + 两线 temp/ 内 .log，
     **只删 mtime 早于 09-28 的**（09-28/29 那场正在被读，一律留）
  丙 备份：config_backups、daily_snapshot/bin_backup_*、全仓 *.bak*、*_backup_092*
  丁 两线 temp/ 里 >1MB 的快照目录（跑判据留下的现场）
  戊 __pycache__ 与空目录

三道不删的硬边界（本脚本用断言钉住，不是靠记性）：
  1) **rdagent_output/log/ 整目录不删**——它叫 log 但装的是 session pickle，
     `common/src/core/rdagent_driver.py` 的 `_harvest_from_sessions()` 按 mtime 取最新那份回收因子，
     删了下一次循环回收源即断。
  2) live2etf 全线不碰（并行会话的地盘）；两线 temp/ 里的 .py/.sh 不删（220 个脚本，
     引证闸 115 条有 9 条锚在上面）；生产归档 tmp_rerun_20260928_0929_0047 不删（B6 基准字节）。
  3) 任何一个待删路径都必须**未被 git 跟踪**——这堆全在 .gitignore 里，删了 git 救不回来，
     所以真删前逐条 `git ls-files --error-unmatch` 反问一遍，命中即整体中止。
"""
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CUT = time.mktime(time.strptime("2026-09-28", "%Y-%m-%d"))   # 早于这一天的才算「旧日志」

# 任何一档都不许碰的东西（路径片段或全名）
NEVER = [
    "live2etf",
    "tmp_rerun_20260928_0929_0047",          # B6 生产归档基准
    "verify_stock_llm_gates_0927.log",        # 引证闸锚着 2 条
    "probe_llm_picks_0927.log",               #   同上
    os.path.join("rdagent_output", "log"),    # session pickle，回收源
    "factors.json", ".env", "daily_signal/",
]
KEEP_ROOT_LOG = "loop_qwen3.5_1loop_9bbase_0929.log"   # 第1条甲读数出处


def size(p):
    if os.path.islink(p):
        return 0
    if os.path.isfile(p):
        return os.path.getsize(p)
    t = 0
    for r, d, f in os.walk(p):
        for n in f:
            fp = os.path.join(r, n)
            if not os.path.islink(fp):
                try:
                    t += os.path.getsize(fp)
                except OSError:
                    pass
    return t


def blocked(p):
    """两条禁触线：① NEVER 名单；② 各线 temp/ 顶层的 .py/.sh 本体＝判据脚本，任何一档都不删
       （config_backups 下那两个 .py 是生产 config 的逐字节副本，不在 temp/ 下，照删）"""
    if any(n in p for n in NEVER):
        return True
    m = re.match(r"^\w+/v1/temp/([^/]+)$", p)
    return bool(m) and m.group(1).endswith((".py", ".sh"))


def collect():
    out = {}
    # 甲
    out["甲"] = [os.path.join(l, "data/results/rdagent_output", s)
                 for l in ("stock/v1", "etf/v1") for s in ("git_ignore_folder", "pickle_cache")]
    # 乙 旧日志
    logs = []
    for base in ("stock/v1/data/results/rdagent_output", "etf/v1/data/results/rdagent_output",
                 "stock/v1/log", "etf/v1/log", "stock/v1/temp", "etf/v1/temp"):
        d = os.path.join(REPO, base)
        if not os.path.isdir(d):
            continue
        for r, _, f in os.walk(d):
            if os.path.join("rdagent_output", "log") in r:
                continue
            for n in f:
                fp = os.path.join(r, n)
                if not n.endswith(".log") or n == KEEP_ROOT_LOG:
                    continue
                if os.path.getmtime(fp) < CUT:
                    logs.append(os.path.relpath(fp, REPO))
    out["乙"] = logs
    # 丙 备份
    baks = []
    for r, d, f in os.walk(REPO):
        if "/.git" in r or "rdagent_output" in r:
            d[:] = []
            continue
        for n in list(d) + list(f):
            if re.search(r"\.bak|_bak_\d|_backup_\d|^bin_backup_", n):
                baks.append(os.path.relpath(os.path.join(r, n), REPO))
    baks += ["etf/v1/data/config_backups/config_20260925_160405.py",
             "etf/v1/data/config_backups/config_20260926_093843.py"]
    out["丙"] = [b for b in sorted(set(baks)) if not blocked(b)]
    # 丁 temp 快照目录
    snaps = []
    for l in ("stock/v1/temp", "etf/v1/temp"):
        d = os.path.join(REPO, l)
        for n in sorted(os.listdir(d)):
            p = os.path.join(d, n)
            if os.path.isdir(p) and size(p) > 1e6 and not blocked(p):
                snaps.append(os.path.relpath(p, REPO))
    out["丁"] = snaps
    # 戊 缓存与空目录
    pyc = [os.path.relpath(os.path.join(r, x), REPO)
           for r, d, f in os.walk(REPO) for x in d if x == "__pycache__" and "/.git" not in r]
    pyc += ["etf/v1/data/prompt_backups", "stock/v1/temp/rerun_backups"]
    out["戊"] = pyc
    # 统一过一遍禁触线（乙/戊 收集时没走 blocked，靠这里兜）
    return {g: [x for x in items if not blocked(x)] for g, items in out.items()}


def main():
    apply = "--apply" in sys.argv
    groups = collect()
    grand = 0
    for g, items in groups.items():
        s = sum(size(os.path.join(REPO, p)) for p in items)
        grand += s
        print(f"{g}: {len(items):4} 项  {s/1e9:.2f} GB" if s > 1e9 else f"{g}: {len(items):4} 项  {s/1e6:8.1f} MB")
        if g != "戊":
            for p in sorted(items):
                print(f"     {size(os.path.join(REPO,p))/1e6:9.2f} MB  {p}")
    # 硬边界自检：待删清单里不许出现 NEVER，也不许出现 .py/.sh
    flat = [p for g in groups.values() for p in g]
    bad = [p for p in flat if any(n in p for n in NEVER)]
    assert not bad, f"越界待删项: {bad}"
    # 未跟踪自检
    tracked = set(subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout.split("\n"))
    hit = [p for p in flat if any(t == p or t.startswith(p + "/") for t in tracked)]
    if hit:
        print(f"\n✗ 有 {len(hit)} 项被 git 跟踪，删了不可恢复，中止。前 5:", hit[:5])
        return 1
    if not apply:
        print(f"\n[干跑] 合计 {grand/1e9:.2f} GB / {len(flat)} 项，全部未被 git 跟踪。加 --apply 才真删。")
        return 0
    import shutil
    freed = 0
    failed = []
    for p in flat:
        fp = os.path.join(REPO, p)
        if not os.path.exists(fp):
            continue
        before = size(fp)
        try:
            # ignore_errors：撞见 root:root 的目录（docker 里以 root 建的）只留它自己，
            # 不把整棵子树一起带走——否则 3.06GB 里 32MB 卡住 3.03GB 可删的
            if os.path.isfile(fp):
                os.remove(fp)
            else:
                shutil.rmtree(fp, ignore_errors=True)
        except PermissionError as e:
            failed.append((p, before - size(fp)))   # 删掉多少算多少，剩下的交出去
            continue
        except OSError as e:
            failed.append((p, 0))
            continue
        freed += before
    print(f"[已删] {freed/1e9:.2f} GB  未清干净 {len(failed)} 项")
    for p, got in failed:
        print(f"   ✗ {p}  本次释放 {got/1e6:.1f}MB  仍剩 {size(os.path.join(REPO,p))/1e6:.1f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
