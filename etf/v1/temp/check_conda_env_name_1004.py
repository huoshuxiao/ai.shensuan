# -*- coding: utf-8 -*-
"""一次性读数（10-04 甲）：直调解释器欠下的那两笔 conda 账，修没修上。

10-03 23:35 那场 official 支 8 秒就崩在 `CondaConf(conda_env_name=None)`。根因是
当晚把子进程从 `conda run -n rdagent python …` 改成**直接点环境里的解释器**（为了
超时可整组收尸）——壳没了，conda 顺手填的那两个东西也没人填了。修在
`official_rdagent._driver_env()`，这一支只回答「修上没有、有没有牙」。

三组读数（每组都能单独判红）：
  G1 代码层：系统 python3.10 真 import 外壳，看 `_driver_env()` 交出去的那份 env
     —— 环境名对不对、conda 目录进没进 PATH、拔掉 `_find_conda` 时这两腿是否**如实退回**
  G2 崩点本体（rdagent 环境解释器，三臂）：`get_factor_env()` 就是 traceback 里那一行
     off  臂（不给 CONDA_DEFAULT_ENV）＝**必须复现**那条 ValidationError，否则探针没牙
     on   臂（用生产那份 env）＝必须成功，且 `bin_path` 非空、指向 envs/rdagent/bin
     bare 臂（只留环境名、把 conda 目录从 PATH 拔掉）＝把「取不到 conda 时它**静默给空串**」
          这一笔拍成读数；这一臂**不算失败**（它量的是 rdagent 的降级行为，不是本仓库的修复）
  G3 生产入口那一层：`FactorRDLoop(FACTOR_PROP_SETTING)`，与 rdagent
     `app/qlib_rd_loop/factor.py:53` 同一句。**判据不是「构造返回没返回」**，是它
     当场跑的那次环境探测的**解释器版本串**：rdagent 会把 `runtime_info.py` 通过
     `LocalEnv._run` 真跑一遍（脚本第一行就是 `print(f"Python {sys.version} on …")`），
     结果落进 `pickle_cache/utils.env.run/*.pkl`。把里面那句和 `RD_PY -c sys.version`
     逐字比对 ⇒「生成的因子代码跑在哪个解释器里」从推断变成可证伪的读数。

副作用与隔离：rdagent 的会话目录/workspace/pickle_cache 路径都**相对 cwd**，所以 G3 的
cwd 是沙箱 `etf/v1/temp/probe_ws_1004/<臂>/`——每臂一枚**新鲜空壳**（里面只有两份 48M
源数据的符号链接 + `.env` 链接）。必须逐臂换新：那枚缓存的键是**脚本内容的哈希**，三臂
跑的是同一个脚本 ⇒ 不清缓存的话 off/bare 臂会直接命中 on 臂那份读数，判据恒真。
源数据只读不写，所以用符号链接、不复制三份。
生产的 `log/` 目录集合在 G3 前后各扫一次，**逐枚比对必须相同**（这条本身就是隔离的牙）。
另：`__init__` 会渲染并发出 LLM 请求（10-04 实测：一次构造留下 12 枚 `debug_tpl`），
所以这一组把 LLM 那一腿**指到死端口**（`OLLAMA_API_BASE=http://127.0.0.1:9` +
`MAX_RETRY=1`）——既不许它去挤正在跑的那条链的 ollama，也不许它把探针拖到超时。

用法：/usr/bin/python3.10 -u etf/v1/temp/check_conda_env_name_1004.py
"""
import json
import os
import shutil
import subprocess
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")
OUTDIR = os.path.join(REPO, "etf", "v1", "data", "results", "rdagent_output")
SCRATCH = os.path.join(REPO, "etf", "v1", "temp", "probe_ws_1004")
RD_PY = "/home/sunwenkun/miniconda3/envs/rdagent/bin/python"
FAILS = []


def say(msg):
    print(msg, flush=True)


def check(label, got, want):
    ok = got == want
    say("  %s %s: 实到 %r 期望 %r" % ("✅" if ok else "✗", label, got, want))
    if not ok:
        FAILS.append(label)
    return ok


def check_true(label, got, why):
    say("  %s %s: 实到 %r（%s）" % ("✅" if got else "✗", label, got, why))
    if not got:
        FAILS.append(label)
    return got


def run(cmd, cwd, env, timeout=600):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                       text=True, timeout=timeout)
    return p, time.time() - t0


