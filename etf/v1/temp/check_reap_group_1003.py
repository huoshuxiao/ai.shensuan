# -*- coding: utf-8 -*-
"""C2/C3 的牙：驱动自成进程组 + 超时定向收尸（10-03）

背景（用户裁「三 / B4 / C2/C3」）：官方支线超时那句用的是 `proc.kill()`，而命令
被 `sg docker -c "conda run …"` 包了两层壳 ⇒ kill 只杀得到最外壳，驱动 python 变
孤儿（10-03 实测主线 rc=0 之后又活了 2h23m，期间还在往 rdagent_output 写 pkl，
更坏的是它将来会写 factors.json，于是下一场回收读到的是上一场孤儿的成品）。
修法两处：C3＝能直调环境内解释器就不走 `conda run`（拔掉一层壳）；
C2＝`Popen(start_new_session=True)` + 超时走 `killpg(SIGTERM → 宽限 → SIGKILL)`。

判据形状（六条臂，每条都可能失败）：
  Q1 直调解释器找得到，且真的落在本线那个 conda 环境目录里。
  Q2 负对照：环境名换成不存在的 ⇒ 必须回 None（证明「退回 conda run」那条分支
     是活的，不是写来好看的）。
  Q3 直调的等价性——这是 C3 唯一真风险：不经过 `conda run` 的 activate，环境内
     的 rdagent/qlib/litellm/dotenv 还 import 得到吗？user-site 还被隔离吗？
     （探针在真子进程里跑，同进程改环境变量是假臂。）
     Q3d 是这一格的正对照：同一解释器**不设** PYTHONNOUSERSITE 时 user-site
     必须漏进 sys.path（10-03 实测会漏，那条路径真实存在），否则「隔离生效」
     只是扫不到东西的恒真。
  Q4 核心：一棵三层树（sh + 两个孙 sleep）自成一组 ⇒ `_reap_group` 之后
     **组内一个不剩**，包括那两个从没被 Popen 直接握住的孙进程。
  Q5 负对照（给 Q4 装牙）：同一棵树、老写法 `proc.kill()` ⇒ 孙进程必须**还活着**
     ——Q4 之所以是绿的不是因为判据松，是因为 killpg 真的多杀到了它们。
     活下来的孤儿由本夹具自己 SIGKILL 收掉（都是自己起的进程，不碰别人）。
  Q6 宽限期那条分支：子进程对 SIGTERM 装聋（signal.SIG_IGN）⇒ 必须走到
     「SIGTERM 未生效→SIGKILL」，且最终真的消失。

用法：/usr/bin/python3.10 etf/v1/temp/check_reap_group_1003.py
"""
import os
import subprocess
import sys
import time

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"

ok = 0
fail = 0


