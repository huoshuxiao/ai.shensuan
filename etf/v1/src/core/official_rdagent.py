# -*- coding: utf-8 -*-
"""官方 RD-Agent(Q) 封装：conda 环境隔离 + 前置体检 + 子进程执行 + 产物回收

rdagent/pyqlib 的依赖树（pandas/numpy 版本钳制、litellm 等）与研究管线
所在的系统 python3.10 互不兼容，混装会互相污染（user-site 冲突已实际发生）。
因此官方循环一律经 `conda run -n <env> python rdagent_driver.py` 在独立
环境的子进程执行；本模块只做体检、拉起与产物回收。"""

import os
import json
import shlex
import time
import shutil
import subprocess
from config import (RESULTS_DIR, RDAGENT_CONDA_ENV, RDAGENT_TIMEOUT_SEC,
                    RDAGENT_QLIB_DOCKER_ENV)

# conda 未进 PATH 时的常见安装位
_CONDA_CANDIDATES = [
    os.path.expanduser("~/miniconda3/condabin/conda"),
    os.path.expanduser("~/miniconda3/bin/conda"),
    os.path.expanduser("~/anaconda3/condabin/conda"),
    os.path.expanduser("~/anaconda3/bin/conda"),
    "/opt/conda/bin/conda",
]

_QLIB_DATA = os.path.expanduser("~/.qlib/qlib_data/cn_data")

# 环境解释器不得看到 ~/.local 的 user-site（那里是系统 python3.10 的
# 管线依赖，与 rdagent 依赖树版本冲突），一切以环境内 site-packages 为准
_ENV_ISOLATED = {**os.environ, "PYTHONNOUSERSITE": "1"}

# 沙箱容器资源参数只影响 factor 循环本体，随驱动子进程一起注入
# （rdagent 侧 QlibDockerConf 用 QLIB_DOCKER_ 前缀读环境变量）
_ENV_DRIVER = {**_ENV_ISOLATED, **RDAGENT_QLIB_DOCKER_ENV}


def _find_conda():
    found = shutil.which("conda")
    if found:
        return found
    for p in _CONDA_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _env_probe(conda, env, code, timeout=90):
    """在目标 conda 环境内执行一段 python 探针，返回 (是否成功, 首行输出)"""
    try:
        r = subprocess.run(
            [conda, "run", "--no-capture-output", "-n", env,
             "python", "-c", code],
            capture_output=True, text=True, timeout=timeout,
            env=_ENV_ISOLATED)
        out = (r.stdout or r.stderr).strip().splitlines()
        return r.returncode == 0, out[-1][:120] if out else ""
    except FileNotFoundError:
        return False, "conda 不可执行"
    except subprocess.TimeoutExpired:
        return False, f"探针超时（>{timeout}s，多为 conda 冷启动卡顿）"


def _docker_info(argv):
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True,
                           timeout=20)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out:
            return True, f"daemon 可达 (server {out})"
        err = (r.stderr or "").strip().splitlines()
        return False, (err[-1][:120] if err else f"daemon 无响应 (rc={r.returncode})")
    except subprocess.TimeoutExpired:
        return False, "docker info 超时（daemon 未启动？）"
    except FileNotFoundError:
        return False, "docker 命令不可执行"


def _sg_docker(cmd_argv):
    """把任意命令包进 docker 组：sg 的 -c 吃一条 shell 字符串，
    其后所有参数会被拼成命令，故必须整体传一个引号串"""
    return [shutil.which("sg"), "docker", "-c",
            " ".join(shlex.quote(c) for c in cmd_argv)]


_DOCKER_INFO_FMT = "docker info --format '{{.ServerVersion}}'"


