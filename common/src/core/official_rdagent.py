# -*- coding: utf-8 -*-
"""官方 RD-Agent(Q) 封装：conda 环境隔离 + 前置体检 + 子进程执行 + 产物回收

rdagent/pyqlib 的依赖树（pandas/numpy 版本钳制、litellm 等）与研究管线
所在的系统 python3.10 互不兼容，混装会互相污染（user-site 冲突已实际发生）。
因此官方循环一律在独立 conda 环境的子进程里执行：优先直调环境内解释器
（`<root>/envs/<env>/bin/python rdagent_driver.py`，进程树只有一层，超时收得到
驱动本身），找不到该解释器才退回 `conda run -n <env> python …`；本模块只做
体检、拉起、超时收尸与产物回收。"""

import os
import json
import pickletools
import shlex
import signal
import time
import shutil
import subprocess
from config import (RDAGENT_OUTPUT_DIR, RDAGENT_CONDA_ENV, DATA_DIR,
                    RDAGENT_TIMEOUT_SEC, RDAGENT_SOURCE_DIR,
                    RDAGENT_COSTEER_MAX_LOOP, RDAGENT_COSTEER_KB_PATH,
                    RDAGENT_LLM_MODEL,
                    RDAGENT_LLM_KWARGS, LLM_REASONING_EFFORT, LLM_NUM_CTX,
                    RDAGENT_QLIB_DOCKER_ENV, RDAGENT_QLIB_PROVIDER)

# conda 未进 PATH 时的常见安装位
_CONDA_CANDIDATES = [
    os.path.expanduser("~/miniconda3/condabin/conda"),
    os.path.expanduser("~/miniconda3/bin/conda"),
    os.path.expanduser("~/anaconda3/condabin/conda"),
    os.path.expanduser("~/anaconda3/bin/conda"),
    "/opt/conda/bin/conda",
]


# 环境解释器不得看到 ~/.local 的 user-site（那里是系统 python3.10 的
# 管线依赖，与 rdagent 依赖树版本冲突），一切以环境内 site-packages 为准
_ENV_ISOLATED = {**os.environ, "PYTHONNOUSERSITE": "1"}

# 甲-2 那把知识库旋钮的「本场到底怎么接的」一句话读数，由 _driver_env 写、
# try_official_rdagent 打印。留空（股票线）时这里永远是空串、一个字都不打。
_COSTEER_KB_NOTE = ""