def child_json():
    return ('import json, os, sys\n'
            '_REPO = %r\n'
            'for _p in (os.path.join(_REPO, "etf", "v1", "src", "config"),\n'
            '           os.path.join(_REPO, "etf", "v1", "src", "core"),\n'
            '           os.path.join(_REPO, "common", "src", "core"),\n'
            '           os.path.join(_REPO, "common", "src")):\n'
            '    sys.path.insert(0, _p)\n' % REPO)


# ── G1 代码层：外壳交出去的那份 env ──────────────────────────────────────────
G1_CHILD = child_json() + r'''
import official_rdagent as off
from config import RDAGENT_CONDA_ENV as env_name
conda = off._find_conda()
conda_dir = os.path.dirname(os.path.abspath(conda)) if conda else None

env = dict(off._driver_env())
_orig = off._find_conda
off._find_conda = lambda: None          # 负对照：假装配不到 conda
env_teeth = dict(off._driver_env())
off._find_conda = _orig

print("CHILD " + json.dumps({
    "module": off.__file__,
    "env_name": env_name,
    "conda": conda,
    "conda_dir": conda_dir,
    "path_head": env.get("PATH", "").split(":")[0],
    "path_tail_has_conda": conda_dir in env.get("PATH", "").split(":"),
    "conda_default_env": env.get("CONDA_DEFAULT_ENV"),
    "teeth_name": env_teeth.get("CONDA_DEFAULT_ENV"),
    "teeth_path_head": env_teeth.get("PATH", "").split(":")[0],
    "orig_path_head": os.environ.get("PATH", "").split(":")[0],
    "isolated": env.get("PYTHONNOUSERSITE"),
    "full_env": env,          # 只在内存里递给 G2/G3，不落盘（父进程环境含密钥）
}, ensure_ascii=False))
'''


def g1():
    say("[G1] 代码层：_driver_env() 交给子进程的那份 env（真 import，不猜）")
    p, dt = run([sys.executable, "-c", G1_CHILD], cwd=SRC,
                env={**os.environ, "PYTHONPATH": ""})
    line = next((ln for ln in (p.stdout or "").splitlines()
                 if ln.startswith("CHILD ")), None)
    if line is None:
        check("子进程出读数", (p.returncode, p.stderr[-400:]), "有 CHILD 行")
        return None
    d = json.loads(line[len("CHILD "):])
    suffix = os.path.join("common", "src", "core", "official_rdagent.py")
    check("official_rdagent 解析到 common 那份", d["module"][-len(suffix):], suffix)
    check("CONDA_DEFAULT_ENV＝本线配置的环境名", d["conda_default_env"], d["env_name"])
    check("conda 目录挂到 PATH 最前", d["path_head"], d["conda_dir"])
    check("user-site 仍被隔离", d["isolated"], "1")
    # 负对照：装配不到 conda 时 PATH 那条腿必须**退回原样**（证明它是条件加，不是恒真）。
    # 前置：父 PATH 首段本来就不能是 condabin，否则两臂恒等＝没有测试
    check_true("负对照有牙：父 PATH 首段 ≠ conda 目录",
               d["orig_path_head"] != d["conda_dir"],
               "相等则这一臂两臂恒等，不作数")
    check("拔掉 _find_conda 后 PATH 首段退回原样（负对照）",
          d["teeth_path_head"], d["orig_path_head"])
    check("环境名那一腿与 conda 无关（拔掉照样注入）", d["teeth_name"], d["env_name"])
    say(f"     用时 {dt:.1f}s｜conda={d['conda']}")
    return d


# ── G2 崩点本体：get_factor_env() 三臂 ──────────────────────────────────────
G2_CHILD = child_json() + r'''
mode = sys.argv[1]
from rdagent.components.coder.factor_coder.config import get_factor_env
out = {"mode": mode, "seen_name": os.environ.get("CONDA_DEFAULT_ENV")}
try:
    e = get_factor_env()
    out.update(ok=True, name=e.conf.conda_env_name, bin_path=e.conf.bin_path)
except Exception as ex:
    out.update(ok=False, type=type(ex).__name__, msg=str(ex).replace("\n", " ")[:300])
print("CHILD " + json.dumps(out, ensure_ascii=False))
'''


