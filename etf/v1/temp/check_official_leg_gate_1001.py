# -*- coding: utf-8 -*-
"""验收 G 格（official 这条腿静默 0 产出 ⇒ 链路非零退出）的正反两支自检（10-01）

来历：10-01 15:5x 那场生产 ③，`docker info` 单次 20s 探测超时 ⇒ 前置检查判红 ⇒
RD-Agent 那条腿**一个因子没交**，而共享层打印的还是「✅ official 产出 0 个因子」、
子进程退出码 0、整条链 exit=0、墙钟 1h17m（真起容器应是 3~5 小时）。
甲-1 已在共享层把探测改成 2 次 × 45s；这一支自检管的是**另一半**：探测真失败时，
链路必须念红并且非零退出，不能再"默默没跑还 exit 0"。

判据本体不在这份文件里重写：直接 import 入口模块的 `official_leg_evidence` /
`verify_analysis`，保证自检与链路用的是同一把尺子（同 09-29 那份 negctl 的做法）。

八支：
    P1 真日志切片（09-29 那场，official 产出 9、前置检查全绿）        ⇒ G ✅
    R1 真日志切片（10-01 生产场，docker 超时自陈 + 产出 0）            ⇒ G ❌
    R2 真切片派生：只把「产出 9」改成「产出 0」，其余一字未动            ⇒ G ❌
       （这一支是**尺子的牙**：切片其余部分全同，唯一差异就是那个数字）
    R3 自陈「本次未运行」却报产出 9（两处对不上）                        ⇒ G ❌
    R4 前置检查整屏全绿、没有任何自陈、却产出 0（真·静默）                ⇒ G ❌
    R5 多源那一段跑了（有 llm 产出行），唯独 official 那行整条不见          ⇒ G ❌
    W1 多源那一段压根没重算（`--resume` 命中主线断点）                    ⇒ G ⚪ 不拦
       （不许把正常续传打成红 ⇒ 恒拦也是废尺子）
    X1 给 `--allow-official-absent` 后，R1 那片必须从 ❌ 降成 ⚪、且不碰 A–F
末了两条接线证明（判据红了要能活到"退出码"那一层才算数）：
    Z1 R1 那片喂进 `print_checks` ⇒ 返回的红名单**含 G**（② 因此不起、链路非零）
    Z2 W1 那片喂进 `print_checks` ⇒ 红名单**不含** G（豁免/无信息这条路径走得通）
"""
import contextlib
import io
import os
import re
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/src"
TEMP = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/temp"
sys.path.insert(0, SRC)
os.chdir(SRC)
sys.argv = ["run_etf_daily_chain.py", "--audit"]     # 只 import，不触发 main
import run_etf_daily_chain as chain                   # noqa: E402

CORE, AUX = chain.CORE_ARTIFACTS, chain.AUX_ARTIFACTS
T_START = 1_000.0
G_NAME = "G official 这条腿真的交出了因子（不是静默 0）"


def mk(rows=100, last_date="2026-09-28", last_value=100.0, mtime=2_000.0, nbytes=1000):
    return {"exists": True, "mtime": mtime, "bytes": nbytes,
            "when": "01-01 00:00:00", "rows": rows,
            "last_date": last_date, "last_value": last_value}


def good_pair():
    before = {k: mk(mtime=500.0, rows=90, last_date="2026-09-25") for k in CORE + AUX}
    after = {k: mk(mtime=2_000.0, rows=100) for k in CORE + AUX}
    return before, after


def slice_mainline(path):
    """取真日志的**主线那一段**（第一个 `--- 折 ` 之前）+ 结尾一行「最终资金」
    ⇒ D 格要跨层对表，缺了它会红在别的格上，干扰对 G 的读数。"""
    txt = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"^--- 折 \d", txt, re.M)
    body = txt[: m.start()] if m else txt
    return body + "\n  最终资金      : 100.00\n"


def g_of(log, **kw):
    b, a = good_pair()
    checks = chain.verify_analysis(b, a, T_START, log, **kw)
    names = [c[0] for c in checks]
    assert G_NAME in names, f"G 不在验收表里（表={names}）⇒ 接线断了"
    return checks[names.index(G_NAME)][1], checks


results = []


def arm(tag, want, got, detail):
    """want/got ∈ {True, False, None}；边算边打（崩在显示行 = 整张表丢失）"""
    ok = want is got
    results.append((tag, ok, want, got))
    mark = {True: "✅", False: "❌", None: "⚪"}
    print(f"  {'✅' if ok else '❌'} {tag:<28} 期望 {mark[want]} 实得 {mark[got]}  {detail}")
    return ok


GREEN = slice_mainline(os.path.join(TEMP, "chain_resume_0929_1645.log"))
RED_REAL = slice_mainline(os.path.join(TEMP, "wf_prod_1001.log"))

