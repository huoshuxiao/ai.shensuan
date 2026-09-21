# -*- coding: utf-8 -*-
"""拉起一整轮 RD-Agent(Q) factor 循环（R1），复用 official_rdagent 的环境/sg 逻辑。"""
import _bootstrap  # noqa: F401
import os, sys, subprocess
sys.path.insert(0, os.path.join(os.getcwd(), "core"))
import official_rdagent as o
from config import RESULTS_DIR

OUT = os.path.join(RESULTS_DIR, "rdagent_output")
LOOPS = int(sys.argv[1]) if len(sys.argv) > 1 else 1
LOG = os.path.join(OUT, f"loop_r1_{LOOPS}loop_{os.environ.get('STAMP','run')}.log")

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