def g2(base_env):
    say("[G2] 崩点本体：rdagent 的 get_factor_env()（traceback 里那一行），三臂")
    if not os.path.exists(RD_PY):
        check("rdagent 环境解释器在场", RD_PY, "存在")
        return

    def arm(mode, mutate):
        env = dict(base_env)
        mutate(env)
        p, dt = run([RD_PY, "-c", G2_CHILD, mode, REPO], cwd=OUTDIR,
                    env=env, timeout=420)
        line = next((ln for ln in (p.stdout or "").splitlines()
                     if ln.startswith("CHILD ")), None)
        if line is None:
            return {"mode": mode, "ok": None,
                    "msg": (p.stderr or "")[-400:], "rc": p.returncode}, dt
        return json.loads(line[len("CHILD "):]), dt

    def off_(env):
        _drop_name(env)

    def bare(env):
        _drop_conda(env)

    d, dt = arm("off", off_)
    say(f"  [off 臂] 不给环境名 ⇒ ok={d.get('ok')} type={d.get('type')}"
        f"（用时 {dt:.1f}s）")
    say(f"           报错原文：{str(d.get('msg'))[:220]}")
    check("off 臂必须复现 ValidationError", d.get("type"),
          "ValidationError") if not d.get("ok") else check(
              "off 臂必须复现 ValidationError", "构造成功了(=探针没牙)",
              "ValidationError")

    d, dt = arm("on", lambda e: None)
    say(f"  [on 臂] 用生产那份 env ⇒ {json.dumps(d, ensure_ascii=False)[:200]}"
        f"（用时 {dt:.1f}s）")
    if check_true("on 臂构造成功", d.get("ok") is True, "崩点不再复现"):
        check("on 臂环境名是本线那个", d.get("name"),
              base_env["CONDA_DEFAULT_ENV"])
        check_true("on 臂 bin_path 非空（②那笔没静默降级）",
                   bool(d.get("bin_path")), "空串＝生成的因子代码会用错解释器")
        check_true("on 臂 bin_path 指向 envs/rdagent/bin",
                   "/envs/rdagent/bin" in (d.get("bin_path") or ""),
                   "这格才证明代码跑在 rdagent 环境里")

    d, dt = arm("bare", bare)
    say(f"  [bare 臂] 只给环境名、PATH 里没有 conda ⇒ "
        f"{json.dumps(d, ensure_ascii=False)[:220]}（用时 {dt:.1f}s）")
    say("           这一臂量的是 rdagent 的降级行为，**不计入失败**："
        "不报错、bin_path 给空串 ⇒ 判据看上面那格，不是看它崩不崩")
    if d.get("ok"):
        say("  读数：bare 臂 bin_path=%r" % ("非空" if d.get("bin_path") else ""))
    else:
        say("  读数：bare 臂反而抛了 %s（与预期『静默空串』不同，如实记下）"
            % d.get("type"))


# ── G3 生产入口那一层：FactorRDLoop 构造 + 它当场跑的环境探测 ────────────────
G3_CHILD = child_json() + r'''
mode = sys.argv[1]
from dotenv import load_dotenv          # 与驱动同一句，让 .env 那层也在场
load_dotenv(".env")
out = {"mode": mode}
try:
    from rdagent.app.qlib_rd_loop.conf import FACTOR_PROP_SETTING
    from rdagent.app.qlib_rd_loop.factor import FactorRDLoop
    loop = FactorRDLoop(FACTOR_PROP_SETTING)     # 与 factor.py:53 同一句
    out.update(ok=True, cls=type(loop).__name__)
except Exception as ex:
    out.update(ok=False, type=type(ex).__name__,
               msg=str(ex).replace("\n", " ")[:300])
print("CHILD " + json.dumps(out, ensure_ascii=False))
'''

READ_CHILD = r'''
import glob, json, pickle, sys
d = sys.argv[1]
res = []
for p in sorted(glob.glob(d + "/pickle_cache/utils.env.run/*.pkl")):
    try:
        with open(p, "rb") as fh:
            r = pickle.load(fh)
        res.append({"file": p.rsplit("/", 1)[-1], "exit_code": r.exit_code,
                    "stdout": (r.stdout or "")[:500]})
    except Exception as e:
        res.append({"file": p.rsplit("/", 1)[-1], "err": type(e).__name__})
print("CHILD " + json.dumps({"n": len(res), "runs": res}, ensure_ascii=False))
'''

