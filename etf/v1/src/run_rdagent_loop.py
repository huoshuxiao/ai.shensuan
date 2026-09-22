# -*- coding: utf-8 -*-
"""拉起一整轮 RD-Agent(Q) factor 循环，复用 official_rdagent 的环境/sg 逻辑。"""
import _bootstrap  # noqa: F401
import os, re, sys, subprocess
sys.path.insert(0, os.path.join(os.getcwd(), "core"))
import official_rdagent as o
from config import RESULTS_DIR

OUT = os.path.join(RESULTS_DIR, "rdagent_output")
LOOPS = int(sys.argv[1]) if len(sys.argv) > 1 else 1
# 日志名带模型 tag：换模型复跑时靠文件名区分是哪条支线的产物
# tag 只取模型名本体（去掉 provider 前缀/版本后缀），否则会带 `/` 撑坏路径
try:
    with open(os.path.join(OUT, ".env"), encoding="utf-8") as f:
        _m = re.search(r"^LITELLM_CHAT_MODEL=(\S+)", f.read(), re.M)
    TAG = (_m.group(1).split("/")[-1].split(":")[0].replace("qwen2.5-", "")
           if _m else "unknown")
except Exception:
    TAG = "unknown"
LOG = os.path.join(OUT, f"loop_{TAG}_{LOOPS}loop_{os.environ.get('STAMP','run')}.log")

conda = o._find_conda()
driver = os.path.join(os.path.dirname(os.path.abspath(o.__file__)),
                      "rdagent_driver.py")
cmd = [conda, "run", "--no-capture-output", "-n", o.RDAGENT_CONDA_ENV,
       "python", driver, "--out", OUT, "--loops", str(LOOPS)]
_, need_sg, _ = o._docker_access()
if need_sg:
    cmd = o._sg_docker(cmd)

os.makedirs(OUT, exist_ok=True)
logf = open(LOG, "w")
proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                        cwd=OUT, env=o._ENV_DRIVER, start_new_session=True)
print(f"launched pid={proc.pid} loops={LOOPS} need_sg={need_sg}")
print(f"log={LOG}")
