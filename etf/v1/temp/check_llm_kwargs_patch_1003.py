# -*- coding: utf-8 -*-
"""夹具：`RDAGENT_LLM_KWARGS` 那层 litellm 补丁到底有没有牙（10-03 乙+）。

要防的两件事，都是「开关装了但静默失效」这一类：
  1. rdagent 的 backend 是 `from litellm import completion` —— **早绑定**。
     补丁打在它之后，它手里那份还是原函数，注入清单一个都进不去，而驱动照样跑
     三小时、日志长得一模一样。⇒ 必须有反序那一臂**当场抛**。
  2. 坏配置（少个引号、写成嵌套对象）静默退回「不注入」＝今天那副 0 轮的老样子。
     ⇒ 解析必须抛，不能吞。

三档：
  父进程  F1–F6  纯假件（不 import 真 litellm / 真 rdagent，系统 python3.10 就能跑）
  子进程  F7a    真件·正确顺序：先补丁后 import rdagent ⇒ _verify 必须放行，且绑定就是补丁
  子进程  F7b    真件·反序：先 import rdagent 后补丁 ⇒ _verify 必须判红（这一臂红才算夹具成立）
  子进程  F8     整条 `driver.main()` 真跑一遍：只把**循环入口**换成假件（backend 是真件），
                 于是「补丁打在 import 之前」这个次序被真早绑定检验一次，全程 0 次 LLM 调用。
                 on 臂必须看到补丁进 backend；off 臂必须看到原函数 + 外壳念『未设置』。
F7a/F7b/F8 要用 **rdagent 环境的解释器** 跑才有真件；拿系统 python3.10 跑会如实打「跳过」
（跳过≠通过，报告里要分开念）。

用法：
  /usr/bin/python3.10 etf/v1/temp/check_llm_kwargs_patch_1003.py          # 只跑 F1–F6
  env PYTHONPATH= /home/sunwenkun/miniconda3/envs/rdagent/bin/python \
      etf/v1/temp/check_llm_kwargs_patch_1003.py                          # 连真件两臂一起跑
"""
import copy
import json
import os
import subprocess
import sys
import types

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DRIVER_DIR = os.path.join(REPO, "common", "src", "core")
sys.path.insert(0, DRIVER_DIR)
import rdagent_driver as drv  # noqa: E402  被测对象（本文件不许 import 项目其他模块）

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  %s %s: 实到 %r 期望 %r" % ("✅" if ok else "✗", label, got, want))
    if not ok:
        FAILS.append(label)
    return ok


def check_true(label, cond, note=""):
    ok = bool(cond)
    print("  %s %s %s" % ("✅" if ok else "✗", label, note))
    if not ok:
        FAILS.append(label)
    return ok


class FakeLiteLLM(types.ModuleType):
    """假 litellm：记下每次 completion 收到的 kwargs"""

    def __init__(self):
        super().__init__("litellm")
        self.calls = []

    def completion(self, *args, **kwargs):
        self.calls.append(kwargs)
        return "RESP"


def fake_backend_module(fn, name="rdagent.oai.backend.litellm"):
    mod = types.ModuleType(name)
    mod.completion = fn
    sys.modules[name] = mod
    return mod