def say(cond, label, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✅ {label}")
    else:
        fail += 1
        print(f"  ✗ {label}  {detail}")
    return cond


def pg_members(pgid, marker):
    """进程组里 cmdline 含 marker 的 pid 列表（marker 用四位数，避免撞别的进程）"""
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().decode("utf-8", "replace").replace("\0", " ")
            if os.getpgid(pid) != pgid or marker not in cmd:
                continue
            pids.append(pid)
        except (OSError, ProcessLookupError):
            continue
    return pids


def kill_pids(pids, why):
    """只收本夹具自己起的那些 pid（逐个 kill，绝不 killpg 夹具所在组）"""
    for pid in pids:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    if pids:
        print(f"     （清理 {why}: {pids}）")


def launch_sh(marker_a, marker_b, new_session):
    """起一棵三层树：sh 自己 + 两个孙 sleep（标记数字唯一，供 pg_members 认人）"""
    script = f"sleep {marker_a} & sleep {marker_b} & wait"
    kw = {}
    if new_session:
        kw["start_new_session"] = True
    return subprocess.Popen(["/bin/sh", "-c", script],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kw)


def arm_finder(o, config):
    print("== Q1/Q2：直调解释器（C3）==")
    conda = o._find_conda()
    env_py = o._find_env_python(conda)
    env_name = str(config.RDAGENT_CONDA_ENV)
    if not say(bool(env_py), "Q1a 找得到环境内解释器", "实得 None"):
        return None
    say(os.path.basename(env_py) == "python",
        "Q1b 拿到的是 python 本体而不是 conda 包装", f"实得 {env_py!r}")
    say(f"envs/{env_name}/" in env_py.replace(os.path.expanduser("~"), "~"),
        f"Q1c 确实落在本线那个环境（{env_name}）目录里", f"实得 {env_py!r}")
    # Q2 负对照：把模块全局的环境名换成不存在的（_find_env_python 读的就是它）
    saved = o.RDAGENT_CONDA_ENV
    try:
        o.RDAGENT_CONDA_ENV = "nope-env-1003-not-exist"
        none_val = o._find_env_python(conda)
        say(none_val is None,
            "Q2 环境名不存在 ⇒ 如实回 None（退回 conda run 那条分支是活的）",
            f"实得 {none_val!r}")
    finally:
        o.RDAGENT_CONDA_ENV = saved
    return env_py


def arm_equivalence(env_py):
    print("== Q3：直调 == conda run 的等价性（C3 唯一真风险）==")
    code = (
        "import sys, importlib.util as u;"
        "print('PREFIX', sys.prefix);"
        "[print('SITEPATH', p) for p in sys.path if '.local/lib' in p];"
        "print('DEPS', ' '.join(m + ':' + str(bool(u.find_spec(m)))"
        " for m in ('rdagent','qlib','litellm','dotenv')))"
    )

    def probe(extra_env):
        p = subprocess.run([env_py, "-c", code], capture_output=True, text=True,
                           timeout=180, env=extra_env)
        kv, site = {}, []
        for line in p.stdout.splitlines():
            if line.startswith(("PREFIX", "DEPS")):
                key, _, val = line.partition(" ")
                kv[key] = val
            elif line.startswith("SITEPATH"):
                site.append(line.split(" ", 1)[1])
        return p, kv, site

    # 与生产同一构造：`_ENV_ISOLATED = {**os.environ, "PYTHONNOUSERSITE": "1"}`
    iso = {**os.environ, "PYTHONNOUSERSITE": "1"}
    bare = {k: v for k, v in os.environ.items() if k != "PYTHONNOUSERSITE"}
    p, kv, site = probe(iso)
    if not say(p.returncode == 0, "Q3a 直调解释器能正常跑探针",
               f"rc={p.returncode} {p.stderr[-200:]}"):
        return
    say("/envs/" in kv.get("PREFIX", "") and "base" not in kv.get("PREFIX", "").rstrip("/").split("/")[-1],
        "Q3b sys.prefix 落在环境内（不是 miniconda base）",
        f"实得 {kv.get('PREFIX')!r}")
    say(kv.get("DEPS", "").count(":True") == 4,
        "Q3c rdagent/qlib/litellm/dotenv 四个都能 import（activate 不是必需的）",
        f"实得 {kv.get('DEPS')!r}")
    # 正对照：同一解释器**不设** PYTHONNOUSERSITE 时 user-site 必须漏进来，
    # 否则「隔离生效」那一格只是扫不到东西的恒真
    p2, _, site2 = probe(bare)
    say(p2.returncode == 0 and site2,
        "Q3d 正对照——不设隔离位时 user-site 确实混进 sys.path",
        f"rc={p2.returncode} 实得 {site2}")
    say(not site,
        "Q3e 生产那套隔离位（PYTHONNOUSERSITE=1）下 user-site 仍在门外",
        f"实得 {site}")


def arm_reap(o):
    print("== Q4：killpg 真收整组（含孙进程）==")
    proc = launch_sh(4111, 4112, new_session=True)
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        say(False, "Q4a 起的子进程立刻没了")
        return
    # 标记「sleep 41」同时命中外壳 sh 的命令行与两个孙进程 ⇒ 正好三个人
    # （外壳 fork 两个孙需要零点几秒，不等它会数到 1 而假红——10-03 第二次跑实测到）
    deadline = time.time() + 5.0
    before = pg_members(pgid, "sleep 41")
    while len(before) < 3 and time.time() < deadline:
        time.sleep(0.2)
        before = pg_members(pgid, "sleep 41")
    say(len(before) == 3, "Q4a 动手前组里确实是三个人（sh + 两个孙 sleep）",
        f"实得 {before}")
    t0 = time.time()
    note = o._reap_group(proc, grace=10)
    time.sleep(0.5)          # 只等信号送达，孙进程被 SIGTERM 即死，不会「自己到期」
    left = pg_members(pgid, "sleep 41")
    say(not left, "Q4b 收尸后组内一个不剩（两个孙 sleep 也被带走）",
        f"残留 {left}")
    say(proc.poll() is not None,
        "Q4c 外壳自己也已退出", f"poll={proc.poll()}")
    print(f"     收尸用了 {time.time() - t0:.1f}s，读数：{note}")


def arm_old_way_has_teeth():
    """Q5 负对照：老写法杀不到孙进程 ⇒ Q4 的绿不是判据松"""
    print("== Q5：负对照——老写法 proc.kill() 杀不到孙进程 ==")
    proc = launch_sh(4211, 4212, new_session=False)
    time.sleep(1.0)
    proc.kill()
    proc.wait()
    survivors = pg_members(os.getpgid(os.getpid()), "sleep 42")
    if not survivors:
        survivors = _scan_marker("sleep 4211") + _scan_marker("sleep 4212")
    say(len(survivors) >= 1,
        "Q5 proc.kill() 之后孙进程仍在运行（这就是 10-03 那枚孤儿的形状）",
        f"实得残留 {survivors}")
    kill_pids(survivors, "Q5 留下的孤儿（本夹具自己起的）")


def _scan_marker(literal):
    """按**字面**子串扫 /proc 的 cmdline（调用方自己保证串形与命令行一致）

    Q6 原先传的是数字 4311、这里拼成 "sleep 4311"，而桩进程的命令行写的是
    `time.sleep(4311)`（没有空格）⇒ 那一格恒真。改成字面匹配，拼串交给调用方。
    """
    hits = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                cmd = fh.read().decode("utf-8", "replace").replace("\0", " ")
        except OSError:
            continue
        if literal in cmd:
            hits.append(int(entry))
    return hits


def arm_sigterm_stub(o):
    """Q6：子进程对 SIGTERM 装聋 ⇒ 必须走宽限期→SIGKILL 那条分支"""
    print("== Q6：SIGTERM 无效时走宽限期→SIGKILL ==")
    # 标记放在 argv 尾部 ⇒ /proc 的 cmdline 里有它的**字面**形态，能被 _scan_marker 认到
    marker = "reapstub4311"
    code = ("import signal, time;"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            "time.sleep(300)")
    proc = subprocess.Popen([sys.executable, "-c", code, marker],
                            stdout=subprocess.DEVNULL, start_new_session=True)
    time.sleep(2.0)
    before = _scan_marker(marker)
    # 先证明「尺子数得到它」：没这一步，Q6b 的「残留为空」可能只是从来没扫到过
    if not say(len(before) == 1,
               "Q6a 动手前装聋的桩进程确实在跑（且扫描数得到）",
               f"实得 {before}") or proc.poll() is not None:
        kill_pids(before, "Q6 未能起稳的桩")
        return
    pgid = os.getpgid(proc.pid)
    say(pgid != os.getpgid(os.getpid()),
        "Q6b 桩进程自成一组（不是夹具自己那组）",
        f"桩组 {pgid} vs 夹具组 {os.getpgid(os.getpid())}")
    note = o._reap_group(proc, grace=3)
    say("SIGKILL" in note,
        "Q6c 读数如实写成走了 SIGKILL 那一步（宽限期那条分支是活的）",
        f"实得 {note!r}")
    left = _scan_marker(marker)
    say(not left, "Q6d 装聋的子进程最终被收掉", f"残留 {left}")
    kill_pids(left, "Q6 残留（本夹具自己起的桩）")


def main():
    print("== C2/C3 收尸夹具（10-03）==")
    sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
    import _bootstrap  # noqa: F401  平铺导入的挂路
    import config
    import official_rdagent as o

    env_py = arm_finder(o, config)
    if env_py:
        arm_equivalence(env_py)
    arm_reap(o)
    arm_old_way_has_teeth()
    arm_sigterm_stub(o)
    print(f"合计 通过 {ok}，不通过 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