def _kb_pickle_class(path):
    """从知识库 pickle 的**字节**里读顶层类名，不 import、不反序列化（甲-2 前置体检）。

    为什么不能直接 `pickle.load`：rdagent 只装在 conda 环境里，父进程（系统
    python3.10）import 不到那个模块，unpickle 会在「找不到类」这一步就炸；而
    `pickletools.genops` 只解析 opcode、不执行被序列化对象的 `__reduce__`，
    既读得出版本又不 import 任何东西（顺带避开反序列化注入面）。

    为什么必须在起场前读出来：site-packages
    `components/coder/CoSTEER/knowledge_management.py:63-76` 拿到不是
    `CoSTEERKnowledgeBaseV2` 的对象时**直接抛 ValueError**，整场崩在 coding 之前、
    一次 LLM 都不会发生（10-04 构造级探针 D 臂实测：放一份 V1 进读路径＝
    `ValueError: The former knowledge base is not compatible with the current version`）。
    顶层对象的类名是字节流里**第一个** `CoSTEERKnowledgeBase*`，取到即可判版本。
    ⚠️ 它有两种 opcode 写法：协议 ≤3 是 `GLOBAL`（操作数直接是 (模块名, 类名)），
    协议 ≥4 是 `STACK_GLOBAL`（先把两个字符串压栈，操作数为 None）。rdagent 那句
    `pickle.dump(...)` 没传 protocol ⇒ 跟解释器默认协议走（本机 python3.10＝协议 5，
    实测落的是 STACK_GLOBAL）。10-05 只认 `GLOBAL` 的第一版判据会被自己的夹具抓出来：
    真品读不出版本 ⇒ 每场都悄悄降成「只写不读」，甲-2 就成了没牙的改动。
    ⚠️ `pickletools.genops` 在本机 3.10 上吐的是 `(opcode, arg, pos)` 三元组
    （带 `.name` 的 OpcodeInfo 是 3.14 才有的），而 `GLOBAL` 的操作数在 3.10 上是
    **一整串** `"模块名 类名"`、在别的版本上是二元组 ⇒ 两种都要拆（第一版按
    `arg[1]` 取下标，拿到的是模块名的第 2 个字符，判据同样没牙）。
    """
    def _text(x):
        return x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x)

    def _pair(x):
        """把 GLOBAL 的操作数归一成 (模块名, 类名)；拆不出两段就返回 None"""
        if isinstance(x, (tuple, list)) and len(x) == 2:
            return _text(x[0]), _text(x[1])
        x = _text(x)
        if " " in x:
            module, _, name = x.rpartition(" ")
            return module, name
        return None

    try:
        strings = []          # STACK_GLOBAL 的两个压栈操作数（模块名, 类名）
        with open(path, "rb") as fh:
            for code, arg, _pos in pickletools.genops(fh):
                if code.name in ("UNICODE", "BINUNICODE", "SHORT_BINUNICODE",
                                 "BINUNICODE8"):
                    strings.append(_text(arg))
                    del strings[:-2]
                    continue
                if code.name == "GLOBAL":
                    pair = _pair(arg)
                    if pair:
                        strings = list(pair)
                elif code.name != "STACK_GLOBAL":
                    continue
                # STACK_GLOBAL 的类名就是压栈的最后一个字符串；GLOBAL 已归一成同形
                if len(strings) == 2 and strings[1].startswith(
                        "CoSTEERKnowledgeBase"):
                    return strings[1]
    except Exception as e:      # noqa: BLE001 读不动/非 pickle 都归为「不认」
        return f"（读不出来：{type(e).__name__}）"
    return "（字节流里没有 CoSTEERKnowledgeBase 类名）"

