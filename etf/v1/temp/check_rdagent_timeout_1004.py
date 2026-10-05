# -*- coding: utf-8 -*-
"""丙（10-04 用户裁「丙」→ 看代价后回「B」）：官方支 factor 循环的墙钟闸 7200s → 21600s 落位核对。

大目标：日更链的 official 支要真挖出因子。10-04 14:36 那场被这道闸收的尸——
逐阶段读数是 direct_exp_gen 14m08s 走完、coding 烧掉 1h42m **仍没走完**、
Step 2 running（qlib 回测容器）从没走到。抬闸是为了让一整轮跑得完。

接缝只有一处：common/src/config_base.py:486 `env("RDAGENT_TIMEOUT", "7200")`，
本线前缀 ⇒ 键名 ETF_RDAGENT_TIMEOUT（写在 etf/v1/.env，不进版本库）。
外层天花板派生自它：etf/v1/src/config/config.py:357 = 本值 + 600。

为什么要这把尺子（四格判据各挡一种假绿，另有一格只出读数）：
  C1 只证「本线 import 到 21600」——挡不住"值其实写死在别处"；
  C2 只证派生跟着走——外层若没跟着抬，multi_source_mine 的 as_completed 会先把
     子进程掐了（09-24「GP 那 4 个因子整段被跳过」就是这条路）。
  C3 子进程里改 env 值 ⇒ 读数必须跟着变。**这一格才证明旋钮接在机器上**：
     没有它，C1 可以由一行硬编码满足。
  C4 股票线（没有 STOCK_RDAGENT_TIMEOUT 那行）必须仍是 7200 ⇒ 证明共享层默认值
     一行未动、本线那行没越过前缀边界。
  C5 本线 .env 里这个键恰好 1 行 ⇒ 挡「同名键写两遍、注释与进程相反」那个已知坑。
  ℹ️ 读数格：股票线外层天花板仍是底座默认 300s（它从没按自家超时派生）——只报不改。
退出码：0＝五格全绿；1＝任一格红。
"""
import os
import re
import subprocess
import sys

# 本文件在 etf/v1/temp/ 下 ⇒ 往上四级才是仓库根
REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
ETF_V1 = os.path.join(REPO, "etf", "v1")
STOCK_V1 = os.path.join(REPO, "stock", "v1")
ENV_FILE = os.path.join(ETF_V1, ".env")

_PROBE = """
import os, sys
sys.path.insert(0, os.environ["LINE_SRC"])
import _bootstrap  # noqa: F401
from config import RDAGENT_TIMEOUT_SEC, MULTI_SOURCE
print(RDAGENT_TIMEOUT_SEC, MULTI_SOURCE["timeout_seconds"])
"""

results = []


def record(cell, ok, detail):
    results.append(ok)
    print(f"  {'✅' if ok else '❌'} {cell}：{detail}")


def probe(line_root, extra_env=None):
    env = dict(os.environ)
    env["LINE_SRC"] = os.path.join(line_root, "src")
    env.pop("ETF_RDAGENT_TIMEOUT", None)
    env.pop("STOCK_RDAGENT_TIMEOUT", None)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True,
                       text=True, env=env, cwd=line_root)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])
    a, b = r.stdout.strip().split()
    return int(a), int(b)


print("[1004 丙] 官方支超时闸落位核对")

# C1 本线 import 真值（现档 21600＝6h；16:0x 曾落 14400，用户看代价后回「B」再抬一档）
sec, outer = probe(ETF_V1)
record("C1 本线读到抬后的闸", sec == 21600, f"RDAGENT_TIMEOUT_SEC={sec}（要 21600）")

# C2 派生的外层天花板
record("C2 外层天花板跟着抬", outer == sec + 600 == 22200,
       f'MULTI_SOURCE["timeout_seconds"]={outer}（要 {sec}+600=22200）')

# C3 旋钮真的接在机器上：改 env 值读数必须跟着变
sec2, outer2 = probe(ETF_V1, {"ETF_RDAGENT_TIMEOUT": "9999"})
record("C3 改 env 值读数跟着变", sec2 == 9999 and outer2 == 10599,
       f"注入 9999 ⇒ 读到 {sec2}/{outer2}（要 9999/10599；不变就是硬编码）")

# C4 股票线默认值未动（前缀边界的负对照）
ssec, souter = probe(STOCK_V1)
record("C4 股票线仍是旧默认", ssec == 7200,
       f"股票线读到 RDAGENT_TIMEOUT_SEC={ssec}（要 7200，共享层默认一行未动）")

# ℹ️ 读数格（**不作判据**，本次未改）：股票线的外层天花板从没按自家超时派生
#    （stock/v1/src/config/config.py 里 grep 不到 MULTI_SOURCE["timeout_seconds"]）
#    ⇒ 它停在底座默认 300s，而自家子进程上限是 7200s。
#    含义：若股票线走 multi_source_mine 起 official 支，外层会先把子进程掐掉，
#    和 09-24「GP 那 4 个因子整段被跳过」是同一条路。本线（ETF）在 config.py:357 派生好了。
print(f"  ℹ️ 跨线读数：股票线 MULTI_SOURCE['timeout_seconds']={souter}"
      f"（底座默认 300，未按 {ssec}+600 派生；只报，未改）")

# C5 事实源只有一处：本线 .env 里这个键恰好写 1 行（同名键重复是本线已知坑）
with open(ENV_FILE, encoding="utf-8") as f:
    n = len(re.findall(r"^ETF_RDAGENT_TIMEOUT=", f.read(), re.M))
record("C5 .env 里键唯一", n == 1, f"^ETF_RDAGENT_TIMEOUT= 命中 {n} 行（要恰好 1）")

print(f"\n[1004 丙] {len(results)} 格 / 绿 {sum(results)} / 红 {len(results) - sum(results)}")
sys.exit(0 if all(results) else 1)