# LLM 那一腿指到死端口：连接立刻被拒 ⇒ 探针几十秒就出读数，不去挤正在跑那条链的
# ollama（16G 纯 CPU、50% 整机占用，抢一次核就是两场一起变慢）。
# 键名来源：litellm 的 ollama 转换器 `api_base or get_secret("OLLAMA_API_BASE")`
# （litellm/llms/ollama/common_utils.py:76），rdagent 侧 `LITELLM_` 前缀映射到
# LLMSettings.max_retry / retry_wait_seconds（oai/llm_conf.py:41-42）。
DEAD_LLM = {
    "OLLAMA_API_BASE": "http://127.0.0.1:9",
    "LITELLM_MAX_RETRY": "1",
    "LITELLM_RETRY_WAIT_SECONDS": "0",
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",   # 不开这个，每臂先花 20s 抓不到网的费用表
}


def _fresh_arm(name):
    """每臂一枚新鲜空壳：pickle_cache 的键是脚本内容哈希 ⇒ 不清就等于三臂共用一份读数"""
    d = os.path.join(SCRATCH, name)
    if os.path.isdir(d):
        shutil.rmtree(d)              # 只删本支自己造的沙箱子目录（真件是符号链接，删不断）
    g = os.path.join(d, "git_ignore_folder")
    os.makedirs(g)
    for src in ("factor_implementation_source_data",
                "factor_implementation_source_data_debug"):
        os.symlink(os.path.join(SCRATCH, "git_ignore_folder", src),
                   os.path.join(g, src))
    os.symlink(os.path.join(SCRATCH, ".env"), os.path.join(d, ".env"))
    return d


def _read_env_result(d):
    """拿 RD_PY 解那枚 EnvResult（系统 python 没有 rdagent，解不开这个类）"""
    p, _ = run([RD_PY, "-c", READ_CHILD, d], cwd=SRC,
               env={**os.environ, **DEAD_LLM}, timeout=180)
    line = next((ln for ln in (p.stdout or "").splitlines()
                 if ln.startswith("CHILD ")), None)
    if line is None:
        return None
    js = json.loads(line[len("CHILD "):])
    return " ".join(r.get("stdout") or "" for r in js["runs"]) if js["n"] else None


def _text(s):
    return (s or "").replace("\n", " ")[:220]


def g3(base_env):
    say("[G3] 生产入口那一层：FactorRDLoop 构造 ⇒ 判据是它当场跑出来的**解释器版本串**")
    if not os.path.exists(RD_PY):
        check("rdagent 环境解释器在场", RD_PY, "存在")
        return
    prep_scratch()
    ver = subprocess.run([RD_PY, "-c", "import sys;print(sys.version)"],
                         capture_output=True, text=True).stdout.strip()
    sysver = sys.version
    # 有牙前置：两个解释器的版本串必须**可区分**，否则「跑对环境」这个判据是恒真的
    check_true("判据有牙：RD_PY 与系统 python 的版本串可区分",
               ver not in sysver and sysver not in ver,
               f"RD_PY={_text(ver)}｜本进程={_text(sysver)}")
    say(f"  期望出现在环境探测输出里的那串：{ver}")

    prod_log_before = _ls(os.path.join(OUTDIR, "log"))

    def arm(name, mutate):
        d = _fresh_arm(name)
        env = dict(base_env)
        env.update(DEAD_LLM)
        mutate(env)
        t0 = time.time()
        try:
            p, _ = run([RD_PY, "-c", G3_CHILD, name], cwd=d, env=env, timeout=240)
            rc, err = p.returncode, (p.stderr or "")
            line = next((ln for ln in (p.stdout or "").splitlines()
                         if ln.startswith("CHILD ")), None)
        except subprocess.TimeoutExpired as e:
            rc, err, line = None, (e.stderr or b"").decode()[-400:], None
        got = json.loads(line[len("CHILD "):]) if line else {
            "ok": None, "type": "无读数", "msg": _text(err)}
        # 变异器自己的牙：这一臂的 PATH 首段真被改掉了没有（没改掉＝两臂恒等，读数不作数）
        got["mutated"] = (env.get("PATH", "").split(":")[0]
                          != base_env.get("PATH", "").split(":")[0]
                          or "CONDA_DEFAULT_ENV" not in env)
        res = _read_env_result(d)
        got["res"] = res
        say(f"  [{name} 臂] 用时 {time.time() - t0:.1f}s rc={rc}｜构造 ok={got.get('ok')} "
            f"type={got.get('type')}｜环境探测缓存 {0 if res is None else 1} 枚")
        if not got.get("ok"):
            say(f"           构造报错原文：{_text(got.get('msg'))}")
        if res is not None:
            say(f"           环境探测原文：{_text(res)}")
        return got

    d = arm("on", lambda e: None)
    check_true("on 臂：环境探测真跑出 RD_PY 的版本串（代码会跑在对的环境）",
               d.get("res") and ver in d["res"],
               "空/None=这一层没跑到环境探测；不同版本串=用错解释器")

    d = arm("off", _drop_name)
    check("off 臂复现 conda 那一类崩（探针有牙）", d.get("type"), "ValidationError")
    check("off 臂跑不到环境探测（崩在它前面）", d.get("res"), None)

    d = arm("bare", _drop_conda)
    say("  [bare 臂] 这一臂量的是 rdagent 的降级行为，**不计入失败**：它给空 bin_path 时"
        "不报错，只看那次环境探测落在哪个解释器上")
    if d.get("res") is None:
        say(f"           读数：bare 臂没跑出环境探测（{d.get('type')}）⇒ 空 bin_path 那臂"
            "连探测都进不去，比静默更糟，如实记下")
    elif ver in d["res"]:
        say("           读数：bare 臂版本串**仍相同** ⇒『用错解释器』这一格在这一层没被证明，"
            "结论只能停在 bin_path='' 那格")
    else:
        say(f"           读数：bare 臂跑出的是另一个解释器 ⇒ {_text(d['res'])[:120]}"
            "＝「生成的因子代码跑在错环境」这一格拍到实证")

    check("生产 log 会话目录一个没动（隔离有牙）",
          _ls(os.path.join(OUTDIR, "log")), prod_log_before)
    here = set()
    for name in ("on", "off", "bare"):
        d = os.path.join(SCRATCH, name)
        here |= {f"{name}/{x}" for x in (_ls(os.path.join(d, "log"))
                                         | _ls(os.path.join(d, "pickle_cache")))}
    say(f"  本场在沙箱里新建（三臂各自隔离）：{len(here)} 枚 {sorted(here)[:4]}")