def _driver_env():
    """驱动子进程环境：沙箱容器参数 + 可选的演化轮数 + 可选的模型注入。

    容器参数只影响 factor 循环本体（rdagent 侧 QlibDockerConf 用 QLIB_DOCKER_
    前缀读环境变量）。CoSTEER_MAX_LOOP 与 LITELLM_CHAT_MODEL 走同一批发出去：
    rdagent_driver 用 load_dotenv(".env") 且默认不覆盖已有环境变量，所以在父进程
    这里给值就压过工作区 .env 那份手改值（ETF 线用它把轮数从 4 提到 8）；配置留空
    则不注入，沿用 .env，避免凭空盖住本地设置。

    CONDA_DEFAULT_ENV 与 conda 所在目录是**直调解释器之后欠下的两笔**（10-03 夜
    拔 `conda run` 那层壳换来超时可整组收尸，代价是 conda 顺手填的那两个东西没了）：
      ① rdagent 的 `CondaConf.conda_env_name` 是必填 str，值只从 CONDA_DEFAULT_ENV
         取（components/coder/factor_coder/config.py:40）⇒ 空就是 pydantic 判红，
         构造 FactorRDLoop 当场崩（10-03 23:35 那场，8 秒退出、0 次 LLM 调用）。
      ② 它的 validator 再拿 **shell 里的 `conda`** 去要该环境的 PATH
         （utils/env.py:616-622），**找不到 conda 不报错、静默给空串**，而 LocalEnv
         执行 LLM 生成的因子代码时把这个空串拼在 PATH 最前（utils/env.py:524）
         ⇒ 代码会拿系统 python 跑、qlib 导不进，循环一路"实现失败"却不崩。
      ③ 10-04 那场又量出第三笔、也是真正卡死产出的一笔：factor 求值走的是
         `FactorCoSTEERSettings.python_bin`，默认值就是字面量 `"python"`，而
         `factor.py:execute()` 用 `subprocess.check_output("python <code>", shell=True)`
         **直接吃驱动进程的 PATH**。只挂 condabin（那里只有 `conda` 一个文件）
         ⇒ 本机 `/usr/bin` 又只有 `python3`、没有 `python` ⇒ 每个因子任务都返回
         `/bin/sh: 1: python: not found` ⇒ 读不到 `result.h5` ⇒ 打印
         `No factor value generated` ⇒ 让 LLM 去批一份根本没跑过的代码。
         10-03 23:46 直调解释器之后三场（10-03 夜、10-04 中午、10-04 傍晚）净增全 0，
         病根就是这一条，与提示词/窗口/索引形状无关。
    所以三个都给：环境名照本线配置，`conda` 目录与**环境自己的 `bin`** 一起挂 PATH 最前
    （后者才装得出 `python`，实测 pandas 2.2.0 + qlib 可 import）。

    甲-2（10-04 深夜裁）那把知识库旋钮与上面三笔 PATH 欠账无关，规则只有一句：
    `RDAGENT_COSTEER_KB_PATH` 留空＝一个键都不发、行为与 10-04 之前逐字节相同；
    给了路径＝读与写指同一个文件（这一场从上一场写过的实现起步）；文件已存在但顶层
    类不是 V2 ⇒ 当场降为**只写不读**，收场的 dump 会把它覆写成合法 V2、下一场自愈
    （判版本与理由见 `_kb_pickle_class`）。
    """
    env = {**_ENV_ISOLATED, **RDAGENT_QLIB_DOCKER_ENV}
    env["CONDA_DEFAULT_ENV"] = RDAGENT_CONDA_ENV
    conda = _find_conda()
    if conda:
        conda_dir = os.path.dirname(os.path.abspath(conda))
        # condabin 或 …/bin：两者上一层都是 conda 安装根
        env_bin = os.path.join(os.path.dirname(conda_dir), "envs",
                               RDAGENT_CONDA_ENV, "bin")
        for directory in ([env_bin, conda_dir] if os.path.isdir(env_bin)
                          else [conda_dir]):
            if directory not in env.get("PATH", "").split(":"):
                env["PATH"] = directory + ":" + env.get("PATH", "")
    loops = str(RDAGENT_COSTEER_MAX_LOOP).strip()
    if loops.isdigit():
        env["CoSTEER_MAX_LOOP"] = loops
    # 甲-2：coding 阶段 CoSTEER 知识库跨场落盘（rdagent 侧前缀是 `CoSTEER_`，
    # 字段名 knowledge_base_path＝读、new_knowledge_base_path＝写；10-04 构造级
    # 探针 B 臂实测这副大小写在进程内真被 pydantic-settings 读到）。
    global _COSTEER_KB_NOTE
    _COSTEER_KB_NOTE = ""
    kb = str(RDAGENT_COSTEER_KB_PATH).strip()
    if kb:
        # 绝对路径照 expanduser 用；**相对路径挂在本线 RDAGENT_OUTPUT_DIR 底下**——
        # 子进程的 CWD 虽然也是这个目录、写相对路径碰巧能通，但那是巧合不是接口：
        # 仓库搬过一次家（09-29 数据层进 common），写死的绝对路径会把知识库悄悄
        # 建到老位置上，而 rdagent 的 dump 自己会 mkdir(parents=True) 把树造出来。
        kb_abs = os.path.expanduser(kb)
        kb_path = (kb_abs if os.path.isabs(kb_abs) else
                   os.path.abspath(os.path.join(RDAGENT_OUTPUT_DIR, kb_abs)))
        cls = _kb_pickle_class(kb_path) if os.path.exists(kb_path) else ""
        if cls and cls != "CoSTEERKnowledgeBaseV2":
            _COSTEER_KB_NOTE = (f"⚠️ 知识库 {kb_path} 顶层类={cls}（不是 "
                                f"CoSTEERKnowledgeBaseV2）⇒ 本场只写不读，"
                                f"收场 dump 会覆写它、下一场自愈")
        else:
            env["CoSTEER_KNOWLEDGE_BASE_PATH"] = kb_path
            _COSTEER_KB_NOTE = (f"知识库＝{kb_path}"
                                + ("（文件还不存在⇒本场从空库起步）" if not cls
                                   else "（读回类型 V2）"))
        env["CoSTEER_NEW_KNOWLEDGE_BASE_PATH"] = kb_path
    model = str(RDAGENT_LLM_MODEL).strip()
    if model:
        env["LITELLM_CHAT_MODEL"] = model
    # 官方支那一次调用的顶层 kwargs（见 config_base 那两段）。10-04 起**不再只有
    # 一条手写的 JSON**：本线把「关思考 / 撑窗口」写在同一处源头（`LLM_REASONING_
    # EFFORT`/`LLM_NUM_CTX`。这里把它翻译成 litellm 唯一认得的那副写法——`LITELLM_*` 环境
    # 变量既表达不出 think:false，也表达不出窗口。⚠️ 窗口这把 10-04 裁「乙」后**只剩这一腿吃**。
    # 显式给了 RDAGENT_LLM_KWARGS 就照原样透传（起场器与既有夹具走这条，优先级最高）；
    # 两个源头都是默认值时一个键都不注入 ⇒ 行为与 10-03 那场一字不差。
    extra = str(RDAGENT_LLM_KWARGS).strip()
    if not extra:
        derived = {}
        if LLM_REASONING_EFFORT == "none":
            derived["think"] = False
        if LLM_NUM_CTX > 0:
            derived["num_ctx"] = int(LLM_NUM_CTX)
        if derived:
            extra = json.dumps(derived)
    if extra:
        env["RDAGENT_LLM_KWARGS"] = extra
    return env


