# -*- coding: utf-8 -*-
"""一次性读数（10-03 乙+）：配置层那两根线接对了没有 —— 真 import，不猜。

夹具 `check_llm_kwargs_patch_1003.py` 量的是驱动那三个函数的语义；这一支只补它
够不着的那一格：`ETF_RDAGENT_LLM_KWARGS` 这个环境变量 → 本线 config →
`official_rdagent._driver_env()` → 子进程 env。中间任何一环写错（键名打错、
忘了进 import 清单、留空时照样注入）都会让「开关默认关」这个承诺变成假的。

两臂各起一个子进程（环境变量必须在 import 之前定），判据是**互反**的：
  开臂  子进程 env 里必须有 `RDAGENT_LLM_KWARGS`，且值与传入串逐字节相同
  关臂  子进程 env 里必须**没有**这个键（留空＝不注入，不是注入空串）

⚠️ 10-04 语义变了（用户裁「选项B」＝统一项只写在 `etf/v1/.env`）：留空不再等于
不注入，而是由 `ETF_LLM_REASONING_EFFORT`/`ETF_LLM_NUM_CTX` **派生**。所以这一支的
「关臂」今天重跑会红——红的是判据过期，不是代码坏。接管它的是
`check_kwargs_source_1004.py`（四臂＋主线读侧）。这一支留档只对它写下的那一场有效。

用法：/usr/bin/python3.10 -u etf/v1/temp/check_kwargs_env_plumbing_1003.py
"""
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")

CHILD = r"""
import json, os, sys
_REPO = %r
for _p in (os.path.join(_REPO, "etf", "v1", "src", "config"),
           os.path.join(_REPO, "etf", "v1", "src", "core"),
           os.path.join(_REPO, "common", "src", "core"),
           os.path.join(_REPO, "common", "src")):
    sys.path.insert(0, _p)
import official_rdagent as off
from config import RDAGENT_LLM_KWARGS as cfg_val
env = off._driver_env()
print("CHILD " + json.dumps({
    "module": off.__file__,
    "cfg_repr": repr(cfg_val),
    "env_has": "RDAGENT_LLM_KWARGS" in env,
    "env_val": env.get("RDAGENT_LLM_KWARGS"),
}, ensure_ascii=False))
""" % REPO

RAW = '{"think": false, "num_ctx": 16384}'
FAILS = []


def run(extra):
    p = subprocess.run([sys.executable, "-c", CHILD], capture_output=True,
                       text=True, cwd=SRC, env={**os.environ, **extra})
    line = next((ln for ln in (p.stdout or "").splitlines()
                 if ln.startswith("CHILD ")), None)
    if line is None:
        raise SystemExit(f"子进程没出读数 rc={p.returncode}\n{p.stderr[-800:]}")
    return json.loads(line[len("CHILD "):])


def check(label, got, want):
    ok = got == want
    print("  %s %s: 实到 %r 期望 %r" % ("✅" if ok else "✗", label, got, want))
    if not ok:
        FAILS.append(label)


print("[臂1] 设置 ETF_RDAGENT_LLM_KWARGS（乙+ 那一串）")
d = run({"ETF_RDAGENT_LLM_KWARGS": RAW})
check("official_rdagent 解析到 common 那份", os.sep + os.path.join(
    "common", "src", "core", "official_rdagent.py"),
    d["module"][-len(os.sep + os.path.join("common", "src", "core",
                                           "official_rdagent.py")):])
check("config 读到的值", d["cfg_repr"], repr(RAW))
check("子进程 env 有该键", d["env_has"], True)
check("注入值逐字节相同", d["env_val"], RAW)

print("[臂2] 留空＝不注入（今天默认，闸门关着）")
d = run({"ETF_RDAGENT_LLM_KWARGS": ""})
check("config 读到空串", d["cfg_repr"], "''")
check("子进程 env 没有这个键（不是空串）", d["env_has"], False)

print("\n" + ("全部通过" if not FAILS else "失败项: " + ", ".join(FAILS)))
raise SystemExit(1 if FAILS else 0)
