# -*- coding: utf-8 -*-
"""shell/<线>/ → <线>/v1/temp/ 的路径修正 + 逐站点实算对拍（第 2 版，带 --apply）。

改法（脚本从仓库根下 2 层变 3 层，所以反推表达式恰好多一跳）：
  A. `"..", ".."`                → `"..", "..", ".."`
  B. `os.path.dirname(` × k(≥2)  → 外面再套一层
只改 **temp 顶层**的脚本；子目录里的归档副本（snap_*/config.py、tmp_*/config_copy.py）
当年就在 3 层、搬家后还在 3 层，深度没变 ⇒ 一行不动（脚本里用 EXCLUDE 显式挡掉并打印理由）。

对拍判据（非恒真）：逐文件按顺序 exec 模块级赋值，得到改名前后的解析值两张表——
  · 旧值落在 `shell/…` 的：新值必须落在对应 `<线>/v1/temp/…`（预期同批搬家），否则记 ❌
  · 其余（反推仓库根 / 兄弟目录）：新旧必须**逐字符相等**，否则记 ❌
  · 解析不出来的行（用了跑不动的右值）单独计数，不算通过也不静默
"""
import ast
import os
import re
import sys

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
LINES = {"stock/v1/temp": "shell/stock", "etf/v1/temp": "shell/etf",
         "live2etf/v1/temp": "shell/live2etf"}
# 归档副本：深度未变，禁改
EXCLUDE_SUBSTR = ("/snap_0925_daily_selfeval/", "/snap_0925/", "/tmp_conc_0928/",
                  "/rerun_backups/", "/tmp_")
TWO, THREE = '"..", ".."', '"..", "..", ".."'
D = "os.path.dirname("


def outer_close(text, first_open):
    depth = 0
    for i in range(first_open, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("括号不配对")


def bump_dirname_chains(text):
    sites = []
    for m in re.finditer(re.escape(D), text):
        s = m.start()
        if text[max(0, s - len(D)):s] == D:
            continue
        k, pos = 0, s
        while text[pos:pos + len(D)] == D:
            k, pos = k + 1, pos + len(D)
        if k < 2 or k > 4:
            continue
        oc = outer_close(text, s + len(D) - 1)
        if "__file__" not in text[pos:oc]:
            continue
        sites.append((s, oc, k))
    for s, oc, _k in reversed(sites):
        text = text[:s] + D + text[s:oc + 1] + ")" + text[oc + 1:]
    return text, len(sites)


def resolve(path, rel):
    """该脚本搬家前的绝对路径（用于对拍）"""
    for d, o in LINES.items():
        if rel.startswith(d + "/"):
            return os.path.join(REPO, o, rel[len(d) + 1:])
    return path


def parse_table(src_text, file_path):
    """顺序 exec 模块级「名字 = 纯路径表达式」，返回 {名字: 值}；跑不动的跳过"""
    import os as _os
    ns = {"__file__": file_path, "os": _os, "sys": sys, "__name__": "__audit__"}
    out = {}
    try:
        tree = ast.parse(src_text)
    except SyntaxError:
        return out
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        try:
            mod = ast.Module(body=[node], type_ignores=[])
            code = compile(mod, "<audit>", "exec")
            exec(code, ns)
        except Exception:
            continue
        v = ns.get(tgt.id)
        if isinstance(v, str):
            out[tgt.id] = v
    return out


def main():
    do = "--apply" in sys.argv
    files, skipped = [], []
    for d in LINES:
        for root, _dirs, fs in os.walk(os.path.join(REPO, d)):
            files += [os.path.join(root, f) for f in fs if f.endswith((".py", ".sh"))]
    touched = 0
    n_a_sites = n_b_sites = 0
    bad, moved_ok, unresolvable, changed_vars = [], [], [], 0
    for path in sorted(files):
        rel = os.path.relpath(path, REPO)
        why = next((x for x in EXCLUDE_SUBSTR if x in "/" + rel), None)
        if why:
            skipped.append((rel, why))
            continue
        orig = open(path, encoding="utf-8").read()
        text = orig
        guard = "\x00T3\x00"
        t = text.replace(THREE, guard)
        a = t.count(TWO)
        text = t.replace(TWO, THREE).replace(guard, THREE)
        text, b = bump_dirname_chains(text)
        if text == orig:
            continue
        touched += 1
        n_a_sites += a
        n_b_sites += b
        old_path = resolve(path, rel)
        if len(orig.split("\n")) != len(text.split("\n")):
            bad.append((rel, 0, "行数变化", f"{len(orig.splitlines())}→{len(text.splitlines())}"))
        vo = parse_table(orig, old_path)
        vn = parse_table(text, path)
        for name in sorted(set(vo) | set(vn)):
            if name not in vo or name not in vn:
                unresolvable.append((rel, name, "只在一侧解析出来"))
                continue
            o, n = vo[name], vn[name]
            if isinstance(o, str) and isinstance(n, str):
                if os.path.normpath(o) == os.path.normpath(n):
                    continue
                o, n = os.path.normpath(o), os.path.normpath(n)
            if o == n:
                continue
            changed_vars += 1
            if isinstance(o, str) and o.startswith(os.path.join(REPO, "shell")):
                tail = os.path.relpath(o, os.path.join(REPO, "shell"))
                want = os.path.normpath(os.path.join(REPO, tail)) if False else None
                # 期望：shell/<线>/x → <线>/v1/temp/x
                for d, pre in (("stock", "shell/stock"), ("etf", "shell/etf"), ("live2etf", "shell/live2etf")):
                    if o.startswith(os.path.join(REPO, pre)):
                        want = os.path.normpath(os.path.join(REPO, f"{d}/v1/temp",
                                                             os.path.relpath(o, os.path.join(REPO, pre))))
                if n == want:
                    moved_ok.append((rel, name, o, n))
                else:
                    bad.append((rel, name, o, n))
            else:
                bad.append((rel, name, o, n))
        if do:
            open(path, "w", encoding="utf-8").write(text)

    print(f"扫描 {len(files)} 个 temp 脚本；改 {touched} 个文件，站点 A={n_a_sites} B={n_b_sites}")
    print(f"禁改的归档副本 {len(skipped)} 个：")
    for rel, why in skipped:
        print(f"   ⏸ {rel}  (深度未变，命中 {why})")
    print(f"\n随脚本同批搬家（预期，旧 shell/… → 新 temp/…）{len(moved_ok)} 个变量：")
    for rel, name, o, n in moved_ok[:15]:
        print(f"   {rel}:{name}\n      旧 {o}\n      新 {n}")
    print(f"\n右值解析不出来、需人工看的 {len(unresolvable)} 处：")
    for rel, name, why in unresolvable[:20]:
        print(f"   ⚠️ {rel}:{name}  {why}")
    print(f"\n【对拍不等＝真问题】{len(bad)} 处：")
    for rel, name, o, n in bad[:40]:
        print(f"   ❌ {rel}:{name}\n      旧 {o}\n      新 {n}")
    print("\n模式：", "已落盘" if do else "干跑（加 --apply 才写文件）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
