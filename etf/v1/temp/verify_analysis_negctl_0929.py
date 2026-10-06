# -*- coding: utf-8 -*-
"""`verify_analysis()` 的正反两支自检（09-29）

为什么要这一支：③ 的验收 A–J 是「分析面真的跑了」的唯一凭据，如果它恒绿，
链路就等于把「没跑」报成「跑对」。所以除了拿 09-29 那场真产物形状喂它（正支，应全绿），
还要**人造六种坏**（负支，每一条都必须变红；N6 是唯一的例外——它要求的是"只报红"那一格
真的翻了、而红名单里不许有它）：
    N1 四张核心表一张都没写（mtime 全在起链时刻之前）⇒ A 红
    N2 净值表末值与日志回显的「最终资金」差一口（跨层对表）⇒ D 红
    N3 净值表末日比上一场倒退 ⇒ B/F 红
    N4 一张表都读不出日期列 ⇒ F 红（`all([])` 恒真就是条恒真判据，09-29 加）
    N5 `print_checks` 的三态渲染：✅/❌/⚪ 各归各位，只有 ❌ 进红名单
       （① 和 ③ 现在都走这一张表打印机，它错了两张表一起错）
    N6 给 J 的取数桥里库键少一枚 ⇒ J 翻成「只报红」，而红名单仍为空（10-06 加）
判据本体不在这份文件里重写：直接 import 入口模块的 `verify_analysis`，
保证自检与链路用的是同一把尺子。

10-01 同步（加了 G 格之后）：`LOG_OK` 里补了一行真的 official 产出行 ⇒ **P 支当时要求
七条全绿（一格都不许红、也不许是 ⚪）**；原先 P 只要求"红的条数覆盖空集"＝恒真，
这一格不能留成那样的尺子。G 自己的正反两支在 `check_official_leg_gate_1001.py`。

10-05 同步（加了 H 格「本场净增 > 0」之后，乙-3）：
    · 计数词从「七条 A–G」改成「八条 A–H」；
    · `LOG_OK` 里那行「本场净增」**不手写**——由共享层 `core.official_rdagent._recover_factors`
      真打出来（在 /tmp 造一枚两因子文件、baseline 传空集）。共享层一改措辞这里就当场退出，
      不会留下一片过期行形继续冒充通过；
    · 上面 10-01 那句"也不许是 ⚪"当时只写在说明里、代码只查了红名单（`exact=True` 管红
      不管灰）⇒ 加了 H 之后这片会灰在 H 上，那句说明就成了没人核对过的话。现在把它落成
      判据 P2。
H 自己的正反两支（放行 2 / 判红 2 / 该 ⚪ 的 4 / 开关与接线 / 拔牙）在
`check_official_net_new_gate_1005.py`。

10-06 同步（加了 I 格「signals／trades 不许塌方」，用户裁「补」）：
    · 计数词从「八条 A–H」改成「九条 A–I」；
    · `mk()` 补了 `n_dates`。这一条是**本份夹具自己的牙**：I 读的就是那一列，原先不给，
      I 在这份正反两支里恒走「两边都是 None ⇒ 不比」那一支 ⇒ 日期那条判据在这儿从没被执行过；
    · I 的七格夹具（真实 CSV 字节 + 拔牙负对照）**不在这份文件里**，在
      `check_row_collapse_gate_1006.py`。分工说清：本份吃**手写 dict**（快，管的是"整张表
      在坏形状下会不会恒绿"），那一份吃**从生产拷来的真字节 + 链路自己的 `stamp()`**
      （管的是"读数是不是真的能从字节里长出来"）。
    · 同批修掉 A 格一处崩溃：`after[k]['when']` 硬取 ⇒ 任何一张核心表不见了就 `KeyError`，
      整张验收表（连着 I 那句「整张表不见了」）根本没机会打出来；改成 `.get('when', '缺表')`。
      这一条是 `check_row_collapse_gate_1006.py` 的 C5 那一格抓到的——**判据想拦的那的形状
      先把取证层打死**，与 10-01「能失败的那一层如果同时是唯一取证层」同病。

10-06 同批二（加了 J 格「回收层点名的净增候选要在库键里」，用户裁 C-1「设／只报」）：
    · 计数词从「九条 A–I」改成「十条 A–J」；
    · **正支 P 必须给 J 传取数桥**：J 不传桥恒走 ⚪，而 P2 那条「一格 ⚪ 都不许有」会把整支
      正对照打死。桥是手写的三枚名字（不是真产物），J 自己的七格＋归档七格在
      `check_library_membership_gate_1006.py`（那边吃生产 `factors.json` 的真字节）。
    · 新增 **N6**：把这座桥的库键集合里少抹一枚 ⇒ J 必须翻成「只报红」。这一支钉的是
      "P 那格绿是**真读出来的**、不是 J 恒绿"——桥改了判决不动＝这份夹具对 J 恒盲
      （10-06 自己踩过的坑：`mk()` 不给新判据读的那一列，老夹具就看不见新格）。
    · J 的红是 `REPORT_ONLY_RED` 哨兵 ⇒ 天生进不了 `judge()` 的红名单（那里口径是 `ok is False`），
      所以 N6 要求的是「红名单仍为空 **且** J 那格翻了」两个一起成立；只查前者＝恒真。
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/src"
sys.path.insert(0, SRC)
os.chdir(SRC)
sys.argv = ["run_etf_daily_chain.py", "--audit"]     # 只 import，不触发 main
import run_etf_daily_chain as chain                   # noqa: E402

CORE = chain.CORE_ARTIFACTS
AUX = chain.AUX_ARTIFACTS
T_START = 1_000.0        # 假的"起链时刻"，配合下面的 mtime 用


def mk(rows=100, last_date="2026-09-28", last_value=100.0, mtime=2_000.0,
       nbytes=1000, n_dates=None):
    """一枚产物的读数（只填 verify_analysis 真正会看的字段）

    10-06 补 `n_dates`：I 格（signals／trades 塌方闸）读的就是这一列。原先这里不给，
    I 在本份夹具里**恒走"两边都是 None ⇒ 不比"**那一支 ⇒ 日期那条判据在这份正反两支里
    从没被执行过（同 09-29 的教训：可失败的那一步最会藏恒真判据）。默认按"一行一个交易日"
    给一个真值，正支 P 就要求「90→100 行、90→100 格」照样绿；I 自己的七格在
    `check_row_collapse_gate_1006.py`（那边喂的是从生产拷来的真实 CSV 字节 + 链路自己的 `stamp()`）。
    """
    return {"exists": True, "mtime": mtime, "bytes": nbytes,
            "when": "01-01 00:00:00", "rows": rows,
            "last_date": last_date, "last_value": last_value,
            "n_dates": rows if n_dates is None else n_dates}


def good_pair():
    before = {k: mk(mtime=500.0, rows=90, last_date="2026-09-25") for k in CORE + AUX}
    after = {k: mk(mtime=2_000.0, rows=100) for k in CORE + AUX}
    return before, after


def real_net_line():
    """让共享层**真打**一行「本场净增 N 个因子」（baseline 传空集 ⇒ N=2），取原文给 LOG_OK。

    抓不到那一行（或它不含「净增 2」）⇒ 当场退出，不返回一个手写的行形冒充通过：
    判据的正则与共享层的措辞是绑死的，措辞一改这份夹具必须响。
    """
    from core.official_rdagent import _recover_factors
    d = tempfile.mkdtemp(prefix="negctl_1005_")
    try:
        with open(os.path.join(d, "factors.json"), "w", encoding="utf-8") as f:
            json.dump([{"name": "fixture_alpha_1005", "expr": "Ref($close,1)/$close-1"},
                       {"name": "fixture_beta_1005", "expr": "Mean($close,5)/$close-1"}], f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = _recover_factors(d, set())
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert got and len(got) == 2, f"回收夹具本身坏了：{got}"
    hit = [ln.strip() for ln in buf.getvalue().splitlines() if "本场净增" in ln]
    if len(hit) != 1 or "净增 2" not in hit[0]:
        raise SystemExit(f"[夹具坏了] 期望共享层打出恰好 1 行「本场净增 2」，"
                         f"实得 {hit} ⇒ 措辞变了，H 格正则与这份夹具一起过期")
    return hit[0]


NET_LINE = real_net_line()


LOG_OK = ("  最终资金      : 100.00\n" + NET_LINE +
          "\n  ✅ official   产出 9 个因子\n  多源挖掘追加: 2\n")
LOG_BAD = "  最终资金      : 4321.00"

# J（10-06 C-1）读的三样：起场前候选名、收场后候选名、库键集合。正支给一座"净增那枚在库里"
# 的桥，N6 把库键少抹一枚 ⇒ 判决必须跟着翻（不翻＝这份夹具对 J 恒盲）。
_J_OLD = frozenset({"fixture_alpha_1005", "fixture_beta_1005"})
_J_NEW = _J_OLD | {"fixture_gamma_1006"}


def bridge(keys=_J_NEW):
    return {"before": _J_OLD, "after": _J_NEW,
            "library_before": _J_OLD, "library_after": frozenset(keys),
            "archive": {"skipped": "自检不落盘（真归档的七格在 check_library_membership_gate_1006.py）"}}


results = []


def judge(tag, checks, must_red, exact=False):
    """must_red：这一支里**必须**变红的判据序号（0 起）；exact ⇒ 红名单必须**恰好**等于它

    三态里只有 `False` 算红（`None` 是「判不出、只念不拦」，与 `print_checks` 同一口径）。
    第四种 `REPORT_ONLY_RED`（10-06 J 格）在这里同样**不算红**——这正是"只报"的定义。
    """
    red = [i for i, (_, ok, _) in enumerate(checks) if ok is False]
    want = set(must_red)
    got = set(red)
    ok = (got == want) if exact else (want <= got)
    results.append((tag, ok, sorted(got), sorted(want)))
    for i, (name, is_ok, ev) in enumerate(checks):
        mark = "✅" if is_ok is True else ("❌" if is_ok is False else "⚪")
        print(f"    {mark} [{i}] {name}：{ev}")
    return ok


print("\n──── 正支 P：09-29 真产物形状 + 真产出行 + 共享层真打的净增行 + 给 J 的桥（十条 A–J 不许红）────")
b, a = good_pair()
p = chain.verify_analysis(b, a, T_START, LOG_OK, official_candidates=bridge())
judge("P 全绿", p, must_red=[], exact=True)
# P2：把 10-01 那句"也不许是 ⚪"落成判据（`exact=True` 只钉得住红名单，钉不住灰格）。
# H 加进来之后这片必须有真净增行才绿得起来 ⇒ 少了 NET_LINE 这一支就红，正是想要的耦合。
# J 加进来之后这片还必须有一座桥（不传桥 J 恒 ⚪，P2 就打死在 J 上）——这条耦合是有意的。
gray = [i for i, (_, is_ok, _) in enumerate(p) if is_ok is None]
ok_p2 = gray == []
print(f"  {'✅' if ok_p2 else '❌'} 十条里一格 ⚪ 都不许有（P2）  实得灰格序号={gray}")
results.append(("P2 不许有 ⚪", ok_p2, [], []))

print("\n──── 负支 N1：四张核心表一张没写（mtime 都早于起链时刻）────")
b, a = good_pair()
for k in CORE:
    a[k] = mk(mtime=600.0)          # 600 < T_START=1000 ⇒ 本场没写过
judge("N1 A 必须红", chain.verify_analysis(b, a, T_START, LOG_OK), must_red={0})

print("\n──── 负支 N2：日志说 4321、净值表末值是 100（跨层对不上）────")
b, a = good_pair()
judge("N2 D 必须红", chain.verify_analysis(b, a, T_START, LOG_BAD), must_red={3})

print("\n──── 负支 N3：净值表从 100 行缩到 40 行、末日倒退────")
b, a = good_pair()
a["results/equity_daily.csv"] = mk(rows=40, last_date="2026-09-20", mtime=2_000.0)
judge("N3 B/F 必须红", chain.verify_analysis(b, a, T_START, LOG_OK), must_red={1, 5})

print("\n──── 负支 N4：一张表都读不出日期列────")
b, a = good_pair()
for k in CORE + AUX:
    b[k] = {**b[k], "last_date": None}     # 全表无日期 ⇒ F 不得因 `all([])` 恒真而变绿
judge("N4 F 必须红", chain.verify_analysis(b, a, T_START, LOG_OK), must_red={5})

print("\n──── 负支 N5：print_checks 的三态渲染（现在 ①③ 两张表都走它）────")
import contextlib
import io
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    red = chain.print_checks("自检", [("真跑对", True, "x"),
                                      ("真跑坏", False, "x"),
                                      ("没信息", None, "x")])
shown = buf.getvalue()
ok5 = (red == ["真跑坏"]
       and "[✅] 真跑对" in shown and "[❌] 真跑坏" in shown
       and "[⚪ 无信息] 没信息" in shown)
print(f"  {'✅' if ok5 else '❌'} 三态：红名单={red}｜渲染={shown.strip().splitlines()}")
results.append(("N5 三态渲染", ok5, [], []))

print("\n──── 负支 N6：J 的桥少抹一枚库键 ⇒ J 翻成「只报红」，红名单仍为空 ────")
b, a = good_pair()
n6 = chain.verify_analysis(b, a, T_START, LOG_OK,
                           official_candidates=bridge(keys=_J_OLD))
j_row = [c for c in n6 if c[0].startswith("J ")][0]
red_n6 = chain.print_checks("自检 N6", [c for c in n6 if c[0] != j_row[0]])
ok6 = (j_row[1] == chain.REPORT_ONLY_RED and red_n6 == [])
print(f"  {'✅' if ok6 else '❌'} N6 J={j_row[1]!r}（要求哨兵 {chain.REPORT_ONLY_RED!r}）"
      f"｜除 J 外红名单={red_n6}（要求空）")
results.append(("N6 桥改一格 J 就翻", ok6, [], []))

print("\n──── 判定 ────")
bad = [tag for tag, ok, _, _ in results if not ok]
for tag, ok, got, want in results:
    print(f"  {tag:<18} {'✅' if ok else '❌'} 红的条数={got} 要求覆盖={want}")
if bad:
    raise SystemExit(f"[自检失败] {bad} ⇒ 验收判据与它的对照不一致，链路的 ③ 验收不可信")
print("[自检通过] 正支十条 A–J 全绿（exact 红名单为空 + P2 一格 ⚪ 都没有）"
      "+ 六支人造坏各红在指定的那一条（N6 红在 J 上、但只报不进红名单）"
      "⇒ A–J 与那张表打印机都不是恒绿")
