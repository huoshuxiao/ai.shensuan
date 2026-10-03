# -*- coding: utf-8 -*-
"""数据层进 common 的验收夹具（09-29）

要证的不是"文件在不在"，而是**三条线各自的 config 在 import 期算出来的落位**
确实指到 common/data/<线>/，且旧的线内目录已经没有人再指：

1. BASE_DATA_DIR / CACHE_DIR / QLIB_DATA_DIR / RDAGENT_QLIB_PROVIDER 全在
   common/data/<线>/ 底下，且目录真实存在（不是被 makedirs 现场造出来的空壳）。
2. qlib provider 仍保留 `qlib_data/cn_data` 两级尾巴（缺了容器会在里面联网重拉），
   且 docker 挂载点 = provider 往上两级（由构造保证，这里逐条核）。
3. 每条线自己的专属基础目录跟着走：股票线 daily_snapshot + industry_map，
   ETF 线 universe_all + index_cache + risk。
4. 分析产物**没有**跟着搬：RESULTS_DIR / LIBRARY_DIR / LIVE_DATA_DIR 仍在本线 data/ 下。
5. 负对照：旧落位（<线>/v1/data/qlib、etf/v1/data/cache …）在磁盘上不存在，
   且配置里没有任何一个键还指着它 —— 把常量改回去，本夹具必须判红。

用法：/usr/bin/python3.10 etf/v1/temp/check_data_layer_common_0929.py
退出码 0 = 全绿；非 0 = 有判据红（打印哪条）。
"""

import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 3))
PY = "/usr/bin/python3.10"

LINES = {
    # owns_bin：这条线自己有一份 qlib bin 面板（live2etf 没有，它只读 ETF 线的）
    "stock": {"prefix": "STOCK_", "extra": ["ASHARE_SNAPSHOT_DIR", "ASHARE_INDUSTRY_CSV"],
              "owns_bin": True},
    "etf": {"prefix": "ETF_", "extra": ["UNIVERSE_ALL_DIR", "RISK_DIR", "RDAGENT_SOURCE_DIR"],
            "owns_bin": True},
    "live2etf": {"prefix": "LIVE2ETF_",
                 "extra": ["ETF_LINE_UNIVERSE_ALL", "ETF_LINE_LIST_DATES"],
                 "owns_bin": False},
}

PROBE = """
import os, sys, json
v1 = os.path.abspath(__V1__)
src = os.path.join(v1, "src")
sys.path.insert(0, src)
import _bootstrap  # noqa: F401
import config as C
keys = ["V1_ROOT", "DATA_DIR", "BASE_DATA_DIR", "CACHE_DIR", "QLIB_DATA_DIR",
        "RESULTS_DIR", "LIBRARY_DIR", "LIVE_DATA_DIR", "RDAGENT_QLIB_PROVIDER"] + __EXTRA__
out = {k: str(getattr(C, k)) for k in keys if hasattr(C, k)}
out["_docker_volumes"] = str(C.RDAGENT_QLIB_DOCKER_ENV.get("QLIB_DOCKER_EXTRA_VOLUMES"))
print(json.dumps(out, ensure_ascii=False))
"""

FAILS = []
READINGS = []


def fail(msg):
    FAILS.append(msg)
    print(f"  ✗ {msg}")


def ok(msg):
    print(f"  ✓ {msg}")


