# -*- coding: utf-8 -*-
"""拉起一整轮 RD-Agent(Q) factor 循环，复用 official_rdagent 的环境/sg 逻辑。

只在股票线进程里作为脚本执行才有效应：全部动作收在 main() 里，
被 import 时不触发（曾经的教训：巡检脚本 import 本文件即误跑了一轮，
把 8 核和 LLM 全占走）。
"""
import _bootstrap  # noqa: F401
import os
import re
import subprocess
import sys

import official_rdagent as o
from config import RDAGENT_OUTPUT_DIR


def _model_tag(out_dir):
    """日志名带模型 tag：换模型复跑时靠文件名区分是哪条支线的产物。
    tag 只取模型名本体（去掉 provider 前缀/版本后缀），否则会带 `/` 撑坏路径"""
    try:
        with open(os.path.join(out_dir, ".env"), encoding="utf-8") as f:
            m = re.search(r"^LITELLM_CHAT_MODEL=(\S+)", f.read(), re.M)
        return (m.group(1).split("/")[-1].split(":")[0].replace("qwen2.5-", "")
                if m else "unknown")
    except Exception:
        return "unknown"


def main(loops=1):
    out = RDAGENT_OUTPUT_DIR
    log = os.path.join(
        out, f"loop_{_model_tag(out)}_{loops}loop_"
             f"{os.environ.get('STAMP', 'run')}.log")
    conda = o._find_conda()
    driver = os.path.join(os.path.dirname(os.path.abspath(o.__file__)),
                          "rdagent_driver.py")
    cmd = [conda, "run", "--no-capture-output", "-n", o.RDAGENT_CONDA_ENV,
           "python", driver, "--out", out, "--loops", str(loops)]
    _, need_sg, _ = o._docker_access()
    if need_sg:
        cmd = o._sg_docker(cmd)

    os.makedirs(out, exist_ok=True)
    logf = open(log, "w")
    proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                            cwd=out, env=o._ENV_DRIVER, start_new_session=True)
    print(f"launched pid={proc.pid} loops={loops} need_sg={need_sg}")
    print(f"log={log}")
    return proc.pid


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