print("\n──── 真日志切片两支（P1 放行 / R1 判红）────")
ev_g = chain.official_leg_evidence(GREEN)
arm("P1 09-29 真切片（产出 9）", True, g_of(GREEN)[0],
    f"n={ev_g['n']} 红项={len(ev_g['red_items'])} 自陈={ev_g['skip']}")
ev_r = chain.official_leg_evidence(RED_REAL)
arm("R1 10-01 真切片（docker 超时⇒0）", False, g_of(RED_REAL)[0],
    f"n={ev_r['n']} 红项={ev_r['red_items'][:1]} 自陈={ev_r['skip']}")

print("\n──── 尺子的牙：同一片只改那一个数字（R2）────")
TYPED = GREEN.replace("official   产出 9 个因子", "official   产出 0 个因子", 1)
assert TYPED != GREEN, "替换没生效 ⇒ 这一支没有牙（切片里那行字形不对）"
arm("R2 全片一字未动、只把 9 改成 0", False, g_of(TYPED)[0],
    f"差异面=那一个数字；派生前 {ev_g['n']} ⇒ 现 {chain.official_leg_evidence(TYPED)['n']}")

print("\n──── 四种「腿没了」的形状各归各位（R3/R4/R5/W1）────")
R3 = ("  RD-Agent(Q) 前置检查:\n    ✅ conda: /x\n"
      "  ℹ️ RD-Agent(Q) 本次未运行（前置依赖缺失: docker（生成代码沙箱））\n"
      "  ✅ official   产出 9 个因子\n  最终资金      : 100.00\n")
arm("R3 自陈未运行却报产出 9", False, g_of(R3)[0], "两处对不上，有一处在说谎")

R4 = ("  RD-Agent(Q) 前置检查:\n    ✅ conda: /x\n    ✅ docker: daemon 可达\n"
      "  ✅ official   产出 0 个因子\n  ✅ llm        产出 2 个因子\n"
      "  多源挖掘追加: 2\n  最终资金      : 100.00\n")
arm("R4 前置检查全绿、无自陈、产出 0", False, g_of(R4)[0], "真·静默：打了 ✅ 却交了白卷")

R5 = ("  ========== 多因子源并行 ==========\n  ✅ llm        产出 2 个因子\n"
      "  多源挖掘追加: 2\n  最终资金      : 100.00\n")
arm("R5 这一段跑了、official 那行整条失踪", False, g_of(R5)[0],
    f"segment_ran={chain.official_leg_evidence(R5)['segment_ran']} n=None")

W1 = ("  💾 断点已存 主线多源挖掘：2 个因子，累计试验 N=298（口径 main）\n"
      "  最终资金      : 100.00\n")
arm("W1 多源段没重算（续传命中）⇒ 不拦", None, g_of(W1)[0],
    f"segment_ran={chain.official_leg_evidence(W1)['segment_ran']} ⇒ ⚪")

print("\n──── 豁免开关（X1）与接线（Z1/Z2）────")
g_r1, all_r1 = g_of(RED_REAL)
g_x1, all_x1 = g_of(RED_REAL, allow_official_absent=True)
_, all_w1 = g_of(W1)
arm("X1 给了 --allow-official-absent ⇒ G 降 ⚪", None, g_x1, "同一片，只多了那颗开关")
same_af = all_r1[:6] == all_x1[:6]
print(f"  {'✅' if same_af else '❌'} X1b A–F 六条的结论与证据一字未动  "
      f"（只许 G 变，改判据不许顺手动别人）")
results.append(("X1b A–F 不受开关影响", same_af, True, True))

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    red1 = chain.print_checks("自检 R1", all_r1)
    red2 = chain.print_checks("自检 W1", all_w1)
z1 = any(n.startswith("G ") for n in red1)
z2 = not any(n.startswith("G ") for n in red2)
arm("Z1 R1 的红名单里有 G（⇒ ② 不起、exit≠0）", True, z1, f"红名单={red1}")
arm("Z2 W1 的红名单里没有 G", True, z2, f"红名单={red2}")

print("\n──── 判定 ────")
bad = [tag for tag, ok, _, _ in results if not ok]
for tag, ok, want, got in results:
    print(f"  {tag:<34} {'✅' if ok else '❌'} 期望={want} 实得={got}")
if bad:
    raise SystemExit(f"[自检失败] {bad} ⇒ G 格与它的对照不一致，链路的「腿没了」又会被念成绿")
print(f"[自检通过] {len(results)} 格：放行 1（P1，真切片）+ 判红 5（R1/R2/R3/R4/R5）"
      "+ 不拦 1（W1 续传）+ 豁免与接线 3（X1/X1b/Z1/Z2）⇒ G 不是恒绿也不是恒红")
