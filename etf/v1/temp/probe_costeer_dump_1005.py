# -*- coding: utf-8 -*-
"""甲-2 最后一寸：rdagent **生产那个 settings 类**读不读我们发的键 + 它自己 dump 的字节能不能被下一场读回

只往 /tmp 下的临时目录写，一个字节都不碰仓库产物、不起 official 循环。

为什么还要这一格：夹具那格消费侧只实例化了 `CoSTEERSettings`（父类），而生产走的是
`FactorCoSTEER.__init__:19 → FACTOR_COSTEER_SETTINGS = FactorCoSTEERSettings()`，
那一份把 `model_config.env_prefix` 改成了 `FACTOR_CoSTEER_`（factor_coder/config.py:10-11）；
`knowledge_base_path` 是**父类字段**，只能靠 `ExtendedBaseSettings.settings_customise_sources`
（core/conf.py:15-43）补回来的那个父类 env source（前缀 `CoSTEER_`）读到。
这条链不通⇒甲-2 在真场里就是个空转的旋钮，夹具全绿也照样不落盘。

四段判据（末段是负对照）：
  ① 生产 settings 类：带注入 ⇒ 两字段＝路径；把两键摘掉再实例化 ⇒ 回到 None（差异必须落在这一层）。
  ② 真 dump：拿①那份 settings 构造 `CoSTEERRAGStrategyV2`，写路径的**父目录事先不存在**，
     调 rdagent 自己的 `dump_knowledge_base()` ⇒ 文件必须出现（顺手证 mkdir 那段）。
  ③ 尺子＋下一场：父进程用本仓库 `_kb_pickle_class` 读②的真品字节 ⇒ 必须认得出 V2；
     同一环境再起一次、读键＝②那枚文件 ⇒ `load_or_init_knowledge_base` 不许抛。
  ④ 负对照：拿 rdagent **真类**落一枚 V1 字节当读键 ⇒ rdagent 必须当场崩（rc≠0），
     且本仓库尺子要认出 V1。这一格决定③那圈降级是真在护一个存在的坑，还是护了个空。
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for _p in (os.path.join(REPO, "etf", "v1", "src", "config"),
           os.path.join(REPO, "etf", "v1", "src", "core"),
           os.path.join(REPO, "common", "src", "core"),
           os.path.join(REPO, "common", "src")):
    sys.path.insert(0, _p)

import official_rdagent as off  # noqa: E402

READ_KEY = "CoSTEER_KNOWLEDGE_BASE_PATH"
WRITE_KEY = "CoSTEER_NEW_KNOWLEDGE_BASE_PATH"

CHILD = r"""
import sys
from pathlib import Path
# 生产那一份 settings：FactorCoSTEER.__init__ 里 `setting = FACTOR_COSTEER_SETTINGS`
from rdagent.components.coder.factor_coder.config import FactorCoSTEERSettings
from rdagent.components.coder.CoSTEER.knowledge_management import (
    CoSTEERKnowledgeBaseV1, CoSTEERKnowledgeBaseV2, CoSTEERRAGStrategyV2)

mode = sys.argv[1]
kb = Path(sys.argv[2])
s = FactorCoSTEERSettings()
print("PREFIX_OWN=%s" % s.model_config.get("env_prefix"))
print("READ=%s" % s.knowledge_base_path)
print("WRITE=%s" % s.new_knowledge_base_path)

if mode == "dump":
    rag = CoSTEERRAGStrategyV2(settings=s,
                               former_knowledge_base_path=Path(s.knowledge_base_path)
                               if s.knowledge_base_path else None,
                               dump_knowledge_base_path=kb,
                               evolving_version=2)
    print("INIT_CLASS=%s" % type(rag.knowledgebase).__name__)
    rag.dump_knowledge_base()
    print("FILE_EXISTS=%s" % kb.exists())
    print("SIZE=%d" % (kb.stat().st_size if kb.exists() else -1))
elif mode == "load":
    rag = CoSTEERRAGStrategyV2(settings=s,
                               former_knowledge_base_path=kb,
                               dump_knowledge_base_path=kb,
                               evolving_version=2)
    print("RELOADED_CLASS=%s" % type(rag.knowledgebase).__name__)
    rag.load_dumped_knowledge_base()
    print("LOADED_DUMP_CLASS=%s" % type(rag.knowledgebase).__name__)
elif mode == "make_v1":
    kb.write_bytes(__import__("pickle").dumps(CoSTEERKnowledgeBaseV1()))
    print("V1_BYTES=%d" % kb.stat().st_size)