def official_chat_model(env_file):
    """official 支线这一场用哪个聊天模型，连同来源一起返回（只加读数，不设闸）。

    「跑一次会起几个本地模型」原先在本机没有一处能看见：主线侧的模型在 config 的
    LLM_MODEL（由 `llm_client.describe_endpoint` 打印），official 侧的模型写在各线
    `rdagent_output/.env` 的 LITELLM_CHAT_MODEL，两份配置各说各话——而多源挖掘里
    这两支是**并发**跑的（`multi_source_mining` 的 ThreadPoolExecutor），所以同一台
    机上会同时驻留两个聊天模型（外带那份 .env 里的嵌入模型）。旋钮
    `RDAGENT_LLM_MODEL` 留空时行为一字未变（仍以那份 .env 为准），它只是让体检表
    能把两边并排念出来。
    """
    injected = str(RDAGENT_LLM_MODEL).strip()
    if injected:
        return injected, "注入 RDAGENT_LLM_MODEL"
    try:
        with open(env_file, encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip().startswith("LITELLM_CHAT_MODEL="):
                    return ln.strip().split("=", 1)[1], f"工作区 {env_file}"
    except OSError:
        pass
    return "（两份都没设，走 rdagent 默认）", env_file


def _find_conda():
    found = shutil.which("conda")
    if found:
        return found
    for p in _CONDA_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


_ENV_DRIVER = _driver_env()   # ←必须在 _find_conda 之后：它要拿 conda 目录填 PATH


def _find_env_python(conda):
    """环境内解释器的绝对路径；找不到回 None（调用方退回 `conda run` 写法）。

    直调它是为了**收尸**而不是省事：`conda run` 会再落一层 `/tmp/tmpXXXX` 的
    bash 壳，超时那句 `proc.kill()` 只杀得到最外面那层，驱动 python 变成孤儿
    继续跑（10-03 实测：主线 rc=0 之后驱动 pid 又活了 2h23m，期间一直在往
    `rdagent_output/log/<会话>/…/token_cost/<pid>/*.pkl` 写盘）。孤儿不只是占
    内存——它还会在将来某个时刻写 `factors.json`，于是下一场的回收读到的可能
    是上一场孤儿的成品。拔掉这层壳后进程树只剩驱动本身，`killpg` 才打得到。
    """
    roots = []
    if conda:
        # …/miniconda3/condabin/conda 或 …/miniconda3/bin/conda ⇒ 取安装根
        roots.append(os.path.dirname(os.path.dirname(os.path.abspath(conda))))
    roots += [os.path.expanduser("~/miniconda3"),
              os.path.expanduser("~/anaconda3"), "/opt/conda"]
    for root in roots:
        p = os.path.join(root, "envs", RDAGENT_CONDA_ENV, "bin", "python")
        if os.path.exists(p):
            return p
    return None


def _reap_group(proc, grace=30):
    """超时/异常后把整组收干净：SIGTERM 给进程组，宽限期后再 SIGKILL。

    只对自己拉起的那一组下手（`Popen(start_new_session=True)` 使 pgid＝pid，
    组里只有本线那棵子树），绝不按名字扫进程——别的会话的驱动不在这个组里。
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return "进程组已自行退出（无需收尸）"
    note = "SIGTERM→整组"
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return "进程组已自行退出（无需收尸）"
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
            note = "SIGTERM 未生效→SIGKILL→整组"
        except ProcessLookupError:
            note = "SIGTERM 后整组自行退出"
        proc.wait()
    return note


def _leftover_containers(need_sg):
    """收尸后的残留容器只数不动（容器由驱动内部同步起，正常应随组一起退）"""
    argv = _sg_docker(["docker", "ps", "-q"]) if need_sg else ["docker", "ps", "-q"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return len([x for x in (r.stdout or "").split() if x])
    except Exception as e:
        return f"读失败({type(e).__name__})"


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


# 探测预算：2 次 × 45s ⇒ 最坏 90s。整个进程只体检一次（`_PREFLIGHT` 缓存），
# 而这一腿本来就是"小时级容器循环"的入口，90 秒换掉一次谎报是划算的。
_DOCKER_PROBE_ATTEMPTS = 2
_DOCKER_PROBE_TIMEOUT = 45


def _docker_info(argv, attempts=None, timeout=None):
    """探一次 docker daemon；**只有"超时"这一种失败才重试**。

    为什么要第二次：原来单次 20s 一超时就直接念「daemon 未启动？」，而 10-01 15:5x
    那场实测是——这条判红 ⇒ official 支**静默产出 0 个因子**、全场退出码仍是 0；
    16:3x 手工敲同一条命令**秒回**（server 26.1.3、7 个容器、当前用户在 docker 组）。
    ⇒ "daemon 真没起"和"这次探测没抢到 CPU"是两件事，单次超时不足以分辨。
    权限被拒 / daemon 无响应（rc≠0）那两类是确定性错误，再敲一遍只会更慢，不重试。
    """
    attempts = _DOCKER_PROBE_ATTEMPTS if attempts is None else attempts
    timeout = _DOCKER_PROBE_TIMEOUT if timeout is None else timeout
    for i in range(max(1, attempts)):
        try:
            r = subprocess.run(list(argv), capture_output=True, text=True,
                               timeout=timeout)
            out = (r.stdout or "").strip()
            if r.returncode == 0 and out:
                return True, (f"daemon 可达 (server {out})" if i == 0 else
                              f"daemon 可达 (server {out}，第 {i + 1} 次探测才通 ⇒ "
                              f"机器在抖动，不是没起)")
            err = (r.stderr or "").strip().splitlines()
            return False, (err[-1][:120] if err else f"daemon 无响应 (rc={r.returncode})")
        except subprocess.TimeoutExpired:
            if i + 1 < max(1, attempts):
                continue
            return False, (f"docker info 超时（daemon 未启动？"
                           f"已试 {max(1, attempts)} 次、每次 >{timeout}s）")
        except FileNotFoundError:
            return False, "docker 命令不可执行"
    return False, "docker 探测未执行"


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


def _fmt_mtime(p):
    """文件 mtime → 'YYYY-MM-DD HH:MM'，供检查表里比对时间先后"""
    try:
        return time.strftime("%Y-%m-%d %H:%M",
                             time.localtime(os.path.getmtime(p)))
    except OSError:
        return "缺失"


def _newest_source_mtime(src_dir, suffix="_daily.csv"):
    """行情源目录里最新一份日线 csv 的 (mtime, 文件名)；无匹配则 (0, "")"""
    newest, name = 0.0, ""
    for root, _dirs, files in os.walk(src_dir):
        for fn in files:
            if not fn.endswith(suffix):
                continue
            try:
                m = os.path.getmtime(os.path.join(root, fn))
            except OSError:
                continue
            if m > newest:
                newest, name = m, fn
    return newest, name


def _rdagent_data_checks(output_dir):
    """qlib bin 与 coding 源数据面板是否落后于本线行情源目录。

    两道重建（`dump_qlib_bin.py` 造 bin、`pregen_source_data.py` 造
    `daily_pv.h5`）目前都只能手跑，忘了就会让官方循环在旧面板上编码、
    白烧一轮 LLM 时间（本机一轮 ≈50min）。判据用 mtime 而不是读 h5 的末
    日期：股票线那份面板 752MB，整读一次要几十秒，而循环真正关心的只是
    「行情是否在数据产物之后又更新过」；`pregen_source_data.py` 自身另有
    一道「h5 末日期 >= bin 日历末交易日」的内容比对，两道不互相替代。

    本线没有本地行情源目录（股票线用社区全市场包）时整组跳过。"""
    src = RDAGENT_SOURCE_DIR
    if not src or not os.path.isdir(src):
        return []
    newest, newest_file = _newest_source_mtime(src)
    if not newest:
        return [("RD-Agent 数据新鲜度", True,
                 f"{src} 无日线 csv 缓存，跳过比对")]
    # DATA_DIR = <line>/v1/data，其上一层就是本线 v1 根
    v1_root = os.path.dirname(os.path.abspath(DATA_DIR))
    day_txt = os.path.join(RDAGENT_QLIB_PROVIDER, "calendars", "day.txt")
    out_dir = output_dir or RDAGENT_OUTPUT_DIR
    h5 = os.path.join(out_dir, "git_ignore_folder",
                      "factor_implementation_source_data", "daily_pv.h5")
    dump_py = os.path.normpath(os.path.join(v1_root, "..", "..", "common", "src", "data", "etf", "dump_qlib_bin.py"))
    pregen_py = os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "rdagent_docker", "pregen_source_data.py"))
    src_stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(newest))
    stale = f"行情缓存更新于 {src_stamp}（{newest_file}），晚于它"

    checks = []
    bin_ok = os.path.exists(day_txt) and os.path.getmtime(day_txt) >= newest
    checks.append((
        "qlib bin 新鲜度", bin_ok,
        f"末次 dump {_fmt_mtime(day_txt)}" if bin_ok else
        f"bin 落后（{_fmt_mtime(day_txt)}，{stale}）；先跑 "
        f"/usr/bin/python3.10 {dump_py}"))
    h5_ok = os.path.exists(h5) and os.path.getmtime(h5) >= newest
    checks.append((
        "源数据面板 daily_pv.h5", h5_ok,
        f"末次生成 {_fmt_mtime(h5)}" if h5_ok else
        f"面板落后（{_fmt_mtime(h5)}，{stale}）；先跑 "
        f"QLIB_PROVIDER_URI={RDAGENT_QLIB_PROVIDER} conda run -n "
        f"{RDAGENT_CONDA_ENV} python {pregen_py} {out_dir}"))
    return checks


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
    # 光判目录存在不够：这份 bin 要真有 calendars/day.txt 才算可开数据，
    # 且 rdagent 的 QTDockerEnv.prepare 会拿挂载根拼 <mount>/qlib_data/cn_data
    # 做存在性检查，缺了就在容器里联网重拉（本机拉不动，等于回测挂）。
    # 末交易日一并打印：跑 RD-Agent 前先确认数据是不是最新的
    day_txt = os.path.join(RDAGENT_QLIB_PROVIDER, "calendars", "day.txt")
    data_end = ""
    if os.path.isfile(day_txt):
        with open(day_txt) as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
        data_end = lines[-1] if lines else ""
    data_ok = bool(data_end)
    checks.append(("qlib 行情数据", data_ok,
                   f"{RDAGENT_QLIB_PROVIDER} (末交易日 {data_end})" if data_ok
                   else f"{day_txt} 缺失：需把本线 qlib bin 放到 "
                        f"{RDAGENT_QLIB_PROVIDER}（见 config 的 RDAGENT_QLIB_PROVIDER）"))
    # 数据"存在"不等于"可用"：行情缓存刷新过而 bin/面板没重建时，循环会在
    # 旧面板上编码，故再比一道 mtime（本线无本地行情源时自动跳过）
    checks += _rdagent_data_checks(output_dir)
    from llm_client import llm_available, describe_endpoint
    env_file = os.path.join(output_dir if output_dir
                            else RDAGENT_OUTPUT_DIR, ".env")
    llm_ok = llm_available() or os.path.exists(env_file)
    llm_detail = (describe_endpoint() if llm_available()
                  else f"管线端点未配置；已用 RD-Agent 自身 {env_file}")
    # 主线侧模型在上面那一行，official 侧模型是另一个进程另读一份配置 ⇒ 同一条
    # 读数里把两边一起念出来，免得"这场到底起了几个本地模型"要翻两个文件才知道
    chat_model, model_src = official_chat_model(env_file)
    checks.append(("LLM 端点", llm_ok,
                   f"{llm_detail}｜official 支线={chat_model}（来源={model_src}）"))
    return checks


_PREFLIGHT = None  # 进程级缓存：walk-forward 逐折重入时不重复刷检查表


def try_official_rdagent(output_dir=RDAGENT_OUTPUT_DIR):
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
    env_py = _find_env_python(conda)
    driver = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "rdagent_driver.py")
    os.makedirs(output_dir, exist_ok=True)
    if env_py:
        cmd = [env_py, driver, "--out", output_dir]
    else:
        cmd = [conda, "run", "--no-capture-output", "-n", RDAGENT_CONDA_ENV,
               "python", driver, "--out", output_dir]
    _, need_sg, _ = _docker_access()
    if need_sg:
        # sg 切换有效组后，子进程及其内部 Python docker SDK
        # 继承该组，socket 可达
        cmd = _sg_docker(cmd)
    print(f"  ▶️ RD-Agent(Q) 子进程启动: {' '.join(cmd)}")
    print(f"     （工作目录 {output_dir}，超时上限 "
          f"{RDAGENT_TIMEOUT_SEC}s，产物实时回显如下）")
    print(f"     解释器直调={env_py or '否，走 conda run（多一层壳）'}")
    if "CoSTEER_MAX_LOOP" in _ENV_DRIVER:
        print(f"     coding 演化轮数 CoSTEER_MAX_LOOP="
              f"{_ENV_DRIVER['CoSTEER_MAX_LOOP']}（由本仓库配置注入，"
              f"优先于工作区 .env）")
    # 甲-2 那把旋钮的状态**只在启动打印里出现、不进前置检查表**：前置检查里的
    # 任何 ❌ 都会让整场不跑（missing 非空即 return None），而知识库坏版本的正确
    # 处置是「降为只写不读、继续跑」，用它挡场等于把一个优化项变成依赖。
    if _COSTEER_KB_NOTE:
        print(f"     {_COSTEER_KB_NOTE}")

    # 起场前的既有因子名单＝回收读数的基线（必须在 Popen 之前抄，晚一步就被覆盖了）
    _baseline = _existing_factor_names(output_dir)

    def harvest(reason):
        """循环没走通时也要把既有产物交回主线

        驱动子进程在 finally 里做回收（崩溃轮也会写 factors.json），所以
        超时/非零退出/根本没拉起来这三种情况下，factors.json 里往往仍有历轮
        攒下的定义与官方 IC。早先这里直接 return None，等于把已烧掉的 LLM
        时间整份丢掉，也是 ETF 线因子库里 0 条 official 的直接原因之一。

        ⚠️ 但「捞回来几条」从来不等于「这一场写出几条」：10-03 与 10-04 两场
        都由这里念出「官方产出 12 个因子」，而 `cmp` 对起场前快照是逐字节相同
        ＝本场 0 写入。所以起场前先把既有名单抄下来，回收时按它做差、只把净增
        念成产出（返回值不变，改的是读数口径，不改行为）。"""
        print(f"  ⚠️ {reason}；尝试回收既有产物（驱动崩溃轮也会写 factors.json）")
        return _recover_factors(output_dir, baseline=_baseline)

    try:
        # start_new_session：让驱动自成一组，超时那句 _reap_group 才打得到它
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                cwd=output_dir, env=_ENV_DRIVER,
                                start_new_session=True)
    except Exception as e:
        return harvest(f"子进程拉起失败: {type(e).__name__}: {e}")
    start, timed_out = time.time(), False
    for line in proc.stdout:
        print("    [RD-Agent] " + line.rstrip())
        if time.time() - start > RDAGENT_TIMEOUT_SEC:
            note = _reap_group(proc)
            timed_out = True
            break
    proc.wait()
    if timed_out:
        print(f"     收尸读数: {note}｜残留容器={_leftover_containers(need_sg)}")
        return harvest(f"超时 {RDAGENT_TIMEOUT_SEC}s 已终止")
    if proc.returncode != 0:
        return harvest(f"factor 循环退出码 {proc.returncode}")
    print("  ✅ RD-Agent(Q) factor 循环执行完毕，回收产物...")
    return _recover_factors(output_dir, baseline=_baseline)


def _factor_artifact_paths(output_dir):
    """回收读的是哪几个文件——基线与回收必须共用同一份清单，否则两边口径会飘"""
    return [
        os.path.join(output_dir, "factors.json"),
        os.path.join(output_dir, "result.json"),
        os.path.join(output_dir, "latest", "factors.json"),
    ]


def _existing_factor_names(output_dir):
    """起场前 factors.json 里已有的因子名，作为回收读数的「净增」基线

    返回空集有两种含义（都如实表示"没有可比的旧档"）：文件不存在、或读不出
    JSON。此时 _recover_factors 会退回只报累计条数，不假装算得出净增。
    """
    for path in _factor_artifact_paths(output_dir):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                return set()
            if not isinstance(data, list):
                return set()
            return {item.get("name", f"official_{i}")
                    for i, item in enumerate(data)}
    return set()


def _recover_factors(output_dir, baseline=None):
    """从环境子进程落盘的 JSON 中回收因子表达式

    baseline（起场前的因子名集合）只影响读数口径，不影响返回值：
    给得出基线时额外念一行「本场净增」，这是 10-03 / 10-04 两场假绿的源头。
    """
    candidates = _factor_artifact_paths(output_dir)
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
            print(f"  📦 既有产物累计 {len(factors)} 个因子（{path}，"
                  f"落盘于 {_fmt_mtime(path)}）")
            if baseline is not None:
                new = [f["name"] for f in factors if f["name"] not in baseline]
                print(f"  {'✅' if new else '⚠️'} 本场净增 {len(new)} 个因子"
                      f"{'：' + '、'.join(new) if new else ''}"
                      + ("" if new else "（全是起场前旧档，本场 0 写入）"))
            else:
                print("  ℹ️ 本场净增无法判定（起场前没有可比基线）")
            return factors
    print("  ⚠️ 循环完成但未在产出目录找到 factors.json/result.json，"
          "详见子进程日志与 rdagent 会话目录")
    return None
