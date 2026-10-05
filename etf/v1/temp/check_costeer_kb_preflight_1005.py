# -*- coding: utf-8 -*-
"""10-05 起场前只读探针：甲-2 落地后这一场真会起来吗（不改任何配置、不起子进程）

三块读数：
  ① 前置体检表——**任何一行 ❌ 都会让 try_official_rdagent 直接 return None**（＝整场不跑），
     所以起场前必须现看一遍，不能拿昨天那张的印象当今天的数；
  ② 驱动子进程 env 里 CoSTEER 那两把键（知识库真发出去了没、路径挂在哪）；
  ③ 起场打印那行状态读数（`_COSTEER_KB_NOTE`）今天会念成什么——档还不存在时应当是
     「本场从空库起步」，念成「顶层类=… ⇒ 只写不读」就说明有东西提前写过。
"""
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for _p in (os.path.join(REPO, "etf", "v1", "src", "config"),
           os.path.join(REPO, "etf", "v1", "src", "core"),
           os.path.join(REPO, "common", "src", "core"),
           os.path.join(REPO, "common", "src")):
    sys.path.insert(0, _p)

import official_rdagent as off  # noqa: E402
from config import RDAGENT_OUTPUT_DIR, RDAGENT_COSTEER_KB_PATH  # noqa: E402

print(f"official 模块={off.__file__}")
print(f"配置键现值={RDAGENT_COSTEER_KB_PATH!r}")

bad = 0
print("\n① 前置体检（❌＝这一场根本不会起）:")
for name, ok, detail in off.rdagent_preflight(RDAGENT_OUTPUT_DIR):
    print(f"   {'✅' if ok else '❌'} {name}: {detail}")
    bad += 0 if ok else 1

env = off._driver_env()
print("\n② 子进程 env 里 CoSTEER 那两把:")
keys = [k for k in sorted(env) if k.upper().startswith("COSTEER")]
for k in keys:
    print(f"   {k}={env[k]}")
if not keys:
    print("   （一个都没有＝键没落地，甲-2 等于没开）")
    bad += 1

print(f"\n③ 起场那行读数会念: {off._COSTEER_KB_NOTE!r}")
kb = env.get("CoSTEER_NEW_KNOWLEDGE_BASE_PATH", "")
if kb:
    print(f"   目标档={kb} 存在={os.path.exists(kb)}"
          + (f" mtime={time.strftime('%F %T', time.localtime(os.path.getmtime(kb)))}"
             if os.path.exists(kb) else ""))
    parent = os.path.dirname(kb)
    print(f"   父目录存在={os.path.isdir(parent)}（rdagent 收场 dump 会自建，但写不进去"
          f"要在那时才发现＝白烧一场）")

print(f"\n合计：{'可以起' if not bad else str(bad) + ' 项挡场'}")
sys.exit(1 if bad else 0)