for line, spec in LINES.items():
    v1 = os.path.join(REPO, line, "v1")
    print(f"\n──── {line} 线 ────")
    code = PROBE.replace("__V1__", repr(v1)).replace("__EXTRA__", repr(spec["extra"]))
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, cwd=v1)
    if r.returncode != 0:
        fail(f"{line} 线 import config 就红了：\n{r.stdout[-800:]}\n{r.stderr[-800:]}")
        continue
    import json
    cfg = json.loads(r.stdout.strip().splitlines()[-1])
    READINGS.append((line, cfg))

    base = cfg["BASE_DATA_DIR"]
    want = os.path.join(REPO, "common", "data", line)
    if os.path.abspath(base) != want:
        fail(f"BASE_DATA_DIR = {base}，不在 {want}")
    else:
        ok(f"BASE_DATA_DIR = {os.path.relpath(base, REPO)}")
    if not (os.path.isdir(base) and os.listdir(base)):
        fail(f"{base} 不存在或是空壳（搬家没做成）")

    for k in ("CACHE_DIR", "QLIB_DATA_DIR") + tuple(spec["extra"]):
        p = cfg.get(k)
        if not p:
            fail(f"配置里没有键 {k}")
            continue
        if k == "QLIB_DATA_DIR" and not spec["owns_bin"]:
            # 只核它是从 BASE_DATA_DIR 派生的（不要求磁盘上有：这条线没有自己的 bin，
            # 而且 makedirs 不该为一条不跑官方循环的线造一个空壳目录）
            if os.path.abspath(p) != os.path.join(os.path.abspath(base), "qlib"):
                fail(f"QLIB_DATA_DIR = {p} 不是 BASE_DATA_DIR/qlib")
            else:
                ok("QLIB_DATA_DIR 派生正确（本线无自己的 bin，不落盘）")
            continue
        # live2etf 跨线只读：它借的是 ETF 线的基础数据，不是本线的
        root = want if not (line == "live2etf" and k.startswith("ETF_LINE_")) \
            else os.path.join(REPO, "common", "data", "etf")
        pp = os.path.abspath(p if os.path.isabs(p) else os.path.join(v1, p))
        if not pp.startswith(root + os.sep):
            fail(f"{k} = {p} 不在 {os.path.relpath(root, REPO)} 底下")
        elif not os.path.exists(pp):
            fail(f"{k} = {os.path.relpath(pp, REPO)} 磁盘上没有")
        else:
            ok(f"{k} → {os.path.relpath(pp, REPO)}")

    prov = cfg["RDAGENT_QLIB_PROVIDER"]
    if not spec["owns_bin"]:
        ok(f"provider 派生 = {os.path.relpath(prov, REPO)}（本线不跑官方循环，不落盘）")
    elif not prov.endswith(os.path.join("qlib_data", "cn_data")):
        fail(f"provider 尾巴被拆了（容器会联网重拉）：{prov}")
    elif not os.path.isdir(os.path.join(prov, "calendars")):
        fail(f"provider 指向的目录里没有 calendars/：{prov}")
    else:
        ok(f"provider = {os.path.relpath(prov, REPO)}（含 calendars/）")

    import json as _j
    vols = _j.loads(cfg["_docker_volumes"])
    mount = list(vols)[0]
    if os.path.abspath(mount) != os.path.dirname(os.path.dirname(os.path.abspath(prov))):
        fail(f"docker 挂载点 {mount} 不等于 provider 往上两级")
    elif spec["owns_bin"] and not os.path.isdir(mount):
        fail(f"docker 挂载点不存在：{mount}")
    else:
        ok(f"docker 挂载 = {os.path.relpath(mount, REPO)} → {list(vols.values())[0]['bind']}")

    # 分析产物不许跟着搬
    for k in ("RESULTS_DIR", "LIBRARY_DIR", "LIVE_DATA_DIR"):
        p = os.path.abspath(cfg[k])
        if not p.startswith(os.path.join(v1, "data") + os.sep):
            fail(f"{k} = {p} 被搬离了本线 data/（分析产物不该进 common）")
        else:
            ok(f"{k} 仍在本线：{os.path.relpath(p, REPO)}")

    # 旧落位必须已经不在磁盘上
    for old in ("qlib", "cache", "daily_snapshot", "universe_all", "index_cache", "risk"):
        p = os.path.join(v1, "data", old)
        if os.path.exists(p):
            fail(f"旧落位还在磁盘上：{os.path.relpath(p, REPO)}")
    ok("线内 data/ 下已无基础数据目录")

print("\n──── 跨线只读（live2etf 借 ETF 全池）────")
l2 = [c for ln, c in READINGS if ln == "live2etf"]
et = [c for ln, c in READINGS if ln == "etf"]
if l2 and et:
    borrowed = os.path.abspath(l2[0]["ETF_LINE_UNIVERSE_ALL"])
    real = os.path.abspath(et[0]["UNIVERSE_ALL_DIR"])
    if borrowed != real:
        fail(f"live2etf 指的镜像目录 {borrowed} ≠ ETF 线自己的 {real}")
    else:
        ok(f"两线读到同一个全市场镜像：{os.path.relpath(real, REPO)}"
           f"（{len(os.listdir(real))} 个文件）")

print(f"\n{'=' * 60}\n{'全绿 ✅' if not FAILS else f'{len(FAILS)} 条判红 ❌'}")
sys.exit(0 if not FAILS else 1)