# ---------------------------------------------------------------- 父进程：假件六格
def arm_fakes():
    print("[F1] 留空＝不注入")
    check("空串解析", drv._parse_llm_kwargs(""), {})
    check("None 解析", drv._parse_llm_kwargs(None), {})
    check("只有空格", drv._parse_llm_kwargs("   "), {})

    print("[F2] 合法注入清单（乙+ 实测那一串）")
    raw = '{"think": false, "num_ctx": 16384}'
    check("解析结果", drv._parse_llm_kwargs(raw), {"think": False, "num_ctx": 16384})

    print("[F3] 坏配置必须抛，不许静默退回不注入")
    for bad, why in [('{"think": fals}', "少一个字母的 JSON"),
                     ('think=False', "写成 k=v 不是 JSON"),
                     ('[1,2]', "顶层不是对象")]:
        try:
            drv._parse_llm_kwargs(bad)
            raised = None
        except ValueError as e:
            raised = type(e).__name__
        check_true(f"{why} ⇒ 抛 ValueError", raised == "ValueError", f"实到 {raised!r}")

    print("[F4] 值必须是标量（嵌套对象 litellm 那一层不认）")
    try:
        drv._parse_llm_kwargs('{"options": {"think": false}}')
        raised = None
    except ValueError:
        raised = "ValueError"
    check_true("嵌套 dict ⇒ 抛", raised == "ValueError", f"实到 {raised!r}")

    print("[F5] 补丁真把 kwargs 递给了底层 completion（假件）")
    fake = FakeLiteLLM()
    sys.modules["litellm"] = fake
    patch = drv._patch_llm_kwargs({"think": False, "num_ctx": 16384})
    check("litellm.completion 已被换成补丁", fake.completion is patch, True)
    fake.completion(messages=[{"role": "user", "content": "x"}])
    check_true("底层收到 1 次调用", len(fake.calls) == 1, f"实到 {len(fake.calls)}")
    got = fake.calls[0] if fake.calls else {}
    check("think 进去了", got.get("think"), False)
    check("num_ctx 进去了", got.get("num_ctx"), 16384)
    # 调用方显式给的必须赢过补丁（补丁只填缺省）
    fake.calls.clear()
    fake.completion(think=True, extra="keep")
    check("调用方显式 think=True 不被覆盖", fake.calls[0].get("think"), True)
    check("调用方其余参数原样透传", fake.calls[0].get("extra"), "keep")

    print("[F6] _verify_llm_patch：绑对放行 / 绑错必须判红")
    fake_backend_module(patch)                       # 情形一：rdagent 拿到的就是补丁
    try:
        drv._verify_llm_patch(patch)
        v1 = "OK"
    except RuntimeError:
        v1 = "RAISED"
    check_true("绑对了 ⇒ 放行（上面那行是它的读数）", v1 == "OK", f"实到 {v1}")
    stale = fake_backend_module(FakeLiteLLM().completion, "rdagent.oai.backend.litellm")
    try:
        drv._verify_llm_patch(patch)
        v2 = None
    except RuntimeError as e:
        v2 = str(e)
    check_true("早绑定（rdagent 手里还是原函数）⇒ 抛 RuntimeError", v2 is not None,
               "" if v2 else "实到：没抛，这道闸是空的")
    check_true("报错文案点名『不要跑这一场』", bool(v2 and "不要跑" in v2), "")
    del stale
    return 0


# ---------------------------------------------------------- 子进程：真件两臂（早绑定实测）
def arm_real(order):
    """order='before' 先补丁后 import（应当放行）；'after' 反序（应当判红）"""
    sys.path.insert(0, DRIVER_DIR)
    import rdagent_driver as d
    kwargs = {"think": False, "num_ctx": 16384}
    if order == "before":
        patch = d._patch_llm_kwargs(kwargs)
        from importlib import import_module
        mod = import_module("rdagent.oai.backend.litellm")
        bound_same = mod.completion is patch
    else:
        from importlib import import_module
        mod = import_module("rdagent.oai.backend.litellm")
        patch = d._patch_llm_kwargs(kwargs)
        bound_same = mod.completion is patch
    verdict = None
    try:
        d._verify_llm_patch(patch)
        verdict = "PASS"
    except RuntimeError:
        verdict = "RED"
    except Exception as e:  # 别的异常也算没放行，但要把类型念出来
        verdict = f"{type(e).__name__}"
    print("REAL %s: 绑定同一个对象=%s verify=%s" % (order, bound_same, verdict))
    # 两臂都要求「按设计走」⇒ rc=0：before 必须放行，after 必须判红（反序那臂红才算夹具成立）。
    if order == "before":
        exit_0 = bound_same and verdict == "PASS"
    else:
        exit_0 = (not bound_same) and verdict == "RED"
    return 0 if exit_0 else 1