def _ls(d):
    try:
        return set(os.listdir(d))
    except OSError:
        return set()


def _drop_name(env):
    """off 臂：把修上去的那个键拿掉，复现 10-03 23:35 的崩点"""
    env.pop("CONDA_DEFAULT_ENV", None)


def _drop_conda(env):
    """bare 臂：环境名留着、但 PATH 里没有 conda —— 量 rdagent 的静默降级"""
    head, _, rest = env.get("PATH", "").partition(":")
    if os.path.basename(head) == "condabin":
        env["PATH"] = rest


def prep_scratch():
    """备一次、三臂共用只读物：`factor_implementation_source_data`（48M）+ `.env`。

    不能拿生产 `rdagent_output` 当 cwd——第⑧步折内可能再起驱动，两边同时往一处
    建会话目录，我事后清扫就会误删别人在跑的那一枚。rdagent 这些路径都相对 cwd，
    所以换目录＝换写入目标；真正被写的 `log/`、`pickle_cache/`、`RD-Agent_workspace/`
    每臂一枚（`_fresh_arm`），这里只放**只读**的源数据，各臂符号链接过来。
    """
    g = os.path.join(SCRATCH, "git_ignore_folder")
    os.makedirs(g, exist_ok=True)
    for name in ("factor_implementation_source_data",
                 "factor_implementation_source_data_debug"):
        dst = os.path.join(g, name)
        if not os.path.isdir(dst):
            shutil.copytree(os.path.join(OUTDIR, "git_ignore_folder", name), dst)
    src_env = os.path.join(OUTDIR, ".env")
    if os.path.isfile(src_env) and not os.path.isfile(os.path.join(SCRATCH, ".env")):
        shutil.copy2(src_env, os.path.join(SCRATCH, ".env"))
    return g


def main():
    say("仓库 %s｜系统解释器 %s" % (REPO, sys.executable))
    d = g1()
    # G2/G3 的子进程 env ＝ 生产那一刻外壳**真会交出去**的那一份（G1 从真件里拿的），
    # 不是我照着重拼一份 —— 这样「修上了」才是对生产说的
    base = d["full_env"] if d and d.get("full_env") else dict(os.environ)
    if not (d and d.get("full_env")):
        say("  ⚠️ 没拿到真件那份 env，G2/G3 退回用本进程 env（读数对生产不算数）")
    g2(base)
    g3(base)
    say("\n" + ("全部通过" if not FAILS else "失败项: " + ", ".join(FAILS)))
    raise SystemExit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
