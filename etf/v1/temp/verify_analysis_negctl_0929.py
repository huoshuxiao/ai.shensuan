# -*- coding: utf-8 -*-
"""`verify_analysis()` 的正反两支自检（09-29）

为什么要这一支：③ 的验收 A–G 是「分析面真的跑了」的唯一凭据，如果它恒绿，
链路就等于把「没跑」报成「跑对」。所以除了拿 09-29 那场真产物形状喂它（正支，应全绿），
还要**人造五种坏**（负支，每一条都必须变红）：
    N1 四张核心表一张都没写（mtime 全在起链时刻之前）⇒ A 红
    N2 净值表末值与日志回显的「最终资金」差一口（跨层对表）⇒ D 红
    N3 净值表末日比上一场倒退 ⇒ B/F 红
    N4 一张表都读不出日期列 ⇒ F 红（`all([])` 恒真就是条恒真判据，09-29 加）
    N5 `print_checks` 的三态渲染：✅/❌/⚪ 各归各位，只有 ❌ 进红名单
       （① 和 ③ 现在都走这一张表打印机，它错了两张表一起错）
判据本体不在这份文件里重写：直接 import 入口模块的 `verify_analysis`，
保证自检与链路用的是同一把尺子。

10-01 同步（加了 G 格之后）：`LOG_OK` 里补了一行真的 official 产出行 ⇒ **P 支现在要求
七条全绿（一格都不许红、也不许是 ⚪）**；原先 P 只要求"红的条数覆盖空集"＝恒真，
这一格不能留成那样的尺子。G 自己的正反两支在 `check_official_leg_gate_1001.py`。
"""
import os
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/src"
sys.path.insert(0, SRC)
os.chdir(SRC)
sys.argv = ["run_etf_daily_chain.py", "--audit"]     # 只 import，不触发 main
import run_etf_daily_chain as chain                   # noqa: E402

CORE = chain.CORE_ARTIFACTS
AUX = chain.AUX_ARTIFACTS
T_START = 1_000.0        # 假的"起链时刻"，配合下面的 mtime 用


def mk(rows=100, last_date="2026-09-28", last_value=100.0, mtime=2_000.0,
       nbytes=1000):
    """一枚产物的读数（只填 verify_analysis 真正会看的字段）"""
    return {"exists": True, "mtime": mtime, "bytes": nbytes,
            "when": "01-01 00:00:00", "rows": rows,
            "last_date": last_date, "last_value": last_value}


def good_pair():
    before = {k: mk(mtime=500.0, rows=90, last_date="2026-09-25") for k in CORE + AUX}
    after = {k: mk(mtime=2_000.0, rows=100) for k in CORE + AUX}
    return before, after


LOG_OK = ("  最终资金      : 100.00\n"
          "  ✅ official   产出 9 个因子\n  多源挖掘追加: 2\n")
LOG_BAD = "  最终资金      : 4321.00"

results = []


def judge(tag, checks, must_red, exact=False):
    """must_red：这一支里**必须**变红的判据序号（0 起）；exact ⇒ 红名单必须**恰好**等于它

    三态里只有 `False` 算红（`None` 是「判不出、只念不拦」，与 `print_checks` 同一口径）。
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


print("\n──── 正支 P：09-29 真产物形状 + 真 official 产出行（七条 A–G 一条都不许红）────")
b, a = good_pair()
p = chain.verify_analysis(b, a, T_START, LOG_OK)
judge("P 全绿", p, must_red=[], exact=True)

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

print("\n──── 判定 ────")
bad = [tag for tag, ok, _, _ in results if not ok]
for tag, ok, got, want in results:
    print(f"  {tag:<18} {'✅' if ok else '❌'} 红的条数={got} 要求覆盖={want}")
if bad:
    raise SystemExit(f"[自检失败] {bad} ⇒ 验收判据与它的对照不一致，链路的 ③ 验收不可信")
print("[自检通过] 正支七条 A–G 全绿（exact）+ 五支人造坏各红在指定的那一条 "
      "⇒ A–G 与那张表打印机都不是恒绿")