def _docker_access():
    """实测 /var/run/docker.sock 读权限。

    `which docker` 只证明二进制存在；rdagent 内部用 Python docker SDK
    直连 socket，权限不足时会在循环中途抛 PermissionError。usermod
    加组后旧会话（含 cron/子进程）不会自动获得新组，须经 `sg docker`
    包装。返回 (是否可用, 是否需 sg 包装, 说明)。"""
    exe = shutil.which("docker")
    if not exe:
        return False, False, "未找到 docker 二进制"
    ok, detail = _docker_info(
        [exe, "info", "--format", "{{.ServerVersion}}"])
    if ok:
        return True, False, detail
    sg = shutil.which("sg")
    if sg and "permission denied" in detail.lower():
        ok2, detail2 = _docker_info([sg, "docker", "-c", _DOCKER_INFO_FMT])
        if ok2:
            return True, True, ("当前会话未含 docker 组（usermod 后未重登录），"
                                "已改用 sg docker 提权: " + detail2)
        return False, False, f"sg docker 提权后仍不可用: {detail2}"
    return False, False, detail


_QIB_SANDBOX_IMAGE = "local_qlib:latest"


def _sandbox_image_ready():
    """qlib 回测沙箱镜像是否已在本地。

    rdagent 首次调用会在循环内部 `docker build` 该镜像（基础镜像来自
    docker hub）。docker hub 直连不通时这一步会静默重试很久才抛
    BuildError，故体检先确认镜像存在与否，把结论直接写进检查表。"""
    _, need_sg, _ = _docker_access()
    exe = shutil.which("docker")
    if not exe:
        return False, "未找到 docker 二进制"
    argv = [exe, "images", "-q", _QIB_SANDBOX_IMAGE]
    if need_sg:
        argv = _sg_docker(argv)
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        iid = (r.stdout or "").strip().splitlines()
        if r.returncode == 0 and iid and iid[0]:
            return True, f"{_QIB_SANDBOX_IMAGE} ({iid[0][:12]})"
        return False, (f"{_QIB_SANDBOX_IMAGE} 不存在；循环首次运行会尝试在线 "
                       "docker build（需可达的镜像源），建议先用 "
                       "data/results/rdagent_docker/Dockerfile 本地构建")
    except Exception as e:
        return False, f"镜像查询失败: {type(e).__name__}: {e}"


def rdagent_preflight(output_dir=None):
    """RD-Agent(Q) 官方 factor 循环的逐项前置检查。

    返回 [(依赖名, 是否就绪, 说明)]。检查对象是 conda 环境 `env`
    （而非本进程解释器）：rdagent/pyqlib 必须装在环境内，管线进程的
    user-site 副本不算数。任一缺失都让 factor 循环无法成立，
    先体检再决定运行与否，把原因写进日志而不是刷 traceback。"""
    checks = []
    conda = _find_conda()
    checks.append(("conda", bool(conda),
                   conda or f"PATH 与常见安装位均未找到"))
    env_name = RDAGENT_CONDA_ENV
    if conda:
        ok, detail = _env_probe(
            conda, env_name,
            "import importlib.metadata as m; "
            "print('rdagent ' + m.version('rdagent'))")
        checks.append((f"环境[{env_name}] rdagent", ok,
                       detail or "导入失败"))
        ok, detail = _env_probe(
            conda, env_name,
            "import qlib; print('pyqlib ' + qlib.__version__)")
        checks.append((f"环境[{env_name}] pyqlib", ok,
                       detail or "导入失败"))
    else:
        checks.append((f"环境[{env_name}] rdagent", False, "无 conda"))
        checks.append((f"环境[{env_name}] pyqlib", False, "无 conda"))
    ok, need_sg, detail = _docker_access()
    checks.append(("docker（生成代码沙箱）", ok, detail))
    if ok:
        ok, detail = _sandbox_image_ready()
        checks.append(("qlib 沙箱镜像", ok, detail))
    data_ok = os.path.isdir(_QLIB_DATA)
    checks.append(("qlib 行情数据", data_ok,
                   _QLIB_DATA if data_ok else
                   f"{_QLIB_DATA} 不存在（可 ln -s 指向已有 qlib_data）"))
    from llm_client import llm_available, describe_endpoint
    env_file = os.path.join(output_dir if output_dir else
                            f"{RESULTS_DIR}/rdagent_output", ".env")
    llm_ok = llm_available() or os.path.exists(env_file)
    llm_detail = (describe_endpoint() if llm_available()
                  else f"管线端点未配置；已用 RD-Agent 自身 {env_file}")
    checks.append(("LLM 端点", llm_ok, llm_detail))
    return checks