# -------------------------------------------- 子进程：driver.main() 接线次序（不发一次 LLM）
def arm_main(mode):
    """mode='on' 设了 RDAGENT_LLM_KWARGS；'off' 留空。

    只把 **rdagent 的循环入口**换成假件（`sys.modules` 里塞一枚
    `rdagent.app.qlib_rd_loop.factor`），backend 那侧 import 的是**真件**——
    于是 `driver.main()` 里「先打补丁、后 import」这两行的次序被真早绑定检验一次，
    而整场一个 token 都不发。假件被调用时把当场看到的 litellm/backend 绑定关系念回来。
    """
    import types
    sys.path.insert(0, DRIVER_DIR)
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "llm_kwargs_child_out_1003")
    if mode == "on":
        os.environ[drv.LLM_KWARGS_ENV] = '{"think": false, "num_ctx": 16384}'
    else:
        os.environ.pop(drv.LLM_KWARGS_ENV, None)

    obs = {}

    fake = types.ModuleType("rdagent.app.qlib_rd_loop.factor")

    def fake_main(loop_n=1):
        import litellm
        from importlib import import_module
        mod = import_module("rdagent.oai.backend.litellm")   # 真 backend，早绑定在此发生
        obs["called"] = True
        obs["loop_n"] = loop_n
        obs["patched"] = hasattr(litellm.completion, "__rdagent_llm_kwargs__")
        obs["bound"] = mod.completion is litellm.completion
        obs["kwargs"] = getattr(litellm.completion, "__rdagent_llm_kwargs__", None)

    fake.main = fake_main
    sys.modules["rdagent.app.qlib_rd_loop.factor"] = fake

    rc = "NO_EXIT"
    try:
        sys.argv = ["rdagent_driver.py", "--out", out_dir, "--loops", "1"]
        drv.main()
    except SystemExit as e:
        rc = e.code
    except Exception as e:
        rc = f"{type(e).__name__}: {e}"
    finally:
        if os.path.isdir(out_dir) and not os.listdir(out_dir):
            os.rmdir(out_dir)
    print("REALMAIN %s: rc=%s %s" % (mode, rc,
                                     json.dumps(obs, ensure_ascii=False, sort_keys=True)))
    if mode == "on":
        ok = (rc == 0 and obs.get("called") and obs.get("patched")
              and obs.get("bound") and obs.get("loop_n") == 1
              and obs.get("kwargs") == {"think": False, "num_ctx": 16384})
    else:
        ok = (rc == 0 and obs.get("called") and obs.get("bound")
              and not obs.get("patched") and obs.get("kwargs") is None)
    return 0 if ok else 1


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        return arm_real(sys.argv[2])
    if len(sys.argv) > 2 and sys.argv[1] == "--child-main":
        return arm_main(sys.argv[2])

    rc = arm_fakes()

    print("\n[F7] 真件两臂（需要 rdagent 环境：先 import litellm/rdagent 不联网）")
    try:
        import litellm  # noqa: F401
        import rdagent.oai.backend.litellm  # noqa: F401
        have = True
    except Exception as e:
        print(f"  ℹ️ 跳过：本解释器（{sys.executable}）import 真件失败 {type(e).__name__}: {e}")
        print("     跳过≠通过。F1–F6 已在假件上把语义钉死，早绑定这一格要用 rdagent 环境补。")
        have = False
    if have:
        for order, want_rc in (("before", 0), ("after", 0)):
            p = subprocess.run([sys.executable, os.path.abspath(__file__), "--child", order],
                               capture_output=True, text=True, cwd=REPO)
            tail = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("REAL ")]
            ok = p.returncode == want_rc
            print("  %s F7 %s: rc=%s %s" % ("✅" if ok else "✗", order, p.returncode,
                                            tail[-1] if tail else (p.stderr or "")[-200:]))
            if not ok:
                FAILS.append(f"F7-{order}")

        print("\n[F8] driver.main() 的接线次序（真 driver + 真 backend，循环入口换成假件，"
              "一次 LLM 都不发）")
        for mode in ("on", "off"):
            p = subprocess.run([sys.executable, os.path.abspath(__file__),
                                "--child-main", mode],
                               capture_output=True, text=True, cwd=REPO)
            lines = (p.stdout or "").splitlines()
            line = next((ln for ln in lines if ln.startswith("REALMAIN ")), None)
            obs, child_rc = {}, None
            if line is not None:
                child_rc = line.split("rc=", 1)[1].split(" ", 1)[0]
                try:
                    obs = json.loads(line[line.index("{"):])
                except ValueError:
                    obs = {}
            printed_idle = any("未设置" in ln for ln in lines)
            if not obs:
                ok = False
            elif mode == "on":
                ok = bool(obs.get("patched")) and bool(obs.get("bound")) \
                    and obs.get("kwargs") == {"think": False, "num_ctx": 16384} \
                    and obs.get("loop_n") == 1
            else:
                ok = not obs.get("patched") and bool(obs.get("bound")) and printed_idle
            print("  %s F8 %s: 子rc=%s %s%s" % (
                "✅" if ok else "✗", mode, child_rc,
                line if line else (p.stderr or "")[-300:],
                "" if mode != "off" else "  |外壳念了『未设置』=%s" % printed_idle))
            if not ok:
                FAILS.append(f"F8-{mode}")
    print("\n" + ("全部通过" if not FAILS else "失败项: " + ", ".join(FAILS)))
    return 1 if FAILS else rc


if __name__ == "__main__":
    raise SystemExit(main())
