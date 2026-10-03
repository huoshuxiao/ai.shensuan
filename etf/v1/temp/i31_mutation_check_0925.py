# -*- coding: utf-8 -*-
"""变异检查：把四道闸逐一中和，确认对应测试真的会红（防恒真断言）。

复制成 `<sandbox>/etf/v1/{src,tests,pytest.ini}` + `<sandbox>/common` 软链，
是为了让 `_bootstrap.py` 按自身路径反推出的仓库根（这里 = sandbox）真的能
找到 common/ —— 否则变异副本连 import 都过不去，pytest 会以
"Interrupted: 1 error during collection" 退出，被误判成"测试抓住了变异"。
所以本脚本先跑一次**未变异对照**，必须全绿才继续。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
V1 = os.path.join(REPO, "etf", "v1")
SANDBOX = os.path.join(HERE, "i31_mutation_sandbox")

MUTATIONS = [
    ("#29 区间闸失效（越界也放行）", "src/run_live.py",
     "    lo, hi = RISK_BOUNDS[key]\n    if not lo <= v <= hi:",
     "    lo, hi = RISK_BOUNDS[key]\n    if False:"),
    ("#30 重算闸空挂（原样返回）", "src/main.py",
     "    out, n_fixed, n_dropped = [], 0, 0",
     "    return list(factors or [])\n    out, n_fixed, n_dropped = [], 0, 0"),
    ("#31 来源一刀切 pipeline", "src/main.py",
     '                src = f.get("source") or "pipeline"',
     '                src = "pipeline"'),
    ("#31 存量行不改写（去掉 extra）", "src/main.py",
     '                           extra={"source": src})',
     '                           )'),
    ("#32 只写不校验", "src/run_feedback.py",
     "    res = fn()\n    if before is None:",
     "    res = fn()\n    return res\n    if before is None:"),
]


def build():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    dst_v1 = os.path.join(SANDBOX, "etf", "v1")
    os.makedirs(dst_v1)
    for d in ("src", "tests"):
        shutil.copytree(os.path.join(V1, d), os.path.join(dst_v1, d),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy(os.path.join(V1, "pytest.ini"),
                os.path.join(dst_v1, "pytest.ini"))
    # 仓库根的其他成员用软链补齐（只读引用，不改生产）。common 必须是软链：
    # 它同时是 sys.path 成员，复制一份会让 FactorLibrary 这类模块出现两个
    # 实例，副本里 monkeypatch 的模块对象就和测试导入的不是同一个了。
    for name in os.listdir(REPO):
        if name in ("etf", "shell", ".git"):
            continue
        src = os.path.join(REPO, name)
        if os.path.isdir(src):
            os.symlink(src, os.path.join(SANDBOX, name))


def pytest(tests):
    r = subprocess.run([sys.executable, "-m", "pytest", *tests,
                        "-p", "no:cacheprovider", "-q"],
                       cwd=os.path.join(SANDBOX, "etf", "v1"),
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    # pytest 的结果行是 `35 passed in 1.69s` / `2 failed, 33 passed ...`，
    # 末行常常被插件的 warning 挤掉，所以按内容找而不是按位置取
    summary = next((l for l in reversed(lines)
                    if any(w in l for w in ("passed", "failed", "error"))),
                   lines[-1] if lines else "?")
    return r.returncode, summary, out


def mutate(path, old, new):
    full = os.path.join(SANDBOX, "etf", "v1", path)
    with open(full, encoding="utf-8") as f:
        content = f.read()
    if old not in content:
        return False
    with open(full, "w", encoding="utf-8") as f:
        f.write(content.replace(old, new, 1))
    return True


build()
code, tail, out = pytest(["tests/test_llm_boundary_guards.py"])
if code != 0:
    print(f"❌ 对照组（未变异）就不绿，检查副本环境：{tail}")
    print(out[-1500:])
    sys.exit(1)
print(f"✅ 对照：副本未变异 {tail}")

results = []
for label, path, old, new in MUTATIONS:
    build()
    if not mutate(path, old, new):
        print(f"❌ {label}: 锚点没找到，变异未生效")
        results.append(False)
        continue
    code, tail, out = pytest(["tests/test_llm_boundary_guards.py"])
    if "error during collection" in out:
        print(f"❌ {label}: 收集期就崩（不算抓住）| {tail}")
        results.append(False)
        continue
    red = code != 0
    victims = [l.split(" - ")[0].replace("FAILED ", "")
               for l in out.splitlines() if l.startswith("FAILED ")]
    print(f"  {'✅ 变异被抓住' if red else '❌ 变异后仍全绿（断言恒真）'} | "
          f"{label} | {tail}")
    if victims:
        print(f"      变红用例: {', '.join(victims[:4])}"
              f"{'…' if len(victims) > 4 else ''}")
    results.append(red)

shutil.rmtree(SANDBOX, ignore_errors=True)
print("\n🎉 全部变异被逐条抓住" if all(results) else "\n⚠️ 有变异逃过测试")