"""


def kv_of(stdout):
    d = {}
    for ln in stdout.splitlines():
        if "=" in ln:
            k, _, v = ln.partition("=")
            d[k] = v.strip()
    return d


TEMP = tempfile.mkdtemp(prefix="costeer_kb_probe_1005.")
KB = os.path.join(TEMP, "not_created_yet", "knowledge_base_v2.pkl")
BAD = os.path.join(TEMP, "real_v1.pkl")
ENV_PY = off._find_env_python(off._find_conda())
if ENV_PY is None:
    print("没有 rdagent 那个 conda 环境，本探针不成立")
    sys.exit(2)

BASE = off._driver_env()
fail = []


def child(mode, path, overrides):
    env = {**BASE, **overrides}
    return subprocess.run([ENV_PY, "-c", CHILD, mode, path], env=env, cwd=TEMP,
                          capture_output=True, text=True, timeout=900)


print("① 生产 settings 类读不读我们发的键（同一进程实例化两回：带注入 / 摘键）")
r = child("dump", KB, {READ_KEY: "", WRITE_KEY: KB})
kv = kv_of(r.stdout)
print("   rc=%d %s" % (r.returncode, kv if kv else (r.stderr or "")[-600:]))
if r.returncode != 0:
    fail.append("① 子进程就崩了：%s" % (r.stderr or "")[-800:])
if kv.get("WRITE") != KB:
    fail.append("① FACTOR_COSTEER_SETTINGS.new_knowledge_base_path 没读到 %s（读到的是 %r）"
                "⇒ 父类 env source 没接上，甲-2 在真场里是空转旋钮" % (KB, kv.get("WRITE")))
if kv.get("READ") not in ("", "None"):
    fail.append("① 空读键却读出了值：%r" % kv.get("READ"))

print("\n② rdagent 自己 dump 出来的真字节（写路径父目录事先不存在）")
if r.returncode == 0:
    print("   INIT_CLASS=%s FILE_EXISTS=%s SIZE=%s" % (kv.get("INIT_CLASS"), kv.get("FILE_EXISTS"), kv.get("SIZE")))
    if kv.get("INIT_CLASS") != "CoSTEERKnowledgeBaseV2":
        fail.append("② 空库起步的顶层类不是 V2：%r" % kv.get("INIT_CLASS"))
    if kv.get("FILE_EXISTS") != "True":
        fail.append("② 调了 dump_knowledge_base() 文件却没出现⇒「跨场记忆」根本不存在")
    elif kv.get("SIZE") in ("0", "-1", None):
        fail.append("② 文件是空的：%s" % kv.get("SIZE"))

print("\n③ 本仓库那把尺子读真品字节 + 下一场真类读回")
if os.path.exists(KB):
    cls = off._kb_pickle_class(KB)
    print("   bytes=%d  _kb_pickle_class=%s" % (os.path.getsize(KB), cls))
    if cls != "CoSTEERKnowledgeBaseV2":
        fail.append("③ rdagent 真品字节被自己的尺子认不出（%r）⇒ 生产里每场都会悄悄降成只写不读" % cls)
    r2 = child("load", KB, {READ_KEY: KB, WRITE_KEY: KB})
    k2 = kv_of(r2.stdout)
    print("   下一场 rc=%d RELOADED_CLASS=%s LOADED_DUMP_CLASS=%s"
          % (r2.returncode, k2.get("RELOADED_CLASS"), k2.get("LOADED_DUMP_CLASS")))
    if r2.returncode != 0:
        fail.append("③ 读回上一场自己写的库居然崩：%s" % (r2.stderr or "")[-800:])
    for key in ("RELOADED_CLASS", "LOADED_DUMP_CLASS"):
        if k2.get(key) != "CoSTEERKnowledgeBaseV2":
            fail.append("③ %s=%r 不是 V2" % (key, k2.get(key)))
else:
    fail.append("③ ②没落盘，这一格无从验")

print("\n④ 负对照：真 V1 字节当读键 ⇒ rdagent 该当场崩、尺子该认出 V1")
r3 = child("make_v1", BAD, {READ_KEY: "", WRITE_KEY: BAD})
if r3.returncode != 0:
    fail.append("④ 造不出真 V1 字节：%s" % (r3.stderr or "")[-600:])
else:
    cls_bad = off._kb_pickle_class(BAD) if os.path.exists(BAD) else None
    print("   _kb_pickle_class=%s" % cls_bad)
    if cls_bad != "CoSTEERKnowledgeBaseV1":
        fail.append("④ 尺子认不出真 V1（%r）⇒ ③那圈降级对真品没有牙" % cls_bad)
    r4 = child("load", BAD, {READ_KEY: BAD, WRITE_KEY: BAD})
    tail = " / ".join((r4.stderr or r4.stdout).strip().splitlines()[-2:])
    print("   rdagent 吃 V1：rc=%d 末两行=%s" % (r4.returncode, tail[:300]))
    if r4.returncode == 0:
        fail.append("④ 负对照失效：rdagent 拿 V1 当读键不崩⇒③的降级在护一个不存在的坑")

print("\n合计：%s" % ("①②③④ 全过 ⇒ 旋钮真接到生产 settings、库真落盘、下一场真读得回、坏版本真会被拦"
                     if not fail else "有 %d 处不合格：" % len(fail)))
for f in fail:
    print("   ✗ " + f)
print("（临时目录 %s，本探针不写仓库任何产物）" % TEMP)
sys.exit(0 if not fail else 1)
