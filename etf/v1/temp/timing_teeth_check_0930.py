# -*- coding: utf-8 -*-
"""负对照：证明 `tests/test_etf_admission.py` 第 14 节那两条时序钉子**有牙**。

09-29 实测「调仓日早一根 K 线」值 27.8~29.9pp 净年化/年，而旧三条 `topk_rebalance`
测试全用平值池（毛收益恒 0）⇒ 挪一天差是 0，那条链上没有任何钉子。新补的两条用
变价池，本脚本把它俩的牙量出来：同一份测试体在三个臂下各跑一遍。

    臂 A  真模块（从 src 正常 import）                       ⇒ 两条都该过
    臂 B  /tmp 副本，只把 `lo, hi = i + 1, …` 拔成 `i + 2, …`  ⇒ 两条都该红
    臂 C  /tmp 副本，与 src **逐字节相同**（只走同一套装载路径） ⇒ 两条都该过

C 的意义：红必须来自那一行，不能来自"我从 /tmp 加载了一个模块"这件事本身。
没有 C 的话，B 的红可能是装载方式造的假红（夹具根本没跑到断言）。

判据一行未改：本脚本不碰生产文件、不写 `data/`，副本全在 `temp/tmp_timing_teeth_0930/`。
跑法：
    cd etf/v1 && /usr/bin/python3.10 temp/timing_teeth_check_0930.py
"""
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V1 = os.path.dirname(HERE)
SRC = os.path.join(V1, "src")
TESTS = os.path.join(V1, "tests")
WORK = os.path.join(HERE, "tmp_timing_teeth_0930")

ORIG = os.path.join(SRC, "etf_admission.py")
TARGET_LINE = "    lo, hi = i + 1, min(i + 1 + hold, len(days))"
PATCHED_LINE = "    lo, hi = i + 2, min(i + 2 + hold, len(days))"

TESTS_TO_RUN = ["test_rebalance_credits_boundary_interval_to_new_basket",
                "test_rebalance_boundary_interval_needs_the_signal_a_day_early"]


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def load_as_etf_admission(path):
    """把 path 当 `etf_admission` 装进 sys.modules（测试体的 `import etf_admission` 会拿到它）。"""
    for m in list(sys.modules):
        if m == "etf_admission" or m == "test_etf_admission":
            del sys.modules[m]
    spec = importlib.util.spec_from_file_location("etf_admission", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["etf_admission"] = mod
    spec.loader.exec_module(mod)
    return mod


def run_test_bodies(ea_path):
    """在给定 etf_admission 实现下导入测试模块并跑两条测试体，返回 {测试名: 'pass'/'FAIL: 摘要'}。"""
    load_as_etf_admission(ea_path)
    spec = importlib.util.spec_from_file_location(
        "test_etf_admission", os.path.join(TESTS, "test_etf_admission.py"))
    t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t)
    out = {}
    for name in TESTS_TO_RUN:
        try:
            getattr(t, name)()
            out[name] = "pass"
        except AssertionError as e:
            body = [ln.strip() for ln in str(e).splitlines() if ln.strip()]
            brief = next((ln for ln in body if "Mismatched elements" in ln
                          or ln.startswith("assert")), body[-1] if body else "")
            out[name] = f"FAIL {brief[:72]}"
    return out


def main():
    os.makedirs(WORK, exist_ok=True)
    sys.path.insert(0, SRC)
    sys.path.insert(0, TESTS)
    # 与 tests/conftest.py 同一套隔离：数据目录指临时、规模闸关掉
    tmp_iso = os.path.join(WORK, "isodata")
    for k, v in [("ETF_DATA_DIR", os.path.join(tmp_iso, "data")),
                 ("ETF_BASE_DATA_DIR", os.path.join(tmp_iso, "base_data")),
                 ("ETF_REPORT_DIR", os.path.join(tmp_iso, "report")),
                 ("ETF_LOG_DIR", os.path.join(tmp_iso, "log")),
                 ("ETF_FREQ", "daily"),
                 ("ETF_RDAGENT_OFFICIAL_FALLBACK", "false"),
                 ("ETF_MIN_SCALE", "0")]:
        os.environ[k] = v
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("ETF_LLM_BASE_URL", None)

    print("=" * 78)
    print("负对照 · 第 14 节两条时序钉子的牙（臂 A 真 / 臂 B 拔一行 / 臂 C 逐字节同）")
    print("=" * 78)

    src_copy = os.path.join(WORK, "ea_src_snapshot.py")
    patch_copy = os.path.join(WORK, "ea_lo_i_plus_2.py")
    ident_copy = os.path.join(WORK, "ea_identical.py")
    shutil.copy2(ORIG, src_copy)
    shutil.copy2(ORIG, ident_copy)

    text = open(ORIG, encoding="utf-8").read()
    n = text.count(TARGET_LINE)
    if n != 1:
        print(f"[夹具失败] 要拔的那一行在 {ORIG} 里出现 {n} 次（须恰好 1 次）—— 口径变了，停止")
        return 1
    open(patch_copy, "w", encoding="utf-8").write(text.replace(TARGET_LINE, PATCHED_LINE))

    diff = subprocess.run(["diff", "-u", src_copy, patch_copy],
                          capture_output=True, text=True).stdout
    changed = [ln for ln in diff.splitlines()
               if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
    if len(changed) != 2:
        print(f"[夹具失败] B 臂副本与原件的差异行有 {len(changed)} 条（须恰好 2 条：一删一增）")
        print(diff)
        return 1
    d2 = subprocess.run(["diff", "-q", src_copy, ident_copy], capture_output=True, text=True)
    print(f"[副本] 原件 sha={sha256(src_copy)}｜B 臂 sha={sha256(patch_copy)}"
          f"｜C 臂 sha={sha256(ident_copy)}")
    print(f"[副本] B 臂差异行数 = {len(changed)}（− `{TARGET_LINE.strip()}` / + `{PATCHED_LINE.strip()}`）")
    print(f"[副本] C 臂 diff -q rc = {d2.returncode}（0 = 与原件逐字节相同）")

    arms = {"A 真模块": ORIG, "B 拔 lo→i+2": patch_copy, "C 逐字节同": ident_copy}
    results = {}
    for label, path in arms.items():
        print(f"\n---- 臂 {label} ----")
        r = run_test_bodies(path)
        results[label] = r
        for name, verdict in r.items():
            print(f"  {name:<52s} {verdict}")

    # ---- 六格判定 ----
    want = {"A 真模块": ["pass", "pass"],
            "B 拔 lo→i+2": ["FAIL", "FAIL"],
            "C 逐字节同": ["pass", "pass"]}
    bad = []
    for label, expects in want.items():
        got = list(results[label].values())
        for name, got_v, exp in zip(TESTS_TO_RUN, got, expects):
            ok = (got_v == "pass") if exp == "pass" else got_v.startswith("FAIL")
            if not ok:
                bad.append(f"{label} / {name}: 期望 {exp} 实得 {got_v}")
    open(os.path.join(WORK, "teeth_readout.txt"), "w", encoding="utf-8").write(
        "\n".join(f"{label}\t{n}={ver}"
                  for label, r in results.items() for n, ver in r.items()) + "\n")

    print("\n===== 六格判定 =====")
    if bad:
        for b in bad:
            print(f"  ✗ {b}")
        print("\n[结论] 牙没量出来 —— 上面任何一格不对，第 14 节的钉子就不能算有牙")
        return 1
    print("  ✅ 六格全对：A 过 / B 两条都红 / C 过")
    print("[结论] B 的红来自那一行（C 用同一套装载路径仍过 ⇒ 不是装载方式造的假红）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