_PREFLIGHT = None  # 进程级缓存：walk-forward 逐折重入时不重复刷检查表


def try_official_rdagent(output_dir=f"{RESULTS_DIR}/rdagent_output"):
    global _PREFLIGHT
    checks = rdagent_preflight(output_dir)
    if (_PREFLIGHT is None
            or [(c[0], c[1]) for c in checks]
            != [(c[0], c[1]) for c in _PREFLIGHT]):
        print("  RD-Agent(Q) 前置检查:")
        for name, ok, detail in checks:
            print(f"    {'✅' if ok else '❌'} {name}: {detail}")
        _PREFLIGHT = checks
    missing = [name for name, ok, _ in checks if not ok]
    if missing:
        print(f"  ℹ️ RD-Agent(Q) 本次未运行（前置依赖缺失: "
              f"{'; '.join(missing)}）")
        return None

    conda = _find_conda()
    driver = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "rdagent_driver.py")
    os.makedirs(output_dir, exist_ok=True)
    cmd = [conda, "run", "--no-capture-output", "-n", RDAGENT_CONDA_ENV,
           "python", driver, "--out", output_dir]
    _, need_sg, _ = _docker_access()
    if need_sg:
        # sg 切换有效组后，conda 子进程及其内部 Python docker SDK
        # 继承该组，socket 可达
        cmd = _sg_docker(cmd)
    print(f"  ▶️ RD-Agent(Q) 子进程启动: {' '.join(cmd)}")
    print(f"     （工作目录 {output_dir}，超时上限 "
          f"{RDAGENT_TIMEOUT_SEC}s，产物实时回显如下）")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                cwd=output_dir, env=_ENV_DRIVER)
    except Exception as e:
        print(f"  ⚠️ 子进程拉起失败: {type(e).__name__}: {e}")
        return None
    start, timed_out = time.time(), False
    for line in proc.stdout:
        print("    [RD-Agent] " + line.rstrip())
        if time.time() - start > RDAGENT_TIMEOUT_SEC:
            proc.kill()
            timed_out = True
            break
    proc.wait()
    if timed_out:
        print(f"  ⚠️ 超时 {RDAGENT_TIMEOUT_SEC}s 已终止，保留部分产物")
        return None
    if proc.returncode != 0:
        print(f"  ⚠️ factor 循环退出码 {proc.returncode}")
        return None
    print("  ✅ RD-Agent(Q) factor 循环执行完毕，回收产物...")
    return _recover_factors(output_dir)


def _recover_factors(output_dir):
    """从环境子进程落盘的 JSON 中回收因子表达式"""
    candidates = [
        os.path.join(output_dir, "factors.json"),
        os.path.join(output_dir, "result.json"),
        os.path.join(output_dir, "latest", "factors.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            factors = []
            for i, item in enumerate(data):
                factors.append({
                    "name": item.get("name", f"official_{i}"),
                    "expr": item.get("expr", item.get("expression", "")),
                    # 驱动侧写的是 mean_ic/icir，历史文件里也叫过 IC/ic，
                    # 三种键名都读一遍，否则真跑出来的 IC 会被静默当成 0
                    "mean_ic": item.get("mean_ic", item.get("IC",
                               item.get("ic", 0.0))),
                    "icir": item.get("icir", item.get("ICIR",
                          item.get("rank_icir", 0.0))),
                    # LaTeX 原式：驱动侧翻译为管线 DSL 后的核对凭据，
                    # 随因子一路带到因子库/报告，供人工复核语义
                    "formulation": item.get("formulation", ""),
                })
            print(f"  ✅ 官方产出 {len(factors)} 个因子（{path}）")
            return factors
    print("  ⚠️ 循环完成但未在产出目录找到 factors.json/result.json，"
          "详见子进程日志与 rdagent 会话目录")
    return None
