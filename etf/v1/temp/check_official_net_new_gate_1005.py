# -*- coding: utf-8 -*-
"""验收 H 格（official 交回的因子**本场净增 > 0**）的正反两支自检（10-05 乙-3）

来历：10-04 19:05→22:21 那场是这条链第一次全程打通（rc=0、5 个 loop 走完、6 枚因子的
h5 与代码全对账），可 `factors.json` 的**名字净增 0** —— 共享层回收读的是固定路径
（`factors.json` / `result.json`），没有新鲜度也没有名字作用域 ⇒ 它交回的 12 条全是
上一场留下的旧档，而日志照打「✅ official 产出 12 个因子」。G 格管"腿没跑"，
这一格管"跑了、但交回的是存货"。

判据本体不在这份文件里重写：直接 import 入口模块的 `official_leg_evidence` /
`verify_analysis`，保证自检与链路用的是同一把尺子（同 1001 那份 G 格自检的做法）。

**读数行不手写**：三种「本场净增」那行由共享层 `core.official_rdagent._recover_factors`
**真打**出来 —— 在 /tmp 造一枚 factors.json，按三种 baseline 各调一次、捕获 stdout 取原文。
好处：共享层哪天改了措辞，这里抓不到那行就当场退出，不会把夹具养成一堆过期行形
（10-01 的教训：改了正文措辞，正则锚住的对照会自己先失效）。

臂（放行 2 / 判红 2 / 不拦 4 / 开关 3 / 接线 2 / 拔牙 2 = 十五格）：
    P1 真产出行(9) + 真「本场净增 2」行                        ⇒ H ✅
    P2 净增行有 0 也有 2 ⇒ 取最大值                            ⇒ H ✅（不许把"有一段是新的"念成全旧）
    R1 真产出行(9) + 真「本场净增 0」行（10-04 那场的形状）      ⇒ H ❌
    R2 同一片只把「产出 9」改成「产出 12」，其余一字未动         ⇒ H ❌（牙：红不依赖 N 是多少）
    W1 主线连 official 产出行都没有（这一段没重算）              ⇒ H ⚪
    W2 真 10-01 切片（docker 超时 ⇒ 产出 0）+ 真净增 0 行        ⇒ G ❌ 且 H ⚪（各管一半，不重复拦）
    W3 有产出行、但「本场净增」那行压根没念（10-04 之前的形状）   ⇒ H ⚪（无读数 ≠ 坏读数）
    W4 真「本场净增无法判定」行（起场前没有可比基线）             ⇒ H ⚪
    X1 R1 那片给 `allow_official_stale` ⇒ H 从 ❌ 降成 ⚪
    X1b 同批**除 H 以外**各条（A–G ＋ 10-06 新加的 I）的结论与证据一字未动（改判据不许顺手动别人）
    X1c 只给 `allow_official_absent` ⇒ H 仍判红（两颗闸各自独立）
    Z1 R1 喂 `print_checks` ⇒ 红名单**含 H**（② 因此不起、链路非零退出）
    Z2 W3 喂 `print_checks` ⇒ 红名单**不含** H（无读数这条路径走得通）
    T1 把 `elif ev["net_new"] == 0:` 拔成 `elif False:`（恒真）⇒ R1 必须**不再红**
    T2 把放行那支的 `True` 拔成 `False`（恒假）                ⇒ P1 必须**变红**
       （T1/T2 读的是 /tmp 里两份**改过的副本**，生产文件一个字节不动；这两支证明
       H 的红与绿都真由那两行给出，不是夹具自己编出来的。）
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
SRC = os.path.join(REPO, "etf", "v1", "src")
TEMP = os.path.join(REPO, "etf", "v1", "temp")
REAL_PY = os.path.join(SRC, "run_etf_daily_chain.py")
sys.path.insert(0, SRC)
os.chdir(SRC)
sys.argv = ["run_etf_daily_chain.py", "--audit"]     # 只 import，不触发 main
import run_etf_daily_chain as chain                   # noqa: E402
from core.official_rdagent import _recover_factors    # noqa: E402

CORE, AUX = chain.CORE_ARTIFACTS, chain.AUX_ARTIFACTS
T_START = 1_000.0
G_NAME = "G official 这条腿真的交出了因子（不是静默 0）"
H_NAME = "H official 交回的因子本场净增 > 0（旧档不算产出）"
PROD_ANCHOR = re.compile(r"^[^\n]*official\s+产出\s*\d+\s*个因子[^\n]*$", re.M)

results = []
scratch = []


def arm(tag, want, got, detail):
    """want/got ∈ {True, False, None}；边算边打（崩在显示行 = 整张表丢失）"""
    ok = want is got
    results.append((tag, ok, want, got))
    mark = {True: "✅", False: "❌", None: "⚪"}
    print(f"  {'✅' if ok else '❌'} {tag:<46} 期望 {mark[want]} 实得 {mark[got]}  {detail}")
    return ok


def mk(rows=100, last_date="2026-09-28", last_value=100.0, mtime=2_000.0, nbytes=1000,
       n_dates=None):
    # `n_dates` 是 10-06 补的：I 格（signals／trades 塌方闸）读的就是这一列。不给的话它两边都
    # 拿到 None ⇒ 那条判据在这里**从没被执行**，我却还在数"验收表几条"。默认"一行一交易日"。
    return {"exists": True, "mtime": mtime, "bytes": nbytes,
            "when": "01-01 00:00:00", "rows": rows,
            "last_date": last_date, "last_value": last_value,
            "n_dates": rows if n_dates is None else n_dates}


def good_pair():
    before = {k: mk(mtime=500.0, rows=90, last_date="2026-09-25") for k in CORE + AUX}
    after = {k: mk(mtime=2_000.0, rows=100) for k in CORE + AUX}
    return before, after


def slice_mainline(path):
    """取真日志的**主线那一段**（第一个 `--- 折 ` 之前）+ 结尾一行「最终资金」
    ⇒ D 格要跨层对表，缺了它会红在别的格上，干扰对 H 的读数。"""
    txt = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"^--- 折 \d", txt, re.M)
    body = txt[: m.start()] if m else txt
    return body + "\n  最终资金      : 100.00\n"


# ───────────────── 真读数行：让共享层自己打出来 ─────────────────
def real_net_lines():
    """三种 baseline 各调一次 `_recover_factors`，从它**真的打出来的那几行**里取原文。

    抓不到「本场净增」那行 ⇒ 直接退出（不是返回 None）：判据与共享层的措辞是绑死的，
    措辞一改这里必须当场响，否则下面每一支喂的都是过期行形。
    """
    d = tempfile.mkdtemp(prefix="h_fixture_1005_")
    scratch.append(d)
    names = ["fixture_alpha_1005", "fixture_beta_1005"]
    with open(os.path.join(d, "factors.json"), "w", encoding="utf-8") as f:
        json.dump([{"name": names[0], "expr": "Ref($close,1)/$close-1",
                    "mean_ic": 0.01, "icir": 0.1},
                   {"name": names[1], "expr": "Mean($close,5)/$close-1",
                    "mean_ic": 0.02, "icir": 0.2}], f)
    out = {}
    cases = (("zero", set(names), "净增 0"), ("new", set(), "净增 2"),
             ("none", None, "无法判定"))
    for tag, baseline, want in cases:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = _recover_factors(d, baseline)
        assert got is not None and len(got) == 2, f"{tag} 支回收本身坏了：{got}"
        hit = [ln for ln in buf.getvalue().splitlines() if "本场净增" in ln]
        if len(hit) != 1:
            raise SystemExit(f"[夹具坏了] baseline={tag} 打出 {len(hit)} 行「本场净增」"
                             "（要求恰好 1 行）⇒ 共享层措辞变了，H 格的正则和这份夹具一起过期")
        line = hit[0].strip()
        if want not in line:
            raise SystemExit(f"[夹具坏了] {tag} 支期望那行含「{want}」，实得：{line}")
        out[tag] = line
    print("  （下面是共享层真打的三行原文，不是手写的行形）")
    for tag, _, _ in cases:
        print(f"    {tag:<5} {out[tag]}")
    return out


NET = real_net_lines()


def attach(main, num=None, *net_lines):
    """把净增那几行插在**真产出行之前**（生产里就是这个顺序：回收先念、产出行后念），
    顺手把行里的数字换成 `num`。锚点找不到 ⇒ 报红退出，不返回原文冒充通过。"""
    m = PROD_ANCHOR.search(main)
    if not m:
        raise SystemExit("[夹具坏了] 切片里找不到「official 产出 N 个因子」那行 ⇒ 锚点失效")
    prod = m.group(0)
    if num is not None:
        prod = re.sub(r"产出\s*\d+\s*个因子", f"产出 {num} 个因子", prod, count=1)
    head = main[:m.start()]
    if net_lines:
        head += "\n".join(net_lines) + "\n"
    return head + prod + main[m.end():]


def strip_prod(main, replacement):
    """把产出行整行换成别的（造「这一段跑了、唯独 official 那行失踪」那片）。锚点缺失即退出。"""
    m = PROD_ANCHOR.search(main)
    if not m:
        raise SystemExit("[夹具坏了] 切片里找不到产出行，换不掉 ⇒ 锚点失效")
    return main.replace(m.group(0), replacement, 1)


def checks_of(log, mod=chain, **kw):
    b, a = good_pair()
    cs = mod.verify_analysis(b, a, T_START, log, **kw)
    names = [c[0] for c in cs]
    if len(names) != 10:
        raise SystemExit(f"[接线断了] 验收表不是十条 A–J（实得 {len(names)} 条：{names}）")
    for nm in (G_NAME, H_NAME):
        if nm not in names:
            raise SystemExit(f"[接线断了] {nm} 不在验收表里（表={names}）")
    return names, cs


def only(log, name, mod=chain, **kw):
    names, cs = checks_of(log, mod, **kw)
    return cs[names.index(name)][1], cs


print("\n──── 放行两支（P1 真净增 2 / P2 取最大值）────")
GREEN0 = slice_mainline(os.path.join(TEMP, "chain_resume_0929_1645.log"))
P1 = attach(GREEN0, None, NET["new"])
P2 = attach(GREEN0, None, NET["zero"], NET["new"])
e = chain.official_leg_evidence(P1)
arm("P1 产出 9 + 净增 2 ⇒ 放行", True, only(P1, H_NAME)[0],
    f"n={e['n']} net_new={e['net_new']}")
e = chain.official_leg_evidence(P2)
arm("P2 净增行有 0 也有 2 ⇒ 取最大值放行", True, only(P2, H_NAME)[0],
    f"net_new={e['net_new']}（两行取最大，不取首行/末行）")

print("\n──── 判红两支（R1 10-04 那场的形状 / R2 尺子的牙）────")
R1 = attach(GREEN0, None, NET["zero"])
R2 = attach(GREEN0, 12, NET["zero"])
e1, e2 = chain.official_leg_evidence(R1), chain.official_leg_evidence(R2)
arm("R1 产出 9 + 净增 0 ⇒ 判红", False, only(R1, H_NAME)[0],
    f"n={e1['n']} net_new={e1['net_new']}（真读数行）")
arm("R2 同片只把 9 改成 12 ⇒ 照样判红", False, only(R2, H_NAME)[0],
    f"差异面=那一个数字：n {e1['n']}→{e2['n']}，net_new 两边都是 {e2['net_new']} ⇒ 红由净增给")

print("\n──── 四支「不该拦」（W1 没产出行 / W2 产出 0 / W3 没念那行 / W4 无基线）────")
W1 = strip_prod(GREEN0, "  ✅ llm        产出 2 个因子")
arm("W1 这一段没重算（连产出行都没有）⇒ 不拦", None, only(W1, H_NAME)[0],
    f"n={chain.official_leg_evidence(W1)['n']} ⇒ 无可比对象（那一半归 G）")
RED_REAL = slice_mainline(os.path.join(TEMP, "wf_prod_1001.log"))
W2 = attach(RED_REAL, None, NET["zero"])
g_w2, _ = only(W2, G_NAME)
arm("W2 产出 0（10-01 真切片）⇒ G 拦", False, g_w2, "静默 0 由 G 判红")
arm("W2 同一片 H ⇒ ⚪（腿没交东西，净增无从谈起）", None, only(W2, H_NAME)[0],
    f"n={chain.official_leg_evidence(W2)['n']} net_new="
    f"{chain.official_leg_evidence(W2)['net_new']}")
W3 = attach(GREEN0)              # 有产出行、那行压根没念 = 10-04 之前的共享层
e = chain.official_leg_evidence(W3)
arm("W3 有产出行但没念净增 ⇒ ⚪ 不拦", None, only(W3, H_NAME)[0],
    f"net_no_line={e['net_no_line']} net_new={e['net_new']}（无读数≠坏读数）")
W4 = attach(GREEN0, None, NET["none"])
e = chain.official_leg_evidence(W4)
arm("W4 自陈「无法判定」⇒ ⚪ 不拦", None, only(W4, H_NAME)[0],
    f"net_no_baseline={e['net_no_baseline']}（起场前没有 factors.json＝第一场的形状）")

print("\n──── 豁免开关（X1/X1b/X1c）：只降 H，除 H 以外一个字都不许跟着动 ────")
h_x1, all_x1 = only(R1, H_NAME, allow_official_stale=True)
_, all_r1 = only(R1, H_NAME)
arm("X1 给了 allow_official_stale ⇒ H 降 ⚪", None, h_x1, "同一片，只多了那颗开关")
# 10-06：表从八条长成九条（多出 I＝signals/trades 塌方闸），旧的 `[:7]` 切片只盖住 A–G ⇒
# 新加的那条 I 落在开关的比对盲区里。改成「按判据名摘掉 H，其余逐条比」⇒ 条数再长也不会漏。
others_r1 = [c for c in all_r1 if c[0] != H_NAME]
others_x1 = [c for c in all_x1 if c[0] != H_NAME]
if len([c for c in others_r1 if c[0].startswith("I ")]) != 1:
    raise SystemExit(f"[夹具坏了] 表里 I 那条不唯一（实得 {[c[0] for c in others_r1]}）"
                     "⇒ 下面这支自证的口径没了，别当它绿")
# 牙（10-06）：拿「I 被这颗开关顺手动了一格」的人造片喂同一把尺 ⇒ 必须看得见。
# 看不见＝比对是瞎的＝X1b 恒真（这正是 [:7] 版本的状态：它数得到 A–G，数不到 I）。
leaked = [(n, (False if ok else True) if n.startswith("I ") else ok,
           "牙：人造 I 被开关动了" if n.startswith("I ") else ev)
          for (n, ok, ev) in others_r1]
blind = (leaked == others_r1)
same = (others_r1 == others_x1) and not blind
print(f"  {'✅' if same else '❌'} {'X1b 除 H 外各条的结论与证据一字未动':<46} "
      f"（比对 {len(others_r1)} 条，含 I；人造泄漏那一支"
      f"{'看不见＝尺子瞎' if blind else '看得见＝这条比对有牙'}）")
results.append(("X1b 除 H 外不受开关影响", same, True, True))
h_abs, _ = only(R1, H_NAME, allow_official_absent=True)
print(f"  {'✅' if h_abs is False else '❌'} "
      f"{'X1c 只给 allow_official_absent ⇒ H 仍判红':<46} 实得 {h_abs}（两颗闸不串门）")
results.append(("X1c 两颗开关互不越界", h_abs is False, False, h_abs))

print("\n──── 接线（Z1/Z2）：判据红了要能活到「退出码」那一层才算数 ────")
_, all_w3 = only(W3, H_NAME)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    red1 = chain.print_checks("自检 R1", all_r1)
    red2 = chain.print_checks("自检 W3", all_w3)
z1 = any(n.startswith("H ") for n in red1)
z2 = not any(n.startswith("H ") for n in red2)
arm("Z1 R1 的红名单里有 H（⇒ ② 不起、exit≠0）", True, z1, f"红名单={red1}")
arm("Z2 W3 的红名单里没有 H", True, z2, f"红名单={red2}")

print("\n──── 拔牙两支（T1 恒真 / T2 恒假，读的都是 /tmp 副本，生产文件不动）────")
BASE_SHA = hashlib.sha256(open(REAL_PY, "rb").read()).hexdigest()


def load_patched(tag, old, new):
    txt = open(REAL_PY, encoding="utf-8").read()
    if txt.count(old) != 1:
        raise SystemExit(f"[夹具坏了] {tag} 的锚点在源文件里出现 {txt.count(old)} 次（要求 1）")
    dst = os.path.join(tempfile.gettempdir(), f"chain_{tag}_1005.py")
    scratch.append(dst)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(txt.replace(old, new, 1))
    spec = importlib.util.spec_from_file_location(f"chain_{tag}_1005", dst)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


t1 = load_patched("t1_never_red", '    elif ev["net_new"] == 0:', '    elif False:')
arm("T1 拔成恒真 ⇒ R1 不再红（红是那行给的）", True, only(R1, H_NAME, mod=t1)[0],
    "同一份真读数，判据少那一支就变绿 ⇒ 尺子有牙")
t2 = load_patched("t2_never_green", '        h_ok, h_ev = True, (f"official 产出',
                  '        h_ok, h_ev = False, (f"official 产出')
arm("T2 拔成恒假 ⇒ P1 变红（绿也是那行给的）", False, only(P1, H_NAME, mod=t2)[0],
    "反向拔牙：放行那一支被改掉，正对照必须当场变色")
now_sha = hashlib.sha256(open(REAL_PY, "rb").read()).hexdigest()
untouched = now_sha == BASE_SHA
print(f"  {'✅' if untouched else '❌'} "
      f"{'T 支只读副本，生产文件逐字节未动':<46} sha256={now_sha[:12]}（起讫同值={untouched}）")
results.append(("生产文件未被拔牙改动", untouched, True, True))

print("\n──── 判定 ────")
bad = [tag for tag, ok, _, _ in results if not ok]
for tag, ok, want, got in results:
    print(f"  {tag:<46} {'✅' if ok else '❌'} 期望={want} 实得={got}")
for p in scratch:
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)
    elif os.path.exists(p):
        os.remove(p)
left = [p for p in scratch if os.path.exists(p)]
print(f"  （夹具残留 {len(left)} 项，应当 0）{left}")
if bad:
    raise SystemExit(f"[自检失败] {bad} ⇒ H 格与它的对照不一致，「旧档当产出」又会被念成绿")
# 分组计数**由 results 现数**，不手写——上一版这里写死「放行 2+判红 2+不拦 4+开关 3+接线 2+拔牙 2」
# 只有 15 格，与 len(results)=17 打脸（W2 那一片同时给了 G 的红与 H 的 ⚪ 两格，没数进去）。
n_pass = len([r for r in results if r[2] is True])
n_red = len([r for r in results if r[2] is False])
n_gray = len([r for r in results if r[2] is None])
print(f"[自检通过] {len(results)} 格（期望三态现数：放行 {n_pass}／判红 {n_red}／⚪ 不拦 {n_gray}，"
      f"合计 {n_pass + n_red + n_gray}）：P1/P2 放行、R1/R2 判红、"
      "W2 那一片的两格（G 红＋H ⚪）、W1/W3/W4 该 ⚪、X1/X1b/X1c 开关各不越界、"
      "Z1/Z2 接线（红名单进 exit≠0 那条路）、T1/T2 反向拔牙、生产文件未动 1 "
      "⇒ H 既不恒绿也不恒红，红与绿都由那两行判据给出")
